import discord
from discord import app_commands
from discord.ext import commands

from config import LIGHT_COOLDOWN_SECONDS
from services import db, fx


class Currency(commands.Cog):
    # A niche setting almost nobody needs, prices show in their native currency unless this is turned on.

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    currency_group = app_commands.Group(
        name="currency",
        description="Niche setting: see prices converted into a currency other than their native one",
    )

    @currency_group.command(name="set", description="Show prices converted into this currency wherever Investo displays one")
    @app_commands.checks.cooldown(1, LIGHT_COOLDOWN_SECONDS)
    @app_commands.describe(code="A currency code, like EUR, GBP, or JPY")
    async def currency_set(self, interaction: discord.Interaction, code: str):
        code = code.strip().upper()
        if len(code) != 3 or not code.isalpha():
            await interaction.response.send_message("That doesn't look like a currency code, try something like `EUR` or `GBP`.")
            return

        await interaction.response.defer()
        # Checking the pair actually resolves catches typos and made-up codes before saving a dead setting.
        rate = await fx.get_fx_rate(code, "USD")
        if rate is None:
            await interaction.followup.send(f"Couldn't find exchange rate data for **{code}**, double check the code.")
            return

        await db.set_currency_pref(interaction.guild_id, interaction.user.id, code)
        await interaction.followup.send(
            f"Got it, prices will now show converted into **{code}** alongside their native currency. Turn it off with `/currency off`."
        )

    @currency_group.command(name="off", description="Stop converting prices, go back to seeing each ticker's native currency")
    @app_commands.checks.cooldown(1, LIGHT_COOLDOWN_SECONDS)
    async def currency_off(self, interaction: discord.Interaction):
        cleared = await db.clear_currency_pref(interaction.guild_id, interaction.user.id)
        if not cleared:
            await interaction.response.send_message("You didn't have a currency conversion set.")
            return
        await interaction.response.send_message("Turned off currency conversion, prices will show in their native currency again.")

    @currency_group.command(name="show", description="Check what currency conversion, if any, you have set")
    @app_commands.checks.cooldown(1, LIGHT_COOLDOWN_SECONDS)
    async def currency_show(self, interaction: discord.Interaction):
        pref = await db.get_currency_pref(interaction.guild_id, interaction.user.id)
        if not pref:
            await interaction.response.send_message("You're not converting prices, everything shows in its native currency.")
            return
        await interaction.response.send_message(f"Prices are currently converted into **{pref}**.")


async def setup(bot: commands.Bot):
    await bot.add_cog(Currency(bot))
