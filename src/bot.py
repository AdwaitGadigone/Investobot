import asyncio
import logging

import discord
from discord.ext import commands
from discord.http import Route

from config import DEV_GUILD_ID, DISCORD_TOKEN, WEBSITE_URL
from services.db import init_db

# Sets up basic console logging so we can see what the bot is doing while it runs.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("investo")

# Every cog (feature file) the bot loads on startup, add new ones here as the bot grows.
INITIAL_EXTENSIONS = (
    "cogs.stocks",
    "cogs.movers",
    "cogs.watchlist",
    "cogs.analyst",
    "cogs.news",
    "cogs.alerts",
    "cogs.scheduler",
    "cogs.notify",
    "cogs.digest",
    "cogs.portfolio",
    "cogs.help",
    "cogs.chat",
)


class InvestoBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        # Needed so cogs/chat.py can read message text on @ mentions, a privileged intent that also has to be enabled in the Developer Portal or login fails.
        intents.message_content = True
        # Shows up under the bot's name in every server's member list, points people at the site.
        activity = discord.Activity(type=discord.ActivityType.watching, name=f"stocks on {WEBSITE_URL.removeprefix('https://')}")
        super().__init__(command_prefix="!", intents=intents, activity=activity)

    async def setup_hook(self):
        # setup_hook runs once automatically, right after login but before the bot starts handling events.
        await init_db()
        await self._update_about_me()

        for ext in INITIAL_EXTENSIONS:
            await self.load_extension(ext)
            log.info("Loaded extension %s", ext)

        if DEV_GUILD_ID:
            # Copies every command to this one server and syncs instantly, useful for testing.
            guild = discord.Object(id=int(DEV_GUILD_ID))
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            log.info("Synced %d commands to dev guild %s", len(synced), DEV_GUILD_ID)
        else:
            # A global sync can take up to an hour to show up in every server the bot is in.
            synced = await self.tree.sync()
            log.info("Synced %d global commands (may take up to an hour to propagate)", len(synced))

    async def _update_about_me(self):
        # Discord.py has no wrapper for this, PATCH applications/@me is a raw call so the "About Me" also plugs the site.
        description = f"Track stocks, crypto, and more. Same watchlists, portfolio, and alerts on the web at {WEBSITE_URL}"
        try:
            await self.http.request(Route("PATCH", "/applications/@me"), json={"description": description})
        except discord.HTTPException:
            log.warning("Could not update application description")

    async def on_ready(self):
        # Fires once the bot has fully connected to Discord's servers.
        log.info("Logged in as %s (id: %s)", self.user, self.user.id)


async def main():
    bot = InvestoBot()
    async with bot:
        await bot.start(DISCORD_TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
