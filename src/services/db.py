import asyncpg

from config import DATABASE_URL

# One shared pool for the whole bot, opening a fresh connection per query would be slow
# and Supabase's free tier caps how many connections can be open at once anyway.
_pool: asyncpg.Pool | None = None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS watchlist (
    guild_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    ticker TEXT NOT NULL,
    added_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (guild_id, user_id, ticker)
);

CREATE TABLE IF NOT EXISTS tracked (
    guild_id BIGINT NOT NULL,
    ticker TEXT NOT NULL,
    added_by BIGINT NOT NULL,
    added_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_alert_date TEXT,
    PRIMARY KEY (guild_id, ticker)
);

CREATE TABLE IF NOT EXISTS alerts (
    id SERIAL PRIMARY KEY,
    guild_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    ticker TEXT NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('above', 'below')),
    target_price REAL NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS price_target_cache (
    ticker TEXT PRIMARY KEY,
    target_price REAL NOT NULL,
    fetched_date TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS guild_settings (
    guild_id BIGINT PRIMARY KEY,
    alerts_role_id BIGINT
);
"""


async def init_db() -> None:
    global _pool
    # statement_cache_size=0 is required for Supabase's connection pooler (Supavisor), it
    # runs in "transaction mode" which doesn't support the prepared statements asyncpg
    # normally caches per query, without this every query after the first would error out.
    _pool = await asyncpg.create_pool(dsn=DATABASE_URL, min_size=1, max_size=5, statement_cache_size=0)
    async with _pool.acquire() as conn:
        # "IF NOT EXISTS" makes this safe to run on every startup, not just the first one.
        await conn.execute(_SCHEMA)


def _affected(status: str) -> int:
    # asyncpg returns command results as a status string like "DELETE 1", the row count
    # is always the last space-separated piece of it.
    return int(status.split()[-1])


# Personal watchlists, scoped per user per server so friends can track different tickers.


async def add_to_watchlist(guild_id: int, user_id: int, ticker: str) -> bool:
    async with _pool.acquire() as conn:
        try:
            await conn.execute(
                "INSERT INTO watchlist (guild_id, user_id, ticker) VALUES ($1, $2, $3)",
                guild_id, user_id, ticker,
            )
            return True
        except asyncpg.UniqueViolationError:
            # The table's PRIMARY KEY caught this, meaning the ticker is already on their watchlist.
            return False


async def remove_from_watchlist(guild_id: int, user_id: int, ticker: str) -> bool:
    async with _pool.acquire() as conn:
        status = await conn.execute(
            "DELETE FROM watchlist WHERE guild_id = $1 AND user_id = $2 AND ticker = $3",
            guild_id, user_id, ticker,
        )
        return _affected(status) > 0


async def get_watchlist(guild_id: int, user_id: int) -> list[str]:
    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT ticker FROM watchlist WHERE guild_id = $1 AND user_id = $2 ORDER BY ticker",
            guild_id, user_id,
        )
        return [r["ticker"] for r in rows]


# The server-wide tracked list, this is exactly what cogs/scheduler.py scans for moves.


async def add_tracked(guild_id: int, ticker: str, added_by: int) -> bool:
    async with _pool.acquire() as conn:
        try:
            await conn.execute(
                "INSERT INTO tracked (guild_id, ticker, added_by) VALUES ($1, $2, $3)",
                guild_id, ticker, added_by,
            )
            return True
        except asyncpg.UniqueViolationError:
            return False


async def remove_tracked(guild_id: int, ticker: str) -> bool:
    async with _pool.acquire() as conn:
        status = await conn.execute(
            "DELETE FROM tracked WHERE guild_id = $1 AND ticker = $2",
            guild_id, ticker,
        )
        return _affected(status) > 0


async def get_tracked(guild_id: int) -> list[str]:
    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT ticker FROM tracked WHERE guild_id = $1 ORDER BY ticker", guild_id
        )
        return [r["ticker"] for r in rows]


async def all_tracked_by_guild() -> dict[int, list[str]]:
    # One query for every server's tickers, instead of a separate query per server.
    async with _pool.acquire() as conn:
        rows = await conn.fetch("SELECT guild_id, ticker FROM tracked")

    result: dict[int, list[str]] = {}
    for row in rows:
        result.setdefault(row["guild_id"], []).append(row["ticker"])
    return result


async def get_last_alert_date(guild_id: int, ticker: str) -> str | None:
    # Lets the scheduler post a "big move" alert once per ticker per day instead of every 15 minutes.
    async with _pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT last_alert_date FROM tracked WHERE guild_id = $1 AND ticker = $2",
            guild_id, ticker,
        )


async def set_last_alert_date(guild_id: int, ticker: str, date_str: str) -> None:
    async with _pool.acquire() as conn:
        await conn.execute(
            "UPDATE tracked SET last_alert_date = $1 WHERE guild_id = $2 AND ticker = $3",
            date_str, guild_id, ticker,
        )


# Price alerts, each belongs to one user and gets deactivated rather than deleted once it triggers.


async def add_alert(guild_id: int, user_id: int, ticker: str, direction: str, target_price: float) -> int:
    async with _pool.acquire() as conn:
        # RETURNING id hands back the row's auto-generated ID, so the user has a short
        # number to reference later instead of the raw alert row.
        return await conn.fetchval(
            "INSERT INTO alerts (guild_id, user_id, ticker, direction, target_price) "
            "VALUES ($1, $2, $3, $4, $5) RETURNING id",
            guild_id, user_id, ticker, direction, target_price,
        )


async def remove_alert(alert_id: int, user_id: int) -> bool:
    async with _pool.acquire() as conn:
        # Checking user_id too stops someone cancelling another person's alert.
        status = await conn.execute(
            "DELETE FROM alerts WHERE id = $1 AND user_id = $2", alert_id, user_id
        )
        return _affected(status) > 0


async def get_user_alerts(guild_id: int, user_id: int) -> list[tuple]:
    async with _pool.acquire() as conn:
        return await conn.fetch(
            "SELECT id, ticker, direction, target_price FROM alerts "
            "WHERE guild_id = $1 AND user_id = $2 AND active = 1",
            guild_id, user_id,
        )


async def get_active_alerts() -> list[tuple]:
    # The scheduler grabs every active alert from every user in every server in one query.
    async with _pool.acquire() as conn:
        return await conn.fetch(
            "SELECT id, guild_id, user_id, ticker, direction, target_price "
            "FROM alerts WHERE active = 1"
        )


async def deactivate_alert(alert_id: int) -> None:
    async with _pool.acquire() as conn:
        await conn.execute("UPDATE alerts SET active = 0 WHERE id = $1", alert_id)


# Alpha Vantage price target cache, their free tier caps out at 25 requests/day total.


async def get_cached_price_target(ticker: str) -> tuple[float, str] | None:
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT target_price, fetched_date FROM price_target_cache WHERE ticker = $1",
            ticker,
        )
        return (row["target_price"], row["fetched_date"]) if row else None


async def set_cached_price_target(ticker: str, target_price: float, date_str: str) -> None:
    async with _pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO price_target_cache (ticker, target_price, fetched_date) VALUES ($1, $2, $3) "
            "ON CONFLICT (ticker) DO UPDATE SET target_price = excluded.target_price, "
            "fetched_date = excluded.fetched_date",
            ticker, target_price, date_str,
        )


# Per-server settings, just the Stock Alerts role for now, set up through /notify.


async def get_alerts_role_id(guild_id: int) -> int | None:
    async with _pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT alerts_role_id FROM guild_settings WHERE guild_id = $1", guild_id
        )


async def set_alerts_role_id(guild_id: int, role_id: int) -> None:
    async with _pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO guild_settings (guild_id, alerts_role_id) VALUES ($1, $2) "
            "ON CONFLICT (guild_id) DO UPDATE SET alerts_role_id = excluded.alerts_role_id",
            guild_id, role_id,
        )
