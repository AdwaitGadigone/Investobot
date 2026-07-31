import os
from pathlib import Path

from dotenv import load_dotenv

# ROOT_DIR is one level up from src/, so paths work no matter where the script is run from.
ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

# Syncs slash commands to just this one server instantly, instead of waiting up to an hour for a global sync.
DEV_GUILD_ID = os.getenv("DEV_GUILD_ID") or None

FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY")
ALPHA_VANTAGE_API_KEY = os.getenv("ALPHA_VANTAGE_API_KEY") or None
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY") or None

# The single channel every server-wide auto update gets posted to.
UPDATES_CHANNEL_ID = os.getenv("UPDATES_CHANNEL_ID") or None

DB_PATH = ROOT_DIR / "data" / "investo.db"

# How often, in minutes, the scheduler checks tracked tickers, news, and price alerts.
CHECK_INTERVAL_MINUTES = 15

# A stock has to move at least this percent since yesterday's close to count as a "big move".
BIG_MOVE_THRESHOLD_PCT = 5.0

# Sonnet over the cheaper Haiku model, since writing quality actually matters for this summary.
ANALYST_TAKE_MODEL = "claude-sonnet-5"

# Fail loudly right away instead of a confusing crash later when the bot tries to log in.
if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_TOKEN is not set. Copy .env.example to .env and fill it in.")
