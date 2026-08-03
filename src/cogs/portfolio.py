import asyncio

import discord
from discord import app_commands
from discord.ext import commands

from services import db, market_data


def _pl_line(ticker: str, shares: float, cost_basis: float, price: float) -> str:
    # Same ANSI color trick used elsewhere in the bot, one line per position.
    esc = chr(27)
    current_value = shares * price
    cost_value = shares * cost_basis
    pl_dollar = current_value - cost_value
    pl_pct = (pl_dollar / cost_value * 100) if cost_value else 0.0
    color_code = "32" if pl_dollar >= 0 else "31"
    reset = f"{esc}[0m"
    return (
        f"{esc}[1;{color_code}m{ticker:<6}{shares:>8.2f} sh @ ${cost_basis:>8.2f} avg -> "
        f"${price:>8.2f}  {pl_dollar:+,.2f} ({pl_pct:+.1f}%){reset}"
    )


class Portfolio(commands.Cog):
    # /portfolio tracks shares someone actually owns with a cost basis, unlike /watchlist's bare tickers.

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    portfolio_group = app_commands.Group(name="portfolio", description="Track shares you actually own")

    @portfolio_group.command(name="buy", description="Log a buy, adds to your position if you already own some")
    @app_commands.describe(
        ticker="Stock ticker symbol, e.g. AAPL",
        shares="Number of shares bought",
        price="Price per share you paid",
    )
    async def portfolio_buy(self, interaction: discord.Interaction, ticker: str, shares: float, price: float):
        ticker = ticker.upper().strip()
        if shares <= 0 or price <= 0:
            await interaction.response.send_message("Shares and price both need to be positive numbers.")
            return

        try:
            await market_data.get_quote(ticker)
        except market_data.TickerNotFoundError:
            await interaction.response.send_message(f"`{ticker}` doesn't look like a valid ticker.")
            return

        await db.buy_position(interaction.guild_id, interaction.user.id, ticker, shares, price)
        await interaction.response.send_message(
            f"Logged **{shares:g}** shares of **{ticker}** at **${price:,.2f}**. "
            f"Check `/portfolio view` for your updated position."
        )

    @portfolio_group.command(name="sell", description="Log a sell, reduces or closes your position")
    @app_commands.describe(ticker="Stock ticker symbol", shares="Number of shares sold")
    async def portfolio_sell(self, interaction: discord.Interaction, ticker: str, shares: float):
        ticker = ticker.upper().strip()
        if shares <= 0:
            await interaction.response.send_message("Shares needs to be a positive number.")
            return

        result = await db.sell_position(interaction.guild_id, interaction.user.id, ticker, shares)
        if result == "not_found":
            await interaction.response.send_message(f"You don't have a position in **{ticker}**.")
        elif result == "too_many":
            await interaction.response.send_message(f"You don't own that many shares of **{ticker}**.")
        else:
            await interaction.response.send_message(f"Logged selling **{shares:g}** shares of **{ticker}**.")

    @portfolio_group.command(name="remove", description="Fully remove a position, regardless of share count")
    async def portfolio_remove(self, interaction: discord.Interaction, ticker: str):
        ticker = ticker.upper().strip()
        removed = await db.remove_position(interaction.guild_id, interaction.user.id, ticker)
        msg = f"Removed your **{ticker}** position." if removed else f"You don't have a position in **{ticker}**."
        await interaction.response.send_message(msg)

    @portfolio_group.command(name="view", description="See your holdings and profit/loss")
    async def portfolio_view(self, interaction: discord.Interaction):
        # Fetching a live price per position takes longer than Discord's 3 second window.
        await interaction.response.defer()

        positions = await db.get_portfolio(interaction.guild_id, interaction.user.id)
        if not positions:
            await interaction.followup.send("You don't have any positions yet. Log one with `/portfolio buy`.")
            return

        # Fetched together instead of one at a time, so wall-clock time doesn't scale with position count.
        quotes = await asyncio.gather(
            *(market_data.get_quote(ticker) for ticker, _, _ in positions), return_exceptions=True
        )

        lines = []
        total_value = 0.0
        total_cost = 0.0
        for (ticker, shares, cost_basis), quote in zip(positions, quotes):
            if isinstance(quote, Exception):
                # A delisted or temporarily broken ticker shouldn't hide the rest of the portfolio.
                continue
            price = quote["price"]
            lines.append(_pl_line(ticker, shares, cost_basis, price))
            total_value += shares * price
            total_cost += shares * cost_basis

        if not lines:
            await interaction.followup.send("Couldn't fetch live prices for your positions right now, try again shortly.")
            return

        total_pl = total_value - total_cost
        total_pl_pct = (total_pl / total_cost * 100) if total_cost else 0.0

        embed = discord.Embed(
            title=f"{interaction.user.display_name}'s Portfolio",
            description="```ansi\n" + "\n".join(lines) + "\n```",
            color=discord.Color.green() if total_pl >= 0 else discord.Color.red(),
        )
        embed.add_field(name="Total Value", value=f"${total_value:,.2f}", inline=True)
        embed.add_field(name="Total P/L", value=f"{total_pl:+,.2f} ({total_pl_pct:+.1f}%)", inline=True)
        embed.set_footer(text="Prices delayed, data: Yahoo Finance")

        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Portfolio(bot))
