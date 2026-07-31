import asyncio
from datetime import datetime, timedelta, timezone

import finnhub
import requests
import yfinance as yf

from config import ALPHA_VANTAGE_API_KEY, FINNHUB_API_KEY
from services import db

_finnhub_client = finnhub.Client(api_key=FINNHUB_API_KEY) if FINNHUB_API_KEY else None  # stays None if no key, so features just return empty data


class TickerNotFoundError(Exception):
    pass  # raised whenever someone gives the bot a ticker that doesn't exist, like /stock BLAHBLAH123


def _fetch_quote_sync(ticker: str) -> dict:
    t = yf.Ticker(ticker)
    info = t.fast_info

    try:
        last_price = info.last_price
        prev_close = info.previous_close
    except Exception:
        raise TickerNotFoundError(ticker)  # yfinance has no clean "not found" error, it just returns broken data for bad tickers

    if last_price is None or prev_close is None:
        raise TickerNotFoundError(ticker)

    change = last_price - prev_close
    change_pct = (change / prev_close * 100) if prev_close else 0.0

    long_name = ticker.upper()
    try:
        long_name = t.info.get("longName") or t.info.get("shortName") or long_name  # slower call, falls back to the ticker symbol if it fails
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
    return await asyncio.to_thread(_fetch_quote_sync, ticker)  # yfinance blocks, so this runs it on a thread instead of freezing the bot


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
        return None  # a free-tier Finnhub key can't reach every endpoint, skip quietly instead of crashing the command
    return trends[0] if trends else None  # Finnhub returns newest month first, so index 0 is the one we actually want


async def get_recommendation_trends(ticker: str) -> dict | None:
    return await asyncio.to_thread(_fetch_recommendation_trends_sync, ticker)


def _fetch_price_target_sync(ticker: str) -> dict | None:
    if not _finnhub_client:
        return None
    try:
        target = _finnhub_client.price_target(ticker.upper())
    except finnhub.exceptions.FinnhubAPIException:
        return None  # price targets need a paid Finnhub plan, a free key gets a 403 here every time
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
    # backup price target source for when Finnhub's is blocked behind its paid plan, used by both /stock and /rating
    ticker = ticker.upper()
    today = datetime.now(timezone.utc).date().isoformat()

    cached = await db.get_cached_price_target(ticker)
    if cached and cached[1] == today:
        return cached[0]  # Alpha Vantage's free tier is only 25 requests/day total, so we reuse this for the rest of the day

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


def summarize_recommendation(trends: dict | None) -> tuple[str, str] | None:
    # boils the full strong buy/buy/hold/sell/strong sell breakdown down into one quick label like "Buy" for the main /stock embed
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

    score = (strong_buy * 2 + buy * 1 + hold * 0 + sell * -1 + strong_sell * -2) / total  # strong buy/sell count double, same as TipRanks-style scoring

    if score >= 1.2:
        return "🟢", "Strong Buy"
    if score >= 0.4:
        return "🟢", "Buy"
    if score >= -0.4:
        return "🟡", "Hold"
    if score >= -1.2:
        return "🔴", "Sell"
    return "🔴", "Strong Sell"
