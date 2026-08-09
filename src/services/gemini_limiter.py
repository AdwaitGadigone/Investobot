import asyncio
import logging
import time
from collections import deque

from config import GEMINI_RPD_LIMIT, GEMINI_RPM_LIMIT

log = logging.getLogger("investo.gemini_limiter")

# One shared bucket for every Gemini caller, they're the same model so it's the same quota at Google's end.
_minute_window: deque[float] = deque()
_day_window: deque[float] = deque()
_lock = asyncio.Lock()


async def try_acquire() -> bool:
    # Returns False instead of waiting, a minute-long queue would be a useless delay for a chat reply.
    async with _lock:
        now = time.time()
        while _minute_window and now - _minute_window[0] >= 60:
            _minute_window.popleft()
        while _day_window and now - _day_window[0] >= 86400:
            _day_window.popleft()

        if len(_minute_window) >= GEMINI_RPM_LIMIT or len(_day_window) >= GEMINI_RPD_LIMIT:
            log.warning(
                "Gemini rate limit reached (%d/%d this minute, %d/%d today), skipping this call",
                len(_minute_window), GEMINI_RPM_LIMIT, len(_day_window), GEMINI_RPD_LIMIT,
            )
            return False

        _minute_window.append(now)
        _day_window.append(now)
        return True


async def get_usage() -> tuple[int, int, int, int]:
    # Read-only, for /status, doesn't consume a slot the way try_acquire does.
    async with _lock:
        now = time.time()
        while _minute_window and now - _minute_window[0] >= 60:
            _minute_window.popleft()
        while _day_window and now - _day_window[0] >= 86400:
            _day_window.popleft()
        return len(_minute_window), GEMINI_RPM_LIMIT, len(_day_window), GEMINI_RPD_LIMIT
