"""Refresh the scanX Demerger Tracking board (Screener full-text-search).

Replicates financiallyfree.in's demerger pipeline tracker: every company with a
live demerger is tracked through Board Approval -> NCLT Approval -> Record Date
-> Listing.  Stage comes from ordered keyword rules over the filing headlines
(listing beats record date beats NCLT beats board beats announced); dates are
pulled from the headline text where present.  Scrape failures keep the
last-good JSON on disk (same contract as refresh_special.py / refresh_fii.py).
"""
from __future__ import annotations
import argparse, json, os, re, sys
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scripts"))
from earnings_intel.data.special import fetch_fulltext  # noqa: E402

IST = timezone(timedelta(hours=5, minutes=30))
_SESSION_CACHE = ROOT / "screener_session.json"

# ---- demerger pipeline ------------------------------------------------------
STAGES = ["ANNOUNCED", "BOARD_APPROVED", "NCLT", "RECORD_DATE", "LISTED"]
_RANK = {s: i for i, s in enumerate(STAGES)}

# Screener full-text queries, tagged with the stage a hit implies.  Ordered
# most-advanced-first so a filing surfaced by two queries keeps the higher hint.
QUERIES = [
    ('"listing" "demerged"', "LISTED"),
    ('"record date" "demerger"', "RECORD_DATE"),
    ('"NCLT" "demerger"', "NCLT"),
    ('"board approved the scheme of arrangement"', "BOARD_APPROVED"),
    ('"scheme of arrangement" "demerger"', "ANNOUNCED"),
    ('"demerger"', "ANNOUNCED"),
]


def _atomic(path, text):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _sid():
    sid = os.environ.get("SCREENER_SESSIONID")
    if not sid and _SESSION_CACHE.exists():
        try:
            sid = json.loads(_SESSION_CACHE.read_text()).get("sessionid")
        except Exception:  # noqa: BLE001
            sid = None
    return sid


# ---- stage classification (pure, unit-tested) -------------------------------
# SEBI LODR boilerplate ("Listing Obligations...", "Listing Regulations") must
# not count as a listing event.
_LODR = re.compile(r"listing\s+(?:obligations?|regulations?|requirements?|"
                   r"agreement|department|centre)", re.I)


def classify_stage(text: str) -> str:
    """Ordered keyword rules -> furthest pipeline stage mentioned in the text.

    LISTED beats RECORD_DATE beats NCLT beats BOARD_APPROVED beats ANNOUNCED.
    """
    low = _LODR.sub(" ", str(text or "")).lower()
    if re.search(r"\blist(?:ing|ed)\b", low):
        return "LISTED"
    if "record date" in low:
        return "RECORD_DATE"
    if "nclt" in low or "national company law tribunal" in low:
        return "NCLT"
    if "board" in low and "approv" in low:
        return "BOARD_APPROVED"
    return "ANNOUNCED"


# ---- date extraction (regex style mirrors earnings_intel/data/orders.py) ----
_MON = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"])}


def _ymd(y: int, mo: int, d: int):
    if 1 <= mo <= 12 and 1 <= d <= 31:
        return f"{y:04d}-{mo:02d}-{d:02d}"
    return None


def _iso(d) -> str | None:
    """Normalise a Screener-style date ('06 Jun 2026' / '2026-06-06') to ISO."""
    if not d:
        return None
    d = str(d).strip()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", d)
    if m:
        return _ymd(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.match(r"^(\d{1,2})\s+([A-Za-z]{3,9})\.?,?\s+(\d{4})$", d)
    if m and m.group(2)[:3].lower() in _MON:
        return _ymd(int(m.group(3)), _MON[m.group(2)[:3].lower()], int(m.group(1)))
    return None


def extract_date(text: str) -> str | None:
    """Best-effort: pull the first plausible date out of headline text -> ISO."""
    if not text:
        return None
    t = str(text)
    for m in re.finditer(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9})\.?,?\s+(\d{4})\b", t):
        mon = _MON.get(m.group(2)[:3].lower())
        if mon and _ymd(int(m.group(3)), mon, int(m.group(1))):
            return _ymd(int(m.group(3)), mon, int(m.group(1)))
    for m in re.finditer(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b", t):
        mon = _MON.get(m.group(1)[:3].lower())
        if mon and _ymd(int(m.group(3)), mon, int(m.group(2))):
            return _ymd(int(m.group(3)), mon, int(m.group(2)))
    m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", t)
    if m and _ymd(int(m.group(1)), int(m.group(2)), int(m.group(3))):
        return _ymd(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    for m in re.finditer(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})\b", t):   # dd/mm/yyyy
        if _ymd(int(m.group(3)), int(m.group(2)), int(m.group(1))):
            return _ymd(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    return None


# ---- per-company aggregation (pure, unit-tested) -----------------------------
def aggregate(items: list, max_headlines: int = 6) -> list:
    """Classified headline dicts -> per-company pipeline rows.

    items: [{code, name, stage, date, text, url[, stage_date]}...]
    Company stage = furthest stage seen; stage_date = the (extracted or filing)
    date of that stage; keeps the newest `max_headlines` headlines.  Rows sort
    most-advanced stage first, then newest date.
    """
    by: dict = {}
    for it in items:
        code = str(it.get("code") or "").strip()
        if not code:
            continue
        co = by.get(code)
        if co is None:
            co = {"code": code, "name": it.get("name") or code,
                  "stage": "ANNOUNCED", "stage_date": None, "headlines": []}
            by[code] = co
        stage = it.get("stage") if it.get("stage") in _RANK else "ANNOUNCED"
        sd = it.get("stage_date") or _iso(it.get("date"))
        if _RANK[stage] > _RANK[co["stage"]]:
            co["stage"], co["stage_date"] = stage, sd
        elif _RANK[stage] == _RANK[co["stage"]] and sd and (co["stage_date"] or "") < sd:
            co["stage_date"] = sd
        text = (it.get("text") or "").strip()
        if text and not any(h["text"] == text for h in co["headlines"]):
            co["headlines"].append({"date": it.get("date"), "stage": stage,
                                    "text": text, "url": it.get("url") or ""})
    rows = list(by.values())
    for co in rows:
        co["headlines"].sort(key=lambda h: _iso(h.get("date")) or "", reverse=True)
        co["headlines"] = co["headlines"][:max_headlines]
    rows.sort(key=lambda r: (
        _RANK[r["stage"]],
        r.get("stage_date") or (_iso(r["headlines"][0].get("date")) if r["headlines"] else "") or ""),
        reverse=True)
    return rows


# ---- scrape -------------------------------------------------------------------
def build_rows(months: int, max_pages: int = 2) -> list:
    sid = _sid()
    cutoff = (datetime.now(IST) - timedelta(days=int(months * 30.5))).strftime("%Y-%m-%d")
    items: list = []
    seen: dict = {}
    for q, hint in QUERIES:
        try:
            hits = fetch_fulltext(sid, q, max_pages=max_pages, announcements_only=True)
        except Exception as e:  # noqa: BLE001
            print(f"[demerger] query failed ({q[:28]}...): {type(e).__name__}: {e}")
            continue
        for d in hits:
            text = (d.get("snippet") or d.get("name") or "").strip()[:300]
            stage = classify_stage(text)
            if _RANK[hint] > _RANK[stage]:
                stage = hint                       # query context beats a terse snippet
            filed = _iso(d.get("date"))
            if filed and filed < cutoff:
                continue
            k = (d["code"], text[:80])
            prev = seen.get(k)
            if prev is not None:                   # same filing from two queries
                if _RANK[stage] > _RANK[prev["stage"]]:
                    prev["stage"] = stage
                continue
            item = {"code": d["code"], "name": d.get("name") or d["code"],
                    "stage": stage, "date": d.get("date"),
                    "stage_date": extract_date(text) or filed,
                    "text": text, "url": d.get("pdf_url") or d.get("url") or ""}
            seen[k] = item
            items.append(item)
    return aggregate(items)


def main() -> None:
    ap = argparse.ArgumentParser(description="Refresh scanX Demerger Tracking board")
    ap.add_argument("--months", type=int, default=12, help="lookback window (months)")
    ap.add_argument("--max-pages", type=int, default=2)
    ap.add_argument("--out", default=str(ROOT / "docs" / "data"))
    args = ap.parse_args()
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)

    try:
        rows = build_rows(args.months, args.max_pages)
    except Exception as e:  # noqa: BLE001
        print(f"[demerger] scrape failed: {type(e).__name__}: {e} — keeping last-good JSON")
        return
    if not rows:
        print("[demerger] no data (need a logged-in Screener session) — keeping last-good JSON")
        return

    _atomic(out / "demergers.json", json.dumps(rows, indent=2))
    now = datetime.now(IST)
    stages = Counter(r["stage"] for r in rows)
    meta = {"generated_at_ist": now.strftime("%Y-%m-%d %H:%M:%S IST"), "count": len(rows),
            "by_stage": {s: stages.get(s, 0) for s in STAGES},
            "source": "Screener full-text-search"}
    _atomic(out / "demergers_meta.json", json.dumps(meta, indent=2))
    print(f"[demerger] {len(rows)} companies | {dict(stages)} | {now:%H:%M:%S IST}")


if __name__ == "__main__":
    main()
