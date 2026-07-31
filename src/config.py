import os
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent  # one level up from src/, so paths work no matter where the script is run from
load_dotenv(ROOT_DIR / ".env")

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
DEV_GUILD_ID = os.getenv("DEV_GUILD_ID") or None  # syncs slash commands to just this server instantly instead of waiting up to an hour for a global sync
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY")
ALPHA_VANTAGE_API_KEY = os.getenv("ALPHA_VANTAGE_API_KEY") or None
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY") or None
UPDATES_CHANNEL_ID = os.getenv("UPDATES_CHANNEL_ID") or None  # the single channel every server-wide auto update gets posted to

DB_PATH = ROOT_DIR / "data" / "investo.db"

CHECK_INTERVAL_MINUTES = 15  # how often the scheduler wakes up to check tracked tickers, news, and price alerts
BIG_MOVE_THRESHOLD_PCT = 5.0  # a stock has to move at least this much since yesterday's close to count as a "big move"
ANALYST_TAKE_MODEL = "claude-sonnet-5"  # Sonnet over the cheaper Haiku since writing quality actually matters for the /rating summary

if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_TOKEN is not set. Copy .env.example to .env and fill it in.")  # fail loudly now instead of a confusing crash later
