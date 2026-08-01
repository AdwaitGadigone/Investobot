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
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or None

# The single channel every server-wide auto update gets posted to.
UPDATES_CHANNEL_ID = os.getenv("UPDATES_CHANNEL_ID") or None

# Supabase's Postgres connection string (transaction pooler URI), replaces the old local
# SQLite file, which got wiped every time bot-hosting.net redeployed the container.
DATABASE_URL = os.getenv("DATABASE_URL")

# How often, in minutes, the scheduler checks tracked tickers, news, and price alerts.
CHECK_INTERVAL_MINUTES = 15

# A stock has to move at least this percent since yesterday's close to count as a "big move".
BIG_MOVE_THRESHOLD_PCT = 5.0

# Gemini's free tier has no billing requirement at all, which is what /rating's AI take runs on.
ANALYST_TAKE_MODEL = "gemini-flash-latest"

# Same free model, used instead for the @mention chat feature, kept as its own setting
# in case the two features ever need to be tuned differently down the line.
CHAT_MODEL = "gemini-flash-latest"

# Fail loudly right away instead of a confusing crash later when the bot tries to log in.
if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_TOKEN is not set. Copy .env.example to .env and fill it in.")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set. Copy .env.example to .env and fill it in.")
