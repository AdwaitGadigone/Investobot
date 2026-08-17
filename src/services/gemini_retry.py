import logging
import time

from google.genai import errors as genai_errors

log = logging.getLogger("investo.gemini_retry")

_MAX_RETRIES = 2
_RETRY_BACKOFF_SECONDS = 2


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
