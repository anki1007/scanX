"""Store + sync tests: idempotent upserts and incremental state (tmp DuckDB).

Skipped automatically when ``duckdb`` is not installed (CI's lean list).
"""
from __future__ import annotations

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta, timezone

import pytest

duckdb = pytest.importorskip("duckdb")

from quantlab.config import QuantLabSettings
from quantlab.normalize import Dataset
from quantlab.store import SCHEMA_DDL, FundamentalsStore
from quantlab.sync import InstrumentResolver, SyncEngine, content_hash

ISIN = "INE002A01018"
FETCHED = datetime(2026, 7, 27, 12, 0, 0)


def make_dataset(value: float = 50.11) -> Dataset:
    return Dataset(
        table="share_holdings",
        columns=("isin", "category", "period", "holding_pct", "fetched_at"),
        rows=[
            (ISIN, "promoters", "Mar 2026", value, FETCHED),
            (ISIN, "fii", "Mar 2026", 19.16, FETCHED),
        ],
        scope={"isin": ISIN},
    )


# ---------------------------------------------------------------------------
# store level
# ---------------------------------------------------------------------------
def test_schema_created_and_counts_zero(tmp_path):
    with FundamentalsStore(tmp_path / "ql.duckdb") as store:
        counts = store.table_counts()
        assert set(counts) == set(SCHEMA_DDL)
        assert all(count == 0 for count in counts.values())


def test_upsert_is_idempotent(tmp_path):
    with FundamentalsStore(tmp_path / "ql.duckdb") as store:
        assert store.upsert(make_dataset()) == 2
        assert store.upsert(make_dataset()) == 2       # replay: replaced, not appended
        assert store.row_count("share_holdings") == 2


def test_upsert_replaces_scope_with_new_values(tmp_path):
    with FundamentalsStore(tmp_path / "ql.duckdb") as store:
        store.upsert(make_dataset(value=50.11))
        store.upsert(make_dataset(value=49.99))
        assert store.row_count("share_holdings") == 2
        value = store.conn.execute(
            "SELECT holding_pct FROM share_holdings WHERE category = 'promoters'"
        ).fetchone()[0]
        assert value == pytest.approx(49.99)


def test_upsert_scope_isolation_between_isins(tmp_path):
    other = Dataset(
        table="share_holdings",
        columns=("isin", "category", "period", "holding_pct", "fetched_at"),
        rows=[("INE009A01021", "promoters", "Mar 2026", 72.3, FETCHED)],
        scope={"isin": "INE009A01021"},
    )
    with FundamentalsStore(tmp_path / "ql.duckdb") as store:
        store.upsert(make_dataset())
        store.upsert(other)
        store.upsert(make_dataset())                   # replay first scope only
        assert store.row_count("share_holdings") == 3  # 2 + 1


def test_upsert_unknown_table_is_programmer_error(tmp_path):
    bad = Dataset(table="not_a_table", columns=("a",), rows=[(1,)], scope={})
    with FundamentalsStore(tmp_path / "ql.duckdb") as store:
        with pytest.raises(ValueError, match="unknown table"):
            store.upsert(bad)


def test_sync_state_roundtrip(tmp_path):
    with FundamentalsStore(tmp_path / "ql.duckdb") as store:
        assert store.get_sync_state(ISIN, "share_holdings", "default") is None
        store.set_sync_state(
            ISIN, "share_holdings", "default",
            content_hash="abc123", status="ok",
            synced_at=datetime(2026, 7, 27, 10, 0, tzinfo=timezone.utc),
        )
        state = store.get_sync_state(ISIN, "share_holdings", "default")
        assert state is not None
        assert state.content_hash == "abc123"
        assert state.status == "ok"
        assert state.last_synced_at == datetime(2026, 7, 27, 10, 0, tzinfo=timezone.utc)
        # overwrite (same PK) keeps a single row
        store.set_sync_state(
            ISIN, "share_holdings", "default", content_hash="def", status="error", error="x",
        )
        assert store.row_count("_sync_state") == 1
        assert store.get_sync_state(ISIN, "share_holdings", "default").status == "error"


# ---------------------------------------------------------------------------
# sync-engine level (stub client, no network)
# ---------------------------------------------------------------------------
HOLDINGS = {
    "status": "success",
    "data": [{"category": "promoters", "history": [{"period": "Mar 2026", "value": 50.11}]}],
}


class StubClient:
    def __init__(self, payloads: dict[str, dict]):
        self.payloads = payloads
        self.calls: list[tuple[str, str]] = []

    def fetch(self, endpoint: str, isin: str, *, params=None):
        self.calls.append((endpoint, isin))
        return self.payloads[endpoint]


class StaticResolver(InstrumentResolver):
    def resolve(self, symbols):
        return {s: ISIN for s in symbols}, []


def make_engine(tmp_path, store, payloads):
    settings = QuantLabSettings(
        db_path=tmp_path / "ql.duckdb",
        token_file=tmp_path / "tok.txt",
        refresh_after_hours=24.0,
    )
    client = StubClient(payloads)
    engine = SyncEngine(settings, store, client, resolver=StaticResolver(settings))
    return engine, client


def test_sync_twice_is_idempotent_and_incremental(tmp_path):
    with FundamentalsStore(tmp_path / "ql.duckdb") as store:
        engine, client = make_engine(tmp_path, store, {"share_holdings": HOLDINGS})

        first = engine.run(["RELIANCE"], endpoints=["share_holdings"])
        assert first.updated == 1
        assert first.rows_written == 1
        assert store.row_count("share_holdings") == 1

        # Second run inside the freshness window: no fetch, no writes.
        second = engine.run(["RELIANCE"], endpoints=["share_holdings"])
        assert second.skipped_fresh == 1
        assert second.fetched == 0
        assert store.row_count("share_holdings") == 1
        assert len(client.calls) == 1

        # Full run: fetches again, row count still stable (idempotent).
        third = engine.run(["RELIANCE"], endpoints=["share_holdings"], full=True)
        assert third.fetched == 1
        assert store.row_count("share_holdings") == 1


def test_sync_unchanged_content_skips_rewrite(tmp_path):
    with FundamentalsStore(tmp_path / "ql.duckdb") as store:
        engine, client = make_engine(tmp_path, store, {"share_holdings": HOLDINGS})
        engine.run(["RELIANCE"], endpoints=["share_holdings"])

        # Age the sync state past the freshness window, keep the same hash.
        old = datetime.now(timezone.utc) - timedelta(hours=48)
        store.set_sync_state(
            ISIN, "share_holdings", "default",
            content_hash=content_hash(HOLDINGS), status="ok", synced_at=old,
        )
        report = engine.run(["RELIANCE"], endpoints=["share_holdings"])
        assert report.fetched == 1        # window expired -> re-fetched
        assert report.unchanged == 1      # ... but hash matched -> no rewrite
        assert report.updated == 0


def test_sync_records_failures_and_continues(tmp_path):
    from quantlab.errors import QuantLabAPIError

    class FailingClient(StubClient):
        def fetch(self, endpoint, isin, *, params=None):
            if endpoint == "share_holdings":
                raise QuantLabAPIError("HTTP 500 boom", status_code=500)
            return super().fetch(endpoint, isin, params=params)

    with FundamentalsStore(tmp_path / "ql.duckdb") as store:
        settings = QuantLabSettings(db_path=tmp_path / "ql.duckdb", token_file=tmp_path / "t")
        client = FailingClient({"key_ratios": {"status": "success", "data": []}})
        engine = SyncEngine(settings, store, client, resolver=StaticResolver(settings))
        report = engine.run(["RELIANCE"], endpoints=["share_holdings", "key_ratios"])
        assert report.failed == 1
        assert report.updated == 1        # key_ratios still synced
        state = store.get_sync_state(ISIN, "share_holdings", "default")
        assert state.status == "error"
        assert "boom" in (state.error or "")
