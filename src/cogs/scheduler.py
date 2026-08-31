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
)
from cogs.stocks import build_quote_embed
from services import db, market_data

log = logging.getLogger("investo.scheduler")


# CDR tickers (WDC.NE, TSLA.NE, XEQT.TO...) run 2-4 chars longer than a plain US ticker, a fixed <6
# padding looked fine until one of those showed up and threw off every column after it for the rest
# of the list, this cap is generous enough for the longest realistic symbol without wasting space
# on every line just because one CDR happened to be in the list.
_MAX_TICKER_WIDTH = 10


def _ticker_width(tickers) -> int:
    # Computed per-list instead of a single global constant, so a watchlist of only short tickers still
    # aligns tight, this is what actually guarantees every column lines up regardless of what's in it.
    return min(max((len(t) for t in tickers), default=6), _MAX_TICKER_WIDTH)


def _dollar_str(amount: float) -> str:
    # "+$1,234.56" / "-$1,234.56", used anywhere a raw dollar change or P/L amount is shown, not just a
    # plain price, the sign belongs before the $ to read naturally instead of "$+1,234.56".
    return f"{'+' if amount >= 0 else '-'}${abs(amount):,.2f}"


def _digest_line(quote: dict, ticker_width: int = 6) -> str:
    # Same ANSI color trick as cogs/stocks.py's quote embed, one line per ticker in the digest DM.
    # Shows today's move as both percent and a real dollar amount, not just percent alone.
    esc = chr(27)
    is_up = quote["change_pct"] >= 0
    color_code = "32" if is_up else "31"
    arrow = "▲" if is_up else "▼"
    reset = f"{esc}[0m"
    pct_str = f"{quote['change_pct']:+.2f}%"
    return (
        f"{esc}[1;{color_code}m{quote['ticker']:<{ticker_width}} ${quote['price']:>10,.2f}   "
        f"{arrow}{pct_str:>7}   {_dollar_str(quote['change']):>10}{reset}"
    )


def _portfolio_digest_line(ticker: str, shares: float, cost_basis: float, quote: dict, ticker_width: int = 6) -> str:
    # Value, today's move, and all-time P/L (as both percent and a real dollar amount) on one line,
    # same ANSI-color pattern as the watchlist line above.
    esc = chr(27)
    reset = f"{esc}[0m"
    price = quote["price"]
    value = shares * price
    cost = shares * cost_basis
    pl_dollar = value - cost
    pl_pct = (pl_dollar / cost * 100) if cost else 0.0
    color_code = "32" if pl_pct >= 0 else "31"
    today_arrow = "▲" if quote["change_pct"] >= 0 else "▼"
    pl_arrow = "▲" if pl_pct >= 0 else "▼"
    # Every number is right-aligned to a fixed width, not just however many characters it happens to
    # take, a single big swing (a real P/L easily hits +100% or four figures over time) would otherwise
    # shift every column after it out of line for that one row, throwing off the whole list.
    today_str = f"{quote['change_pct']:+.2f}%"
    pl_pct_str = f"{pl_pct:+.2f}%"
    return (
        f"{esc}[1;{color_code}m{ticker:<{ticker_width}} ${value:>9,.2f}   "
        f"{today_arrow}{today_str:>7}   {pl_arrow}{pl_pct_str:>8}   {_dollar_str(pl_dollar):>11}{reset}"
    )


def _build_ansi_block(lines: list[str], prefix: str = "", limit: int = 4096) -> str:
    # Embed descriptions cap at 4096 chars, 4x an embed field's 1024, comfortably fitting even a large
    # watchlist or portfolio (rarely more than 100 lines of about 40 characters each) without ever
    # needing a second "(cont.)" section. Still has a last-resort truncation for a genuinely enormous
    # list, budgeted correctly against the real limit on every shrink, not appended after already being
    # at it, the exact off-by-limit mistake made (and caught) once already earlier in this same file.
    if not lines:
        return "```ansi\n" + prefix + "\n```"

    shown = list(lines)
    hidden = 0
    while True:
        body = "```ansi\n" + prefix + "\n".join(shown) + "\n```"
        note = f"\n*+{hidden} more, see the full list on the website.*" if hidden else ""
        if len(body + note) <= limit or not shown:
            return body + note
        shown = shown[:-1]
        hidden += 1


def _portfolio_digest_summary(total_value: float, total_today: float, total_pl: float, total_cost: float) -> str:
    # Sits above the per-position lines, same shape as the "Total value / Today / All-time" cards on the website.
    esc = chr(27)
    reset = f"{esc}[0m"
    today_base = total_value - total_today
    today_pct = (total_today / today_base * 100) if today_base else 0.0
    pl_pct = (total_pl / total_cost * 100) if total_cost else 0.0
    today_color = "32" if total_today >= 0 else "31"
    pl_color = "32" if total_pl >= 0 else "31"
    # Labels padded to a common width and every percent at the same 2-decimal precision as the per-position
    # rows below, "All-time" used to round to 1 decimal while "Today" used 2, an inconsistency worth fixing
    # alongside the rest of this formatting pass, not just the numbers everyone actually complained about.
    return (
        f"{'Total':<9}${total_value:,.2f}\n"
        f"{esc}[1;{today_color}m{'Today':<9}{total_today:+,.2f} ({today_pct:+.2f}%){reset}\n"
        f"{esc}[1;{pl_color}m{'All-time':<9}{total_pl:+,.2f} ({pl_pct:+.2f}%){reset}"
    )


async def _build_digest_embed(
    guild_id: int, user_id: int, content: str, quote_cache: dict[str, dict] | None = None
) -> tuple[list[discord.Embed], bool]:
    # Shared by the daily auto-send and the dropdown's live edit, so both always render identically.
    # Returns a list of embeds (a message can hold up to 10) instead of one embed with fields, so
    # watchlist and portfolio each get the full 4096-character description budget instead of splitting
    # a 1024-character field into a "(cont.)" second one for anything longer than about 16 lines.
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

    header = discord.Embed(title="🌤️ Your Morning Digest", color=discord.Color.blurple())
    embeds = [header]
    has_content = False

    if content in ("watchlist", "both"):
        tickers = await db.get_watchlist(guild_id, user_id)
        if tickers:
            await ensure_quotes(tickers)
            priced_tickers = [t for t in tickers if t in quote_cache]
            # Biggest gainer first, biggest loser last, so the movers that actually matter aren't buried
            # alphabetically, plain ticker order told you nothing useful at a glance.
            priced_tickers.sort(key=lambda t: quote_cache[t]["change_pct"], reverse=True)
            if priced_tickers:
                has_content = True
                width = _ticker_width(priced_tickers)
                lines = [_digest_line(quote_cache[t], width) for t in priced_tickers]
                embeds.append(
                    discord.Embed(
                        title="👀 Watchlist", description=_build_ansi_block(lines), color=discord.Color.blurple()
                    )
                )

    if content in ("portfolio", "both"):
        positions = await db.get_portfolio(guild_id, user_id)
        if positions:
            await ensure_quotes([p["ticker"] for p in positions])
            priced = [p for p in positions if p["ticker"] in quote_cache]
            # Sorted by P/L, not today's move, a portfolio's headline number is how the position has
            # actually done since you bought it, not its blip today, that's what watchlist is for.
            # Shares cancel out of (value - cost) / cost, so this is equivalent to the per-position math
            # in _portfolio_digest_line below without needing to build the full line just to sort by it.
            def _pl_pct(p):
                cost_basis = p["cost_basis"]
                return (quote_cache[p["ticker"]]["price"] - cost_basis) / cost_basis * 100 if cost_basis else 0.0

            priced.sort(key=_pl_pct, reverse=True)
            if priced:
                has_content = True
                total_value = sum(p["shares"] * quote_cache[p["ticker"]]["price"] for p in priced)
                total_cost = sum(p["shares"] * p["cost_basis"] for p in priced)
                total_today = sum(p["shares"] * quote_cache[p["ticker"]]["change"] for p in priced)
                summary = _portfolio_digest_summary(total_value, total_today, total_value - total_cost, total_cost)
                width = _ticker_width(p["ticker"] for p in priced)
                lines = [
                    _portfolio_digest_line(p["ticker"], p["shares"], p["cost_basis"], quote_cache[p["ticker"]], width)
                    for p in priced
                ]
                # Each row dropped its "today"/"P/L" text labels to stay short (a long ANSI line drifts
                # further out of alignment on Discord clients that don't render the code block perfectly
                # monospace), this one-time header is what still tells the columns apart.
                col_header = " " * (width + 14) + f"{'today':>8}   {'p/l %':>9}   {'p/l $':>11}\n"
                # The summary's own ANSI codes need to be inside the same ```ansi fence as the lines below
                # it, a fence opened AFTER it left those escape codes rendering as literal garbled text.
                description = _build_ansi_block(lines, prefix=summary + "\n" + col_header)
                embeds.append(discord.Embed(title="💼 Portfolio", description=description, color=discord.Color.blurple()))

    if not has_content:
        header.description = "Nothing to show yet. Add tickers with `/watchlist` or holdings with `/portfolio`."

    embeds[-1].set_footer(text="Switch what this shows with the dropdown below, or turn it off with /digest")
    return embeds, has_content


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
        # Refetching quotes takes longer than Discord's 3-second interaction window, defer avoids
        # "the application didn't respond in time" instead of failing the interaction outright.
        await interaction.response.defer()
        # Persisted, so tomorrow's auto-send opens on whatever the user last picked here too.
        await db.set_digest_content(self.guild_id, self.user_id, content)
        embeds, _ = await _build_digest_embed(self.guild_id, self.user_id, content)
        try:
            await interaction.edit_original_response(embeds=embeds, view=DigestView(self.guild_id, self.user_id, content))
        except discord.HTTPException:
            # _build_ansi_block above should already prevent this, but a stuck "thinking..." with zero
            # feedback is worse than an honest error, this is a safety net not the primary fix.
            log.exception("Failed to edit digest message for user %s", self.user_id)
            await interaction.followup.send("Couldn't update the digest, try `/digest` again in a bit.", ephemeral=True)


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
    # Every ticker fetched concurrently instead of one at a time, a 10-ticker list was taking 10x as long
    # as a single fetch and regularly blowing past Discord's interaction deadline.
    quotes = await asyncio.gather(*(market_data.get_quote(t) for t in tickers), return_exceptions=True)

    rows = []
    day_tickers = []
    for ticker, quote in zip(tickers, quotes):
        if isinstance(quote, Exception):
            log.exception("Failed to fetch quote for server digest ticker %s", ticker, exc_info=quote)
            continue
        if period == "day":
            rows.append(
                {"ticker": ticker, "price": quote["price"], "change_pct": quote["change_pct"], "change": quote["change"]}
            )
        else:
            day_tickers.append((ticker, quote))

    if day_tickers:
        changes = await asyncio.gather(
            *(market_data.get_period_change(t, period) for t, _ in day_tickers), return_exceptions=True
        )
        for (ticker, quote), change in zip(day_tickers, changes):
            if isinstance(change, Exception) or not change:
                continue
            rows.append(
                {"ticker": ticker, "price": quote["price"], "change_pct": change["pct"], "change": change["amount"]}
            )

    return rows


async def _build_server_digest_embed(
    tickers: list[str], period: str, include_movers: bool, big_movers: list[dict]
) -> list[discord.Embed]:
    # A list of embeds instead of one embed with fields, same reasoning as the personal digest above,
    # the Tracked list gets the full 4096-character description budget instead of splitting into a
    # "(cont.)" field past about 16 lines.
    label = SERVER_DIGEST_PERIOD_LABELS.get(period, "Today")
    header = discord.Embed(title=f"📊 Server Digest: {label}", color=discord.Color.blurple())
    embeds = [header]

    if tickers:
        rows = await _server_digest_rows(tickers, period)
        if rows:
            # Same as the personal digest's watchlist: biggest mover first, not whatever order /track
            # added them in, this list was never actually sorted despite looking like it should be.
            rows.sort(key=lambda r: r["change_pct"], reverse=True)
            width = _ticker_width(r["ticker"] for r in rows)
            lines = [_digest_line(r, width) for r in rows]
            embeds.append(
                discord.Embed(title="📋 Tracked", description=_build_ansi_block(lines), color=discord.Color.blurple())
            )
        else:
            header.description = "No data available for the tracked list right now."
    else:
        header.description = "Nobody's tracking any tickers yet, add some with `/track`."

    if include_movers:
        # Filtered per-server so a ticker already shown in the tracked list above isn't repeated here too.
        tracked_set = set(tickers)
        notable = sorted(
            (m for m in big_movers if m["ticker"] not in tracked_set),
            key=lambda m: abs(m["change_pct"]),
            reverse=True,
        )[:8]
        if notable:
            width = _ticker_width(m["ticker"] for m in notable)
            embeds[-1].add_field(
                name="🔥 Notable movers",
                value="```ansi\n" + "\n".join(_digest_line(m, width) for m in notable) + "\n```",
                inline=False,
            )

    embeds[-1].set_footer(text="Switch the time window below, admins can reconfigure with /serverdigest")
    return embeds


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
        # Refetching a whole tracked list takes longer than Discord's 3-second interaction window, defer
        # avoids "the application didn't respond in time" instead of failing the interaction outright.
        await interaction.response.defer()
        # Deliberately not saved anywhere, this only changes how THIS posted message looks, not tomorrow's default.
        period = self.values[0]
        embeds = await _build_server_digest_embed(self.tickers, period, self.include_movers, self.big_movers)
        view = ServerDigestView(self.tickers, period, self.include_movers, self.big_movers)
        try:
            await interaction.edit_original_response(embeds=embeds, view=view)
        except discord.HTTPException:
            log.exception("Failed to edit server digest message for guild %s", interaction.guild_id)
            await interaction.followup.send("Couldn't update the digest, try `/serverdigest` again in a bit.", ephemeral=True)


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

    async def _resolve_channel(self, channel_id: int):
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            try:
                # get_channel only checks the bot's local cache, fetch_channel asks Discord directly.
                channel = await self.bot.fetch_channel(channel_id)
            except discord.HTTPException:
                log.warning("Could not resolve updates channel %s", channel_id)
                return None

        return channel

    async def _check_movers(self):
        # Only price moves here, not individual news articles, a heavily-covered ticker can have 50+ headlines a day.
        # Every guild has its own alert channel (set via /notify), a role from guild B can never ping in guild A's channel.
        channel_ids = await db.get_all_updates_channel_ids()
        if not channel_ids:
            return

        # Fetches every tracked ticker across every server in one go, grouped by server.
        tracked_by_guild = await db.all_tracked_by_guild()
        # Same one-query-for-everyone treatment, used to be a separate get_last_alert_date query per
        # (guild, ticker) pair every single loop, that's every tracked ticker on every server, every
        # CHECK_INTERVAL_MINUTES, for a single column already sitting right there in the tracked table.
        alert_dates_by_guild = await db.all_last_alert_dates_by_guild()
        today = datetime.now(timezone.utc).date().isoformat()
        # Shared across every guild in this pass, so a ticker tracked by 3 servers is fetched once, not 3 times.
        quote_cache: dict[str, dict] = {}

        for guild_id, tickers in tracked_by_guild.items():
            channel_id = channel_ids.get(guild_id)
            if channel_id is None:
                # Nobody in this server has run /notify yet, so there's nowhere to post its alerts.
                continue
            channel = await self._resolve_channel(channel_id)
            if channel is None:
                continue

            role_id = await db.get_alerts_role_id(guild_id)
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
                last_alert = alert_dates_by_guild.get(guild_id, {}).get(ticker)
                if abs(quote["change_pct"]) >= BIG_MOVE_THRESHOLD_PCT and last_alert != today:
                    embed = build_quote_embed(quote)
                    embed.title = f"📈 Big move: {embed.title}" if quote["change"] >= 0 else f"📉 Big move: {embed.title}"
                    await channel.send(content=ping, embed=embed)
                    await db.set_last_alert_date(guild_id, ticker, today)

    async def _check_breaking_moves(self):
        # Catches a genuinely wild move on a well-known stock even if nobody bothered to /track it, for every guild.
        channel_ids = await db.get_all_updates_channel_ids()
        if not channel_ids:
            return

        tracked_by_guild = await db.all_tracked_by_guild()
        today = datetime.now(timezone.utc).date().isoformat()

        quote_cache: dict[str, dict] = {}
        for ticker in BREAKING_WATCH_TICKERS:
            try:
                if ticker not in quote_cache:
                    quote_cache[ticker] = await market_data.get_quote(ticker)
            except Exception:
                log.exception("Failed to fetch quote for breaking-move check on %s", ticker)
                continue

            quote = quote_cache[ticker]
            if abs(quote["change_pct"]) < BREAKING_MOVE_THRESHOLD_PCT:
                continue

            last_alert = await db.get_breaking_alert_date(ticker)
            if last_alert == today:
                continue

            embed = build_quote_embed(quote)
            direction = "📈" if quote["change"] >= 0 else "📉"
            embed.title = f"{direction} Breaking move: {embed.title}"
            # Appends to the source footer build_quote_embed already set, instead of overwriting it with stale text.
            embed.set_footer(text=f"{embed.footer.text} • not on anyone's tracked list, just a huge move")

            for guild_id, channel_id in channel_ids.items():
                # Already covered by _check_movers for this specific guild at a lower threshold, skip to avoid a double alert.
                if ticker in tracked_by_guild.get(guild_id, []):
                    continue
                channel = await self._resolve_channel(channel_id)
                if channel is not None:
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
            embeds, has_content = await _build_digest_embed(guild_id, user_id, content, quote_cache)
            if not has_content:
                # Nothing to summarize, skip the DM entirely instead of sending an empty one.
                continue

            try:
                user = await self.bot.fetch_user(user_id)
                await user.send(embeds=embeds, view=DigestView(guild_id, user_id, content))
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
            embeds = await _build_server_digest_embed(tickers, cfg["period"], cfg["include_movers"], big_movers)
            view = ServerDigestView(tickers, cfg["period"], cfg["include_movers"], big_movers)
            try:
                await channel.send(embeds=embeds, view=view)
            except discord.HTTPException:
                log.warning("Could not post server digest to channel %s", cfg["channel_id"])


async def setup(bot: commands.Bot):
    await bot.add_cog(Scheduler(bot))
