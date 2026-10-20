import asyncio
import time

import yfinance as yf

# FX rates barely move minute to minute for casual display purposes, a long TTL keeps this
# from firing a Yahoo request on every single price line in a digest or portfolio view.
_FX_CACHE_TTL = 10 * 60.0
_fx_cache: dict[str, tuple[float, float]] = {}


def _fetch_rate_sync(pair: str) -> float | None:
    try:
        info = yf.Ticker(pair).fast_info
        rate = info.last_price
    except Exception:
        return None
    return rate if rate else None


async def get_fx_rate(from_currency: str, to_currency: str) -> float | None:
    # Returns the number of to_currency units one from_currency unit buys, or None if the pair can't be fetched.
    from_currency = from_currency.upper()
    to_currency = to_currency.upper()
    if from_currency == to_currency:
        return 1.0

    pair = f"{from_currency}{to_currency}=X"
    now = time.time()
    cached = _fx_cache.get(pair)
    if cached and now - cached[1] < _FX_CACHE_TTL:
        return cached[0]

    rate = await asyncio.to_thread(_fetch_rate_sync, pair)
    if rate is None:
        # Yahoo doesn't list every pair in both directions, the inverse often exists even when the direct one doesn't.
        inverse = await asyncio.to_thread(_fetch_rate_sync, f"{to_currency}{from_currency}=X")
        rate = (1 / inverse) if inverse else None

    if rate is not None:
        _fx_cache[pair] = (rate, now)
    return rate
