import asyncpg

from config import DATABASE_URL

# One shared pool for the whole bot, Supabase's free tier caps how many connections can be open at once.
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
    alerts_role_id BIGINT,
    updates_channel_id BIGINT
);

CREATE TABLE IF NOT EXISTS digest_optin (
    guild_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    content TEXT NOT NULL DEFAULT 'watchlist',
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS portfolio (
    guild_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    ticker TEXT NOT NULL,
    shares DOUBLE PRECISION NOT NULL,
    cost_basis DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (guild_id, user_id, ticker)
);

CREATE TABLE IF NOT EXISTS breaking_move_alerts (
    ticker TEXT PRIMARY KEY,
    last_alert_date TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS server_digest (
    guild_id BIGINT PRIMARY KEY,
    channel_id BIGINT NOT NULL,
    period TEXT NOT NULL DEFAULT 'day',
    include_movers BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS bot_guilds (
    guild_id BIGINT PRIMARY KEY,
    name TEXT NOT NULL,
    icon_hash TEXT
);

CREATE TABLE IF NOT EXISTS feedback (
    id SERIAL PRIMARY KEY,
    guild_id BIGINT,
    user_id BIGINT NOT NULL,
    category TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS bot_status (
    singleton_id INTEGER PRIMARY KEY DEFAULT 1 CHECK (singleton_id = 1),
    last_heartbeat TIMESTAMPTZ NOT NULL
);
"""


async def init_db() -> None:
    global _pool
    # statement_cache_size=0 is required for Supabase's pooler, its transaction mode can't support prepared statements.
    _pool = await asyncpg.create_pool(dsn=DATABASE_URL, min_size=1, max_size=5, statement_cache_size=0)
    async with _pool.acquire() as conn:
        # "IF NOT EXISTS" makes this safe to run on every startup, not just the first one.
        await conn.execute(_SCHEMA)
        # CREATE TABLE IF NOT EXISTS above only helps fresh databases, existing ones need this to pick up new columns.
        await conn.execute("ALTER TABLE digest_optin ADD COLUMN IF NOT EXISTS content TEXT NOT NULL DEFAULT 'watchlist'")
        await conn.execute("ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS updates_channel_id BIGINT")


def _affected(status: str) -> int:
    # asyncpg returns results as a status string like "DELETE 1", the row count is always the last piece of it.
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


async def all_last_alert_dates_by_guild() -> dict[int, dict[str, str | None]]:
    # One query for every server's last-alert dates, instead of a separate query per (guild, ticker) pair,
    # _check_movers used to do exactly that every CHECK_INTERVAL_MINUTES, this is that same table already
    # queried by all_tracked_by_guild above, just carrying the extra column along instead of a second round trip.
    async with _pool.acquire() as conn:
        rows = await conn.fetch("SELECT guild_id, ticker, last_alert_date FROM tracked")

    result: dict[int, dict[str, str | None]] = {}
    for row in rows:
        result.setdefault(row["guild_id"], {})[row["ticker"]] = row["last_alert_date"]
    return result


async def set_last_alert_date(guild_id: int, ticker: str, date_str: str) -> None:
    async with _pool.acquire() as conn:
        await conn.execute(
            "UPDATE tracked SET last_alert_date = $1 WHERE guild_id = $2 AND ticker = $3",
            date_str, guild_id, ticker,
        )


# Price alerts, each belongs to one user and gets deactivated rather than deleted once it triggers.
async def add_alert(guild_id: int, user_id: int, ticker: str, direction: str, target_price: float) -> int:
    async with _pool.acquire() as conn:
        # RETURNING id hands back the auto-generated ID as a short number the user can reference later.
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
    # Ordered so the display numbering in cogs/alerts.py is stable, alerts.id is a global sequence, not per-user.
    async with _pool.acquire() as conn:
        return await conn.fetch(
            "SELECT id, ticker, direction, target_price FROM alerts "
            "WHERE guild_id = $1 AND user_id = $2 AND active = 1 ORDER BY id ASC",
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


async def get_updates_channel_id(guild_id: int) -> int | None:
    async with _pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT updates_channel_id FROM guild_settings WHERE guild_id = $1", guild_id
        )


async def set_updates_channel_id(guild_id: int, channel_id: int) -> None:
    async with _pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO guild_settings (guild_id, updates_channel_id) VALUES ($1, $2) "
            "ON CONFLICT (guild_id) DO UPDATE SET updates_channel_id = excluded.updates_channel_id",
            guild_id, channel_id,
        )


# Every guild that has an updates channel configured, fetched once per scheduler pass instead of once per guild.
async def get_all_updates_channel_ids() -> dict[int, int]:
    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT guild_id, updates_channel_id FROM guild_settings WHERE updates_channel_id IS NOT NULL"
        )
    return {r["guild_id"]: r["updates_channel_id"] for r in rows}


# Daily digest opt-in, one row per person per server, checked every morning by the scheduler.
async def enable_digest(guild_id: int, user_id: int, content: str = "watchlist") -> bool:
    async with _pool.acquire() as conn:
        try:
            await conn.execute(
                "INSERT INTO digest_optin (guild_id, user_id, content) VALUES ($1, $2, $3)",
                guild_id, user_id, content,
            )
            return True
        except asyncpg.UniqueViolationError:
            return False


async def disable_digest(guild_id: int, user_id: int) -> bool:
    async with _pool.acquire() as conn:
        status = await conn.execute(
            "DELETE FROM digest_optin WHERE guild_id = $1 AND user_id = $2",
            guild_id, user_id,
        )
        return _affected(status) > 0


async def get_digest_content(guild_id: int, user_id: int) -> str | None:
    # None means never opted in, distinct from a real saved preference, /digest_now falls back to "both" for that case.
    async with _pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT content FROM digest_optin WHERE guild_id = $1 AND user_id = $2",
            guild_id, user_id,
        )


async def set_digest_content(guild_id: int, user_id: int, content: str) -> None:
    async with _pool.acquire() as conn:
        await conn.execute(
            "UPDATE digest_optin SET content = $1 WHERE guild_id = $2 AND user_id = $3",
            content, guild_id, user_id,
        )


async def is_digest_enabled(guild_id: int, user_id: int) -> bool:
    async with _pool.acquire() as conn:
        row = await conn.fetchval(
            "SELECT 1 FROM digest_optin WHERE guild_id = $1 AND user_id = $2",
            guild_id, user_id,
        )
        return row is not None


async def all_digest_optins() -> list[tuple[int, int, str]]:
    # Same one-query-for-everyone pattern as all_tracked_by_guild above.
    async with _pool.acquire() as conn:
        rows = await conn.fetch("SELECT guild_id, user_id, content FROM digest_optin")
        return [(r["guild_id"], r["user_id"], r["content"]) for r in rows]


# Personal portfolio, separate from /watchlist which has no position attached, cost_basis is a weighted average.
async def get_portfolio(guild_id: int, user_id: int) -> list[tuple]:
    async with _pool.acquire() as conn:
        return await conn.fetch(
            "SELECT ticker, shares, cost_basis FROM portfolio "
            "WHERE guild_id = $1 AND user_id = $2 ORDER BY ticker",
            guild_id, user_id,
        )


async def buy_position(guild_id: int, user_id: int, ticker: str, shares: float, price: float) -> None:
    async with _pool.acquire() as conn:
        existing = await conn.fetchrow(
            "SELECT shares, cost_basis FROM portfolio WHERE guild_id = $1 AND user_id = $2 AND ticker = $3",
            guild_id, user_id, ticker,
        )
        if existing:
            # Blends into one weighted average cost instead of tracking each purchase as its own lot.
            total_shares = existing["shares"] + shares
            total_cost = existing["shares"] * existing["cost_basis"] + shares * price
            new_cost_basis = total_cost / total_shares
            await conn.execute(
                "UPDATE portfolio SET shares = $1, cost_basis = $2 "
                "WHERE guild_id = $3 AND user_id = $4 AND ticker = $5",
                total_shares, new_cost_basis, guild_id, user_id, ticker,
            )
        else:
            await conn.execute(
                "INSERT INTO portfolio (guild_id, user_id, ticker, shares, cost_basis) "
                "VALUES ($1, $2, $3, $4, $5)",
                guild_id, user_id, ticker, shares, price,
            )


async def sell_position(guild_id: int, user_id: int, ticker: str, shares: float) -> str:
    # Returns "ok", "not_found", or "too_many" so the cog can show a specific error message.
    async with _pool.acquire() as conn:
        existing = await conn.fetchrow(
            "SELECT shares FROM portfolio WHERE guild_id = $1 AND user_id = $2 AND ticker = $3",
            guild_id, user_id, ticker,
        )
        if not existing:
            return "not_found"
        if shares > existing["shares"]:
            return "too_many"

        remaining = existing["shares"] - shares
        # Epsilon guards against float dust, e.g. 10.0 - 10.0 landing on 0.0000000001 instead of exactly 0.
        if remaining <= 1e-9:
            await conn.execute(
                "DELETE FROM portfolio WHERE guild_id = $1 AND user_id = $2 AND ticker = $3",
                guild_id, user_id, ticker,
            )
        else:
            await conn.execute(
                "UPDATE portfolio SET shares = $1 WHERE guild_id = $2 AND user_id = $3 AND ticker = $4",
                remaining, guild_id, user_id, ticker,
            )
        return "ok"


async def remove_position(guild_id: int, user_id: int, ticker: str) -> bool:
    async with _pool.acquire() as conn:
        status = await conn.execute(
            "DELETE FROM portfolio WHERE guild_id = $1 AND user_id = $2 AND ticker = $3",
            guild_id, user_id, ticker,
        )
        return _affected(status) > 0


async def edit_position(
    guild_id: int, user_id: int, ticker: str, shares: float | None, cost_basis: float | None
) -> bool:
    # Overwrites directly instead of blending like buy_position, this is for fixing a typo'd entry.
    async with _pool.acquire() as conn:
        existing = await conn.fetchrow(
            "SELECT shares, cost_basis FROM portfolio WHERE guild_id = $1 AND user_id = $2 AND ticker = $3",
            guild_id, user_id, ticker,
        )
        if not existing:
            return False

        new_shares = shares if shares is not None else existing["shares"]
        new_cost_basis = cost_basis if cost_basis is not None else existing["cost_basis"]
        await conn.execute(
            "UPDATE portfolio SET shares = $1, cost_basis = $2 WHERE guild_id = $3 AND user_id = $4 AND ticker = $5",
            new_shares, new_cost_basis, guild_id, user_id, ticker,
        )
        return True


# Dedup for the breaking-move scan, one row per ticker (not per server), since it checks a fixed global list.
async def get_breaking_alert_date(ticker: str) -> str | None:
    async with _pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT last_alert_date FROM breaking_move_alerts WHERE ticker = $1", ticker
        )


async def set_breaking_alert_date(ticker: str, date_str: str) -> None:
    async with _pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO breaking_move_alerts (ticker, last_alert_date) VALUES ($1, $2) "
            "ON CONFLICT (ticker) DO UPDATE SET last_alert_date = excluded.last_alert_date",
            ticker, date_str,
        )


# One row per server, /serverdigest posts a daily tracked-list summary to whatever channel this points at.
async def set_server_digest(guild_id: int, channel_id: int, period: str, include_movers: bool) -> None:
    async with _pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO server_digest (guild_id, channel_id, period, include_movers) VALUES ($1, $2, $3, $4) "
            "ON CONFLICT (guild_id) DO UPDATE SET "
            "channel_id = excluded.channel_id, period = excluded.period, include_movers = excluded.include_movers",
            guild_id, channel_id, period, include_movers,
        )


async def disable_server_digest(guild_id: int) -> bool:
    async with _pool.acquire() as conn:
        status = await conn.execute("DELETE FROM server_digest WHERE guild_id = $1", guild_id)
        return _affected(status) > 0


async def all_server_digests() -> list[dict]:
    async with _pool.acquire() as conn:
        rows = await conn.fetch("SELECT guild_id, channel_id, period, include_movers FROM server_digest")
        return [dict(r) for r in rows]


# Lets the website figure out which of a user's own Discord servers actually have the bot installed.
async def upsert_bot_guild(guild_id: int, name: str, icon_hash: str | None) -> None:
    async with _pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO bot_guilds (guild_id, name, icon_hash) VALUES ($1, $2, $3) "
            "ON CONFLICT (guild_id) DO UPDATE SET name = excluded.name, icon_hash = excluded.icon_hash",
            guild_id, name, icon_hash,
        )


async def remove_bot_guild(guild_id: int) -> None:
    async with _pool.acquire() as conn:
        await conn.execute("DELETE FROM bot_guilds WHERE guild_id = $1", guild_id)


async def sync_bot_guilds(guilds: list[tuple[int, str, str | None]]) -> None:
    # Reconciles the full table against bot.guilds on startup, Discord doesn't replay join/leave events missed offline.
    async with _pool.acquire() as conn:
        async with conn.transaction():
            for guild_id, name, icon_hash in guilds:
                await conn.execute(
                    "INSERT INTO bot_guilds (guild_id, name, icon_hash) VALUES ($1, $2, $3) "
                    "ON CONFLICT (guild_id) DO UPDATE SET name = excluded.name, icon_hash = excluded.icon_hash",
                    guild_id, name, icon_hash,
                )
            current_ids = [g[0] for g in guilds]
            await conn.execute("DELETE FROM bot_guilds WHERE guild_id <> ALL($1::bigint[])", current_ids)


# Kept durably, not just DMed to owners, so nothing gets lost if nobody's watching DMs when it comes in.
async def add_feedback(guild_id: int | None, user_id: int, category: str, message: str) -> None:
    async with _pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO feedback (guild_id, user_id, category, message) VALUES ($1, $2, $3, $4)",
            guild_id, user_id, category, message,
        )


async def ping() -> None:
    # Just proves the pool can actually round-trip a query, for /status and the website's status page.
    async with _pool.acquire() as conn:
        await conn.fetchval("SELECT 1")


async def update_heartbeat() -> None:
    async with _pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO bot_status (singleton_id, last_heartbeat) VALUES (1, now()) "
            "ON CONFLICT (singleton_id) DO UPDATE SET last_heartbeat = excluded.last_heartbeat"
        )
