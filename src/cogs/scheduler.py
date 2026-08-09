import asyncio
import logging
from datetime import datetime, timezone

import discord
from discord.ext import commands, tasks

from config import (
    BIG_MOVE_THRESHOLD_PCT,
    BREAKING_MOVE_THRESHOLD_PCT,
    BREAKING_WATCH_TICKERS,
    CHECK_INTERVAL_MINUTES,
    DIGEST_TIME_UTC,
    UPDATES_CHANNEL_ID,
)
from cogs.stocks import build_quote_embed
from services import db, market_data

log = logging.getLogger("investo.scheduler")


def _digest_line(quote: dict) -> str:
    # Same ANSI color trick as cogs/stocks.py's quote embed, one line per ticker in the digest DM.
    esc = chr(27)
    is_up = quote["change_pct"] >= 0
    color_code = "32" if is_up else "31"
    arrow = "▲" if is_up else "▼"
    reset = f"{esc}[0m"
    return (
        f"{esc}[1;{color_code}m{quote['ticker']:<6}${quote['price']:>10,.2f}  "
        f"{arrow} {quote['change_pct']:+.2f}%{reset}"
    )


def _portfolio_digest_line(ticker: str, shares: float, cost_basis: float, quote: dict) -> str:
    # Value + today's move + all-time P/L on one line, same ANSI-color pattern as the watchlist line above.
    esc = chr(27)
    reset = f"{esc}[0m"
    price = quote["price"]
    value = shares * price
    cost = shares * cost_basis
    pl_pct = ((value - cost) / cost * 100) if cost else 0.0
    color_code = "32" if pl_pct >= 0 else "31"
    today_arrow = "▲" if quote["change_pct"] >= 0 else "▼"
    return (
        f"{esc}[1;{color_code}m{ticker:<6}${value:>9,.2f}  "
        f"today {today_arrow}{quote['change_pct']:+.2f}%  P/L {pl_pct:+.1f}%{reset}"
    )


def _portfolio_digest_summary(total_value: float, total_today: float, total_pl: float, total_cost: float) -> str:
    # Sits above the per-position lines, same shape as the "Total value / Today / All-time" cards on the website.
    esc = chr(27)
    reset = f"{esc}[0m"
    today_base = total_value - total_today
    today_pct = (total_today / today_base * 100) if today_base else 0.0
    pl_pct = (total_pl / total_cost * 100) if total_cost else 0.0
    today_color = "32" if total_today >= 0 else "31"
    pl_color = "32" if total_pl >= 0 else "31"
    return (
        f"Total ${total_value:,.2f}\n"
        f"{esc}[1;{today_color}mToday {total_today:+,.2f} ({today_pct:+.2f}%){reset}\n"
        f"{esc}[1;{pl_color}mAll-time {total_pl:+,.2f} ({pl_pct:+.1f}%){reset}"
    )


async def _build_digest_embed(
    guild_id: int, user_id: int, content: str, quote_cache: dict[str, dict] | None = None
) -> discord.Embed:
    # Shared by the daily auto-send and the dropdown's live edit, so both always render identically.
    if quote_cache is None:
        quote_cache = {}

    async def ensure_quotes(tickers: list[str]) -> None:
        missing = [t for t in tickers if t not in quote_cache]
        if not missing:
            return
        fetched = await asyncio.gather(*(market_data.get_quote(t) for t in missing), return_exceptions=True)
        for ticker, result in zip(missing, fetched):
            if isinstance(result, Exception):
                log.exception("Failed to fetch quote for digest ticker %s", ticker, exc_info=result)
            else:
                quote_cache[ticker] = result

    embed = discord.Embed(title="🌤️ Your Morning Digest", color=discord.Color.blurple())

    if content in ("watchlist", "both"):
        tickers = await db.get_watchlist(guild_id, user_id)
        if tickers:
            await ensure_quotes(tickers)
            lines = [_digest_line(quote_cache[t]) for t in tickers if t in quote_cache]
            if lines:
                embed.add_field(name="👀 Watchlist", value="```ansi\n" + "\n".join(lines) + "\n```", inline=False)

    if content in ("portfolio", "both"):
        positions = await db.get_portfolio(guild_id, user_id)
        if positions:
            await ensure_quotes([p["ticker"] for p in positions])
            priced = [p for p in positions if p["ticker"] in quote_cache]
            if priced:
                total_value = sum(p["shares"] * quote_cache[p["ticker"]]["price"] for p in priced)
                total_cost = sum(p["shares"] * p["cost_basis"] for p in priced)
                total_today = sum(p["shares"] * quote_cache[p["ticker"]]["change"] for p in priced)
                summary = _portfolio_digest_summary(total_value, total_today, total_value - total_cost, total_cost)
                lines = [
                    _portfolio_digest_line(p["ticker"], p["shares"], p["cost_basis"], quote_cache[p["ticker"]])
                    for p in priced
                ]
                embed.add_field(
                    name="💼 Portfolio",
                    value=f"{summary}\n```ansi\n" + "\n".join(lines) + "\n```",
                    inline=False,
                )

    if not embed.fields:
        # embed.fields stays empty either way, so callers can still tell "nothing to show" apart from real content.
        embed.description = "Nothing to show yet. Add tickers with `/watchlist` or holdings with `/portfolio`."

    embed.set_footer(text="Switch what this shows with the dropdown below, or turn it off with /digest")
    return embed


class DigestContentSelect(discord.ui.Select):
    def __init__(self, guild_id: int, user_id: int, current: str):
        options = [
            discord.SelectOption(label="Watchlist", value="watchlist", emoji="👀", default=current == "watchlist"),
            discord.SelectOption(label="Portfolio", value="portfolio", emoji="💼", default=current == "portfolio"),
            discord.SelectOption(label="Both", value="both", emoji="🌤️", default=current == "both"),
        ]
        super().__init__(placeholder="Change what this shows...", options=options, min_values=1, max_values=1)
        self.guild_id = guild_id
        self.user_id = user_id

    async def callback(self, interaction: discord.Interaction):
        content = self.values[0]
        # Persisted, so tomorrow's auto-send opens on whatever the user last picked here too.
        await db.set_digest_content(self.guild_id, self.user_id, content)
        embed = await _build_digest_embed(self.guild_id, self.user_id, content)
        await interaction.response.edit_message(embed=embed, view=DigestView(self.guild_id, self.user_id, content))


class DigestView(discord.ui.View):
    # A simple 24h-timeout view, not a persistent one, so it won't survive a redeploy but needs no startup wiring.
    def __init__(self, guild_id: int, user_id: int, content: str):
        super().__init__(timeout=86400)
        self.add_item(DigestContentSelect(guild_id, user_id, content))


# Labels double as the dropdown order on the posted server digest, day first since that's the default.
SERVER_DIGEST_PERIOD_LABELS = {
    "day": "Today",
    "week": "Past week",
    "month": "Past month",
    "three_month": "Past 3 months",
    "year": "Past year",
}


async def _server_digest_rows(tickers: list[str], period: str) -> list[dict]:
    rows = []
    for ticker in tickers:
        try:
            quote = await market_data.get_quote(ticker)
        except Exception:
            log.exception("Failed to fetch quote for server digest ticker %s", ticker)
            continue

        if period == "day":
            rows.append({"ticker": ticker, "price": quote["price"], "change_pct": quote["change_pct"]})
            continue

        change = await market_data.get_period_change(ticker, period)
        if change:
            rows.append({"ticker": ticker, "price": quote["price"], "change_pct": change["pct"]})
    return rows


async def _build_server_digest_embed(
    tickers: list[str], period: str, include_movers: bool, big_movers: list[dict]
) -> discord.Embed:
    label = SERVER_DIGEST_PERIOD_LABELS.get(period, "Today")
    embed = discord.Embed(title=f"📊 Server Digest: {label}", color=discord.Color.blurple())

    if tickers:
        rows = await _server_digest_rows(tickers, period)
        if rows:
            embed.add_field(
                name="📋 Tracked", value="```ansi\n" + "\n".join(_digest_line(r) for r in rows) + "\n```", inline=False
            )
        else:
            embed.description = "No data available for the tracked list right now."
    else:
        embed.description = "Nobody's tracking any tickers yet, add some with `/track`."

    if include_movers:
        # Filtered per-server so a ticker already shown in the tracked list above isn't repeated here too.
        tracked_set = set(tickers)
        notable = [m for m in big_movers if m["ticker"] not in tracked_set]
        if notable:
            embed.add_field(
                name="🔥 Notable movers",
                value="```ansi\n" + "\n".join(_digest_line(m) for m in notable[:8]) + "\n```",
                inline=False,
            )

    embed.set_footer(text="Switch the time window below, admins can reconfigure with /serverdigest")
    return embed


class ServerDigestPeriodSelect(discord.ui.Select):
    def __init__(self, tickers: list[str], period: str, include_movers: bool, big_movers: list[dict]):
        options = [
            discord.SelectOption(label=label, value=key, default=key == period)
            for key, label in SERVER_DIGEST_PERIOD_LABELS.items()
        ]
        super().__init__(placeholder="Change the time window...", options=options, min_values=1, max_values=1)
        self.tickers = tickers
        self.include_movers = include_movers
        self.big_movers = big_movers

    async def callback(self, interaction: discord.Interaction):
        # Deliberately not saved anywhere, this only changes how THIS posted message looks, not tomorrow's default.
        period = self.values[0]
        embed = await _build_server_digest_embed(self.tickers, period, self.include_movers, self.big_movers)
        view = ServerDigestView(self.tickers, period, self.include_movers, self.big_movers)
        await interaction.response.edit_message(embed=embed, view=view)


class ServerDigestView(discord.ui.View):
    def __init__(self, tickers: list[str], period: str, include_movers: bool, big_movers: list[dict]):
        super().__init__(timeout=86400)
        self.add_item(ServerDigestPeriodSelect(tickers, period, include_movers, big_movers))


class Scheduler(commands.Cog):
    # The background loop, every CHECK_INTERVAL_MINUTES it checks tracked tickers for big price moves and personal price alerts.

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Starts every loop as soon as this cog is loaded, defined below with @tasks.loop decorators.
        self.check_loop.start()
        self.digest_loop.start()
        self.heartbeat_loop.start()

    def cog_unload(self):
        # Stops every loop cleanly so a hot-reload during development doesn't leave duplicates running.
        self.check_loop.cancel()
        self.digest_loop.cancel()
        self.heartbeat_loop.cancel()

    # tasks.loop turns this into a function that automatically repeats itself every CHECK_INTERVAL_MINUTES.
    @tasks.loop(minutes=CHECK_INTERVAL_MINUTES)
    async def check_loop(self):
        await self._check_movers()
        await self._check_breaking_moves()
        await self._check_alerts()

    # before_loop runs once before the very first iteration of the loop above.
    @check_loop.before_loop
    async def before_check_loop(self):
        # Waiting here stops the loop from firing before the bot has even finished logging in.
        await self.bot.wait_until_ready()

    # A time= loop fires once a day at that exact clock time instead of repeating on an interval.
    @tasks.loop(time=DIGEST_TIME_UTC)
    async def digest_loop(self):
        await self._send_digests()
        await self._send_server_digests()

    @digest_loop.before_loop
    async def before_digest_loop(self):
        await self.bot.wait_until_ready()

    # Short interval on purpose, this is what the website's status page uses to tell if the bot is actually alive.
    @tasks.loop(minutes=2)
    async def heartbeat_loop(self):
        await db.update_heartbeat()

    @heartbeat_loop.before_loop
    async def before_heartbeat_loop(self):
        await self.bot.wait_until_ready()

    async def _updates_channel(self):
        if not UPDATES_CHANNEL_ID:
            return None

        channel = self.bot.get_channel(int(UPDATES_CHANNEL_ID))
        if channel is None:
            try:
                # get_channel only checks the bot's local cache, fetch_channel asks Discord directly.
                channel = await self.bot.fetch_channel(int(UPDATES_CHANNEL_ID))
            except discord.HTTPException:
                log.warning("Could not resolve UPDATES_CHANNEL_ID=%s", UPDATES_CHANNEL_ID)
                return None

        return channel

    async def _check_movers(self):
        # Only posts price moves here, not individual news articles, since a heavily-covered ticker
        # can have 50+ headlines a day and posting each one is the flood this used to cause.
        channel = await self._updates_channel()
        if channel is None:
            return

        # Fetches every tracked ticker across every server in one go, grouped by server.
        tracked_by_guild = await db.all_tracked_by_guild()
        today = datetime.now(timezone.utc).date().isoformat()
        # Shared across every guild in this pass, so a ticker tracked by 3 servers is fetched once, not 3 times.
        quote_cache: dict[str, dict] = {}

        for guild_id, tickers in tracked_by_guild.items():
            role_id = await db.get_alerts_role_id(guild_id)
            # Stays None until someone sets up /notify in this server, in which case nothing gets pinged.
            ping = f"<@&{role_id}>" if role_id else None

            for ticker in tickers:
                if ticker not in quote_cache:
                    try:
                        quote_cache[ticker] = await market_data.get_quote(ticker)
                    except market_data.TickerNotFoundError:
                        continue
                    except Exception:
                        # One bad ticker shouldn't stop the rest of the list from being checked.
                        log.exception("Failed to fetch quote for %s", ticker)
                        continue

                quote = quote_cache[ticker]
                last_alert = await db.get_last_alert_date(guild_id, ticker)
                if abs(quote["change_pct"]) >= BIG_MOVE_THRESHOLD_PCT and last_alert != today:
                    embed = build_quote_embed(quote)
                    embed.title = f"📈 Big move: {embed.title}" if quote["change"] >= 0 else f"📉 Big move: {embed.title}"
                    await channel.send(content=ping, embed=embed)
                    await db.set_last_alert_date(guild_id, ticker, today)

    async def _check_breaking_moves(self):
        # Catches a genuinely wild move on a well-known stock even if nobody bothered to /track it.
        channel = await self._updates_channel()
        if channel is None:
            return

        tracked_by_guild = await db.all_tracked_by_guild()
        # Already covered by _check_movers above at a lower threshold, skip to avoid a double alert.
        already_tracked = {t for tickers in tracked_by_guild.values() for t in tickers}
        today = datetime.now(timezone.utc).date().isoformat()

        for ticker in BREAKING_WATCH_TICKERS:
            if ticker in already_tracked:
                continue

            try:
                quote = await market_data.get_quote(ticker)
            except Exception:
                log.exception("Failed to fetch quote for breaking-move check on %s", ticker)
                continue

            last_alert = await db.get_breaking_alert_date(ticker)
            if abs(quote["change_pct"]) >= BREAKING_MOVE_THRESHOLD_PCT and last_alert != today:
                embed = build_quote_embed(quote)
                direction = "📈" if quote["change"] >= 0 else "📉"
                embed.title = f"{direction} Breaking move: {embed.title}"
                embed.set_footer(text="Data: Yahoo Finance (delayed) • not on anyone's tracked list, just a huge move")
                await channel.send(embed=embed)
                await db.set_breaking_alert_date(ticker, today)

    async def _check_alerts(self):
        alerts = await db.get_active_alerts()

        # If 3 people set an alert on the same ticker, this means 1 price fetch this loop, not 3.
        quote_cache: dict[str, dict] = {}

        for alert_id, guild_id, user_id, ticker, direction, target_price in alerts:
            if ticker not in quote_cache:
                try:
                    quote_cache[ticker] = await market_data.get_quote(ticker)
                except Exception:
                    log.exception("Failed to fetch quote for alert ticker %s", ticker)
                    continue

            price = quote_cache[ticker]["price"]
            triggered = (direction == "above" and price >= target_price) or (
                direction == "below" and price <= target_price
            )
            if not triggered:
                continue

            await db.deactivate_alert(alert_id)
            try:
                user = await self.bot.fetch_user(user_id)
                await user.send(
                    f"🔔 **{ticker}** just hit **${price:,.2f}**. Your alert for "
                    f"{direction} ${target_price:,.2f} has triggered."
                )
            except discord.HTTPException:
                # Probably means the user has server-member DMs turned off, nothing we can do about that.
                log.warning("Could not DM user %s for alert #%s", user_id, alert_id)

    async def _send_digests(self):
        subscribers = await db.all_digest_optins()
        # Shared across every subscriber, so a ticker on both a watchlist and a portfolio fetches once, not twice.
        quote_cache: dict[str, dict] = {}

        for guild_id, user_id, content in subscribers:
            embed = await _build_digest_embed(guild_id, user_id, content, quote_cache)
            if not embed.fields:
                # Nothing to summarize, skip the DM entirely instead of sending an empty one.
                continue

            try:
                user = await self.bot.fetch_user(user_id)
                await user.send(embed=embed, view=DigestView(guild_id, user_id, content))
            except discord.HTTPException:
                log.warning("Could not DM digest to user %s", user_id)

    async def _get_notable_movers(self) -> list[dict]:
        # Fetched once per digest run and shared across every server, instead of once per server.
        quotes = await asyncio.gather(
            *(market_data.get_quote(t) for t in BREAKING_WATCH_TICKERS), return_exceptions=True
        )
        return [q for q in quotes if not isinstance(q, Exception) and abs(q["change_pct"]) >= BREAKING_MOVE_THRESHOLD_PCT]

    async def _send_server_digests(self):
        configs = await db.all_server_digests()
        if not configs:
            return

        big_movers = await self._get_notable_movers()

        for cfg in configs:
            channel = self.bot.get_channel(cfg["channel_id"])
            if channel is None:
                try:
                    channel = await self.bot.fetch_channel(cfg["channel_id"])
                except discord.HTTPException:
                    log.warning("Could not resolve server digest channel %s for guild %s", cfg["channel_id"], cfg["guild_id"])
                    continue

            tickers = await db.get_tracked(cfg["guild_id"])
            embed = await _build_server_digest_embed(tickers, cfg["period"], cfg["include_movers"], big_movers)
            view = ServerDigestView(tickers, cfg["period"], cfg["include_movers"], big_movers)
            try:
                await channel.send(embed=embed, view=view)
            except discord.HTTPException:
                log.warning("Could not post server digest to channel %s", cfg["channel_id"])


async def setup(bot: commands.Bot):
    await bot.add_cog(Scheduler(bot))
