"""APScheduler wiring: run the incremental sync on a cron schedule.

``apscheduler`` is imported lazily so the package (and the CI's lean
dependency list) imports cleanly without it; a clear error tells the
operator what to install.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from .config import QuantLabSettings
from .errors import MissingDependencyError, QuantLabError

logger = logging.getLogger("quantlab.scheduler")


def _require_apscheduler() -> tuple[Any, Any]:
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError as exc:  # pragma: no cover - exercised only without apscheduler
        raise MissingDependencyError(
            "apscheduler is required for scheduled syncs. Install it with: "
            "pip install 'apscheduler>=3.10'"
        ) from exc
    return BackgroundScheduler, CronTrigger


def _run_sync_job(settings: QuantLabSettings) -> None:
    """One scheduled run: build fresh components, sync, close the store."""
    # Local imports keep scheduler importable without duckdb installed.
    from .client import UpstoxFundamentalsClient
    from .store import FundamentalsStore
    from .sync import SyncEngine

    logger.info("scheduled sync starting")
    try:
        with FundamentalsStore(settings.db_path) as store:
            client = UpstoxFundamentalsClient(settings)
            report = SyncEngine(settings, store, client).run()
            logger.info("scheduled sync finished %s", report.summary())
    except QuantLabError as exc:
        # Operational failure: log and wait for the next trigger.
        logger.error("scheduled sync failed error=%s", exc)


def build_scheduler(settings: QuantLabSettings) -> Any:
    """Create a BackgroundScheduler with the configured cron trigger."""
    background_scheduler, cron_trigger = _require_apscheduler()
    try:
        trigger = cron_trigger.from_crontab(settings.schedule_cron)
    except ValueError as exc:
        raise QuantLabError(
            f"invalid QUANTLAB_SCHEDULE_CRON {settings.schedule_cron!r}: {exc}"
        ) from exc
    scheduler = background_scheduler()
    scheduler.add_job(
        _run_sync_job,
        trigger=trigger,
        args=[settings],
        id="quantlab-fundamentals-sync",
        name="Upstox fundamentals incremental sync",
        coalesce=True,          # collapse missed runs into one
        max_instances=1,        # never overlap two syncs
        misfire_grace_time=3600,
    )
    return scheduler


def serve(settings: QuantLabSettings) -> None:
    """Start the scheduler and block until Ctrl+C."""
    scheduler = build_scheduler(settings)
    scheduler.start()
    logger.info(
        "scheduler started cron=%r db=%s — Ctrl+C to stop",
        settings.schedule_cron, settings.db_path,
    )
    try:
        while True:
            time.sleep(1.0)
    except (KeyboardInterrupt, SystemExit):
        logger.info("scheduler stopping")
        scheduler.shutdown(wait=True)
