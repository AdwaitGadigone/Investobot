# 📈 Investo

A Discord bot built using [Claude Code](https://claude.com/claude-code) to track stocks
(Test Project). It lives in our server 24/7 and
handles everything from live quotes and interactive charts to analyst ratings, news,
watchlists, and price alerts, plus it posts automatically whenever something we're
tracking makes a big move.

## Commands

| Command | What it does |
|---|---|
| `/stock <ticker> [range]` | Price, day/52wk range, volume, market cap, a price+volume chart, and the analyst consensus, all in one embed. A dropdown under the reply lets you flip between 1D / 1W / 1M / 3M / 6M / YTD / 1Y / 5Y without re-running the command |
| `/rating <ticker>` | The full analyst buy/hold/sell breakdown, the average price target, and an AI-written "Analyst Take" explaining *why* the rating looks the way it does |
| `/news <ticker>` | The 5 most recent headlines for a ticker, each with a link |
| `/watchlist add \| remove \| list` | Your own private watchlist, visible only to you. `add` takes multiple tickers at once, separated by commas or spaces |
| `/track add \| remove \| list` | The server's shared tracked list, this is what the automatic updates below actually scan. `add` takes multiple tickers at once too |
| `/alert set \| list \| remove` | DMs you the moment a ticker crosses a price you choose |
| `/notify` | Toggles the Stock Alerts role, so you get pinged whenever the automatic updates post something |
| `/help` | One clean panel explaining everything above |

### Automatic updates

Every 15 minutes, Investo scans the server's tracked list for big price moves (5% or
more since yesterday's close) and posts them straight to a channel, pinging the Stock
Alerts role if anyone's opted in with `/notify`. (An earlier version also auto-posted
every news article for a ticker, which flooded the channel for anything heavily
covered, like a stock during earnings week, so that part was removed. `/news` still
works fine for checking headlines on demand.)

### Ask it anything

@ mention the bot with a question, whether it's about a specific ticker, a general
investing question, or a follow-up (reply to its message to keep the thread going), and
it replies conversationally. If you mention a ticker (`$AAPL` or bare `AAPL`), it pulls
a live quote in first so the answer is grounded in a real, current price instead of
whatever the model remembers from training.

## Where the data comes from

| Source | Used for |
|---|---|
| **Yahoo Finance** (via `yfinance`) | Prices, fundamentals, and the charts |
| **Finnhub** | Analyst ratings and company news |
| **Alpha Vantage** | Backup analyst price target, since Finnhub only gives that out on a paid plan |
| **Google Gemini** (free tier) | Writes the "Analyst Take" summary in `/rating` and powers @ mention replies |

TipRanks was the original plan for the analyst side of things, but they don't offer a
public API individual developers can sign up for, so this combination of free sources
ended up covering everything TipRanks would have.

## Hosting

Runs 24/7 on [bot-hosting.net](https://bot-hosting.net), deployed straight from this
GitHub repo. Pushing new commits to `main` and redeploying is all it takes to ship a
change.

## Stack

Python, [discord.py](https://discordpy.readthedocs.io/) for the bot itself, SQLite for
storage, matplotlib for the charts, and Google's Gemini API for the AI summary.

## Ideas for later

- Personal portfolio tracking (shares owned, cost basis, profit/loss).
- A fun leaderboard comparing everyone's tracked-portfolio performance.
- One combined daily digest at market open/close instead of scattered updates throughout the day.
- Swapping in other data sources without touching command code, since everything already routes through `services/market_data.py`.
