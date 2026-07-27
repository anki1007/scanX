"""
Pre-bake the "What management said" bundle — GROUNDED facts pulled out of a
company's own filings (concall transcript / PPT / notes, annual report), so the
Fundamental tab shows them on the STATIC GitHub Pages site too.

For each target code it writes docs/data/docs/<CODE>.json:

    {"code","name","generated_at","documents":[…],
     "analysis":{"summary","themes","management_commitments","coverage"},
     "_meta":{"model","grounded","facts_kept","facts_dropped_ungrounded","note"}}

Every fact carries the VERBATIM quote it came from; docanalysis drops any claim
whose quote is not found in the source document, so nothing here is model
opinion. A company with no grounded fact is NOT written — the last good bundle
stays on disk.

    python scripts/refresh_docinsights.py                    # board top 100
    python scripts/refresh_docinsights.py --top 25 --skip-existing
    python scripts/refresh_docinsights.py --codes TATAMOTORS,LT --top 0
    python scripts/refresh_docinsights.py --max-minutes 40   # cloud: commit incrementally

Needs a Gemini key (GEMINI_API_KEY / gemin_api_key.md, kept local, never
published). Without one the bake prints why and exits 0 — the daily job must not
fail just because the key is not configured on that machine.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from earnings_intel.data import docanalysis as da       # noqa: E402
from earnings_intel.data import documents as dm         # noqa: E402
from earnings_intel.data import insights_ai as ia       # noqa: E402

_SESSION = ROOT / "screener_session.json"
_STAMP_RE = re.compile(rb'"generated_at"\s*:\s*"(\d{4}-\d{2}-\d{2})"')


def _read_board(path):
    """Read the board JSON, recovering from a truncated/null-padded concurrent write."""
    try:
        raw = Path(path).read_bytes().rstrip(b"\x00").rstrip()
    except Exception:  # noqa: BLE001
        return []
    try:
        return json.loads(raw)
    except Exception:  # noqa: BLE001
        i = raw.rfind(b"}")               # close the array at the last complete object
        if i > 0:
            try:
                return json.loads(raw[:i + 1] + b"]")
            except Exception:  # noqa: BLE001
                return []
        return []


def _atomic(path, text):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _baked_on(path):
    """Date a bundle was baked, from the generated_at stamp INSIDE the file.

    File mtime is useless in CI (a fresh checkout stamps every file "today"), so
    freshness must live inside the bundle. Legacy/garbage files return "" and are
    treated as stale, which re-bakes them exactly once.
    """
    try:
        with open(path, "rb") as f:
            head = f.read(512)
        m = _STAMP_RE.search(head)
        return m.group(1).decode() if m else ""
    except Exception:  # noqa: BLE001
        return ""


def _skip_baked(path, today, enabled=True):
    """--skip-existing decision: already baked today, per the file's own stamp."""
    return bool(enabled) and Path(path).exists() and _baked_on(path) == today


def _sid():
    sid = os.environ.get("SCREENER_SESSIONID")
    if not sid and _SESSION.exists():
        try:
            sid = json.loads(_SESSION.read_text()).get("sessionid")
        except Exception:  # noqa: BLE001
            sid = None
    return sid


def split_codes(csv):
    """'TATAMOTORS, lt ,,' -> ['TATAMOTORS', 'LT'] (order kept, deduped)."""
    out, seen = [], set()
    for c in str(csv or "").replace(";", ",").split(","):
        c = c.strip().upper()
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def universe(board, top=100, codes=""):
    """(codes, names) — board top-N by composite, then any --codes. PURE."""
    rows = [r for r in (board or []) if isinstance(r, dict) and str(r.get("code") or "").strip()]
    rows.sort(key=lambda r: r.get("composite") or 0, reverse=True)
    names = {str(r["code"]).strip(): str(r.get("name") or "").strip() for r in rows}
    out, seen = [], set()
    for r in rows[:max(0, int(top or 0))]:
        c = str(r["code"]).strip()
        if c not in seen:
            seen.add(c)
            out.append(c)
    for c in split_codes(codes):
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out, names


def fact_count(analysis):
    """Grounded facts in an analysis block (themes + commitments). PURE."""
    a = analysis if isinstance(analysis, dict) else {}
    themes = a.get("themes") if isinstance(a.get("themes"), dict) else {}
    n = sum(len(v or []) for v in themes.values() if isinstance(v, (list, tuple)))
    return n + len(a.get("management_commitments") or [])


def build_bundle(code, name, docs, analysis, today):
    """analyse_documents() output -> the on-disk contract bundle. PURE."""
    a = dict(analysis or {})
    meta = dict(a.pop("_meta", None) or {})
    err = str(a.pop("error", "") or "")
    note = str(meta.get("note") or "")
    if err and err not in note:
        note = (note + " " + err).strip() if note else err
    raw_themes = a.get("themes") if isinstance(a.get("themes"), dict) else {}
    themes = {t: list(raw_themes.get(t) or []) for t in da.THEMES}
    cov = a.get("coverage") if isinstance(a.get("coverage"), dict) else {}
    analysed = cov.get("docs_analysed")
    return {
        "code": str(code or ""),
        "name": str(name or "").strip() or str(code or ""),
        "generated_at": today,
        "documents": [d for d in (docs or []) if isinstance(d, dict)],
        "analysis": {
            "summary": str(a.get("summary") or ""),
            "themes": themes,
            "management_commitments": list(a.get("management_commitments") or []),
            "coverage": {"docs_analysed": int(analysed or 0),
                         "kinds": list(cov.get("kinds") or [])},
        },
        "_meta": {
            "model": str(meta.get("model") or ""),
            "grounded": True,
            "facts_kept": int(meta.get("facts_kept") or 0),
            "facts_dropped_ungrounded": int(meta.get("facts_dropped_ungrounded") or 0),
            "note": note[:400],
        },
    }


def main():
    ap = argparse.ArgumentParser(
        description="Bake grounded document insights (What management said) for the public site")
    ap.add_argument("--top", type=int, default=100, help="how many top board stocks to bake")
    ap.add_argument("--codes", default="", help="extra codes to bake, comma separated")
    ap.add_argument("--docs", type=int, default=3, help="documents read per company (LLM calls)")
    ap.add_argument("--limit", type=int, default=0, help="cap (testing)")
    ap.add_argument("--skip-existing", action="store_true",
                    help="skip codes already baked today (resumable; stamp read from the file)")
    ap.add_argument("--board", default=str(ROOT / "docs" / "data" / "technofunda.json"))
    ap.add_argument("--out", default=str(ROOT / "docs" / "data" / "docs"))
    ap.add_argument("--max-minutes", type=float, default=0,
                    help="stop baking after N minutes (0=no limit) so the cloud commits incrementally")
    args = ap.parse_args()

    if not ia.have_key():
        print("[docs] no Gemini API key — set GEMINI_API_KEY or add gemin_api_key.md "
              "(kept local, never published); skipping the document-insight bake")
        return

    codes, names = universe(_read_board(Path(args.board)), args.top, args.codes)
    if args.limit:
        codes = codes[:args.limit]
    if not codes:
        print(f"[docs] nothing to bake (board empty/unreadable: {args.board}, no --codes)")
        return

    sid = _sid()
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    done, fail, skipped, thin = 0, 0, 0, 0
    today = time.strftime("%Y-%m-%d")
    _bake_start = time.time()
    for i, code in enumerate(codes, 1):
        if args.max_minutes and (time.time() - _bake_start) > args.max_minutes * 60:
            print(f"[docs] time budget {args.max_minutes:.0f}min reached at "
                  f"{i}/{len(codes)} - committing what is baked"); break
        bf = out / f"{code}.json"
        if _skip_baked(bf, today, args.skip_existing):
            skipped += 1; continue
        try:
            docs = dm.fetch_documents(code, sid)
            if not docs:
                thin += 1
                if i <= 8:
                    print(f"  [{i}/{len(codes)}] no documents for {code}")
                continue
            ana = da.analyse_documents(docs, names.get(code) or code, limit=args.docs)
            kept = fact_count(ana)
            if not kept:
                # never publish an empty bundle over a good one — keep last good
                thin += 1
                print(f"  [{i}/{len(codes)}] no grounded facts for {code}"
                      f"{': ' + str(ana.get('error'))[:70] if ana.get('error') else ''}")
                continue
            _atomic(bf, json.dumps(build_bundle(code, names.get(code), docs, ana, today),
                                   separators=(",", ":")))
            done += 1
            if i <= 8 or i % 25 == 0:
                print(f"  [{i}/{len(codes)}] baked {code} — {kept} grounded facts "
                      f"from {len(docs)} documents")
        except Exception as e:  # noqa: BLE001
            fail += 1
            print(f"  [{i}/{len(codes)}] FAIL {code}: {type(e).__name__}")
        time.sleep(0.15)

    print(f"[docs] baked {done}, skipped {skipped}, no-facts {thin}, failed {fail} -> {out}")


if __name__ == "__main__":
    main()
