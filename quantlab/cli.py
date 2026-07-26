"""Command-line interface for the Quant Lab fundamentals pipeline.

Usage (from the repo root)::

    python -m quantlab sync --symbols RELIANCE,TCS
    python -m quantlab sync --full --limit 50
    python -m quantlab status
    python -m quantlab serve-scheduler
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Sequence

from .config import QuantLabSettings
from .errors import QuantLabError

logger = logging.getLogger("quantlab.cli")


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
        stream=sys.stderr,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m quantlab",
        description="Upstox Fundamentals ingestion for Quant Lab (Analytics-token, read-only).",
    )
    parser.add_argument("--db", type=Path, default=None, help="override DuckDB path")
    parser.add_argument("--log-level", default=None, help="DEBUG/INFO/WARNING/ERROR")
    sub = parser.add_subparsers(dest="command", required=True)

    p_sync = sub.add_parser("sync", help="run an incremental (or full) sync")
    p_sync.add_argument(
        "--symbols",
        default=None,
        help="comma-separated symbols/ISINs/instrument keys (default: configured universe)",
    )
    p_sync.add_argument(
        "--symbols-file", type=Path, default=None,
        help="file with one SYMBOL or SYMBOL,ISIN per line",
    )
    p_sync.add_argument(
        "--endpoints", default=None,
        help="comma-separated endpoint names to sync (default: all)",
    )
    p_sync.add_argument("--limit", type=int, default=None, help="cap the number of symbols")
    p_sync.add_argument(
        "--full", action="store_true",
        help="ignore freshness window and content hashes; re-ingest everything",
    )

    sub.add_parser("status", help="print sync-state and table-count summary")
    sub.add_parser("serve-scheduler", help="run the APScheduler cron loop (blocks)")
    sub.add_parser("endpoints", help="list the configured endpoint registry")
    return parser


def _cmd_sync(settings: QuantLabSettings, args: argparse.Namespace) -> int:
    from .client import UpstoxFundamentalsClient
    from .store import FundamentalsStore
    from .sync import SyncEngine

    symbols: list[str] | None = None
    if args.symbols:
        symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    elif args.symbols_file:
        lines = args.symbols_file.read_text(encoding="utf-8").splitlines()
        symbols = [ln.strip() for ln in lines if ln.strip() and not ln.startswith("#")]

    endpoints: list[str] | None = None
    if args.endpoints:
        endpoints = [e.strip() for e in args.endpoints.split(",") if e.strip()]

    # Validate the token BEFORE touching the database, so a missing/expired
    # token fails fast without leaving an empty DuckDB file behind.
    client = UpstoxFundamentalsClient(settings)
    with FundamentalsStore(settings.db_path) as store:
        engine = SyncEngine(settings, store, client)
        report = engine.run(symbols, full=args.full, endpoints=endpoints, limit=args.limit)
    print(f"sync complete: {report.summary()}")
    return 0 if report.failed == 0 else 2


def _cmd_status(settings: QuantLabSettings) -> int:
    from .store import FundamentalsStore

    if not Path(settings.db_path).exists():
        print(f"no database at {settings.db_path} - run `python -m quantlab sync` first")
        return 1
    with FundamentalsStore(settings.db_path) as store:
        print(f"database: {settings.db_path}")
        print("\ntable counts:")
        for table, count in store.table_counts().items():
            print(f"  {table:<20} {count:>10,}")
        print("\nsync state by dataset/variant:")
        rows = store.status_summary()
        if not rows:
            print("  (empty — nothing synced yet)")
        for dataset, variant, total, errors, last in rows:
            print(
                f"  {dataset:<20} {variant:<40} synced={total:<6} "
                f"errors={errors:<5} last={last}"
            )
    return 0


def _cmd_endpoints() -> int:
    from .client import ENDPOINTS

    for spec in ENDPOINTS.values():
        print(f"{spec.name:<20} GET {spec.path}")
        for params in spec.variants:
            shown = {k: v for k, v in params.items()} or {}
            print(f"    variant: {shown if shown else '(default)'}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        settings = QuantLabSettings.from_env()
        overrides: dict[str, object] = {}
        if args.db is not None:
            overrides["db_path"] = args.db
        if args.log_level:
            overrides["log_level"] = args.log_level.upper()
        if overrides:
            from dataclasses import replace

            settings = replace(settings, **overrides)  # type: ignore[arg-type]
        _setup_logging(settings.log_level)

        if args.command == "sync":
            return _cmd_sync(settings, args)
        if args.command == "status":
            return _cmd_status(settings)
        if args.command == "serve-scheduler":
            from .scheduler import serve

            serve(settings)
            return 0
        if args.command == "endpoints":
            return _cmd_endpoints()
        parser.error(f"unknown command {args.command!r}")
        return 2
    except QuantLabError as exc:
        # Operational/config error: readable message, non-zero exit, no traceback.
        logger.error("%s", exc)
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
