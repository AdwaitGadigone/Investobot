import discord
from discord import app_commands
from discord.ext import commands

from services import db

ROLE_NAME = "Stock Alerts"


class Notify(commands.Cog):
    # /notify toggles a role that cogs/scheduler.py pings on big moves/news, created on first use

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="notify", description="Toggle pings for automatic stock move/news updates")
    async def notify(self, interaction: discord.Interaction):
        guild = interaction.guild
        role_id = await db.get_alerts_role_id(guild.id)
        role = guild.get_role(role_id) if role_id else None

        if role is None:
            try:
                role = await guild.create_role(
                    name=ROLE_NAME, mentionable=True, reason="Investo auto-update pings"
                )
            except discord.Forbidden:
                await interaction.response.send_message(
                    "I don't have permission to create roles here, ask an admin to grant Manage Roles."
                )
                return
            await db.set_alerts_role_id(guild.id, role.id)

        member = interaction.user
        try:
            if role in member.roles:
                await member.remove_roles(role, reason="Investo /notify toggle off")
                await interaction.response.send_message("Turned off stock alert pings for you.")
            else:
                await member.add_roles(role, reason="Investo /notify toggle on")
                await interaction.response.send_message(
                    "You'll now get pinged when a tracked stock has a big move or fresh news."
                )
        except discord.Forbidden:
            await interaction.response.send_message(
                "I don't have permission to manage that role, ask an admin to check my role position."
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(Notify(bot))
