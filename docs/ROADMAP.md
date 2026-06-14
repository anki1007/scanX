# Roadmap

A phased, honest plan from the working core (today) to the full autonomous
vision in the spec. Each phase is shippable on its own.

## Phase 0 — Core engine (DONE in this build)

The analytical heart of the system, runnable with zero credentials on synthetic
data and ready to point at live Kite data.

- [x] Data-provider abstraction (`KiteProvider` + `SampleProvider`)
- [x] SUE engine
- [x] PEAD scoring engine
- [x] Technical + microstructure confirmation
- [x] Institutional-flow, transcript, valuation engines
- [x] Composite scoring + STRONG BUY/SELL signal rules
- [x] Risk/position sizing (ATR stops, Kelly-capped, sector limits)
- [x] Alert formatting
- [x] Event-driven backtest with full metrics
- [x] End-to-end pipeline + demo + backtest scripts
- [x] Unit tests for the scoring math

## Phase 1 — Live data + validation (in progress)

Turn the core from "runs on samples" into "runs on the real market".

- [x] **Live filings feed** — `data/nse_bse.py` polls NSE + BSE announcements,
      results and board meetings (Agent 1, live).
- [x] **Round-the-clock scanner** — `scanner.py` + `technofunda.ps1` run the
      09:00–23:55 loop with dedup, crash-restart and a Windows Scheduled Task.
- [x] **Alert delivery** — daily log + CSV + optional Telegram (`alert_sink.py`).
- [ ] **Authenticate Kite** and switch the pipeline/scanner to live `KiteProvider`
      price data (`scripts/kite_login.py` generates the daily token).
- [ ] **Backtest on real history** — pull 5–10 yrs of candles for a liquid
      universe (e.g. Nifty 200) and validate the PEAD edge before risking capital.
- [ ] **PDF/XBRL extraction** — implement `extract_financials()` with pdfplumber +
      camelot so live filings get a full SUE/PEAD score, not just a price read
      (Agent 2).

## Phase 2 — Richer signals + storage

5. **Transcript ingestion** — fetch concall transcripts; upgrade the lexicon
   scorer to a finance-tuned transformer (Agent 5).
6. **Institutional & options feeds** — FII/DII, bulk/block deals, and the NSE
   option chain for the flow and options agents (Agents 6, 7).
7. **Persistence** — PostgreSQL for filings/signals/trades, DuckDB for the
   research/factor data lake. Nightly 21:00 IST batch writes snapshots.

## Phase 3 — Execution + monitoring

8. **Paper trading** — route signals to Kite in dry-run, log fills, track P&L.
9. **Alert channels** — Telegram/Discord/email delivery of the formatted alerts.
10. **Dashboard** — a live artifact / web view of rankings, positions, and P&L.
11. **Guarded live execution** — only after paper-trading metrics match backtest.

## Phase 4 — Autonomy (the spec's end state)

12. Self-learning ranking (feedback loop from realised trade outcomes).
13. Reinforcement-learning position layer.
14. Multi-broker + cross-asset support.

## Guiding principles

- **Validate before you risk.** No live capital until the backtest *and* paper
  trading agree the edge is real.
- **Analysis-only by default.** Order placement stays disabled until explicitly
  enabled, with hard risk limits that can reject a trade.
- **Degrade gracefully.** A missing feed lowers confidence; it never crashes a run.
