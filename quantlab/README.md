# Quant Lab — Upstox Fundamentals Ingestion

Production-grade, read-only ingestion of the Upstox **Fundamentals** APIs into
DuckDB. Uses the Upstox **Analytics Token** (1-year, read-only — no trading
scope), incremental synchronization, retries with exponential backoff, a
token-bucket rate limiter, structured logging and APScheduler cron scheduling.

## Setup

```bash
pip install duckdb apscheduler   # optional deps used at runtime
```

Provide the Analytics token (generate from Upstox Developer Apps → Analytics
tab). **Never commit it.** Either:

- set the env var `UPSTOX_FUNDAMENTAL_ANALYTICS_TOKEN`, or
- put the token in `quantlab_token.txt` at the repo root (gitignored).

The token's JWT `exp` is decoded (without signature verification) purely to
warn about upcoming expiry; the token value is never logged — only a SHA-256
fingerprint prefix appears in logs.

## Run

```bash
python -m quantlab sync --symbols RELIANCE,TCS      # specific symbols
python -m quantlab sync --symbols NSE_EQ\|INE002A01018   # instrument key or bare ISIN also work
python -m quantlab sync --limit 100                 # first 100 of the universe
python -m quantlab sync --full                      # ignore freshness/content-hash
python -m quantlab sync --endpoints key_ratios,share_holdings
python -m quantlab status                           # sync-state + table counts
python -m quantlab endpoints                        # show the endpoint registry
python -m quantlab serve-scheduler                  # cron loop (blocks)
```

Exit codes: `0` success, `1` config/auth/universe error, `2` completed with
per-dataset failures (recorded in `_sync_state` and retried next run).

## Environment variables (all optional)

| Variable | Default | Meaning |
|---|---|---|
| `UPSTOX_FUNDAMENTAL_ANALYTICS_TOKEN` | — | the Analytics token (required unless `quantlab_token.txt` exists) |
| `QUANTLAB_DB_PATH` | `<repo>/quantlab.duckdb` | DuckDB file |
| `QUANTLAB_BASE_URL` | `https://api.upstox.com` | API host |
| `QUANTLAB_RATE_LIMIT_RPS` | `2.0` | token-bucket refill rate |
| `QUANTLAB_RATE_LIMIT_BURST` | `5` | token-bucket capacity |
| `QUANTLAB_MAX_RETRIES` | `5` | retries per request (429/5xx/network) |
| `QUANTLAB_BACKOFF_BASE` / `QUANTLAB_BACKOFF_CAP` | `0.5` / `60` | exponential backoff seconds (+/-50% jitter); `Retry-After` honoured on 429 |
| `QUANTLAB_REFRESH_AFTER_HOURS` | `24` | freshness window for incremental skips |
| `QUANTLAB_INCLUDE_STANDALONE` | `false` | also ingest standalone statements |
| `QUANTLAB_UNIVERSE_SOURCE` | `index` | `index` = `docs/data/fundamental/index.json`, else a path to a symbols file (`SYMBOL` or `SYMBOL,ISIN` per line, or a JSON list) |
| `QUANTLAB_INSTRUMENTS_URL` | Upstox `NSE.json.gz` | instrument master for symbol→ISIN resolution |
| `QUANTLAB_INSTRUMENTS_CACHE` | `.cache/quantlab/NSE.json.gz` | gitignored local cache |
| `QUANTLAB_SCHEDULE_CRON` | `30 18 * * 1-5` | 5-field cron for `serve-scheduler` |
| `QUANTLAB_LOG_LEVEL` | `INFO` | stdlib logging level (`quantlab.*` loggers) |

## Endpoints ingested

All verified against the official docs (July 2026):
`GET https://api.upstox.com/v2/fundamentals/{isin}/...` with
`Authorization: Bearer <token>`.

| Registry name | Path suffix | Variants | Table |
|---|---|---|---|
| `company_profile` | `/profile` | — | `company_profile` |
| `balance_sheet` | `/balance-sheet` | `type=consolidated\|standalone`, `fs=true` | `balance_sheet` |
| `cash_flow` | `/cash-flow` | `type=...`, `fs=true` | `cash_flow` |
| `income_statement` | `/income-statement` | `type=...` × `time_period=yearly\|quarterly`, `fs=true` | `income_statement` |
| `share_holdings` | `/share-holdings` | — | `share_holdings` |
| `key_ratios` | `/key-ratios` | — | `key_ratios` |
| `corporate_actions` | `/corporate-actions` | — | `corporate_actions` |
| `competitors` | `/competitors` | — | `competitors` |

Plus `_sync_state` (isin, dataset, variant → content hash, last sync, status).

## Incremental sync

1. A (isin, endpoint, variant) synced OK within `QUANTLAB_REFRESH_AFTER_HOURS`
   is skipped without an API call (unless `--full`).
2. Fetched payloads are hashed (SHA-256 of the canonical `data` section);
   unchanged content skips the DB rewrite.
3. Writes are idempotent: each dataset carries a natural-key scope that is
   `DELETE`d and re-`INSERT`ed inside one transaction — replays never
   duplicate rows.
4. Failures are recorded with `status='error'` and retried next run.

## Adding a new endpoint

1. Add an `EndpointSpec` to `ENDPOINTS` in `quantlab/client.py` (path
   template + query-param variants) — purely declarative.
2. Write a normalizer in `quantlab/normalize.py` returning a `Dataset`
   (table, columns, rows, natural-key scope) and register it in
   `NORMALIZERS`.
3. Add the table DDL to `SCHEMA_DDL` in `quantlab/store.py`.
4. Done — the sync engine, CLI and scheduler pick it up automatically.

## Tests

```bash
pytest tests/test_quantlab_client.py tests/test_quantlab_normalize.py tests/test_quantlab_store.py
```

Client tests use a stubbed session (no network). Store tests are skipped
automatically when `duckdb` is not installed.
