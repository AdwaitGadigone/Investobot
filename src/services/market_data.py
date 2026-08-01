import asyncio
import time
from datetime import datetime, timedelta, timezone

import finnhub
import requests
import yfinance as yf

from config import ALPHA_VANTAGE_API_KEY, FINNHUB_API_KEY
from services import db

# Stays None if no Finnhub key is set, so the functions below just return empty data instead of crashing.
_finnhub_client = finnhub.Client(api_key=FINNHUB_API_KEY) if FINNHUB_API_KEY else None


class TickerNotFoundError(Exception):
    # Raised whenever someone gives the bot a ticker that doesn't exist, like /stock BLAHBLAH123.
    pass


def _fetch_quote_sync(ticker: str) -> dict:
    t = yf.Ticker(ticker)
    info = t.fast_info

    try:
        last_price = info.last_price
        prev_close = info.previous_close
    except Exception:
        # yfinance has no clean "not found" error, it just returns broken data for bad tickers.
        raise TickerNotFoundError(ticker)

    if last_price is None or prev_close is None:
        raise TickerNotFoundError(ticker)

    change = last_price - prev_close
    change_pct = (change / prev_close * 100) if prev_close else 0.0

    long_name = ticker.upper()
    try:
        # This is a slower, separate call, falls back to the ticker symbol if it fails.
        long_name = t.info.get("longName") or t.info.get("shortName") or long_name
    except Exception:
        pass

    return {
        "ticker": ticker.upper(),
        "name": long_name,
        "price": last_price,
        "prev_close": prev_close,
        "change": change,
        "change_pct": change_pct,
        "day_high": getattr(info, "day_high", None),
        "day_low": getattr(info, "day_low", None),
        "year_high": getattr(info, "year_high", None),
        "year_low": getattr(info, "year_low", None),
        "volume": getattr(info, "last_volume", None),
        "market_cap": getattr(info, "market_cap", None),
    }


async def get_quote(ticker: str) -> dict:
    # yfinance blocks while it waits on the network, running it on a thread keeps the bot responsive.
    return await asyncio.to_thread(_fetch_quote_sync, ticker)


def _fetch_price_history_sync(ticker: str, period: str, interval: str):
    t = yf.Ticker(ticker)
    history = t.history(period=period, interval=interval)
    if history.empty:
        raise TickerNotFoundError(ticker)
    return history


async def get_price_history(ticker: str, period: str, interval: str):
    return await asyncio.to_thread(_fetch_price_history_sync, ticker, period, interval)


def _fetch_recommendation_trends_sync(ticker: str) -> dict | None:
    if not _finnhub_client:
        return None
    try:
        trends = _finnhub_client.recommendation_trends(ticker.upper())
    except finnhub.exceptions.FinnhubAPIException:
        # A free-tier Finnhub key can't reach every endpoint, skip quietly instead of crashing.
        return None
    # Finnhub returns newest month first, so index 0 is the one we actually want.
    return trends[0] if trends else None


async def get_recommendation_trends(ticker: str) -> dict | None:
    return await asyncio.to_thread(_fetch_recommendation_trends_sync, ticker)


def _fetch_price_target_sync(ticker: str) -> dict | None:
    if not _finnhub_client:
        return None
    try:
        target = _finnhub_client.price_target(ticker.upper())
    except finnhub.exceptions.FinnhubAPIException:
        # Price targets need a paid Finnhub plan, a free key gets a 403 here every time.
        return None
    return target or None


async def get_price_target(ticker: str) -> dict | None:
    return await asyncio.to_thread(_fetch_price_target_sync, ticker)


def _fetch_av_target_price_sync(ticker: str) -> float | None:
    if not ALPHA_VANTAGE_API_KEY:
        return None

    resp = requests.get(
        "https://www.alphavantage.co/query",
        params={"function": "OVERVIEW", "symbol": ticker.upper(), "apikey": ALPHA_VANTAGE_API_KEY},
        timeout=10,
    )
    data = resp.json()
    raw = data.get("AnalystTargetPrice")
    if not raw or raw in ("None", "-"):
        return None

    try:
        return float(raw)
    except ValueError:
        return None


async def get_price_target_average(ticker: str) -> float | None:
    # Backup price target source for when Finnhub's is blocked behind its paid plan.
    ticker = ticker.upper()
    today = datetime.now(timezone.utc).date().isoformat()

    cached = await db.get_cached_price_target(ticker)
    if cached and cached[1] == today:
        # Alpha Vantage's free tier is only 25 requests/day total, so we reuse this for the rest of the day.
        return cached[0]

    value = await asyncio.to_thread(_fetch_av_target_price_sync, ticker)
    if value is not None:
        await db.set_cached_price_target(ticker, value, today)
    return value


def _fetch_company_news_sync(ticker: str, days_back: int) -> list[dict]:
    if not _finnhub_client:
        return []

    to_date = datetime.utcnow().date()
    from_date = to_date - timedelta(days=days_back)
    try:
        news = _finnhub_client.company_news(
            ticker.upper(), _from=from_date.isoformat(), to=to_date.isoformat()
        )
    except finnhub.exceptions.FinnhubAPIException:
        return []
    return news or []


async def get_company_news(ticker: str, days_back: int = 3) -> list[dict]:
    return await asyncio.to_thread(_fetch_company_news_sync, ticker, days_back)


# Mirrors investo-web's discover.js exactly, so the bot and site never disagree on rankings.


def _screener_by_market_cap_sync(scr_id: str, count: int = 40, limit: int = 5) -> list[dict]:
    try:
        result = yf.screen(scr_id, count=count)
    except Exception:
        return []

    quotes = [
        q
        for q in result.get("quotes", [])
        if q.get("symbol")
        and (q.get("shortName") or q.get("longName"))
        and q.get("regularMarketPrice") is not None
        and q.get("marketCap") is not None
    ]
    quotes.sort(key=lambda q: q["marketCap"], reverse=True)

    return [
        {
            "ticker": q["symbol"],
            "name": q.get("shortName") or q.get("longName"),
            "price": q["regularMarketPrice"],
            "change": q.get("regularMarketChange") or 0.0,
            "change_pct": q.get("regularMarketChangePercent") or 0.0,
        }
        for q in quotes[:limit]
    ]


def _most_active_sync(count: int = 25, limit: int = 25) -> list[dict]:
    try:
        result = yf.screen("most_actives", count=count)
    except Exception:
        return []

    quotes = [
        q
        for q in result.get("quotes", [])
        if q.get("symbol") and (q.get("shortName") or q.get("longName")) and q.get("regularMarketPrice") is not None
    ]

    return [
        {
            "ticker": q["symbol"],
            "name": q.get("shortName") or q.get("longName"),
            "price": q["regularMarketPrice"],
            "change": q.get("regularMarketChange"),
            "change_pct": q.get("regularMarketChangePercent"),
        }
        for q in quotes[:limit]
    ]


_PERIOD_DAYS = {"week": 7, "month": 30, "three_month": 90, "year": 365, "five_year": 365 * 5}

# Shared by /movers and /performers, an hour-old cache is fine for 25 tickers of history.
_universe_cache: dict | None = None
_universe_cache_time: float = 0.0
_UNIVERSE_CACHE_TTL = 60 * 60


def _closest_point(points: list[tuple[float, float]], target_time: float) -> tuple[float, float]:
    return min(points, key=lambda p: abs(p[0] - target_time))


def _weekly_history_sync(ticker: str) -> list[tuple[float, float]] | None:
    try:
        history = yf.Ticker(ticker).history(period="5y", interval="1wk")
        if history.empty:
            return None
        return [(idx.timestamp(), float(close)) for idx, close in history["Close"].items()]
    except Exception:
        return None


async def _get_ranked_universe() -> dict:
    global _universe_cache, _universe_cache_time
    now = time.time()
    if _universe_cache and now - _universe_cache_time < _UNIVERSE_CACHE_TTL:
        return _universe_cache

    universe = await asyncio.to_thread(_most_active_sync, 25, 25)
    now_time = datetime.now(timezone.utc).timestamp()

    histories = await asyncio.gather(*[asyncio.to_thread(_weekly_history_sync, u["ticker"]) for u in universe])

    valid = []
    for u, points in zip(universe, histories):
        if not points:
            continue
        # Drop bars under 6 days old, Yahoo's newest weekly bar is just today's live price.
        completed = [p for p in points if now_time - p[0] > 6 * 24 * 60 * 60]
        if len(completed) < 2:
            continue

        changes = {}
        for key, days in _PERIOD_DAYS.items():
            _, past_value = _closest_point(completed, now_time - days * 24 * 60 * 60)
            amount = (u["price"] - past_value) if past_value else 0.0
            pct = (amount / past_value * 100) if past_value else 0.0
            changes[key] = {"amount": amount, "pct": pct}

        valid.append({**u, "changes": changes})

    result = {"universe": universe, "valid": valid}
    _universe_cache = result
    _universe_cache_time = now
    return result


def _period_row(entry: dict, period: str) -> dict:
    return {
        "ticker": entry["ticker"],
        "name": entry["name"],
        "price": entry["price"],
        "change": entry["changes"][period]["amount"],
        "change_pct": entry["changes"][period]["pct"],
    }


async def get_top_performers() -> dict:
    # "day" skips the history fetch, the screener already gives today's change directly.
    data = await _get_ranked_universe()
    universe, valid = data["universe"], data["valid"]

    ranked = {
        "day": sorted(
            [u for u in universe if u.get("change_pct") is not None],
            key=lambda u: u["change_pct"],
            reverse=True,
        )[:5]
    }

    for period in _PERIOD_DAYS:
        ranked[period] = [
            _period_row(r, period) for r in sorted(valid, key=lambda r: r["changes"][period]["pct"], reverse=True)[:5]
        ]

    return ranked


async def get_market_movers() -> dict:
    # Non-"day" periods reuse the ranked universe, there's no "week_gainers" screener to pull from.
    day_gainers, day_losers, active, universe_data = await asyncio.gather(
        asyncio.to_thread(_screener_by_market_cap_sync, "day_gainers"),
        asyncio.to_thread(_screener_by_market_cap_sync, "day_losers"),
        asyncio.to_thread(lambda: _most_active_sync(25, 5)),
        _get_ranked_universe(),
    )

    movers = {"day": {"gainers": day_gainers, "losers": day_losers, "active": active}}
    valid = universe_data["valid"]

    for period in _PERIOD_DAYS:
        sorted_by_period = sorted(valid, key=lambda r: r["changes"][period]["pct"], reverse=True)
        movers[period] = {
            "gainers": [_period_row(r, period) for r in sorted_by_period[:5]],
            "losers": [_period_row(r, period) for r in sorted_by_period[-5:][::-1]],
            "active": active,
        }

    return movers


def summarize_recommendation(trends: dict | None) -> tuple[str, str] | None:
    # Boils the full breakdown down into one quick label like "Buy" for the main /stock embed.
    if not trends:
        return None

    strong_buy = trends.get("strongBuy", 0)
    buy = trends.get("buy", 0)
    hold = trends.get("hold", 0)
    sell = trends.get("sell", 0)
    strong_sell = trends.get("strongSell", 0)
    total = strong_buy + buy + hold + sell + strong_sell
    if total == 0:
        return None

    # Strong buy/sell count double, same basic idea TipRanks-style scoring uses.
    score = (strong_buy * 2 + buy * 1 + hold * 0 + sell * -1 + strong_sell * -2) / total

    if score >= 1.2:
        return "🟢", "Strong Buy"
    if score >= 0.4:
        return "🟢", "Buy"
    if score >= -0.4:
        return "🟡", "Hold"
    if score >= -1.2:
        return "🔴", "Sell"
    return "🔴", "Strong Sell"
