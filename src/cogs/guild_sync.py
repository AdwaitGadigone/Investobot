import logging

import discord
from discord.ext import commands

from services import db

log = logging.getLogger("investo.guild_sync")


class GuildSync(commands.Cog):
    # Keeps bot_guilds current so the website knows which of a user's own servers actually have Investo in them.

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        await db.upsert_bot_guild(guild.id, guild.name, guild.icon.key if guild.icon else None)

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild):
        await db.remove_bot_guild(guild.id)


async def setup(bot: commands.Bot):
    await bot.add_cog(GuildSync(bot))
