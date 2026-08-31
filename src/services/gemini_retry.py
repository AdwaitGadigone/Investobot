import logging
import time

from google.genai import errors as genai_errors, types

log = logging.getLogger("investo.gemini_retry")

_MAX_RETRIES = 2
_RETRY_BACKOFF_SECONDS = 2

# The SDK's own retry defaults are up to 5 attempts with exponential backoff up to 60s between each,
# and it retries a 429 (quota exceeded) exactly as eagerly as a genuinely transient 503, even though no
# amount of waiting fixes an exhausted daily quota. Stacked with generate_with_retry's own retries below,
# a single failing call could balloon to several minutes before finally giving up, reproduced live as
# real reports of the bot taking ~5 minutes to say "something went wrong". Every Gemini client should
# pass this so generate_with_retry is the one and only place retries actually happen, on its own terms.
CLIENT_HTTP_OPTIONS = types.HttpOptions(timeout=15000, retry_options=types.HttpRetryOptions(attempts=1))


def generate_with_retry(client, **kwargs):
    # Gemini's own "high demand" 503s are common and genuinely transient (reproduced live: the exact same
    # request succeeds seconds later), worth a couple short retries before actually giving up. A permanent
    # failure (bad key, bad request) raises something other than ServerError, so this doesn't waste time
    # retrying a call that was never going to succeed.
    last_error = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            return client.models.generate_content(**kwargs)
        except genai_errors.ServerError as e:
            last_error = e
            if attempt < _MAX_RETRIES:
                time.sleep(_RETRY_BACKOFF_SECONDS)
    log.warning("Gemini still unavailable after %d retries: %s", _MAX_RETRIES, last_error)
    raise last_error
