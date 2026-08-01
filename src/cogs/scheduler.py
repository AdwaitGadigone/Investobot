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
    # Same ANSI color trick as cogs/stocks.py's quote embed, green for up, red for down,
    # this is one line per ticker inside a single code block in the digest DM.
    esc = chr(27)
    is_up = quote["change_pct"] >= 0
    color_code = "32" if is_up else "31"
    arrow = "▲" if is_up else "▼"
    reset = f"{esc}[0m"
    return (
        f"{esc}[1;{color_code}m{quote['ticker']:<6}${quote['price']:>10,.2f}  "
        f"{arrow} {quote['change_pct']:+.2f}%{reset}"
    )


class Scheduler(commands.Cog):
    # The background loop, every CHECK_INTERVAL_MINUTES it checks tracked tickers for big price moves and personal price alerts.

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Starts both loops as soon as this cog is loaded, defined below with @tasks.loop decorators.
        self.check_loop.start()
        self.digest_loop.start()

    def cog_unload(self):
        # Stops both loops cleanly so a hot-reload during development doesn't leave duplicates running.
        self.check_loop.cancel()
        self.digest_loop.cancel()

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

    @digest_loop.before_loop
    async def before_digest_loop(self):
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
        # Only posts price moves here, not individual news articles. A heavily-covered
        # ticker can have 50+ headlines a day, posting each one separately as its own
        # message is exactly the kind of flood this used to cause. /news stays available
        # for checking headlines on demand instead.
        channel = await self._updates_channel()
        if channel is None:
            return

        # Fetches every tracked ticker across every server in one go, grouped by server.
        tracked_by_guild = await db.all_tracked_by_guild()
        today = datetime.now(timezone.utc).date().isoformat()

        for guild_id, tickers in tracked_by_guild.items():
            role_id = await db.get_alerts_role_id(guild_id)
            # Stays None until someone sets up /notify in this server, in which case nothing gets pinged.
            ping = f"<@&{role_id}>" if role_id else None

            for ticker in tickers:
                try:
                    quote = await market_data.get_quote(ticker)
                except market_data.TickerNotFoundError:
                    continue
                except Exception:
                    # One bad ticker shouldn't stop the rest of the list from being checked.
                    log.exception("Failed to fetch quote for %s", ticker)
                    continue

                last_alert = await db.get_last_alert_date(guild_id, ticker)
                if abs(quote["change_pct"]) >= BIG_MOVE_THRESHOLD_PCT and last_alert != today:
                    embed = build_quote_embed(quote)
                    embed.title = f"📈 Big move: {embed.title}" if quote["change"] >= 0 else f"📉 Big move: {embed.title}"
                    await channel.send(content=ping, embed=embed)
                    await db.set_last_alert_date(guild_id, ticker, today)

    async def _check_breaking_moves(self):
        # Catches a genuinely wild move on a well-known stock even if nobody bothered to
        # /track it, e.g. a mega-cap jumping 20%+ in a day is news on its own.
        channel = await self._updates_channel()
        if channel is None:
            return

        tracked_by_guild = await db.all_tracked_by_guild()
        # Anything already tracked gets covered by _check_movers above at a lower threshold,
        # scanning it again here would just mean two alerts for the same move.
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

        for guild_id, user_id in subscribers:
            tickers = await db.get_watchlist(guild_id, user_id)
            if not tickers:
                # Nothing to summarize, skip the DM entirely instead of sending an empty one.
                continue

            lines = []
            for ticker in tickers:
                try:
                    quote = await market_data.get_quote(ticker)
                except Exception:
                    # One bad ticker shouldn't stop the rest of someone's digest from sending.
                    log.exception("Failed to fetch quote for digest ticker %s", ticker)
                    continue
                lines.append(_digest_line(quote))

            if not lines:
                continue

            embed = discord.Embed(
                title="🌤️ Your Morning Watchlist Digest",
                description="```ansi\n" + "\n".join(lines) + "\n```",
                color=discord.Color.blurple(),
            )
            embed.set_footer(text="Turn this off anytime with /digest")

            try:
                user = await self.bot.fetch_user(user_id)
                await user.send(embed=embed)
            except discord.HTTPException:
                log.warning("Could not DM digest to user %s", user_id)


async def setup(bot: commands.Bot):
    await bot.add_cog(Scheduler(bot))
