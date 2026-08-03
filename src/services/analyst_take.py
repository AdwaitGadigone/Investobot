import asyncio
import logging

from google import genai
from google.genai import types

from config import ANALYST_TAKE_MODEL, GEMINI_API_KEY

log = logging.getLogger("investo.analyst_take")

# Stays None without a key, so /rating just skips this field entirely.
_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

# Strict about not inventing facts, a made-up earnings date would look like real analysis.
SYSTEM_PROMPT = (
    "You write short, plain-English stock analysis summaries for a group of friends who are "
    "casual investors, not professionals. You'll get the current price and today's change, the "
    "analyst buy/hold/sell consensus, a price target if one exists, and a few recent news "
    "headlines. In 3-4 sentences: explain why the consensus looks the way it does, mention "
    "anything in the news that explains the current price move, and end with one thing worth "
    "watching going forward. Only use facts you were actually given, never invent a specific "
    "date, event, or number that wasn't provided. If the news doesn't explain the rating, say "
    "the rating reflects analysts' longer-term view instead of guessing why. Casual tone, no "
    "bullet points, no headers, just a short paragraph."
)


def _build_prompt(ticker: str, name: str, quote: dict, trends: dict | None, target_mean: float | None, news: list[dict]) -> str:
    lines = [
        f"Ticker: {ticker} ({name})",
        f"Price: ${quote['price']:,.2f}, {quote['change_pct']:+.2f}% today",
    ]

    if trends:
        lines.append(
            f"Analyst consensus: {trends.get('strongBuy', 0)} strong buy, {trends.get('buy', 0)} buy, "
            f"{trends.get('hold', 0)} hold, {trends.get('sell', 0)} sell, {trends.get('strongSell', 0)} strong sell"
        )
    else:
        lines.append("Analyst consensus: not available")

    lines.append(f"Average price target: ${target_mean:,.2f}" if target_mean else "Average price target: not available")

    if news:
        lines.append("Recent headlines:")
        for article in news[:5]:
            lines.append(f"- {article.get('headline', 'Untitled')} ({article.get('source', 'unknown source')})")
    else:
        lines.append("Recent headlines: none available")

    # Everything Gemini is allowed to reference gets spelled out here as plain facts.
    return "\n".join(lines)


def _generate_sync(prompt: str) -> str | None:
    if not _client:
        return None

    try:
        response = _client.models.generate_content(
            model=ANALYST_TAKE_MODEL,
            contents=prompt,
            # system_instruction keeps the ground rules separate from the actual facts being summarized.
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                # This model's internal "thinking" step counts against this same budget and can't be turned off.
                max_output_tokens=2048,
            ),
        )
    except Exception:
        # /rating should still work fine, just skip this one field.
        log.exception("Gemini API call failed while generating analyst take")
        return None

    text = getattr(response, "text", None)
    return text.strip() if text else None


async def generate_analyst_take(
    ticker: str, name: str, quote: dict, trends: dict | None, target_mean: float | None, news: list[dict]
) -> str | None:
    if not _client:
        return None

    prompt = _build_prompt(ticker, name, quote, trends, target_mean, news)
    # The Gemini SDK blocks too, same reason every other API call here runs on a thread.
    return await asyncio.to_thread(_generate_sync, prompt)
