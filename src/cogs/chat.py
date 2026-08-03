import time

import discord
from discord.ext import commands

from services import chat_ai

# Stops an accidental double @mention (or spam) from firing two Gemini calls back to back.
_COOLDOWN_SECONDS = 8
_last_used: dict[int, float] = {}


class Chat(commands.Cog):
    # Lets people @ mention the bot in a channel for an AI reply, instead of only via slash commands.

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        if self.bot.user not in message.mentions:
            return

        now = time.monotonic()
        if now - _last_used.get(message.author.id, 0) < _COOLDOWN_SECONDS:
            return
        _last_used[message.author.id] = now
        # Prunes stale entries on the way in instead of a separate cleanup task, keeps this dict from growing forever.
        for user_id, last in list(_last_used.items()):
            if now - last > _COOLDOWN_SECONDS:
                del _last_used[user_id]

        # Strips both mention formats Discord can send (with or without the "!" for nicknames).
        question = message.content
        for mention_format in (f"<@{self.bot.user.id}>", f"<@!{self.bot.user.id}>"):
            question = question.replace(mention_format, "")
        question = question.strip()

        if not question:
            await message.reply("Ask me anything about stocks, investing, or the market!")
            return

        prior_reply = await self._get_prior_reply(message)

        async with message.channel.typing():
            reply = await chat_ai.generate_chat_reply(question, prior_reply)

        if not reply:
            await message.reply("Something went wrong generating a response, try again in a bit.")
            return

        embed = discord.Embed(description=reply[:4000], color=discord.Color.blurple())
        embed.set_footer(text="AI-generated, not financial advice")
        await message.reply(embed=embed)

    async def _get_prior_reply(self, message: discord.Message) -> str | None:
        # Pulls the bot's prior answer in as context, so a reply to it feels like a real follow-up.
        if not message.reference or not message.reference.message_id:
            return None

        try:
            replied = await message.channel.fetch_message(message.reference.message_id)
        except discord.HTTPException:
            return None

        if replied.author.id != self.bot.user.id:
            return None

        # Our replies are embeds, so the text lives there, not message.content, and the title carries the ticker too.
        if replied.embeds:
            embed = replied.embeds[0]
            parts = [p for p in (embed.title, embed.description) if p]
            return "\n".join(parts) if parts else None
        return replied.content or None


async def setup(bot: commands.Bot):
    await bot.add_cog(Chat(bot))
