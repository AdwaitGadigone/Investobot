# 📈 Investo

A Discord bot built using [Claude Code](https://claude.com/claude-code) to track stocks
(Test Project). It lives in our server 24/7 and
handles everything from live quotes and interactive charts to analyst ratings, news,
watchlists, and price alerts, plus it posts automatically whenever something we're
tracking makes a big move. Everything here also lives on a companion website at
[investoweb.vercel.app](https://investoweb.vercel.app), same account, same data, either surface.

## Commands

| Command | What it does |
|---|---|
| `/stock <ticker> [range]` | Price, day/52wk range, volume, market cap, a price+volume chart, and the analyst consensus, all in one embed. A dropdown under the reply lets you flip between 1D / 1W / 1M / 3M / 6M / YTD / 1Y / 5Y without re-running the command |
| `/compare <ticker1> <ticker2>` | Price, market cap, and analyst consensus for two tickers side by side |
| `/movers` | Top gainers, losers, and most active stocks, with buttons to switch category and a dropdown for the time span (today through 5 years) |
| `/rating <ticker>` | The full analyst buy/hold/sell breakdown, the average price target, and an AI-written "Analyst Take" explaining *why* the rating looks the way it does |
| `/news <ticker>` | The 5 most recent headlines for a ticker, each with a link |
| `/sentiment <ticker>` | An AI read on current news sentiment, bullish/bearish/mixed, grounded in real recent headlines |
| `/company_overview <ticker>` | Business description, sector/industry, and valuation metrics (P/E, P/B, dividend yield, beta) |
| `/fgi` | The crypto market's Fear & Greed Index right now |
| `/watchlist add \| remove \| list` | Your own private watchlist, visible only to you. `add` takes multiple tickers at once, separated by commas or spaces |
| `/track add \| remove \| list` | The server's shared tracked list, this is what the automatic updates below actually scan. `add` takes multiple tickers at once too |
| `/portfolio buy \| sell \| edit \| remove \| view` | Track shares you actually own, with a live profit/loss view (average cost basis, blended across buys). `edit` corrects the share count or average cost directly, without blending, for fixing a typo'd entry |
| `/alert set \| list \| remove` | DMs you the moment a ticker crosses a price you choose |
| `/notify` | Toggles the Stock Alerts role, so you get pinged whenever the automatic updates post something |
| `/digest [content]` | Toggles a personal DM every morning summarizing your watchlist, portfolio, or both. A dropdown right on the DM lets you switch what it shows afterward too |
| `/digest_now` | Sends that same DM immediately instead of waiting for the morning schedule, works whether the daily one is on or off |
| `/serverdigest set \| off` | Admin-only (Manage Server). Posts a daily digest of the server's tracked list to a channel of your choice, with a switchable time window and a section flagging other big movers |
| `/feedback <message> [category]` | Send a bug report, suggestion, or general feedback straight to the bot owner |
| `/status` | Checks Discord latency, the database, Yahoo Finance, and today's Gemini usage, and shows what's actually responding right now |
| `/help` | One clean panel explaining everything above |

### Automatic updates

Every 15 minutes, Investo scans the server's tracked list for big price moves (5% or
more since yesterday's close) and posts them straight to a channel, pinging the Stock
Alerts role if anyone's opted in with `/notify`. It also separately watches a fixed list
of well-known, popular tickers (mega-caps, frequently-in-the-news names) for genuinely
wild moves, 15% or more, even if nobody's tracking them, so something like a huge
single-day spike still gets caught without needing anyone to `/track` it first.
(An earlier version also auto-posted every news article for a ticker, which flooded the
channel for anything heavily covered, like a stock during earnings week, so that part
was removed. `/news` still works fine for checking headlines on demand.)

### Ask it anything

@ mention the bot with a question, whether it's about a specific ticker, a general
investing question, or a follow-up (reply to its message to keep the thread going), and
it replies conversationally. If you mention a ticker (`$AAPL` or bare `AAPL`), it pulls
a live quote in first so the answer is grounded in a real, current price instead of
whatever the model remembers from training.

## Where the data comes from

| Source | Used for |
|---|---|
| **Yahoo Finance** (via `yfinance`) | Fundamentals, charts, and price/change for anything Finnhub doesn't cover (CDRs, crypto, non-US tickers). Free tier runs 15-20 minutes delayed |
| **Finnhub** | Analyst ratings, company news, and real-time price/change for US-listed stocks, no delay, this is what makes quotes actually live instead of 15-20 minutes stale |
| **Alpha Vantage** | Backup analyst price target, since Finnhub only gives that out on a paid plan |
| **Google Gemini** (free tier) | Writes the "Analyst Take" in `/rating`, the sentiment read in `/sentiment`, and powers @ mention replies |
| **alternative.me** | Free, no key needed, the crypto Fear & Greed Index behind `/fgi` |

TipRanks was the original plan for the analyst side of things, but they don't offer a
public API individual developers can sign up for, so this combination of free sources
ended up covering everything TipRanks would have.

## Hosting

Runs 24/7 on [bot-hosting.net](https://bot-hosting.net), deployed straight from this
GitHub repo. Pushing new commits to `main` and redeploying is all it takes to ship a
change.

## Stack

Python, [discord.py](https://discordpy.readthedocs.io/) for the bot itself, Supabase
Postgres for storage (shared with the website), matplotlib for the charts, and Google's
Gemini API for the AI summaries.

## Ideas for later

- A fun leaderboard comparing everyone's tracked-portfolio performance.
- Swapping in other data sources without touching command code, since everything already routes through `services/market_data.py`.
