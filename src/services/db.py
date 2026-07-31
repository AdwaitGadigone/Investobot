import aiosqlite

from config import DB_PATH

_SCHEMA = """
CREATE TABLE IF NOT EXISTS watchlist (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    ticker TEXT NOT NULL,
    added_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (guild_id, user_id, ticker)
);

CREATE TABLE IF NOT EXISTS tracked (
    guild_id INTEGER NOT NULL,
    ticker TEXT NOT NULL,
    added_by INTEGER NOT NULL,
    added_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_alert_date TEXT,
    PRIMARY KEY (guild_id, ticker)
);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    ticker TEXT NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('above', 'below')),
    target_price REAL NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS seen_news (
    guild_id INTEGER NOT NULL,
    news_id TEXT NOT NULL,
    PRIMARY KEY (guild_id, news_id)
);

CREATE TABLE IF NOT EXISTS price_target_cache (
    ticker TEXT PRIMARY KEY,
    target_price REAL NOT NULL,
    fetched_date TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS guild_settings (
    guild_id INTEGER PRIMARY KEY,
    alerts_role_id INTEGER
);
"""


async def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        # "IF NOT EXISTS" makes this safe to run on every startup, not just the first one.
        await db.executescript(_SCHEMA)
        await db.commit()


# Personal watchlists, scoped per user per server so friends can track different tickers.


async def add_to_watchlist(guild_id: int, user_id: int, ticker: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute(
                "INSERT INTO watchlist (guild_id, user_id, ticker) VALUES (?, ?, ?)",
                (guild_id, user_id, ticker),
            )
            await db.commit()
            return True
        except aiosqlite.IntegrityError:
            # The table's PRIMARY KEY caught this, meaning the ticker is already on their watchlist.
            return False


async def remove_from_watchlist(guild_id: int, user_id: int, ticker: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "DELETE FROM watchlist WHERE guild_id = ? AND user_id = ? AND ticker = ?",
            (guild_id, user_id, ticker),
        )
        await db.commit()
        return cur.rowcount > 0


async def get_watchlist(guild_id: int, user_id: int) -> list[str]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT ticker FROM watchlist WHERE guild_id = ? AND user_id = ? ORDER BY ticker",
            (guild_id, user_id),
        )
        rows = await cur.fetchall()
        return [r[0] for r in rows]


# The server-wide tracked list, this is exactly what cogs/scheduler.py scans for moves and news.


async def add_tracked(guild_id: int, ticker: str, added_by: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute(
                "INSERT INTO tracked (guild_id, ticker, added_by) VALUES (?, ?, ?)",
                (guild_id, ticker, added_by),
            )
            await db.commit()
            return True
        except aiosqlite.IntegrityError:
            return False


async def remove_tracked(guild_id: int, ticker: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "DELETE FROM tracked WHERE guild_id = ? AND ticker = ?",
            (guild_id, ticker),
        )
        await db.commit()
        return cur.rowcount > 0


async def get_tracked(guild_id: int) -> list[str]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT ticker FROM tracked WHERE guild_id = ? ORDER BY ticker", (guild_id,)
        )
        rows = await cur.fetchall()
        return [r[0] for r in rows]


async def all_tracked_by_guild() -> dict[int, list[str]]:
    # One query for every server's tickers, instead of a separate query per server.
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT guild_id, ticker FROM tracked")
        rows = await cur.fetchall()

    result: dict[int, list[str]] = {}
    for guild_id, ticker in rows:
        result.setdefault(guild_id, []).append(ticker)
    return result


async def get_last_alert_date(guild_id: int, ticker: str) -> str | None:
    # Lets the scheduler post a "big move" alert once per ticker per day instead of every 15 minutes.
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT last_alert_date FROM tracked WHERE guild_id = ? AND ticker = ?",
            (guild_id, ticker),
        )
        row = await cur.fetchone()
        return row[0] if row else None


async def set_last_alert_date(guild_id: int, ticker: str, date_str: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE tracked SET last_alert_date = ? WHERE guild_id = ? AND ticker = ?",
            (date_str, guild_id, ticker),
        )
        await db.commit()


# Price alerts, each belongs to one user and gets deactivated rather than deleted once it triggers.


async def add_alert(guild_id: int, user_id: int, ticker: str, direction: str, target_price: float) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO alerts (guild_id, user_id, ticker, direction, target_price) VALUES (?, ?, ?, ?, ?)",
            (guild_id, user_id, ticker, direction, target_price),
        )
        await db.commit()
        # SQLite's auto-incremented ID, so the user has a short number to reference later.
        return cur.lastrowid


async def remove_alert(alert_id: int, user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        # Checking user_id too stops someone cancelling another person's alert.
        cur = await db.execute(
            "DELETE FROM alerts WHERE id = ? AND user_id = ?", (alert_id, user_id)
        )
        await db.commit()
        return cur.rowcount > 0


async def get_user_alerts(guild_id: int, user_id: int) -> list[tuple]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT id, ticker, direction, target_price FROM alerts "
            "WHERE guild_id = ? AND user_id = ? AND active = 1",
            (guild_id, user_id),
        )
        return await cur.fetchall()


async def get_active_alerts() -> list[tuple]:
    # The scheduler grabs every active alert from every user in every server in one query.
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT id, guild_id, user_id, ticker, direction, target_price "
            "FROM alerts WHERE active = 1"
        )
        return await cur.fetchall()


async def deactivate_alert(alert_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE alerts SET active = 0 WHERE id = ?", (alert_id,))
        await db.commit()


# News dedup, stops the bot reposting the same headline since Finnhub repeats articles on every call.


async def is_news_seen(guild_id: int, news_id: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT 1 FROM seen_news WHERE guild_id = ? AND news_id = ?",
            (guild_id, news_id),
        )
        return await cur.fetchone() is not None


async def mark_news_seen(guild_id: int, news_id: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute(
                "INSERT INTO seen_news (guild_id, news_id) VALUES (?, ?)",
                (guild_id, news_id),
            )
            await db.commit()
        except aiosqlite.IntegrityError:
            # Already marked as seen from an earlier check.
            pass


# Alpha Vantage price target cache, their free tier caps out at 25 requests/day total.


async def get_cached_price_target(ticker: str) -> tuple[float, str] | None:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT target_price, fetched_date FROM price_target_cache WHERE ticker = ?",
            (ticker,),
        )
        row = await cur.fetchone()
        return (row[0], row[1]) if row else None


async def set_cached_price_target(ticker: str, target_price: float, date_str: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO price_target_cache (ticker, target_price, fetched_date) VALUES (?, ?, ?) "
            "ON CONFLICT(ticker) DO UPDATE SET target_price = excluded.target_price, "
            "fetched_date = excluded.fetched_date",
            (ticker, target_price, date_str),
        )
        await db.commit()


# Per-server settings, just the Stock Alerts role for now, set up through /notify.


async def get_alerts_role_id(guild_id: int) -> int | None:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT alerts_role_id FROM guild_settings WHERE guild_id = ?", (guild_id,)
        )
        row = await cur.fetchone()
        return row[0] if row else None


async def set_alerts_role_id(guild_id: int, role_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO guild_settings (guild_id, alerts_role_id) VALUES (?, ?) "
            "ON CONFLICT(guild_id) DO UPDATE SET alerts_role_id = excluded.alerts_role_id",
            (guild_id, role_id),
        )
        await db.commit()
