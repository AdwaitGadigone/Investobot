import asyncio
import logging
import re

from google import genai
from google.genai import types

from config import CHAT_MODEL, GEMINI_API_KEY
from services import market_data

log = logging.getLogger("investo.chat_ai")

# Stays None without a key, so the @mention feature just goes quiet instead of crashing.
_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

# Casual and conversational on purpose, this is friends chatting, not a compliance-heavy
# financial product, but it's still told to be upfront when it doesn't have live data.
SYSTEM_PROMPT = (
    "You are Investo, a friendly and highly knowledgeable stock market and investing "
    "assistant living inside a Discord server for a group of friends. People will @ "
    "mention you with questions about specific stocks, general investing concepts, or "
    "follow-up questions continuing an earlier reply. Answer like a smart, well-read "
    "friend who follows the markets closely, conversationally and concisely, a few "
    "sentences to a short paragraph unless the question genuinely needs more. If you "
    "were given live data for a stock, trust that over anything from your training. If "
    "someone asks about a current price or today's move and no live data was provided, "
    "say plainly that you don't have live data for it right now instead of guessing a "
    "number. Share opinions and analysis freely, but frame forward-looking takes as your "
    "own view rather than certain fact, this is casual conversation, not professional "
    "financial advice. Never use em dashes, use commas, periods, or parentheses instead."
)

# Common short acronyms that would otherwise false-match the bare-ticker pattern below.
_EXCLUDED_WORDS = {
    "I", "A", "OK", "CEO", "CFO", "CTO", "USA", "UK", "US", "ETF", "IPO", "AI",
    "ATH", "ATL", "YOLO", "FOMO", "DD", "TA", "IMO", "IMHO", "LOL", "OMG", "WTF",
    "FYI", "ASAP", "ATM", "EOD", "EOW", "PM", "AM", "ID", "TV", "PC", "OS",
}

# $AAPL style mentions are an explicit, reliable signal, checked first.
_DOLLAR_TICKER_RE = re.compile(r"\$([A-Za-z]{1,5})\b")

# Bare uppercase words are a much noisier signal, only used as a fallback.
_BARE_TICKER_RE = re.compile(r"\b[A-Z]{2,5}\b")


def _extract_tickers(text: str, limit: int = 3) -> list[str]:
    # $-prefixed tickers go first since they're an explicit, reliable signal, but a bare
    # ticker mentioned alongside one (like "$AAPL and TSLA") still needs to be picked up.
    dollar_matches = [m.upper() for m in _DOLLAR_TICKER_RE.findall(text)]
    bare_matches = [m for m in _BARE_TICKER_RE.findall(text) if m not in _EXCLUDED_WORDS]

    seen: list[str] = []
    for ticker in dollar_matches + bare_matches:
        if ticker not in seen:
            seen.append(ticker)
    return seen[:limit]


async def _gather_ticker_context(tickers: list[str]) -> str:
    # Grounds the model in a real, current price instead of letting it guess from training data.
    lines = []
    for ticker in tickers:
        try:
            quote = await market_data.get_quote(ticker)
        except Exception:
            continue
        lines.append(f"{quote['ticker']} ({quote['name']}): ${quote['price']:,.2f}, {quote['change_pct']:+.2f}% today")
    return "\n".join(lines)


def _generate_sync(prompt: str) -> str | None:
    if not _client:
        return None

    try:
        response = _client.models.generate_content(
            model=CHAT_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                max_output_tokens=800,
            ),
        )
    except Exception:
        log.exception("Gemini API call failed while generating chat reply")
        return None

    text = getattr(response, "text", None)
    return text.strip() if text else None


async def generate_chat_reply(question: str, prior_reply: str | None = None) -> str | None:
    if not _client:
        return None

    tickers = _extract_tickers(question)
    ticker_context = await _gather_ticker_context(tickers) if tickers else ""

    parts = []
    if prior_reply:
        parts.append(f"Your last reply in this thread was:\n{prior_reply}\n")
    if ticker_context:
        parts.append(f"Live data, trust this over your training data:\n{ticker_context}\n")
    parts.append(f"Question: {question}")

    prompt = "\n".join(parts)
    return await asyncio.to_thread(_generate_sync, prompt)
