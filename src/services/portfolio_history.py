import asyncio

import pandas as pd

from services import charts, fx, market_data


def _contribution(closes: pd.Series, shares: float, cost_basis: float, is_short: bool, rate: float) -> pd.Series:
    # Same construction the digest and /portfolio view use for totals: for a short this isn't the
    # notional, it's built so (value - cost) always equals that position's correctly-signed P/L.
    native = (2 * cost_basis - closes) if is_short else closes
    return native * shares * rate


async def get_portfolio_history(positions: list[dict], range_key: str) -> pd.Series | None:
    # Approximate: current share count applied to past prices, there's no transaction log to know
    # exact buy dates, same approach the website's getPortfolioHistory already uses.
    range_opts = charts.RANGE_OPTIONS[range_key]
    histories, quotes = await asyncio.gather(
        asyncio.gather(
            *(market_data.get_price_history(p["ticker"], range_opts["period"], range_opts["interval"]) for p in positions),
            return_exceptions=True,
        ),
        asyncio.gather(*(market_data.get_quote(p["ticker"]) for p in positions), return_exceptions=True),
    )

    # Daily-and-up intervals bucket by calendar date, different exchanges close their daily bar at
    # different exact seconds. Intraday keeps real timestamps so the forward-fill below still works.
    is_intraday = range_opts["interval"].endswith("m") or range_opts["interval"].endswith("h")

    series_list = []
    for p, history, quote in zip(positions, histories, quotes):
        if isinstance(history, Exception) or isinstance(quote, Exception) or history.empty:
            continue
        closes = history["Close"]
        if not is_intraday:
            closes = closes.groupby(closes.index.normalize()).last()

        currency = quote.get("currency") or "USD"
        rate = 1.0 if currency == "USD" else (await fx.get_fx_rate(currency, "USD") or 1.0)
        series_list.append(_contribution(closes, p["shares"], p["cost_basis"], p["direction"] == "short", rate))

    if not series_list:
        return None

    combined = pd.concat(series_list, axis=1)
    if is_intraday:
        # Stocks only have bars during market hours but crypto trades 24/7, forward-fill each ticker's
        # last known price into every timestamp instead of dropping it outside market hours.
        combined = combined.ffill()

    total = combined.sum(axis=1, skipna=True)
    return total.dropna().sort_index()
