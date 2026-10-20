import asyncio

import discord
from discord import app_commands
from discord.ext import commands

from config import HEAVY_COOLDOWN_SECONDS, LIGHT_COOLDOWN_SECONDS
from services import currency, db, fx, market_data
from services.ticker_search import make_owned_autocomplete, ticker_autocomplete


async def _my_portfolio_tickers(guild_id: int, user_id: int) -> list[str]:
    positions = await db.get_portfolio(guild_id, user_id)
    return [p["ticker"] for p in positions]


_owned_ticker_autocomplete = make_owned_autocomplete(_my_portfolio_tickers)


def _pl_dollar(direction: str, shares: float, cost_basis: float, price: float) -> float:
    # A short profits when price falls below what you shorted at, the exact opposite of a long position,
    # every P/L calculation in this cog and the digest routes through this one function so that's never
    # recomputed two different ways in two different places.
    if direction == "short":
        return (cost_basis - price) * shares
    return (price - cost_basis) * shares


async def _open_position(
    guild_id: int, user_id: int, ticker: str, direction: str, shares: float, price: float | None
) -> tuple[bool, str]:
    # Shared by /portfolio buy, /portfolio short, and the digest's quick-buy button. price=None means "use
    # today's live price", a real user's own feedback suggestion, checking an exact number first was
    # friction for the common case of just logging a trade you made right now.
    ticker = ticker.upper().strip()
    verb = "short" if direction == "short" else "buy"
    if shares <= 0:
        return False, "Shares needs to be a positive number."
    if price is not None and price <= 0:
        return False, "Price needs to be a positive number, or leave it blank to use the current price."

    try:
        quote = await market_data.get_quote(ticker)
    except market_data.TickerNotFoundError:
        return False, f"`{ticker}` doesn't look like a valid ticker."
    except market_data.MarketDataTimeoutError:
        return False, "Yahoo Finance is being slow right now, try again in a bit."

    actual_price = price if price is not None else quote["price"]
    result = await db.short_position(guild_id, user_id, ticker, shares, actual_price) if direction == "short" \
        else await db.buy_position(guild_id, user_id, ticker, shares, actual_price)
    if result != "ok":
        # result is the direction actually held, "long" or "short", when it conflicts with this one,
        # pointing at whichever command closes THAT direction, not the one they just tried to use.
        close_cmd = "sell" if result == "long" else "cover"
        return False, (
            f"You already have a **{result}** position in **{ticker}**, `/portfolio {close_cmd}` it "
            f"first before you can {verb} it."
        )
    price_str = currency.fmt_native(actual_price, quote.get("currency"))
    return True, (
        f"Logged {'shorting' if direction == 'short' else 'buying'} **{shares:g}** shares of **{ticker}** "
        f"at **{price_str}**. Check `/portfolio view` for your updated position."
    )


async def _buy_position(guild_id: int, user_id: int, ticker: str, shares: float, price: float | None) -> tuple[bool, str]:
    return await _open_position(guild_id, user_id, ticker, "long", shares, price)


async def _short_position(guild_id: int, user_id: int, ticker: str, shares: float, price: float | None) -> tuple[bool, str]:
    return await _open_position(guild_id, user_id, ticker, "short", shares, price)


async def _close_position(guild_id: int, user_id: int, ticker: str, direction: str, shares: float) -> tuple[bool, str]:
    # Shared by /portfolio sell, /portfolio cover, and the digest's quick-sell button.
    ticker = ticker.upper().strip()
    if shares <= 0:
        return False, "Shares needs to be a positive number."

    result = await db.cover_position(guild_id, user_id, ticker, shares) if direction == "short" \
        else await db.sell_position(guild_id, user_id, ticker, shares)
    if result == "not_found":
        return False, f"You don't have a position in **{ticker}**."
    if result == "too_many":
        return False, f"You don't hold that many shares of **{ticker}**."
    if result not in ("ok",):
        # result is the direction actually held, pointing them at the right command for it.
        other_cmd = "/portfolio cover" if result == "short" else "/portfolio sell"
        return False, f"**{ticker}** is a **{result}** position, use `{other_cmd}` instead."
    verb = "covering" if direction == "short" else "selling"
    return True, f"Logged {verb} **{shares:g}** shares of **{ticker}**."


async def _sell_position(guild_id: int, user_id: int, ticker: str, shares: float) -> tuple[bool, str]:
    return await _close_position(guild_id, user_id, ticker, "long", shares)


async def _cover_position(guild_id: int, user_id: int, ticker: str, shares: float) -> tuple[bool, str]:
    return await _close_position(guild_id, user_id, ticker, "short", shares)


def _pl_line(ticker: str, direction: str, shares: float, cost_basis: float, price: float, cur: str | None) -> str:
    # Same ANSI color trick used elsewhere in the bot, one line per position.
    esc = chr(27)
    pl_dollar = _pl_dollar(direction, shares, cost_basis, price)
    cost_value = shares * cost_basis
    pl_pct = (pl_dollar / cost_value * 100) if cost_value else 0.0
    color_code = "32" if pl_dollar >= 0 else "31"
    reset = f"{esc}[0m"
    tag = " (short)" if direction == "short" else ""
    label = "shorted at" if direction == "short" else "avg"
    return (
        f"{esc}[1;{color_code}m{ticker:<6}{tag}{shares:>8.2f} sh @ {currency.fmt_native_padded(cost_basis, cur)} {label} -> "
        f"{currency.fmt_native_padded(price, cur)}  {pl_dollar:+,.2f} ({pl_pct:+.1f}%){reset}"
    )


class Portfolio(commands.Cog):
    # /portfolio tracks shares someone actually owns with a cost basis, unlike /watchlist's bare tickers.

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    portfolio_group = app_commands.Group(name="portfolio", description="Track shares you actually own")

    @portfolio_group.command(name="buy", description="Log a buy, adds to your position if you already own some")
    @app_commands.checks.cooldown(1, LIGHT_COOLDOWN_SECONDS)
    @app_commands.describe(
        ticker="Stock ticker symbol, e.g. AAPL",
        shares="Number of shares bought",
        price="Price per share you paid, leave blank to use today's live price",
    )
    @app_commands.autocomplete(ticker=ticker_autocomplete)
    async def portfolio_buy(self, interaction: discord.Interaction, ticker: str, shares: float, price: float = None):
        await interaction.response.defer()
        ok, message = await _buy_position(interaction.guild_id, interaction.user.id, ticker, shares, price)
        await interaction.followup.send(message)

    @portfolio_group.command(name="sell", description="Log a sell, reduces or closes your position")
    @app_commands.checks.cooldown(1, LIGHT_COOLDOWN_SECONDS)
    @app_commands.describe(ticker="Stock ticker symbol", shares="Number of shares sold")
    @app_commands.autocomplete(ticker=_owned_ticker_autocomplete)
    async def portfolio_sell(self, interaction: discord.Interaction, ticker: str, shares: float):
        ok, message = await _sell_position(interaction.guild_id, interaction.user.id, ticker, shares)
        await interaction.response.send_message(message)

    @portfolio_group.command(name="short", description="Open a short position, adds to it if you already have one")
    @app_commands.checks.cooldown(1, LIGHT_COOLDOWN_SECONDS)
    @app_commands.describe(
        ticker="Stock ticker symbol, e.g. AAPL",
        shares="Number of shares shorted",
        price="Price per share you shorted at, leave blank to use today's live price",
    )
    @app_commands.autocomplete(ticker=ticker_autocomplete)
    async def portfolio_short(self, interaction: discord.Interaction, ticker: str, shares: float, price: float = None):
        await interaction.response.defer()
        ok, message = await _short_position(interaction.guild_id, interaction.user.id, ticker, shares, price)
        await interaction.followup.send(message)

    @portfolio_group.command(name="cover", description="Buy back shares to close or reduce a short position")
    @app_commands.checks.cooldown(1, LIGHT_COOLDOWN_SECONDS)
    @app_commands.describe(ticker="Stock ticker symbol", shares="Number of shares bought back")
    @app_commands.autocomplete(ticker=_owned_ticker_autocomplete)
    async def portfolio_cover(self, interaction: discord.Interaction, ticker: str, shares: float):
        ok, message = await _cover_position(interaction.guild_id, interaction.user.id, ticker, shares)
        await interaction.response.send_message(message)

    @portfolio_group.command(name="edit", description="Correct a position's shares or average cost, no blending")
    @app_commands.checks.cooldown(1, LIGHT_COOLDOWN_SECONDS)
    @app_commands.describe(
        ticker="Stock ticker symbol",
        shares="Correct total share count, leave blank to only change the average cost",
        avg_cost="Correct average cost per share, leave blank to only change the share count",
    )
    @app_commands.autocomplete(ticker=_owned_ticker_autocomplete)
    async def portfolio_edit(
        self, interaction: discord.Interaction, ticker: str, shares: float = None, avg_cost: float = None
    ):
        ticker = ticker.upper().strip()
        if shares is None and avg_cost is None:
            await interaction.response.send_message("Give at least a new share count or average cost to change.")
            return
        if (shares is not None and shares <= 0) or (avg_cost is not None and avg_cost <= 0):
            await interaction.response.send_message("Shares and average cost both need to be positive numbers.")
            return

        edited = await db.edit_position(interaction.guild_id, interaction.user.id, ticker, shares, avg_cost)
        if not edited:
            await interaction.response.send_message(f"You don't have a position in **{ticker}**.")
            return

        parts = []
        if shares is not None:
            parts.append(f"shares to **{shares:g}**")
        if avg_cost is not None:
            parts.append(f"average cost to **${avg_cost:,.2f}**")
        await interaction.response.send_message(f"Updated **{ticker}**: set " + " and ".join(parts) + ".")

    @portfolio_group.command(name="remove", description="Fully remove a position, regardless of share count")
    @app_commands.checks.cooldown(1, LIGHT_COOLDOWN_SECONDS)
    @app_commands.autocomplete(ticker=_owned_ticker_autocomplete)
    async def portfolio_remove(self, interaction: discord.Interaction, ticker: str):
        ticker = ticker.upper().strip()
        removed = await db.remove_position(interaction.guild_id, interaction.user.id, ticker)
        msg = f"Removed your **{ticker}** position." if removed else f"You don't have a position in **{ticker}**."
        await interaction.response.send_message(msg)

    @portfolio_group.command(name="view", description="See your holdings and profit/loss")
    @app_commands.checks.cooldown(1, HEAVY_COOLDOWN_SECONDS)
    async def portfolio_view(self, interaction: discord.Interaction):
        # Fetching a live price per position takes longer than Discord's 3 second window.
        await interaction.response.defer()

        positions = await db.get_portfolio(interaction.guild_id, interaction.user.id)
        if not positions:
            await interaction.followup.send("You don't have any positions yet. Log one with `/portfolio buy`.")
            return

        # Fetched together instead of one at a time, so wall-clock time doesn't scale with position count.
        quotes = await asyncio.gather(
            *(market_data.get_quote(ticker) for ticker, direction, shares, cost_basis in positions),
            return_exceptions=True,
        )

        lines = []
        total_value = 0.0
        total_cost = 0.0
        total_pl = 0.0
        for (ticker, direction, shares, cost_basis), quote in zip(positions, quotes):
            if isinstance(quote, Exception):
                # A delisted or temporarily broken ticker shouldn't hide the rest of the portfolio.
                continue
            price = quote["price"]
            cur = quote.get("currency") or "USD"
            lines.append(_pl_line(ticker, direction, shares, cost_basis, price, cur))

            # Positions can be in different listing currencies (a Paris-listed EUR stock next to a US one),
            # so everything gets normalized to USD before adding to a single portfolio-wide total.
            fx_rate = 1.0 if cur == "USD" else (await fx.get_fx_rate(cur, "USD") or 1.0)
            total_value += shares * price * fx_rate
            total_cost += shares * cost_basis * fx_rate
            # Summed directly instead of derived from total_value - total_cost, a short's P/L isn't that
            # simple subtraction (it profits when price falls), each position's own correctly-signed
            # number is what actually adds up right across a portfolio mixing longs and shorts.
            total_pl += _pl_dollar(direction, shares, cost_basis, price) * fx_rate

        if not lines:
            await interaction.followup.send("Couldn't fetch live prices for your positions right now, try again shortly.")
            return

        total_pl_pct = (total_pl / total_cost * 100) if total_cost else 0.0

        # Embed descriptions cap at 4096 characters, a huge portfolio (70+ positions) could otherwise blow past
        # that and make Discord reject the whole message instead of just showing fewer lines.
        shown_lines = lines
        hidden_count = 0
        while shown_lines and len("```ansi\n" + "\n".join(shown_lines) + "\n```") > 3900:
            shown_lines = shown_lines[:-1]
            hidden_count += 1

        description = "```ansi\n" + "\n".join(shown_lines) + "\n```"
        if hidden_count:
            description += f"\n*+{hidden_count} more position{'s' if hidden_count != 1 else ''}, see the full list on the website.*"

        embed = discord.Embed(
            title=f"{interaction.user.display_name}'s Portfolio",
            description=description,
            color=discord.Color.green() if total_pl >= 0 else discord.Color.red(),
        )
        # Totals above are normalized to USD, the opt-in /currency setting converts that into whatever's preferred.
        pref = await db.get_currency_pref(interaction.guild_id, interaction.user.id)
        display_value, display_pl = total_value, total_pl
        display_cur = "USD"
        if pref and pref != "USD":
            rate = await fx.get_fx_rate("USD", pref)
            if rate is not None:
                display_value, display_pl, display_cur = total_value * rate, total_pl * rate, pref

        pl_sign = "+" if display_pl >= 0 else "-"
        pl_str = f"{pl_sign}{currency.fmt_native(abs(display_pl), display_cur)}"
        embed.add_field(name="Total Value", value=currency.fmt_native(display_value, display_cur), inline=True)
        embed.add_field(name="Total P/L", value=f"{pl_str} ({total_pl_pct:+.1f}%)", inline=True)
        # A mixed portfolio can have both real-time (Finnhub-covered) and delayed (Yahoo-only) tickers in one embed.
        embed.set_footer(text="Data: Yahoo Finance, real-time price via Finnhub where available")

        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Portfolio(bot))
