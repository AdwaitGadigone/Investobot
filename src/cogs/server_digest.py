import discord
from discord import app_commands
from discord.ext import commands

from services import db


# Same period keys cogs/scheduler.py's SERVER_DIGEST_PERIOD_LABELS uses, kept here too since choices need literals.
PERIOD_CHOICES = [
    app_commands.Choice(name="Today", value="day"),
    app_commands.Choice(name="Past week", value="week"),
    app_commands.Choice(name="Past month", value="month"),
    app_commands.Choice(name="Past 3 months", value="three_month"),
    app_commands.Choice(name="Past year", value="year"),
]


class ServerDigest(commands.Cog):
    # Admin-only setup for the server-wide daily digest, cogs/scheduler.py owns actually building and sending it.

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    group = app_commands.Group(
        name="serverdigest",
        description="Configure this server's daily tracked-ticker digest",
        default_permissions=discord.Permissions(manage_guild=True),
    )

    @group.command(name="set", description="Turn on (or reconfigure) the server's daily digest")
    @app_commands.describe(
        channel="Where the daily digest gets posted",
        period="Time window it opens showing, viewers can still switch it on the message itself",
        include_movers="Also flag other big movers not on this server's tracked list",
    )
    @app_commands.choices(period=PERIOD_CHOICES)
    async def set_(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        period: app_commands.Choice[str] = None,
        include_movers: bool = True,
    ):
        # Redundant with default_permissions, but that's just a default some admins reassign in Integrations settings.
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("You need the Manage Server permission to do this.", ephemeral=True)
            return

        period_value = period.value if period else "day"
        await db.set_server_digest(interaction.guild_id, channel.id, period_value, include_movers)
        await interaction.response.send_message(
            f"Daily server digest set to post in {channel.mention} "
            f"({period.name if period else 'Today'} view, {'with' if include_movers else 'without'} notable movers)."
        )

    @group.command(name="off", description="Turn off the server's daily digest")
    async def off(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("You need the Manage Server permission to do this.", ephemeral=True)
            return

        removed = await db.disable_server_digest(interaction.guild_id)
        await interaction.response.send_message(
            "Turned off the server digest." if removed else "The server digest wasn't turned on."
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(ServerDigest(bot))
