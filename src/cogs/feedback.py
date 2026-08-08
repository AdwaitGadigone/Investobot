import logging

import discord
from discord import app_commands
from discord.ext import commands

from config import LIGHT_COOLDOWN_SECONDS, OWNER_DISCORD_IDS
from services import db

log = logging.getLogger("investo.feedback")

CATEGORY_CHOICES = [
    app_commands.Choice(name="Bug report", value="bug"),
    app_commands.Choice(name="Suggestion", value="suggestion"),
    app_commands.Choice(name="Other", value="other"),
]


class Feedback(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="feedback", description="Send feedback, a suggestion, or report a concern")
    @app_commands.checks.cooldown(1, LIGHT_COOLDOWN_SECONDS)
    @app_commands.describe(message="What's on your mind", category="What kind of feedback this is")
    @app_commands.choices(category=CATEGORY_CHOICES)
    async def feedback(
        self,
        interaction: discord.Interaction,
        message: app_commands.Range[str, 1, 1000],
        category: app_commands.Choice[str] = None,
    ):
        category_value = category.value if category else "other"
        await db.add_feedback(interaction.guild_id, interaction.user.id, category_value, message)

        guild_name = interaction.guild.name if interaction.guild else "DMs"
        label = category.name if category else "Feedback"
        for owner_id in OWNER_DISCORD_IDS:
            try:
                owner = await self.bot.fetch_user(owner_id)
                await owner.send(f"📝 **{label}** from **{interaction.user}** in **{guild_name}**:\n{message}")
            except discord.HTTPException:
                log.warning("Could not DM feedback to owner %s", owner_id)

        await interaction.response.send_message("Thanks, sent straight to the bot owner.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Feedback(bot))
