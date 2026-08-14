import discord
from discord import app_commands
from discord.ext import commands

from config import LIGHT_COOLDOWN_SECONDS
from services import db, market_data
from services.ticker_search import ticker_autocomplete


class Alerts(commands.Cog):
    # Personal price alerts, the actual checking and DMing happens in cogs/scheduler.py's background loop.

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # app_commands.Group turns /alert into a parent command with sub-commands like /alert set.
    alert_group = app_commands.Group(name="alert", description="Manage your personal price alerts")

    @alert_group.command(name="set", description="Get DM'd when a ticker goes above/below a price")
    @app_commands.checks.cooldown(1, LIGHT_COOLDOWN_SECONDS)
    @app_commands.describe(
        ticker="Stock ticker symbol, e.g. AAPL",
        direction="Alert when price goes above or below the target",
        price="Target price in dollars",
    )
    # app_commands.choices restricts the "direction" option to a dropdown of just these two values.
    @app_commands.choices(
        direction=[
            app_commands.Choice(name="above", value="above"),
            app_commands.Choice(name="below", value="below"),
        ]
    )
    @app_commands.autocomplete(ticker=ticker_autocomplete)
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
        except market_data.MarketDataTimeoutError:
            await interaction.response.send_message("Yahoo Finance is being slow right now, try again in a bit.")
            return

        await db.add_alert(interaction.guild_id, interaction.user.id, ticker, direction.value, price)
        # alerts.id is a global sequence shared by everyone, showing it would make a first-ever alert read as "#4".
        position = len(await db.get_user_alerts(interaction.guild_id, interaction.user.id))
        await interaction.response.send_message(
            f"Alert #{position} set. I'll DM you when **{ticker}** goes {direction.value} **${price:,.2f}**."
        )

    @alert_group.command(name="list", description="Show your active price alerts")
    @app_commands.checks.cooldown(1, LIGHT_COOLDOWN_SECONDS)
    async def alert_list(self, interaction: discord.Interaction):
        rows = await db.get_user_alerts(interaction.guild_id, interaction.user.id)
        if not rows:
            await interaction.response.send_message("You have no active alerts.")
            return

        lines = [
            f"#{i} - **{ticker}** {direction} ${price:,.2f}"
            for i, (_, ticker, direction, price) in enumerate(rows, start=1)
        ]
        await interaction.response.send_message("\n".join(lines))

    @alert_group.command(name="remove", description="Cancel one of your price alerts")
    @app_commands.checks.cooldown(1, LIGHT_COOLDOWN_SECONDS)
    @app_commands.describe(alert_id="The alert number shown in /alert list")
    async def alert_remove(self, interaction: discord.Interaction, alert_id: int):
        # alert_id here is the position shown in /alert list, not the raw database ID, resolve it to the real one first.
        rows = await db.get_user_alerts(interaction.guild_id, interaction.user.id)
        if alert_id < 1 or alert_id > len(rows):
            await interaction.response.send_message("Couldn't find that alert (check the number with /alert list).")
            return

        real_id = rows[alert_id - 1]["id"]
        removed = await db.remove_alert(real_id, interaction.user.id)
        msg = f"Removed alert #{alert_id}." if removed else "Couldn't find that alert (check the number with /alert list)."
        await interaction.response.send_message(msg)


async def setup(bot: commands.Bot):
    await bot.add_cog(Alerts(bot))
