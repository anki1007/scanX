# Architecture

This document maps the 12-agent design from the project spec onto the actual
code modules in this repository, and is honest about what runs today versus
what needs external infrastructure.

## Pipeline overview

```
NSE/BSE scanner  ──▶  earnings extraction  ──▶  SUE engine  ──▶  PEAD engine
      (1)                    (2)                    (3)             (4)
                                                                     │
   alerts ◀── execution ◀── risk/sizing ◀── composite score ◀───────┤
    (13)         (12)            (11)             (10)                │
                                                                     ▼
        valuation (9) ◀ corp events (8) ◀ technical (7) ◀ options (6) ◀ flow/transcript (5)
```

In code, the orchestration lives in `earnings_intel/pipeline.py`. Each agent is
a small, independently testable unit so the system degrades gracefully: if one
data source is missing, that component returns a neutral score rather than
breaking the run.

## Agent → module map

| # | Spec agent | Module | Status |
|---|------------|--------|--------|
| 1 | NSE/BSE Scanner | `data/base.py` (`iter_new_earnings`) + provider impls | Interface done; live filings feed is a TODO (needs NSE/BSE source) |
| 2 | Earnings Extraction | `data/kite_provider.py` notes + `models.EarningsReport` | Schema done; PDF/XBRL parsing stubbed (pdfplumber/camelot listed) |
| 3 | SUE Engine | `engines/sue.py` | **Working** |
| 4 | PEAD Engine | `engines/pead.py` | **Working** |
| 5 | Transcript NLP | `engines/transcript.py` | **Working** (lexicon scorer; swappable for a transformer) |
| 6 | Institutional Flow | `engines/institutional.py` | **Working** (logic; needs FII/DII/bulk-deal feed for live) |
| 7 | Options Flow | folded into `engines/scoring.py` input | Neutral default; needs option-chain feed |
| 8 | Microstructure | `engines/technical.py` (volume/delivery/RVOL) | **Working** on OHLCV |
| 9 | Technical Confirmation | `engines/technical.py` | **Working** |
| 10 | Corporate Event | `models.CorporateEvent` + scoring input | Schema done; needs announcements feed |
| 11 | Valuation | `engines/valuation.py` | **Working** |
| 12 | Composite Scoring | `engines/scoring.py` | **Working** |
| — | Signal Rules | `signals.py` | **Working** |
| — | Portfolio Risk | `risk.py` | **Working** |
| — | Kite Execution | `data/kite_provider.py` | Adapter built; order placement guarded/dry-run by default |
| — | Alerts | `alerts.py` | **Working** (formatted text; channels are TODO) |
| — | Backtest | `backtest.py` | **Working** |

## Data-provider abstraction

The single most important design choice: **all market data flows through one
interface**, `data/base.py::DataProvider`. Two implementations ship:

- `KiteProvider` — uses the official `kiteconnect` Python SDK. This is the
  real, deployable path: historical candles, LTP, holdings, positions, margins,
  and (guarded) order placement.
- `SampleProvider` — generates realistic synthetic earnings events and OHLCV
  so the entire pipeline, demo and backtest run with **zero credentials**. This
  is what makes the project testable today.

Swapping providers is a one-line change. Nothing downstream knows or cares which
one is in use.

## Scoring philosophy

Every engine returns a score on a **0–100** scale with a consistent meaning:
50 is neutral, >70 is a strong positive read, <30 is a strong negative read.
This makes the composite (`engines/scoring.py`) a simple, transparent weighted
average, and makes missing inputs safe (they default to 50, i.e. no opinion).

## What is intentionally NOT here yet

- A live NSE/BSE filings/transcript feed (the 60-second scanner needs a source).
- PDF/XBRL extraction wiring (libraries are chosen; parsing is stubbed).
- A live options chain and FII/DII feed.
- Real order placement is **disabled by default** — this system is analysis-only
  until you explicitly opt in. See `ROADMAP.md` for the path to each.
