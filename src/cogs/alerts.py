import discord
from discord import app_commands
from discord.ext import commands

from services import db, market_data


class Alerts(commands.Cog):
    # personal price alerts, the checking and DMing itself happens in cogs/scheduler.py's background loop

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    alert_group = app_commands.Group(name="alert", description="Manage your personal price alerts")

    @alert_group.command(name="set", description="Get DM'd when a ticker goes above/below a price")
    @app_commands.describe(
        ticker="Stock ticker symbol, e.g. AAPL",
        direction="Alert when price goes above or below the target",
        price="Target price in dollars",
    )
    @app_commands.choices(
        direction=[
            app_commands.Choice(name="above", value="above"),
            app_commands.Choice(name="below", value="below"),
        ]
    )
    async def alert_set(
        self,
        interaction: discord.Interaction,
        ticker: str,
        direction: app_commands.Choice[str],
        price: float,
    ):
        ticker = ticker.upper().strip()
        try:
            await market_data.get_quote(ticker)
        except market_data.TickerNotFoundError:
            await interaction.response.send_message(f"`{ticker}` doesn't look like a valid ticker.")
            return

        alert_id = await db.add_alert(interaction.guild_id, interaction.user.id, ticker, direction.value, price)
        await interaction.response.send_message(
            f"Alert #{alert_id} set. I'll DM you when **{ticker}** goes {direction.value} **${price:,.2f}**."
        )

    @alert_group.command(name="list", description="Show your active price alerts")
    async def alert_list(self, interaction: discord.Interaction):
        rows = await db.get_user_alerts(interaction.guild_id, interaction.user.id)
        if not rows:
            await interaction.response.send_message("You have no active alerts.")
            return
        lines = [f"#{aid} - **{ticker}** {direction} ${price:,.2f}" for aid, ticker, direction, price in rows]
        await interaction.response.send_message("\n".join(lines))

    @alert_group.command(name="remove", description="Cancel one of your price alerts")
    @app_commands.describe(alert_id="The alert number shown in /alert list")
    async def alert_remove(self, interaction: discord.Interaction, alert_id: int):
        removed = await db.remove_alert(alert_id, interaction.user.id)
        msg = f"Removed alert #{alert_id}." if removed else "Couldn't find that alert (check the ID with /alert list)."
        await interaction.response.send_message(msg)


async def setup(bot: commands.Bot):
    await bot.add_cog(Alerts(bot))
