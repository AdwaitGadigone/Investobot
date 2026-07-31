import asyncio
import logging

import discord
from discord.ext import commands

from config import DEV_GUILD_ID, DISCORD_TOKEN
from services.db import init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("investo")

INITIAL_EXTENSIONS = (
    "cogs.stocks",
    "cogs.watchlist",
    "cogs.analyst",
    "cogs.news",
    "cogs.alerts",
    "cogs.scheduler",
    "cogs.notify",
    "cogs.help",
)


class InvestoBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()  # only need default perms, we're slash-command only
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        await init_db()
        for ext in INITIAL_EXTENSIONS:
            await self.load_extension(ext)
            log.info("Loaded extension %s", ext)

        if DEV_GUILD_ID:
            guild = discord.Object(id=int(DEV_GUILD_ID))
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)  # instant sync to one server, for testing
            log.info("Synced %d commands to dev guild %s", len(synced), DEV_GUILD_ID)
        else:
            synced = await self.tree.sync()  # global sync, can take up to an hour to propagate
            log.info("Synced %d global commands (may take up to an hour to propagate)", len(synced))

    async def on_ready(self):
        log.info("Logged in as %s (id: %s)", self.user, self.user.id)


async def main():
    bot = InvestoBot()
    async with bot:
        await bot.start(DISCORD_TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
