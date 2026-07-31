import time

import discord
from discord.ext import commands

from services import chat_ai

# Simple per-user cooldown so an accidental double @mention (or spam) doesn't fire two
# Gemini calls back to back, stored in memory since it only needs to survive one session.
_COOLDOWN_SECONDS = 8
_last_used: dict[int, float] = {}


class Chat(commands.Cog):
    # Lets people @ mention the bot directly in a channel and get an AI reply, instead of
    # only being able to talk to it through slash commands.

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
        # If this message is a reply to one of the bot's own answers, pulling that answer
        # back in as context is what makes follow-up questions actually feel like a thread.
        if not message.reference or not message.reference.message_id:
            return None

        try:
            replied = await message.channel.fetch_message(message.reference.message_id)
        except discord.HTTPException:
            return None

        if replied.author.id != self.bot.user.id:
            return None

        # Our own replies are embeds, so the actual text lives in the embed, not message.content.
        # This also covers the automatic big-move alerts, so replying "why" to one of those works,
        # the title carries the ticker (e.g. "Big move: Apple Inc. (AAPL)") that the description alone
        # wouldn't have, which is what lets the ticker/news lookup below actually find the right stock.
        if replied.embeds:
            embed = replied.embeds[0]
            parts = [p for p in (embed.title, embed.description) if p]
            return "\n".join(parts) if parts else None
        return replied.content or None


async def setup(bot: commands.Bot):
    await bot.add_cog(Chat(bot))
