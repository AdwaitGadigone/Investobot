import asyncio
import logging

from google import genai
from google.genai import types

from config import ANALYST_TAKE_MODEL, GEMINI_API_KEY
from services import gemini_limiter

log = logging.getLogger("investo.sentiment")

# Stays None without a key, so /sentiment just skips this field entirely.
_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

SYSTEM_PROMPT = (
    "You read recent news headlines for a stock and summarize the current sentiment in 2-3 "
    "sentences. Say whether the tone reads bullish, bearish, or mixed, and briefly say why "
    "based only on the headlines given, never invent a reason that isn't actually there. If "
    "the headlines don't give a clear read either way, say sentiment looks mixed or unclear "
    "instead of forcing a lean. Casual, plain tone, no bullet points, no headers, just a short "
    "paragraph. Never use em dashes, use commas or periods instead."
)


def _build_prompt(ticker: str, news: list[dict]) -> str:
    lines = [f"Ticker: {ticker}", "Recent headlines:"]
    for article in news[:8]:
        lines.append(f"- {article.get('headline', 'Untitled')} ({article.get('source', 'unknown source')})")
    return "\n".join(lines)


def _generate_sync(prompt: str) -> str | None:
    if not _client:
        return None

    try:
        response = _client.models.generate_content(
            model=ANALYST_TAKE_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT, max_output_tokens=1024),
        )
    except Exception:
        log.exception("Gemini API call failed while generating sentiment read")
        return None

    text = getattr(response, "text", None)
    return text.strip() if text else None


async def generate_sentiment(ticker: str, news: list[dict]) -> str | None:
    if not _client or not news:
        return None
    if not await gemini_limiter.try_acquire():
        return None

    prompt = _build_prompt(ticker, news)
    return await asyncio.to_thread(_generate_sync, prompt)
