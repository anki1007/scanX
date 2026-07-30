"""Incremental sync orchestrator: universe -> resolve -> fetch -> normalize -> store.

Incrementality operates at two levels:

1. **Freshness window** — a (isin, endpoint, variant) synced OK within
   ``refresh_after_hours`` is skipped entirely (no API call) unless
   ``full=True``.
2. **Content hash** — when a payload IS fetched, a SHA-256 of its canonical
   ``data`` section is compared with ``_sync_state``; unchanged payloads skip
   the database rewrite.

Failures are logged, recorded in ``_sync_state`` with ``status='error'`` and
retried automatically on the next run (errored entries bypass the freshness
window).
"""
from __future__ import annotations

import gzip
import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from .client import ENDPOINTS, UpstoxFundamentalsClient, variant_key
from .config import REPO_ROOT, UpstoxLabSettings
from .errors import AuthTokenError, UpstoxLabError
from .normalize import normalize
from .store import FundamentalsStore

logger = logging.getLogger("upstox_lab.sync")

#: NSE/BSE ISIN, e.g. INE002A01018
ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}\d$")
#: Upstox instrument key, e.g. NSE_EQ|INE002A01018
INSTRUMENT_KEY_RE = re.compile(r"^[A-Z]+_[A-Z]+\|(?P<isin>[A-Z0-9]{12})$")

#: Default universe file shipped with the repo (list of NSE/BSE codes).
INDEX_JSON = REPO_ROOT / "docs" / "data" / "fundamental" / "index.json"


# ---------------------------------------------------------------------------
# instrument-key / ISIN resolution
# ---------------------------------------------------------------------------
class InstrumentResolver:
    """Map trading symbols to ISINs via the Upstox NSE instruments master.

    The master (``NSE.json.gz``) is downloaded once and cached under
    ``.cache/upstox_lab/`` (gitignored), refreshed after
    ``instruments_max_age_hours``.  Inputs that are already ISINs or
    ``NSE_EQ|<ISIN>`` instrument keys resolve without the master.
    """

    def __init__(self, settings: UpstoxLabSettings, *, session: Any | None = None) -> None:
        self.settings = settings
        self._session = session
        self._symbol_to_isin: dict[str, str] | None = None

    def resolve(self, symbols: Iterable[str]) -> tuple[dict[str, str], list[str]]:
        """Return ``(resolved: symbol->isin, unresolved: [symbol])``."""
        resolved: dict[str, str] = {}
        pending: list[str] = []
        for raw in symbols:
            symbol = str(raw).strip().upper()
            if not symbol:
                continue
            match = INSTRUMENT_KEY_RE.match(symbol)
            if match:
                resolved[symbol] = match.group("isin")
            elif ISIN_RE.match(symbol):
                resolved[symbol] = symbol
            else:
                pending.append(symbol)
        unresolved: list[str] = []
        if pending:
            mapping = self._load_mapping()
            for symbol in pending:
                isin = mapping.get(symbol)
                if isin:
                    resolved[symbol] = isin
                else:
                    unresolved.append(symbol)
        if unresolved:
            logger.warning(
                "unresolved symbols count=%d sample=%s (no match in the NSE or "
                "BSE instrument masters — a delisted or suspended scrip)",
                len(unresolved), unresolved[:5],
            )
        return resolved, unresolved

    # -- instrument master -----------------------------------------------------
    def _load_mapping(self) -> dict[str, str]:
        """SYMBOL -> ISIN, from the NSE master AND the BSE master.

        Screener identifies BSE-only listings by their numeric scrip code
        (500012, 544291, ...), and 2,457 of the 5,488 companies on the platform
        are such codes. The NSE master cannot contain them by definition, so
        every one of them used to fall out here — which is what the
        "unresolved symbols ... BSE-only codes need an explicit mapping" line in
        every run was reporting, and why the daily universe stopped at ~2,960.

        No hand-maintained mapping file is needed: in Upstox's BSE master the
        ``exchange_token`` IS the BSE scrip code, so it keys straight to the same
        ISIN the rest of the pipeline already uses.

        Measured against the live master (a PUBLIC asset — it needs no token, so
        this is checkable without credentials): 26,838 records, 12,642 of them
        BSE_EQ, and 2,295 of our 2,457 scrip codes match by ``exchange_token``,
        every one carrying an ISIN. The remaining 7% are delisted or suspended
        scrips that Screener still lists — they stay unresolved, which is the
        honest outcome rather than a wrong price.

        NSE wins on collisions. A company listed on both exchanges is the same
        ISIN either way, but the NSE symbol is what the rest of scanX calls it.
        """
        if self._symbol_to_isin is None:
            mapping: dict[str, str] = {}

            def collect(records, segment, keys):
                found = 0
                for record in records:
                    if not isinstance(record, Mapping):
                        continue
                    if record.get("segment") != segment:
                        continue
                    isin = record.get("isin")
                    if not (isinstance(isin, str) and ISIN_RE.match(isin)):
                        continue
                    for field in keys:
                        value = record.get(field)
                        if value is None:
                            continue
                        text = str(value).strip().upper()
                        if text and text not in mapping:
                            mapping[text] = isin
                            found += 1
                return found

            try:
                nse = self._load_master_records(self.settings.instruments_url,
                                                self.settings.instruments_cache)
            except (OSError, ValueError, UpstoxLabError) as exc:
                logger.error("instrument master unavailable error=%s", exc)
                nse = []
            n_nse = collect(nse, "NSE_EQ", ("trading_symbol",))

            # BSE second so NSE keeps the name on any collision
            n_bse = 0
            bse_url = getattr(self.settings, "bse_instruments_url", "")
            if bse_url:
                cache = self.settings.instruments_cache
                bse_cache = cache.with_name(cache.name.replace(".json", "_bse.json")
                                            if ".json" in cache.name
                                            else cache.name + ".bse")
                try:
                    bse = self._load_master_records(bse_url, bse_cache)
                except (OSError, ValueError, UpstoxLabError) as exc:
                    logger.warning("BSE instrument master unavailable error=%s", exc)
                    bse = []
                n_bse = collect(bse, "BSE_EQ", ("exchange_token", "trading_symbol"))

            self._symbol_to_isin = mapping
            logger.info("instrument master loaded nse=%d bse=%d total=%d",
                        n_nse, n_bse, len(mapping))
        return self._symbol_to_isin

    def _load_master_records(self, url: str | None = None,
                             cache: Path | None = None) -> list[Any]:
        cache = cache or self.settings.instruments_cache
        url = url or self.settings.instruments_url
        max_age = self.settings.instruments_max_age_hours * 3600.0
        if not cache.is_file() or (time.time() - cache.stat().st_mtime) > max_age:
            self._download_master(cache, url)
        with gzip.open(cache, "rt", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, list) else []

    def _download_master(self, cache: Path, url: str | None = None) -> None:
        url = url or self.settings.instruments_url
        logger.info("downloading instrument master url=%s", url)
        session = self._session
        if session is None:
            import requests

            session = requests
        try:
            response = session.get(url, timeout=self.settings.request_timeout)
        except Exception as exc:  # broad: network layer varies by session impl
            raise UpstoxLabError(f"instrument master download failed: {exc}") from exc
        status = getattr(response, "status_code", None)
        if status != 200:
            raise UpstoxLabError(f"instrument master download failed: HTTP {status}")
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(response.content)


# ---------------------------------------------------------------------------
# universe loading
# ---------------------------------------------------------------------------
def load_universe(settings: UpstoxLabSettings) -> list[str]:
    """Load the instrument universe per ``settings.universe_source``.

    * ``"index"`` — the repo's ``docs/data/fundamental/index.json`` code list.
    * otherwise — a path to a ``.json`` list or a text file with one entry per
      line (``SYMBOL`` or ``SYMBOL,ISIN``; ``#`` comments allowed).
    """
    source = settings.universe_source
    if source == "index":
        path = INDEX_JSON
    else:
        path = Path(source)
    if not path.is_file():
        raise UpstoxLabError(f"universe source not found: {path}")
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise UpstoxLabError(f"universe JSON must be a list of codes: {path}")
        return [str(item).strip().upper() for item in data if str(item).strip()]
    symbols: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        symbols.append(line.upper())
    return symbols


def parse_symbol_line(line: str) -> tuple[str, str | None]:
    """``"RELIANCE,INE002A01018"`` -> ``("RELIANCE", "INE002A01018")``."""
    if "," in line:
        symbol, _, isin = line.partition(",")
        return symbol.strip().upper(), (isin.strip().upper() or None)
    return line.strip().upper(), None


# ---------------------------------------------------------------------------
# sync engine
# ---------------------------------------------------------------------------
def content_hash(payload: Mapping[str, Any]) -> str:
    """SHA-256 of the canonical JSON of the payload's ``data`` section."""
    data = payload.get("data") if isinstance(payload, Mapping) else None
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class SyncReport:
    """Aggregate result of one sync run (all counters are dataset-variant level)."""

    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    symbols_requested: int = 0
    symbols_resolved: int = 0
    symbols_unresolved: int = 0
    fetched: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped_fresh: int = 0
    failed: int = 0
    rows_written: int = 0

    def summary(self) -> str:
        return (
            f"symbols={self.symbols_resolved}/{self.symbols_requested} "
            f"fetched={self.fetched} updated={self.updated} unchanged={self.unchanged} "
            f"skipped_fresh={self.skipped_fresh} failed={self.failed} "
            f"rows_written={self.rows_written}"
        )


class SyncEngine:
    """Drives incremental synchronization across the endpoint registry."""

    def __init__(
        self,
        settings: UpstoxLabSettings,
        store: FundamentalsStore,
        client: UpstoxFundamentalsClient,
        resolver: InstrumentResolver | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.client = client
        self.resolver = resolver or InstrumentResolver(settings)

    # -- public API ---------------------------------------------------------
    def run(
        self,
        symbols: Iterable[str] | None = None,
        *,
        full: bool = False,
        endpoints: Iterable[str] | None = None,
        limit: int | None = None,
    ) -> SyncReport:
        """Sync ``symbols`` (default: the configured universe).

        ``full=True`` bypasses both the freshness window and the content-hash
        short-circuit.  ``endpoints`` restricts to a subset of registry names.
        """
        report = SyncReport()
        symbol_list = list(symbols) if symbols is not None else load_universe(self.settings)
        if limit is not None:
            symbol_list = symbol_list[:limit]
        report.symbols_requested = len(symbol_list)

        explicit: dict[str, str] = {}
        to_resolve: list[str] = []
        for entry in symbol_list:
            symbol, isin = parse_symbol_line(str(entry))
            if isin and ISIN_RE.match(isin):
                explicit[symbol] = isin
            elif symbol:
                to_resolve.append(symbol)
        resolved, unresolved = self.resolver.resolve(to_resolve)
        resolved.update(explicit)
        report.symbols_resolved = len(resolved)
        report.symbols_unresolved = len(unresolved)

        selected = self._select_endpoints(endpoints)
        logger.info(
            "sync start symbols=%d endpoints=%s full=%s",
            len(resolved), [spec.name for spec in selected], full,
        )
        for symbol, isin in resolved.items():
            self._sync_instrument(symbol, isin, selected, full, report)
        logger.info("sync done %s", report.summary())
        return report

    # -- internals ------------------------------------------------------------
    def _select_endpoints(self, names: Iterable[str] | None) -> list[Any]:
        if names is None:
            return list(ENDPOINTS.values())
        selected = []
        for name in names:
            spec = ENDPOINTS.get(name)
            if spec is None:
                raise ValueError(f"unknown endpoint {name!r}; known: {sorted(ENDPOINTS)}")
            selected.append(spec)
        return selected

    def _iter_variants(self, spec: Any) -> list[Mapping[str, str]]:
        variants = []
        for params in spec.variants:
            if not self.settings.include_standalone and params.get("type") == "standalone":
                continue
            variants.append(params)
        return variants

    def _sync_instrument(
        self,
        symbol: str,
        isin: str,
        specs: list[Any],
        full: bool,
        report: SyncReport,
    ) -> None:
        for spec in specs:
            for params in self._iter_variants(spec):
                vkey = variant_key(params)
                try:
                    self._sync_dataset(symbol, isin, spec.name, params, vkey, full, report)
                except AuthTokenError:
                    # A dead token fails everything — abort instead of hammering.
                    raise
                except UpstoxLabError as exc:
                    # Operational failure: record and continue with the rest.
                    report.failed += 1
                    logger.error(
                        "sync failed endpoint=%s isin=%s symbol=%s variant=%s error=%s",
                        spec.name, isin, symbol, vkey, exc,
                    )
                    try:
                        self.store.set_sync_state(
                            isin, spec.name, vkey,
                            content_hash=None, status="error", error=str(exc)[:500],
                        )
                    except UpstoxLabError as store_exc:  # pragma: no cover
                        logger.error("could not record failure: %s", store_exc)

    def _sync_dataset(
        self,
        symbol: str,
        isin: str,
        endpoint: str,
        params: Mapping[str, str],
        vkey: str,
        full: bool,
        report: SyncReport,
    ) -> None:
        state = self.store.get_sync_state(isin, endpoint, vkey)
        now = datetime.now(timezone.utc)
        if not full and state is not None and state.status == "ok" and state.last_synced_at:
            age_hours = (now - state.last_synced_at).total_seconds() / 3600.0
            if age_hours < self.settings.refresh_after_hours:
                report.skipped_fresh += 1
                logger.debug(
                    "skip fresh endpoint=%s isin=%s variant=%s age_h=%.1f",
                    endpoint, isin, vkey, age_hours,
                )
                return

        payload = self.client.fetch(endpoint, isin, params=params)
        report.fetched += 1
        digest = content_hash(payload)
        if not full and state is not None and state.content_hash == digest:
            report.unchanged += 1
            self.store.set_sync_state(
                isin, endpoint, vkey, content_hash=digest, status="ok", synced_at=now,
            )
            return

        dataset = normalize(endpoint, isin, payload, params, now.replace(tzinfo=None))
        rows = self.store.upsert(dataset)
        self.store.set_sync_state(
            isin, endpoint, vkey, content_hash=digest, status="ok", synced_at=now,
        )
        report.updated += 1
        report.rows_written += rows
        logger.info(
            "stored endpoint=%s isin=%s symbol=%s variant=%s rows=%d",
            endpoint, isin, symbol, vkey, rows,
        )
