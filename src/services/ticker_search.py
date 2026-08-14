import asyncio
import logging
import time

import yfinance as yf
from discord import app_commands

log = logging.getLogger("investo.ticker_search")

# Only types Investo can actually quote/chart, futures and options add real complexity for little payoff
# at this bot's scale, and would need their own handling in market_data.py before showing up here.
_ALLOWED_TYPES = {"EQUITY", "ETF", "CRYPTOCURRENCY", "INDEX", "MUTUALFUND", "CURRENCY"}

# Short-lived, per-process cache, someone typing "APPL" then backspacing to "APP" shouldn't hit Yahoo twice
# for overlapping queries within the same few seconds, same tradeoff as every other cache in this codebase.
_cache: dict[str, tuple[list[dict], float]] = {}
_CACHE_TTL = 5 * 60


def _search_sync(query: str) -> list[dict]:
    try:
        return yf.Search(query, max_results=15).quotes
    except Exception:
        log.exception("Ticker search failed for query %r", query)
        return []


async def search_tickers(query: str) -> list[dict]:
    # A real symbol/company-name search, so someone who types "bitcoin" sees the actual BTC-USD
    # cryptocurrency clearly labeled apart from lookalikes like GBTC (an ETF that also displays "(BTC)"
    # in its own name), instead of having to already know the exact ticker and guess right.
    query = query.strip()
    if not query:
        return []

    now = time.time()
    key = query.lower()
    cached = _cache.get(key)
    if cached and now - cached[1] < _CACHE_TTL:
        return cached[0]

    try:
        quotes = await asyncio.wait_for(asyncio.to_thread(_search_sync, query), timeout=2.5)
    except asyncio.TimeoutError:
        return []

    results = [
        {
            "ticker": q["symbol"],
            "name": q.get("shortname") or q.get("longname") or q["symbol"],
            "type": q.get("typeDisp") or q.get("quoteType") or "",
        }
        for q in quotes
        if q.get("symbol") and q.get("quoteType") in _ALLOWED_TYPES
    ]

    _cache[key] = (results, now)
    # Prunes on write instead of a separate cleanup task, same pattern as the other in-memory caches here.
    for k, (_, t) in list(_cache.items()):
        if now - t >= _CACHE_TTL:
            del _cache[k]

    return results


def _to_choice(r: dict) -> app_commands.Choice[str]:
    name = f"{r['ticker']} · {r['name']} ({r['type']})" if r["type"] else f"{r['ticker']} · {r['name']}"
    return app_commands.Choice(name=name[:100], value=r["ticker"])


async def ticker_autocomplete(interaction, current: str) -> list[app_commands.Choice[str]]:
    # Discord requires this to return within ~3 seconds with no defer() available, search_tickers already
    # has its own timeout, this just guarantees a broken lookup shows "no results" instead of a stuck dropdown.
    try:
        results = await search_tickers(current)
    except Exception:
        return []
    return [_to_choice(r) for r in results[:25]]


def make_owned_autocomplete(fetch_fn):
    # For remove/sell/edit-style commands, autocompletes from what the user already has instead of a fresh
    # market search, no risk of picking the wrong lookalike ticker since these only ever list their own entries.
    async def _autocomplete(interaction, current: str) -> list[app_commands.Choice[str]]:
        try:
            tickers = await fetch_fn(interaction.guild_id, interaction.user.id)
        except Exception:
            return []
        current = current.upper().strip()
        matches = [t for t in tickers if current in t.upper()][:25]
        return [app_commands.Choice(name=t, value=t) for t in matches]

    return _autocomplete
