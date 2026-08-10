import discord
from discord import app_commands
from discord.ext import commands

from config import LIGHT_COOLDOWN_SECONDS
from services import db

ROLE_NAME = "Stock Alerts"


class Notify(commands.Cog):
    # /notify toggles a role that cogs/scheduler.py pings on big moves/news, created the first time anyone uses it.

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="notify", description="Toggle pings for automatic stock move/news updates")
    @app_commands.checks.cooldown(1, LIGHT_COOLDOWN_SECONDS)
    async def notify(self, interaction: discord.Interaction):
        guild = interaction.guild

        # Checks the database first to see if this server already has the role set up.
        role_id = await db.get_alerts_role_id(guild.id)
        role = guild.get_role(role_id) if role_id else None

        if role is None:
            try:
                # Creates the role for the first time and saves its ID so we don't create a duplicate later.
                role = await guild.create_role(
                    name=ROLE_NAME, mentionable=True, reason="Investo auto-update pings"
                )
            except discord.Forbidden:
                await interaction.response.send_message(
                    "I don't have permission to create roles here, ask an admin to grant Manage Roles."
                )
                return
            await db.set_alerts_role_id(guild.id, role.id)
            # Also locks in this server's alert channel to wherever /notify was first run, every guild needs its own.
            await db.set_updates_channel_id(guild.id, interaction.channel_id)

        member = interaction.user
        try:
            # Toggle behaviour: running /notify again removes the role instead of erroring out.
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
