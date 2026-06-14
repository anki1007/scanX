"""Map a Screener code (or company name) -> its sector + that sector's
tailwind label/score, read from the static docs/data/sector_*.json that the
sector refresh writes.

Used by /api/signal (live) and the fundamental baker so the verdict's
bias-check can flag a strong company sitting in a weak (HEADWIND) sector.
Cheap: parsed once and re-read only when the JSON files change on disk.
"""
from __future__ import annotations

import json
from pathlib import Path

_CACHE = {"key": None, "by_code": {}, "by_name": {}, "tw": {}}


def _docs(docs_dir=None) -> Path:
    return Path(docs_dir) if docs_dir else (Path(__file__).resolve().parents[2] / "docs")


def _mtimes(d: Path):
    out = []
    for fn in ("sector_tailwind.json", "sector_stocks.json"):
        try:
            out.append(int((d / "data" / fn).stat().st_mtime))
        except Exception:  # noqa: BLE001
            out.append(0)
    return tuple(out)


def _load(docs_dir=None) -> None:
    d = _docs(docs_dir)
    key = (str(d), _mtimes(d))
    if _CACHE["key"] == key:
        return
    by_code, by_name, tw = {}, {}, {}
    try:
        j = json.loads((d / "data" / "sector_tailwind.json").read_text(encoding="utf-8"))
        for s in j.get("sectors", []):
            nm = s.get("sector")
            if nm:
                tw[nm] = {"label": s.get("signal"), "score": s.get("score")}
    except Exception:  # noqa: BLE001
        pass
    try:
        j = json.loads((d / "data" / "sector_stocks.json").read_text(encoding="utf-8"))
        for sec, rows in (j.get("sectors") or {}).items():
            for r in rows or []:
                c = str(r.get("code") or "").strip().upper()
                if c:
                    by_code.setdefault(c, sec)
                nm = str(r.get("name") or "").strip().upper()
                if nm:
                    by_name.setdefault(nm, sec)
    except Exception:  # noqa: BLE001
        pass
    _CACHE.update({"key": key, "by_code": by_code, "by_name": by_name, "tw": tw})


def sector_for(code=None, name=None, docs_dir=None):
    """Return {name, label, score} for a code/company name, or None if unknown."""
    if not code and not name:
        return None
    _load(docs_dir)
    sec = _CACHE["by_code"].get(str(code).strip().upper()) if code else None
    if sec is None and name:
        sec = _CACHE["by_name"].get(str(name).strip().upper())
    if not sec:
        return None
    tw = _CACHE["tw"].get(sec) or {}
    return {"name": sec, "label": tw.get("label"), "score": tw.get("score")}
