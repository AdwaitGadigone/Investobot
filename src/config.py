import os
from datetime import time, timezone
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

# Supabase's Postgres connection string (transaction pooler URI), the bot's storage since the old local SQLite file got wiped on every bot-hosting.net redeploy.
DATABASE_URL = os.getenv("DATABASE_URL")

# Shown in the bot's Discord presence and used anywhere else the bot points people to the site.
WEBSITE_URL = "https://investoweb.vercel.app"

# Gates cogs/owner.py's hidden !tell command. ADD ANYONES ID HERE TO ENABLE THEM!!!!!!!!!
OWNER_DISCORD_IDS = {
    534762779626700831,
    429115029250375680,  # Hasauce's ID
}

# How often, in minutes, the scheduler checks tracked tickers, news, and price alerts.
CHECK_INTERVAL_MINUTES = 15

# A stock has to move at least this percent since yesterday's close to count as a "big move".
BIG_MOVE_THRESHOLD_PCT = 5.0

# Higher than BIG_MOVE_THRESHOLD_PCT since this scans well-known tickers nobody has to track, so it should only fire on genuinely wild, newsworthy days.
BREAKING_MOVE_THRESHOLD_PCT = 15.0

# Deliberately not every ticker on the market, that would be slow and trip API rate limits, just names people would recognize if they spiked or crashed.
BREAKING_WATCH_TICKERS = [
    # "Magnificent 7" plus other mega-cap tech
    "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA", "NFLX", "AMD", "INTC",
    "ADBE", "CRM", "ORCL", "IBM", "CSCO", "QCOM", "AVGO", "PYPL", "UBER", "SHOP",
    # Popular retail/consumer names
    "DIS", "NKE", "SBUX", "MCD", "WMT", "KO", "PEP", "COST",
    # Names that are frequently in the news or popular with retail traders
    "GME", "AMC", "PLTR", "COIN", "SOFI", "RIVN", "LCID", "SNAP",
    # Finance and other large caps
    "JPM", "GS", "V", "MA", "BRK-B", "XOM", "CVX", "PFE", "JNJ", "UNH",
    # Other large, widely known names
    "BABA", "TSM", "SONY", "T", "VZ", "BA",
]

# 8:30 AM Eastern, written as a fixed UTC time since Discord's scheduler doesn't know about DST, so this drifts an hour for a few weeks twice a year, fine for a morning summary.
DIGEST_TIME_UTC = time(hour=12, minute=30, tzinfo=timezone.utc)

# Gemini's free tier has no billing requirement at all, which is what /rating's AI take runs on.
ANALYST_TAKE_MODEL = "gemini-flash-latest"

# Same free model, kept as its own setting in case chat and /rating ever need different tuning.
CHAT_MODEL = "gemini-flash-latest"

# Shared cap across every Gemini call (chat + analyst take), kept under the free tier so nothing ever bills, verify your exact quota at aistudio.google.com/rate-limit and adjust if it differs.
GEMINI_RPM_LIMIT = 8
GEMINI_RPD_LIMIT = 200

# Fail loudly right away instead of a confusing crash later when the bot tries to log in.
if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_TOKEN is not set. Copy .env.example to .env and fill it in.")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set. Copy .env.example to .env and fill it in.")
