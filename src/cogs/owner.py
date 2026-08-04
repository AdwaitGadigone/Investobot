import logging

import discord
from discord.ext import commands

from config import OWNER_DISCORD_IDS

log = logging.getLogger("investo.owner")


class Owner(commands.Cog):
    # Plain prefix command, not app_commands, so this never registers with Discord and never appears in the / panel.

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="tell", hidden=True)
    async def tell(self, ctx: commands.Context, user_id: int, *, message: str):
        if ctx.author.id not in OWNER_DISCORD_IDS:
            # No reply at all, a wrong-user attempt shouldn't confirm this command even exists.
            return

        if ctx.guild is not None:
            try:
                # Only works in a server, a bot can't delete someone else's message inside a DM.
                await ctx.message.delete()
            except discord.HTTPException:
                pass

        try:
            user = await self.bot.fetch_user(user_id)
            await user.send(message)
            await ctx.author.send(f"Sent to **{user}** ({user_id}).")
        except discord.HTTPException:
            log.warning("Owner !tell failed to DM user %s", user_id)
            await ctx.author.send(f"Couldn't DM user `{user_id}`, they may have DMs closed or the ID is wrong.")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Relays any DM from someone who isn't an owner straight to every owner, so a !tell reply is actually seen.
        if message.author.bot or message.guild is not None or message.author.id in OWNER_DISCORD_IDS:
            return

        text = message.content or "*(no text)*"
        if message.attachments:
            text += "\n" + "\n".join(a.url for a in message.attachments)

        for owner_id in OWNER_DISCORD_IDS:
            try:
                owner = await self.bot.fetch_user(owner_id)
                await owner.send(f"📩 **{message.author}** (`{message.author.id}`) DMed:\n{text}")
            except discord.HTTPException:
                log.warning("Could not relay DM from %s to owner %s", message.author.id, owner_id)


async def setup(bot: commands.Bot):
    await bot.add_cog(Owner(bot))
