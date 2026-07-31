import logging
from datetime import datetime, timezone

import discord
from discord.ext import commands, tasks

from config import BIG_MOVE_THRESHOLD_PCT, CHECK_INTERVAL_MINUTES, UPDATES_CHANNEL_ID
from cogs.stocks import build_quote_embed
from services import db, market_data

log = logging.getLogger("investo.scheduler")


class Scheduler(commands.Cog):
    # The background loop, every CHECK_INTERVAL_MINUTES it checks tracked tickers for big moves/news and price alerts.

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Starts the loop as soon as this cog is loaded, defined below with the @tasks.loop decorator.
        self.check_loop.start()

    def cog_unload(self):
        # Stops the loop cleanly so a hot-reload during development doesn't leave a duplicate loop running.
        self.check_loop.cancel()

    # tasks.loop turns this into a function that automatically repeats itself every CHECK_INTERVAL_MINUTES.
    @tasks.loop(minutes=CHECK_INTERVAL_MINUTES)
    async def check_loop(self):
        await self._check_movers_and_news()
        await self._check_alerts()

    # before_loop runs once before the very first iteration of the loop above.
    @check_loop.before_loop
    async def before_check_loop(self):
        # Waiting here stops the loop from firing before the bot has even finished logging in.
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

    async def _check_movers_and_news(self):
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

                try:
                    articles = await market_data.get_company_news(ticker, days_back=1)
                except Exception:
                    log.exception("Failed to fetch news for %s", ticker)
                    articles = []

                for article in articles:
                    news_id = str(article.get("id") or article.get("url"))
                    if not news_id or await db.is_news_seen(guild_id, news_id):
                        continue

                    await db.mark_news_seen(guild_id, news_id)
                    embed = discord.Embed(
                        title=article.get("headline", "Untitled")[:250],
                        url=article.get("url") or None,
                        description=f"**{ticker}**, from {article.get('source', 'Unknown source')}",
                        color=discord.Color.gold(),
                    )
                    await channel.send(content=ping, embed=embed)

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


async def setup(bot: commands.Bot):
    await bot.add_cog(Scheduler(bot))
