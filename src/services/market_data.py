import asyncio
import time
from datetime import datetime, timedelta, timezone

import finnhub
import requests
import yfinance as yf
# yfinance 1.x fetches over curl_cffi, not requests, this is the actual exception a network failure raises.
from curl_cffi.requests.exceptions import RequestException as CurlRequestException

from config import ALPHA_VANTAGE_API_KEY, FINNHUB_API_KEY
from services import db

# Stays None if no Finnhub key is set, so the functions below just return empty data instead of crashing.
_finnhub_client = finnhub.Client(api_key=FINNHUB_API_KEY) if FINNHUB_API_KEY else None


class TickerNotFoundError(Exception):
    # Raised whenever someone gives the bot a ticker that doesn't exist, like /stock BLAHBLAH123.
    pass


class MarketDataTimeoutError(Exception):
    # Yahoo Finance occasionally hangs for 10-15s+ instead of failing fast, this caps the wait instead of hanging forever.
    pass


# Yahoo Finance has no timeout of its own, some tickers hang 10s+ before failing, this stops the bot waiting on it.
_QUOTE_TIMEOUT_SECONDS = 8


# Company names never change, so this never needs a TTL or eviction like the quote/history caches below.
_name_cache: dict[str, str] = {}


def _fetch_quote_sync(ticker: str) -> dict:
    t = yf.Ticker(ticker)
    info = t.fast_info

    try:
        last_price = info.last_price
        prev_close = info.previous_close
    except CurlRequestException:
        # A real network failure (timeout, DNS, connection refused), not a bad ticker, let the caller retry.
        raise
    except Exception:
        # yfinance has no clean "not found" error, it just returns broken data for bad tickers.
        raise TickerNotFoundError(ticker)

    if last_price is None or prev_close is None:
        raise TickerNotFoundError(ticker)

    change = last_price - prev_close
    change_pct = (change / prev_close * 100) if prev_close else 0.0

    long_name = _name_cache.get(ticker.upper(), ticker.upper())
    if ticker.upper() not in _name_cache:
        try:
            # Names don't change, so this slow call only ever runs once per ticker for the process's lifetime.
            long_name = t.info.get("longName") or t.info.get("shortName") or long_name
            _name_cache[ticker.upper()] = long_name
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


def _fetch_finnhub_quote_sync(ticker: str) -> dict | None:
    if not _finnhub_client:
        return None
    try:
        quote = _finnhub_client.quote(ticker.upper())
    except Exception:
        return None
    # Finnhub returns all-zero fields instead of an error for tickers it doesn't cover, like CDRs or crypto.
    if not quote or not quote.get("c"):
        return None
    return quote


async def get_quote(ticker: str) -> dict:
    # Runs concurrently, Yahoo still supplies name/market cap/range but Finnhub's real-time US price wins when it covers the ticker.
    try:
        yahoo_quote, finnhub_quote = await asyncio.wait_for(
            asyncio.gather(
                asyncio.to_thread(_fetch_quote_sync, ticker),
                asyncio.to_thread(_fetch_finnhub_quote_sync, ticker),
            ),
            timeout=_QUOTE_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        raise MarketDataTimeoutError(ticker) from None
    except CurlRequestException:
        raise MarketDataTimeoutError(ticker) from None

    if finnhub_quote:
        price = finnhub_quote["c"]
        prev_close = finnhub_quote.get("pc") or yahoo_quote["prev_close"]
        change = finnhub_quote.get("d")
        change_pct = finnhub_quote.get("dp")
        yahoo_quote.update(
            {
                "price": price,
                "prev_close": prev_close,
                "change": change if change is not None else price - prev_close,
                "change_pct": change_pct if change_pct is not None else ((price - prev_close) / prev_close * 100 if prev_close else 0.0),
                "day_high": finnhub_quote.get("h") or yahoo_quote["day_high"],
                "day_low": finnhub_quote.get("l") or yahoo_quote["day_low"],
                "price_source": "finnhub",
            }
        )
    else:
        # CDRs, crypto, and non-US tickers fall back to Yahoo's price, which is delayed unlike Finnhub's.
        yahoo_quote["price_source"] = "yahoo"

    return yahoo_quote


def _fetch_price_history_sync(ticker: str, period: str, interval: str):
    t = yf.Ticker(ticker)
    history = t.history(period=period, interval=interval)
    if history.empty:
        raise TickerNotFoundError(ticker)
    return history


async def get_price_history(ticker: str, period: str, interval: str):
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_fetch_price_history_sync, ticker, period, interval), timeout=_QUOTE_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError:
        raise MarketDataTimeoutError(ticker) from None
    except CurlRequestException:
        # A real network failure, not an empty/bad ticker, that's the TickerNotFoundError case above.
        raise MarketDataTimeoutError(ticker) from None


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

# Shared by /movers, an hour-old cache is fine for 25 tickers of history.
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


# Per-ticker, not per-universe: the server digest needs this for arbitrary tracked tickers, not just the top-25.
_period_change_cache: dict[str, tuple[list[tuple[float, float]], float]] = {}
_PERIOD_CHANGE_CACHE_TTL = 60 * 60


async def get_period_change(ticker: str, period: str) -> dict | None:
    now = time.time()
    cached = _period_change_cache.get(ticker)
    if cached and now - cached[1] < _PERIOD_CHANGE_CACHE_TTL:
        points = cached[0]
    else:
        points = await asyncio.to_thread(_weekly_history_sync, ticker)
        if not points:
            return None
        _period_change_cache[ticker] = (points, now)

    now_time = datetime.now(timezone.utc).timestamp()
    completed = [p for p in points if now_time - p[0] > 6 * 24 * 60 * 60]
    days = _PERIOD_DAYS.get(period)
    if len(completed) < 2 or days is None:
        return None

    _, past_value = _closest_point(completed, now_time - days * 24 * 60 * 60)
    latest_value = points[-1][1]
    if not past_value:
        return None
    amount = latest_value - past_value
    return {"amount": amount, "pct": amount / past_value * 100}


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


def _fetch_company_overview_sync(ticker: str) -> dict:
    t = yf.Ticker(ticker)
    try:
        info = t.info
    except CurlRequestException:
        # A real network failure, not a bad ticker, let the caller retry instead of saying "invalid ticker".
        raise
    except Exception:
        raise TickerNotFoundError(ticker)

    if not info or not info.get("longName"):
        raise TickerNotFoundError(ticker)

    price = info.get("currentPrice") or info.get("regularMarketPrice")
    dividend_rate = info.get("dividendRate")
    # Computed from raw dollar figures instead of trusting info["dividendYield"], whose scale has changed across yfinance versions.
    dividend_yield_pct = (dividend_rate / price * 100) if dividend_rate and price else None

    return {
        "name": info.get("longName") or ticker.upper(),
        "summary": info.get("longBusinessSummary"),
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "employees": info.get("fullTimeEmployees"),
        "dividend_yield_pct": dividend_yield_pct,
        "pe_ratio": info.get("trailingPE"),
        "forward_pe": info.get("forwardPE"),
        "price_to_book": info.get("priceToBook"),
        "beta": info.get("beta"),
        "website": info.get("website"),
    }


async def get_company_overview(ticker: str) -> dict:
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_fetch_company_overview_sync, ticker), timeout=_QUOTE_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError:
        raise MarketDataTimeoutError(ticker) from None
    except CurlRequestException:
        raise MarketDataTimeoutError(ticker) from None


def _fetch_fear_greed_sync() -> dict | None:
    try:
        resp = requests.get("https://api.alternative.me/fng/?limit=1", timeout=8)
        resp.raise_for_status()
        data = resp.json()["data"][0]
        return {"value": int(data["value"]), "classification": data["value_classification"]}
    except Exception:
        return None


async def get_crypto_fear_greed() -> dict | None:
    # A free, no-key index (alternative.me), crypto only, there's no equivalent free source for stocks.
    return await asyncio.to_thread(_fetch_fear_greed_sync)
