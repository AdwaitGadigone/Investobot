import logging

import discord
from discord.ext import commands

from config import WEBSITE_URL

log = logging.getLogger("investo.welcome")


def _build_welcome_embed(bot_user: discord.ClientUser) -> discord.Embed:
    embed = discord.Embed(
        title="👋 Thanks for adding Investo!",
        description=(
            "I track stocks, crypto, and more, right in Discord, with a companion website "
            "that shares the same account and data.\n\n"
            "**A few things to try:**\n"
            "`/stock AAPL`: live quote, chart, and analyst take\n"
            "`/watchlist add AAPL, MSFT`: track your own list\n"
            "`/portfolio buy AAPL 10 150`: log a real position with live profit/loss\n"
            "`/help`: see everything I can do\n\n"
            f"Everything's also live at [investoweb.vercel.app]({WEBSITE_URL}), sign in with the "
            "same Discord account, no separate signup.\n\n"
            "Found a bug or have a suggestion? `/feedback` sends it straight to my owner."
        ),
        color=discord.Color.green(),
    )
    if bot_user.avatar:
        embed.set_thumbnail(url=bot_user.avatar.url)
    embed.set_footer(text="Data from Yahoo Finance, Finnhub, and Google Gemini, not financial advice")
    return embed


def _pick_channel(guild: discord.Guild) -> discord.TextChannel | None:
    # Prefers the system channel (where join messages already go) over guessing at whatever's first alphabetically.
    if guild.system_channel and guild.system_channel.permissions_for(guild.me).send_messages:
        return guild.system_channel

    for channel in guild.text_channels:
        if channel.permissions_for(guild.me).send_messages:
            return channel
    return None


class Welcome(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        embed = _build_welcome_embed(self.bot.user)
        channel = _pick_channel(guild)

        if channel is not None:
            try:
                await channel.send(embed=embed)
                return
            except discord.HTTPException:
                log.warning("Could not post welcome message in guild %s", guild.id)

        # No channel we're allowed to post in, fall back to DMing whoever owns the server.
        owner = guild.owner or (await self.bot.fetch_user(guild.owner_id) if guild.owner_id else None)
        if owner:
            try:
                await owner.send(embed=embed)
            except discord.HTTPException:
                log.warning("Could not DM welcome message to owner of guild %s", guild.id)


async def setup(bot: commands.Bot):
    await bot.add_cog(Welcome(bot))
