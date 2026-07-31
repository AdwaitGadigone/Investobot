# Investo

A Discord bot I built for me and my friends to track stocks together. Prices
and general stock info come from Yahoo Finance (through the `yfinance`
library), and analyst ratings, price targets, and news come from
[Finnhub](https://finnhub.io) (free tier). I originally wanted to use
TipRanks for the analyst stuff, but they don't have a public API you can
actually sign up for, so Finnhub ended up being the best free alternative.

## Features (v1)

| Command | What it does |
|---|---|
| `/stock <ticker> [range]` | Price, day/52wk range, volume, market cap, a price+volume chart, and analyst consensus |
| `/rating <ticker>` | Analyst buy/hold/sell breakdown, price target, and an AI-written "Analyst Take" explaining why |
| `/news <ticker>` | Last 5 recent headlines with links |
| `/watchlist add\|remove\|list` | Your personal watchlist (per user) |
| `/track add\|remove\|list` | The server's shared tracked list, feeds the auto updates below |
| `/alert set\|list\|remove` | DMs you when a ticker crosses a price you choose |
| `/notify` | Toggles the "Stock Alerts" role, which gets pinged on the auto updates below |
| `/help` | Shows a full rundown of every command in one embed |
| **Auto updates** | Every 15 min, checks the server's tracked list and posts big moves (5% or more) and fresh news to a channel you pick, pinging the Stock Alerts role if anyone's opted in |

### Ideas for later (not built yet)
- Personal portfolio tracking (shares owned, cost basis, profit/loss). Feels like the natural next step once watchlists are solid.
- A fun leaderboard comparing everyone's tracked-portfolio performance.
- One combined daily digest at market open/close instead of scattered updates throughout the day.
- Being able to swap in other data sources (like Polygon.io) without touching any of the command code, since everything already routes through `services/market_data.py`.

## 1. Create the Discord application and bot

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications) and click **New Application**. Name it "Investo" (or whatever you want).
2. In the left sidebar, open **Bot**. Click **Reset Token** and copy the token. This goes in `.env` as `DISCORD_TOKEN`. Keep it private, anyone with this token can control the bot as if they were you.
3. Still on the **Bot** page, you don't need to turn on any of the privileged intents (Presence, Server Members, Message Content) since we're only using slash commands.
4. Open **OAuth2 -> URL Generator**. Under **Scopes**, check `bot` and `applications.commands`. Under **Bot Permissions**, check the specific ones the bot actually needs: `Send Messages`, `Embed Links`, `Read Message History`, `Use Slash Commands`. (If you'd rather just give it Administrator for simplicity in a small private server, that works too, just know it's more access than the bot needs.)
5. Copy the generated URL at the bottom, open it in your browser, and invite the bot to your server.

## 2. Get the free/paid API keys

1. **Finnhub** (free): sign up at [finnhub.io/register](https://finnhub.io/register), copy the key from the dashboard into `.env` as `FINNHUB_API_KEY`. Powers analyst ratings and news.
2. **Alpha Vantage** (free): sign up at [alphavantage.co/support/#api-key](https://www.alphavantage.co/support/#api-key), copy the key into `.env` as `ALPHA_VANTAGE_API_KEY`. Only used as a backup for the analyst price target since Finnhub gates that behind a paid plan; free tier is 25 calls/day, and we cache each ticker for 24 hours to stay under that.
3. **Anthropic** (paid, usage-based): create a key at [console.anthropic.com](https://console.anthropic.com) with billing enabled, copy it into `.env` as `ANTHROPIC_API_KEY`. Powers the "Analyst Take" summary in `/rating`. Uses Claude Sonnet, costs a few cents per summary depending on usage. Skip this one if you don't want the AI summary, everything else works fine without it.

## 3. Find your server and channel IDs

1. In Discord, go to **User Settings -> Advanced** and turn on **Developer Mode**.
2. Right-click your server icon and choose **Copy Server ID**. That's `DEV_GUILD_ID` (this makes slash commands show up instantly instead of waiting up to an hour for them to sync globally).
3. Right-click the channel you want the auto updates posted in and choose **Copy Channel ID**. That's `UPDATES_CHANNEL_ID`.

## 4. Local setup

```powershell
cd "C:\Users\adwai\OneDrive\Desktop\Investo"
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

Open `.env` and fill in `DISCORD_TOKEN`, `DEV_GUILD_ID`, `FINNHUB_API_KEY`, `ALPHA_VANTAGE_API_KEY`, `ANTHROPIC_API_KEY`, and `UPDATES_CHANNEL_ID`.

## 5. Run it

```powershell
python src\bot.py
```

You should see log lines confirming the cogs loaded, the commands synced, and
"Logged in as Investo#XXXX". Then in Discord, try `/stock AAPL`.

## 6. Try it out

- `/track add AAPL`, then wait for a 5%+ move, or temporarily lower `BIG_MOVE_THRESHOLD_PCT` in `src/config.py` if you want to test it faster.
- `/alert set ticker:AAPL direction:above price:1` will trigger almost right away since $1 is way below AAPL's real price, good for testing the DM flow works.
- `/rating AAPL` and `/news AAPL` both need `FINNHUB_API_KEY` set. Without it they'll just say no data is available.
- `/notify` toggles the Stock Alerts role for you, the bot pings that role on the next auto update.

## Later: GitHub and Heroku

- This is already a git repo (`git init` was run when the project was set up). To push it to GitHub, create a repo there, run `git remote add origin <url>`, then push as normal. `.env` and the local database file are already in `.gitignore` so none of the secrets end up on GitHub.
- A `Procfile` (`worker: python src/bot.py`) is already included for deploying to Heroku. On Heroku: create the app, set `DISCORD_TOKEN`, `FINNHUB_API_KEY`, `ALPHA_VANTAGE_API_KEY`, `ANTHROPIC_API_KEY`, and `UPDATES_CHANNEL_ID` as Config Vars (you can skip `DEV_GUILD_ID` once you're happy with how the commands work, global command syncing is fine long term), push the code, then run `heroku ps:scale worker=1`. Note that Heroku doesn't have a free tier anymore, Eco dynos run about $5/month.
- SQLite works fine on Heroku to start, but its filesystem gets wiped whenever the dyno restarts or you redeploy. If that starts causing problems, switching `services/db.py` over to Postgres (Heroku has an easy add-on for this) is the next step.
