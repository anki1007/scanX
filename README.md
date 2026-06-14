# NSE/BSE Earnings Intelligence Agent

A modular, risk-first system that detects earnings beats on Indian equities,
scores them across fundamental, technical, institutional and options dimensions,
and turns the strongest **PEAD-confirmed** setups into actionable trade plans.

> **Analysis-only by default.** This software generates signals and trade plans.
> It does **not** place live orders unless you explicitly enable execution.
> Nothing here is investment advice.

## Why this exists

The edge being targeted is **Post-Earnings-Announcement Drift (PEAD)**: stocks
that post a strong, surprising earnings beat with bullish guidance and
institutional buying tend to *drift* in that direction for days/weeks before the
market fully reprices them. This system tries to find those setups early and
rank them objectively.

## Quick start

```bash
pip install -r requirements.txt

# Run the full pipeline end-to-end on synthetic data (no credentials needed)
python scripts/run_demo.py

# Validate the strategy with the event-driven backtester
python scripts/run_backtest.py
```

Both scripts run out of the box on a built-in `SampleProvider`. To use live
market data, authenticate Kite and switch one line (see below).

## Using live Kite data

```python
from earnings_intel.data.kite_provider import KiteProvider
from earnings_intel import Pipeline

provider = KiteProvider(api_key="...", access_token="...")  # from your Kite app
pipe = Pipeline(provider=provider)
results = pipe.run(universe=["INFY", "TCS", "RELIANCE"])
```

Order placement is guarded by `KiteProvider(allow_orders=False)` (the default).

## How it works (12 agents)

Scanner → extraction → SUE → PEAD → transcript → institutional flow →
options → microstructure → technical → corporate events → valuation →
composite score → risk/sizing → execution → alerts.

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the agent→module map and
[`docs/ROADMAP.md`](docs/ROADMAP.md) for what's built vs. what's next.

## Layout

```
earnings_intel/
  config.py            # all weights + thresholds (from the spec)
  models.py            # typed data structures
  data/                # provider abstraction: Kite (live) + Sample (synthetic)
  engines/             # SUE, PEAD, technical, institutional, transcript, valuation, scoring
  risk.py              # position sizing, Kelly cap, sector limits, stops/targets
  signals.py           # STRONG BUY / STRONG SELL rules
  alerts.py            # formatted alert output
  backtest.py          # event-driven backtest + metrics
  pipeline.py          # end-to-end orchestration
scripts/               # run_demo.py, run_backtest.py
tests/                 # unit tests for the scoring math
```

## Status

Phase 0 (core engine) is complete and tested. Live data, document extraction,
storage and execution are scoped in the roadmap.

## Round-the-clock live scanner (NSE + BSE + Kite)

A continuous screening service that polls NSE & BSE corporate filings, detects
new results and corporate actions across the whole listed universe, adds a live
price/volume reaction read from Kite, and pushes alerts to a log, a CSV, and
(optionally) Telegram. **Screening + alerts only — it never places orders.**

### One-time setup

```powershell
copy credentials.example.ps1 credentials.ps1   # then edit in your keys
python scripts\kite_login.py                    # generates today's Kite token (optional)
```

### Run it now

```powershell
.\technofunda.ps1                 # live, 09:00-23:55, polling every 60s
.\technofunda.ps1 -Mode demo -Once   # offline smoke test on synthetic data
```

### Schedule it daily (09:00 -> 23:55)

```powershell
.\technofunda.ps1 -Register       # creates the "TechnofundaScanner" task
Start-ScheduledTask TechnofundaScanner   # test immediately
.\technofunda.ps1 -Unregister     # remove it
```

The task runs in your interactive session, so the terminal is visible. The
Python loop self-exits at the end of the window; the PowerShell wrapper restarts
the loop if it ever crashes mid-session.

Alerts are written to `alerts\` — a daily `scanner_YYYYMMDD.log` and an
Excel-friendly `alerts.csv`. Duplicate filings are suppressed via `seen.json`.

### Honest limitations

- **Kite tokens expire daily.** Re-run `scripts\kite_login.py` each morning (or
  before 09:00) for the live price read. Without it, the scanner still detects
  and alerts on filings — it just omits the price/volume reaction.
- **NSE actively rate-limits/blocks bots.** The feed primes browser cookies and
  backs off on failure, but heavy polling can still get throttled; 60s is a sane
  cadence. A filing's *fundamental* SUE/PEAD score needs the PDF/XBRL extraction
  agent (Phase 1) — until then, live alerts carry the event + price reaction,
  while the full scorer is proven end-to-end in demo mode and the backtester.

## scanX — Screener.in PEAD dashboard (GitHub Pages)

Replicates the "Result Screening" flow: pull Screener.in latest results, keep
companies with strong Sales **and** Earnings growth plus a QoQ "Sudden Shift",
score each (fundamental PEAD 0-100, HIGH/MEDIUM/LOW), and publish a live,
filterable dashboard.

```
scripts/refresh_scanx.py   # one refresh -> docs/data/{pead.json,pead.csv,meta.json}
docs/index.html            # the dashboard (auto-refreshes every 60s)
scanx_publish.ps1          # 60s worker: refresh + git push (true live updates)
.github/workflows/refresh.yml  # ~5-min Actions backup when your PC is off
setup_github.ps1           # one-time: init repo + push to anki1007/scanx
```

### Screener login (Google)

Google sign-in can't be scripted, so log in once in your browser, then reuse the
session cookie:

1. Sign in to https://www.screener.in with Google.
2. DevTools → Application → Cookies → `https://www.screener.in` → copy `sessionid`.
3. Put it in `credentials.ps1`:  `$env:SCREENER_SESSIONID = "..."`

Without it, the dashboard still runs on bundled sample data.

### Go live

```powershell
python scripts\refresh_scanx.py            # generate data (sample or live)
powershell -ExecutionPolicy Bypass -File .\setup_github.ps1   # push to GitHub (once)
.\scanx_publish.ps1                          # start the 60s publish loop
```

Then in the repo: **Settings → Pages → Deploy from branch → `main` / `docs`**, and
add the `SCREENER_SESSIONID` Action secret. Dashboard: `https://anki1007.github.io/scanx/`.

### Why 60s lives on your PC, not GitHub

GitHub Actions' minimum schedule is ~5 minutes (best-effort). True 60-second
refresh needs a always-on worker — `scanx_publish.ps1` on your PC pushes each
cycle; the Action is the fallback when the PC is off.

> The PEAD score here is our transparent growth-based formula (weights in
> `screener_screen.py`); it won't match financiallyfree's exact numbers (their
> weighting isn't public), but the screen, ranking and dashboard behave the same.

## Realtime intraday engine (09:15–15:30 IST)

Two engines, as requested:

**1. On your PC (kiteconnect SDK) — the robust, unattended path.**
Polls live Kite quotes for the tradable NSE+BSE universe each minute during
market hours, computes intraday metrics (% change, vs VWAP, day-range position,
volume), merges the scanX PEAD fundamental scores, ranks the movers, and alerts
the ones that matter (PEAD-strong names that are moving). Screening + alerts only.

```powershell
.\realtime.ps1                 # run now (live if Kite token, else synthetic test feed)
.\realtime.ps1 -Once           # one cycle (smoke test)
.\realtime.ps1 -Register       # schedule daily 09:15–15:30 (Windows Task Scheduler)
```
Needs Kite creds in `credentials.ps1` + `python scripts\kite_login.py` (daily token)
for live data; without them it runs a synthetic feed so you can see it work.
Output: alerts in `alerts\`, plus `docs\data\intraday.json` for a live view.

**2. Inside Cowork (Kite MCP) — periodic live briefings.**
A scheduled task `intraday-pead-live-briefing` runs every 30 min on weekdays
(09:00–15:30) and uses the Kite MCP to brief you on the PEAD watchlist's live
moves. Because the MCP needs an interactive login, **click "Run now" once** in the
Scheduled panel to pre-approve it; if Kite isn't logged in at run time it will
say so and give the watchlist without live prices.

> Why two: the Kite MCP only works inside this app, so it can't power an
> unattended OS scheduler — that's what the SDK engine (#1) is for. The MCP task
> (#2) is for in-app periodic briefings.

### Live feed via Dhan (alternative to Kite)

The realtime engine auto-selects its price feed: **Dhan → Kite → synthetic**. To use
Dhan (DhanHQ v2), put your client id + 2FA in `credentials.ps1` and mint a daily token:

```powershell
# credentials.ps1
$env:DHAN_CLIENT_ID   = "..."
$env:DHAN_PIN         = "..."
$env:DHAN_TOTP_SECRET = "..."   # your 2FA seed — stays local, never committed

python scripts\dhan_login.py    # writes dhan_token.json; realtime engine uses it
.\realtime.ps1
```

`DhanProvider` pulls live LTP / OHLC / VWAP / volume for the NSE+BSE equity
universe via `marketfeed/quote` + Dhan's scrip master. It needs only the client
id + access token — never your PIN/secret/TOTP at runtime.

> Scope: Dhan and Kite provide **live prices** (the Intraday tab + price reads).
> They do **not** replace Screener (fundamental results → PEAD board) or the
> NSE/BSE announcement feeds (filings → technofunda alerts). Each source has its lane.

> SECURITY: never place a file containing your Dhan PIN / API secret / TOTP seed
> inside this folder — it's git-tracked and would publish to GitHub. `dhan_login.py`
> reads those from environment variables only; `dhan_token.json` is git-ignored.
