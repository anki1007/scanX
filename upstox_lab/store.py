"""DuckDB persistence layer: schema DDL, idempotent upserts, sync state.

Layout: one table per endpoint family plus ``_sync_state`` which tracks, per
(isin, dataset, variant), the last successful sync time and a content hash of
the raw payload — the backbone of incremental synchronization.

Writes are idempotent: every :class:`~upstox_lab.normalize.Dataset` carries a
natural-key ``scope``; the store runs ``DELETE (scope) + INSERT rows`` inside
a single transaction, so replaying a sync never duplicates rows.

``duckdb`` is imported lazily so the rest of the package (and CI's lean
dependency list) works without it installed.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .errors import MissingDependencyError
from .normalize import Dataset

logger = logging.getLogger("upstox_lab.store")


def _require_duckdb() -> Any:
    try:
        import duckdb
    except ImportError as exc:  # pragma: no cover - exercised only without duckdb
        raise MissingDependencyError(
            "duckdb is required for upstox_lab storage. Install it with: "
            "pip install 'duckdb>=0.10'"
        ) from exc
    return duckdb


#: table name -> CREATE TABLE statement.  Natural keys are documented inline;
#: idempotency is enforced by scope-delete + insert, not by PK constraints,
#: so partial upstream payloads can never violate a constraint mid-sync.
SCHEMA_DDL: dict[str, str] = {
    "company_profile": """
        CREATE TABLE IF NOT EXISTS company_profile (
            isin                  VARCHAR NOT NULL,   -- natural key
            company_profile       VARCHAR,
            sector                VARCHAR,
            sector_mcap_inr       DOUBLE,
            sector_mcap_inr_unit  VARCHAR,
            sector_mcap_usd       DOUBLE,
            sector_mcap_usd_unit  VARCHAR,
            fetched_at            TIMESTAMP
        )
    """,
    "balance_sheet": """
        CREATE TABLE IF NOT EXISTS balance_sheet (
            isin            VARCHAR NOT NULL,   -- key: isin+statement_type+section+particular+period
            statement_type  VARCHAR NOT NULL,
            section         VARCHAR NOT NULL,   -- 'summary' | 'full'
            particular      VARCHAR NOT NULL,
            period          VARCHAR NOT NULL,
            value           DOUBLE,
            change          VARCHAR,
            units_in        VARCHAR,
            fetched_at      TIMESTAMP
        )
    """,
    "cash_flow": """
        CREATE TABLE IF NOT EXISTS cash_flow (
            isin            VARCHAR NOT NULL,   -- key: isin+statement_type+section+particular+period
            statement_type  VARCHAR NOT NULL,
            section         VARCHAR NOT NULL,   -- 'category' | 'full'
            particular      VARCHAR NOT NULL,
            period          VARCHAR NOT NULL,
            value           DOUBLE,
            change          VARCHAR,
            units_in        VARCHAR,
            fetched_at      TIMESTAMP
        )
    """,
    "income_statement": """
        CREATE TABLE IF NOT EXISTS income_statement (
            isin            VARCHAR NOT NULL,   -- key: + statement_type+time_period+section+particular+period
            statement_type  VARCHAR NOT NULL,
            time_period     VARCHAR NOT NULL,   -- 'yearly' | 'quarterly'
            section         VARCHAR NOT NULL,   -- 'category' | 'full'
            particular      VARCHAR NOT NULL,
            period          VARCHAR NOT NULL,
            value           DOUBLE,
            change          VARCHAR,
            units_in        VARCHAR,
            fetched_at      TIMESTAMP
        )
    """,
    "share_holdings": """
        CREATE TABLE IF NOT EXISTS share_holdings (
            isin         VARCHAR NOT NULL,      -- key: isin+category+period
            category     VARCHAR NOT NULL,
            period       VARCHAR NOT NULL,
            holding_pct  DOUBLE,
            fetched_at   TIMESTAMP
        )
    """,
    "key_ratios": """
        CREATE TABLE IF NOT EXISTS key_ratios (
            isin               VARCHAR NOT NULL,  -- key: isin+ratio_name
            ratio_name         VARCHAR NOT NULL,
            company_value      DOUBLE,
            company_value_raw  VARCHAR,
            sector_value       DOUBLE,
            sector_value_raw   VARCHAR,
            fetched_at         TIMESTAMP
        )
    """,
    "corporate_actions": """
        CREATE TABLE IF NOT EXISTS corporate_actions (
            isin                VARCHAR NOT NULL,  -- key: isin+action_type+expiry_date
            action_type         VARCHAR NOT NULL,
            expiry_date         VARCHAR,
            amount              DOUBLE,
            ratio               VARCHAR,
            event_details_json  VARCHAR,
            fetched_at          TIMESTAMP
        )
    """,
    "competitors": """
        CREATE TABLE IF NOT EXISTS competitors (
            isin                      VARCHAR NOT NULL,  -- key: isin+competitor_instrument_key
            competitor_instrument_key VARCHAR,
            competitor_isin           VARCHAR,
            sector                    VARCHAR,
            company_profile           VARCHAR,
            sector_mcap_inr           DOUBLE,
            fetched_at                TIMESTAMP
        )
    """,
    "_sync_state": """
        CREATE TABLE IF NOT EXISTS _sync_state (
            isin            VARCHAR NOT NULL,
            dataset         VARCHAR NOT NULL,
            variant         VARCHAR NOT NULL,
            content_hash    VARCHAR,
            last_synced_at  TIMESTAMP,
            status          VARCHAR,            -- 'ok' | 'error'
            error           VARCHAR,
            PRIMARY KEY (isin, dataset, variant)
        )
    """,
}


@dataclass(frozen=True, slots=True)
class SyncState:
    """One row of ``_sync_state``."""

    isin: str
    dataset: str
    variant: str
    content_hash: str | None
    last_synced_at: datetime | None
    status: str | None
    error: str | None


class FundamentalsStore:
    """Thin, transactional DuckDB wrapper for the fundamentals schema."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self._conn: Any = None

    # -- lifecycle ------------------------------------------------------------
    @property
    def conn(self) -> Any:
        if self._conn is None:
            duckdb = _require_duckdb()
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = duckdb.connect(str(self.db_path))
            self.init_schema()
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> "FundamentalsStore":
        _ = self.conn  # open + create schema eagerly
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def init_schema(self) -> None:
        for table, ddl in SCHEMA_DDL.items():
            self._conn.execute(ddl)
        logger.debug("schema ready tables=%d path=%s", len(SCHEMA_DDL), self.db_path)

    # -- idempotent writes -------------------------------------------------------
    def upsert(self, dataset: Dataset) -> int:
        """Replace ``dataset.scope`` with ``dataset.rows`` in one transaction."""
        if dataset.table not in SCHEMA_DDL:
            raise ValueError(f"unknown table {dataset.table!r}")
        conn = self.conn
        placeholders = ", ".join("?" for _ in dataset.columns)
        column_sql = ", ".join(dataset.columns)
        conn.execute("BEGIN TRANSACTION")
        try:
            if dataset.scope:
                where = " AND ".join(f"{col} = ?" for col in dataset.scope)
                conn.execute(
                    f"DELETE FROM {dataset.table} WHERE {where}",
                    list(dataset.scope.values()),
                )
            if dataset.rows:
                conn.executemany(
                    f"INSERT INTO {dataset.table} ({column_sql}) VALUES ({placeholders})",
                    dataset.rows,
                )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        logger.debug(
            "upsert table=%s rows=%d scope=%s", dataset.table, len(dataset.rows), dict(dataset.scope)
        )
        return len(dataset.rows)

    # -- sync state ---------------------------------------------------------------
    def get_sync_state(self, isin: str, dataset: str, variant: str) -> SyncState | None:
        row = self.conn.execute(
            "SELECT isin, dataset, variant, content_hash, last_synced_at, status, error "
            "FROM _sync_state WHERE isin = ? AND dataset = ? AND variant = ?",
            [isin, dataset, variant],
        ).fetchone()
        if row is None:
            return None
        last = row[4]
        if isinstance(last, datetime) and last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        return SyncState(
            isin=row[0], dataset=row[1], variant=row[2],
            content_hash=row[3], last_synced_at=last, status=row[5], error=row[6],
        )

    def set_sync_state(
        self,
        isin: str,
        dataset: str,
        variant: str,
        *,
        content_hash: str | None,
        status: str,
        error: str | None = None,
        synced_at: datetime | None = None,
    ) -> None:
        synced_at = synced_at or datetime.now(timezone.utc)
        # DuckDB TIMESTAMP is naive; store UTC wall-clock consistently.
        if synced_at.tzinfo is not None:
            synced_at = synced_at.astimezone(timezone.utc).replace(tzinfo=None)
        conn = self.conn
        conn.execute("BEGIN TRANSACTION")
        try:
            conn.execute(
                "DELETE FROM _sync_state WHERE isin = ? AND dataset = ? AND variant = ?",
                [isin, dataset, variant],
            )
            conn.execute(
                "INSERT INTO _sync_state VALUES (?, ?, ?, ?, ?, ?, ?)",
                [isin, dataset, variant, content_hash, synced_at, status, error],
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    # -- reporting -------------------------------------------------------------------
    def row_count(self, table: str) -> int:
        if table not in SCHEMA_DDL:
            raise ValueError(f"unknown table {table!r}")
        result = self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        return int(result[0]) if result else 0

    def status_summary(self) -> list[tuple[str, str, int, int, datetime | None]]:
        """Per (dataset, variant): symbols synced, error count, latest sync."""
        rows = self.conn.execute(
            "SELECT dataset, variant, COUNT(*), "
            "SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END), MAX(last_synced_at) "
            "FROM _sync_state GROUP BY dataset, variant ORDER BY dataset, variant"
        ).fetchall()
        return [(r[0], r[1], int(r[2]), int(r[3] or 0), r[4]) for r in rows]

    def table_counts(self) -> dict[str, int]:
        return {table: self.row_count(table) for table in SCHEMA_DDL}
