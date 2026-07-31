# Investo

A Discord bot I built with [Claude Code](https://claude.com/claude-code) for me and my
friends to track stocks together. It lives in our server and handles everything from
live quotes and charts to analyst ratings, news, watchlists, and price alerts, plus it
posts automatically whenever something we're tracking makes a big move.

## What it does

| Command | What it does |
|---|---|
| `/stock <ticker> [range]` | Price, day/52wk range, volume, market cap, a price+volume chart, and the analyst consensus, all in one embed. `range` picks the chart window: 1 Day, 1 Week, 1 Month, 3 Months, 6 Months, Year to Date, 1 Year, or 5 Years |
| `/rating <ticker>` | The full analyst buy/hold/sell breakdown, the average price target, and an AI-written "Analyst Take" explaining why the rating looks the way it does |
| `/news <ticker>` | The 5 most recent headlines for a ticker, each with a link |
| `/watchlist add\|remove\|list` | Your own private watchlist, just for you |
| `/track add\|remove\|list` | The server's shared tracked list, this is what the automatic updates below actually scan |
| `/alert set\|list\|remove` | DMs you the moment a ticker crosses a price you choose |
| `/notify` | Toggles the Stock Alerts role, so you get pinged whenever the automatic updates post something |
| `/help` | One clean panel explaining everything above |
| **Automatic updates** | Every 15 minutes, scans the tracked list for big moves (5% or more since yesterday's close) and fresh news, and posts them straight to the channel |

## Where the data comes from

- **Yahoo Finance** (via `yfinance`) for prices, fundamentals, and the charts.
- **Finnhub** for analyst ratings and company news.
- **Alpha Vantage** as a backup source for the analyst price target, since Finnhub only gives that out on a paid plan.
- **Google Gemini** (free tier) writes the "Analyst Take" summary in `/rating`.

TipRanks was the original plan for the analyst side of things, but they don't offer a
public API individual developers can sign up for, so this combination of free sources
ended up covering everything TipRanks would have.

## Ideas for later

- Personal portfolio tracking (shares owned, cost basis, profit/loss).
- A fun leaderboard comparing everyone's tracked-portfolio performance.
- One combined daily digest at market open/close instead of scattered updates throughout the day.
- Swapping in other data sources without touching command code, since everything already routes through `services/market_data.py`.

## Stack

Python, [discord.py](https://discordpy.readthedocs.io/) for the bot itself, SQLite for
storage, matplotlib for the charts, and Google's Gemini API for the AI summary. Built to
run locally to start, with a `Procfile` included for deploying to Heroku later.
