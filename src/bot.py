import asyncio
import logging

import discord
from discord.ext import commands

from config import DEV_GUILD_ID, DISCORD_TOKEN
from services.db import init_db

# Sets up basic console logging so we can see what the bot is doing while it runs.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("investo")

# Every cog (feature file) the bot loads on startup, add new ones here as the bot grows.
INITIAL_EXTENSIONS = (
    "cogs.stocks",
    "cogs.watchlist",
    "cogs.analyst",
    "cogs.news",
    "cogs.alerts",
    "cogs.scheduler",
    "cogs.notify",
    "cogs.help",
    "cogs.chat",
)


class InvestoBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        # Needed so cogs/chat.py can actually read what someone typed when they @ mention
        # the bot, not just that a mention happened. This is a privileged intent, it also
        # has to be turned on in the Discord Developer Portal (Bot page) or login will fail.
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        # setup_hook runs once automatically, right after login but before the bot starts handling events.
        await init_db()

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

    async def on_ready(self):
        # Fires once the bot has fully connected to Discord's servers.
        log.info("Logged in as %s (id: %s)", self.user, self.user.id)


async def main():
    bot = InvestoBot()
    async with bot:
        await bot.start(DISCORD_TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
