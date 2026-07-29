"""
Pre-bake the BULL-vs-BEAR debate for the board's top stocks, so the static
GitHub Pages site can show a full argument without ever calling a model.

The site is static: "Run Debate" in the UI REVEALS what this script baked, it
never generates anything. The model runs here, server-side, once per company per
day — there is no runtime endpoint and no key ever reaches a browser.

For each target code it writes docs/data/debate/<CODE>.json:

    {"code","name","generated_at","sector":{"name","signal","score"},
     "debate":{…whatever earnings_intel.data.debate.run_debate produced…},
     "evidence":[…the deterministic evidence pack the debate argued over…],
     "_meta":{"model","provider","rounds","turns","points","evidence_items","note",
              …plus the debate module's own grounding counts, passed through…}}

Evidence comes from three already-baked artefacts — the fundamental bundle
(docs/data/fundamental/<CODE>.json), the grounded filing facts
(docs/data/docs/<CODE>.json) and the company's sector row
(docs/data/sector_tailwind.json) — so the debate argues over numbers and
verbatim quotes this repo already stands behind.

    python scripts/refresh_debate.py                       # board top 100
    python scripts/refresh_debate.py --top 25 --rounds 2
    python scripts/refresh_debate.py --codes TATAMOTORS,LT --top 0 --force
    python scripts/refresh_debate.py --max-minutes 40      # cloud: commit incrementally
    python scripts/refresh_debate.py --skip-any --top 6000 --max-minutes 30   # coverage pass

THIS IS THE ONLY LLM-PRICED BOARD, so the defaults are the cheap ones: the top
100 only, and any company already debated TODAY is skipped (pass --force to pay
for it again). Every company prints a running count and the minutes spent, so a
budget overrun is visible in the log instead of on the bill.

A company whose debate comes back empty is NOT written — the last good debate
stays on disk. With no LLM credentials configured the bake prints why and exits
0; the daily job must not fail just because no key is set on that machine.
"""
from __future__ import annotations

import argparse
import inspect
import json
import logging
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

log = logging.getLogger("technofunda.refresh_debate")

_SESSION = ROOT / "screener_session.json"
_STAMP_RE = re.compile(rb'"generated_at"\s*:\s*"(\d{4}-\d{2}-\d{2})"')
_BAD_KW_RE = re.compile(r"unexpected keyword argument '([^']+)'")

# What "the model actually said something" looks like, whatever shape the debate
# module settles on. Lists count per item, a filled dict/str counts once.
_ARG_KEYS = ("rounds", "turns", "exchanges", "messages", "arguments",
             "bull", "bear", "bull_case", "bear_case", "points")
_VERDICT_KEYS = ("verdict", "judge", "conclusion", "synthesis", "summary")

# _meta fields this script owns; everything else the debate module reports — the
# grounding accounting (cites_invalid, claims_stripped, quotes_unverified, …) —
# is passed through untouched, because that is the evidence the board is honest.
_META_OWN = ("model", "provider", "rounds", "turns", "points", "evidence_items", "note")


# ------------------------------------------------------------------- plumbing
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


def _read_json(path):
    """Parse one sibling artefact, or None — a half-written file must not stop the bake."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))
    except Exception:  # noqa: BLE001
        return None


def _atomic(path, text):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _baked_on(path):
    """Date a debate was baked, from the generated_at stamp INSIDE the file.

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


def _skip_baked(path, today, enabled=True, any_existing=False):
    """Skip decision for one code. PURE.

    Two modes, because this board has two jobs. The daily job KEEPS the top names
    current, so "already debated today" is the right skip. The coverage job walks
    the rest of the universe a slice at a time and must never pay twice for a
    company it already argued, whatever day that was — that is ``any_existing``.
    """
    p = Path(path)
    if any_existing:
        return p.exists() and bool(_baked_on(p))
    return bool(enabled) and p.exists() and _baked_on(p) == today


# Provider signatures for "you are rate-limited / out of quota / we are overloaded".
# Matched on the TEXT because every provider wraps these differently and the
# adapter deliberately raises a plain RuntimeError (GeminiBusy) rather than a
# provider-specific type the scripts would have to import.
# The digit guards on the status codes are load-bearing: this repo uses BSE
# numeric codes as company codes (506597, 530477, ...) and that text reaches
# this function inside error strings, so an unanchored "429" matches inside
# 504293 and reclassifies a real crash as "the provider is just busy" -- the
# exact direction of error that hides an outage. A word boundary cannot be used
# here: written through a non-raw string it becomes a literal backspace (0x08),
# and the alternative then matches nothing at all (which is how it shipped).
_QUOTA_RE = re.compile(
    r"geminibusy|resource_exhausted|rate.?limit|quota|too many requests|overload"
    r"|(?<![0-9])(?:429|503)(?![0-9])|unavailable|(?<![a-z])busy(?![a-z])", re.I)


def _is_quota(text):
    """Does this failure mean 'try again later' rather than 'this is broken'? PURE."""
    return bool(_QUOTA_RE.search(str(text or "")))


def _zero_verdict(done, attempted, busy, covered, floor=5):
    """Nothing baked — is the daily build red? PURE.

    Three outcomes, because "zero debates today" has two very different causes
    and only one of them is worth waking someone up for:

      "ok"        too few attempts to conclude anything, or work got done
      "transient" the provider hit its quota/overload ceiling AND companies
                  already have debates on disk, so the site is still serving.
                  Going red daily for a condition that clears on its own trains
                  the operator to ignore red — the same reason the Vahan crawl
                  exits 0 when it is IP-blocked but has prior data.
      "outage"    anything else: a broken chain, or a quota wall with NOTHING
                  ever baked (nothing is live, so silence would hide it).

    A quota wall must still go red when it is the MINORITY explanation — one
    rate-limited company among four real crashes is a bug, not a busy provider.
    """
    if done or attempted < floor:
        return "ok"
    if busy * 2 >= attempted and covered:
        return "transient"
    return "outage"


def _sid():
    sid = os.environ.get("SCREENER_SESSIONID")
    if not sid and _SESSION.exists():
        try:
            sid = json.loads(_SESSION.read_text()).get("sessionid")
        except Exception:  # noqa: BLE001
            sid = None
    return sid


def have_llm():
    """Credentialled providers, or [] — the import is lazy and never raises."""
    try:
        from earnings_intel import llm
        return [str(p) for p in (llm.available() or [])]
    except Exception as e:  # noqa: BLE001
        log.debug("llm.available() unusable: %s", type(e).__name__)
        return []


# ------------------------------------------------------------------- universe
def split_codes(csv):
    """'TATAMOTORS, lt ,,' -> ['TATAMOTORS', 'LT'] (order kept, deduped). PURE."""
    out, seen = [], set()
    for c in str(csv or "").replace(";", ",").split(","):
        c = c.strip().upper()
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def universe(board, top=100, codes=""):
    """(codes, rows) — board top-N by composite, then any --codes. PURE.

    `rows` maps code -> its board row, which is where the company's name and
    sector come from (the fundamental bundle carries neither reliably).
    """
    rows = [r for r in (board or []) if isinstance(r, dict) and str(r.get("code") or "").strip()]
    rows.sort(key=lambda r: r.get("composite") or 0, reverse=True)
    by_code = {str(r["code"]).strip(): r for r in reversed(rows)}   # first row wins
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
    return out, by_code


def extend_universe(codes, fdir, cap):
    """Append on-disk fundamental codes until `cap`, keeping order. PURE.

    The board (technofunda.json) only ranks a few hundred names, but the site
    publishes a bundle for every listed company — so "debate the whole platform"
    cannot be expressed as a bigger --top against the board alone. This walks
    docs/data/fundamental/*.json, which IS the platform's universe, and appends
    whatever the board never ranked.
    """
    out = list(codes or [])
    seen = set(out)
    cap = max(0, int(cap or 0))
    if len(out) >= cap:
        return out
    try:
        paths = sorted(Path(fdir).glob("*.json"))
    except Exception:  # noqa: BLE001
        return out
    for path in paths:
        if len(out) >= cap:
            break
        code = path.stem.strip().upper()
        if code and code != "INDEX" and code not in seen:
            seen.add(code)
            out.append(code)
    return out


def write_index(out_dir):
    """docs/data/debate/index.json — which codes have a debate, from what is ON DISK.

    The page fetches this first, so a company with no debate shows the honest
    empty state instead of firing a 404 on every view. Built by globbing rather
    than from this run's counters, so a partial run (time budget hit) still
    publishes an index that matches reality.
    """
    d = Path(out_dir)
    rows = []
    for path in sorted(d.glob("*.json")):
        if path.stem == "index":
            continue
        rows.append({"code": path.stem, "generated_at": _baked_on(path)})
    _atomic(d / "index.json", json.dumps(
        {"generated_at": time.strftime("%Y-%m-%d"), "count": len(rows), "codes": rows},
        separators=(",", ":")))
    return len(rows)


def board_field(rows, code, field):
    """One field of a code's board row ('' when the code is not on the board). PURE."""
    r = (rows or {}).get(code)
    return str((r or {}).get(field) or "").strip() if isinstance(r, dict) else ""


# ------------------------------------------------------------ evidence inputs
def company_bundle(raw, code="", name=""):
    """docs/data/fundamental/<CODE>.json -> the ONE flat dict the debate reads. PURE.

    The file nests the Screener block under "fundamental" and keeps prices/signal
    beside it; the debate wants overview/growth/quarters/profit_loss/
    balance_sheet/cash_flow/ratios/shareholding/pros/cons/analysis PLUS prices,
    signal and upstox_ratios in one place. Returns {} when the bundle is missing
    or carries no fundamentals — a debate with no numbers is not worth a token.
    """
    raw = raw if isinstance(raw, dict) else {}
    fund = raw.get("fundamental")
    if not isinstance(fund, dict) or not fund:
        # tolerate a bundle that is already flat (no "fundamental" wrapper)
        fund = raw if (raw.get("overview") or raw.get("quarters")) else {}
    if not fund:
        return {}
    out = dict(fund)
    out["code"] = str(code or fund.get("code") or "").strip()
    out["name"] = str(fund.get("name") or name or "").strip() or out["code"]
    for k in ("prices", "signal", "upstox_ratios"):
        v = raw.get(k) if isinstance(raw.get(k), dict) else out.get(k)
        out[k] = v if isinstance(v, dict) else {}
    out["generated_at"] = str(raw.get("generated_at") or "")
    return out


def filings_for(raw):
    """docs/data/docs/<CODE>.json -> the grounded filing facts, or None. PURE.

    Every fact already carries a verbatim quote + url, so the debate can cite
    management instead of paraphrasing it. Both readings are served — the themes
    sit at the top level AND under "analysis" — because this file is the contract
    between two modules and a renamed key must not silently drop the quotes.
    """
    raw = raw if isinstance(raw, dict) else {}
    ana = raw.get("analysis") if isinstance(raw.get("analysis"), dict) else {}
    docs = [d for d in (raw.get("documents") or []) if isinstance(d, dict)]
    if not ana and not docs:
        return None
    out = dict(ana)
    out["analysis"] = ana
    out["documents"] = docs
    out["generated_at"] = str(raw.get("generated_at") or "")
    return out


def sector_row(tailwind, name):
    """The company's row in docs/data/sector_tailwind.json, or None. PURE."""
    if not name:
        return None
    rows = tailwind.get("sectors") if isinstance(tailwind, dict) else tailwind
    want = str(name).strip().casefold()
    for r in rows or []:
        if isinstance(r, dict) and str(r.get("sector") or "").strip().casefold() == want:
            return dict(r)
    return None


def sector_brief(row, name=""):
    """{name, signal, score} for the baked file — the row itself goes to the model. PURE."""
    r = row if isinstance(row, dict) else {}
    nm = str(r.get("sector") or name or "").strip()
    if not nm:
        return None
    return {"name": nm, "signal": str(r.get("signal") or ""), "score": r.get("score")}


# ------------------------------------------- calling the sibling debate module
def _fit_kwargs(fn, kwargs):
    """Drop kwargs the callee cannot accept, when its signature can be read. PURE."""
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):          # builtins / C callables / odd wrappers
        return dict(kwargs)
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return dict(kwargs)
    return {k: v for k, v in kwargs.items() if k in params}


def call_tolerant(fn, arg, **kwargs):
    """fn(arg, **kwargs), tolerating a signature that differs slightly from the spec.

    earnings_intel/data/debate.py is written by a different pass; a missing
    `provider=` or a renamed `sector=` must degrade to a smaller call, never to a
    dead board. Kwargs are filtered against the real signature first, then dropped
    one at a time as the callee names them. A TypeError raised INSIDE the callee
    is re-raised untouched — it is a bug, not a signature mismatch, and retrying
    it would burn tokens hiding it.
    """
    kw = _fit_kwargs(fn, {k: v for k, v in kwargs.items() if v is not None})
    for _ in range(len(kw) + 1):
        try:
            return fn(arg, **kw)
        except TypeError as e:
            m = _BAD_KW_RE.search(str(e))
            name = m.group(1) if m else ""
            if name not in kw:
                raise
            log.debug("debate callee rejected %s= — retrying without it", name)
            kw.pop(name)
    return fn(arg)


def evidence_for(mod, bundle, **kwargs):
    """debate.evidence_pack(...) -> list[dict]. Deterministic, no model call, never raises."""
    fn = getattr(mod, "evidence_pack", None)
    if not callable(fn):
        return []
    try:
        out = call_tolerant(fn, bundle, **kwargs)
    except Exception as e:  # noqa: BLE001 - evidence is a bonus, never a blocker
        log.debug("evidence_pack failed: %s", type(e).__name__)
        return []
    return [x for x in (out or []) if isinstance(x, dict)]


def run_debate(mod, bundle, **kwargs):
    """debate.run_debate(...) -> dict, always. Exceptions belong to the caller's counter."""
    fn = getattr(mod, "run_debate", None)
    if not callable(fn):
        return {"error": "earnings_intel.data.debate.run_debate is missing"}
    out = call_tolerant(fn, bundle, **kwargs)
    if isinstance(out, dict):
        return out
    return {"error": f"run_debate returned {type(out).__name__}, expected dict"}


# ------------------------------------------------------------- what we publish
def debate_points(debate):
    """How much ACTUAL argument a run produced — 0 means 'do not publish'. PURE.

    Shape-agnostic on purpose: the debate module owns its structure, this only
    has to answer "did the model say anything?". A rounds COUNT (int) is not
    content, a rounds LIST is.
    """
    d = debate if isinstance(debate, dict) else {}
    n = 0
    for k in _ARG_KEYS + _VERDICT_KEYS:
        v = d.get(k)
        if isinstance(v, (list, tuple)):
            n += sum(1 for x in v if x)
        elif isinstance(v, dict) and v:
            n += 1
        elif isinstance(v, str) and v.strip():
            n += 1
    return n


def _turn_list(debate):
    """The debate's turn/round sequence, or []. PURE."""
    for k in ("rounds", "turns", "exchanges"):
        v = debate.get(k)
        if isinstance(v, (list, tuple)):
            return [x for x in v if x]
    return []


def rounds_run(debate, requested=0, points=0):
    """Rounds the model ACTUALLY ran, whatever shape it reported them in. PURE.

    debate.py returns "rounds" as a flat list of TURNS (bull and bear each get
    one per round), each stamped with its round number — so len() is the turn
    count, not the round count, and publishing it as "rounds" would double every
    number an operator uses to reason about spend.
    """
    d = debate if isinstance(debate, dict) else {}
    got = d.get("rounds_run")
    if isinstance(got, int):
        return got
    seq = _turn_list(d)
    if seq:
        nums = [x.get("round") for x in seq
                if isinstance(x, dict) and isinstance(x.get("round"), int)]
        return max(nums) if nums else len(seq)
    return int(requested or 0) if points else 0


def build_bundle(code, name, result, *, sector=None, evidence=None, rounds=3, today=""):
    """run_debate() output -> the on-disk contract bundle. PURE.

    The model's own "_meta"/"error" are lifted into _meta so the published
    "debate" block is nothing but the argument, and the evidence is hoisted to
    the top level whichever side produced it.
    """
    r = dict(result or {})
    meta = dict(r.pop("_meta", None) or {})
    err = str(r.pop("error", "") or "")
    note = str(meta.get("note") or "")
    if err and err not in note:
        note = (note + " " + err).strip() if note else err
    ev = r.pop("evidence", None)
    ev = [x for x in (ev if isinstance(ev, (list, tuple)) else evidence or []) if isinstance(x, dict)]
    pts = debate_points(r)
    # Rounds RUN, never rounds asked for: a run that produced nothing ran zero,
    # whatever --rounds said, so _meta cannot claim work that never happened.
    got = rounds_run(r, rounds, pts)
    r.pop("rounds_run", None)
    return {
        "code": str(code or ""),
        "name": str(name or "").strip() or str(code or ""),
        "generated_at": today,
        "sector": sector if isinstance(sector, dict) else None,
        "debate": r,
        "evidence": ev,
        "_meta": {
            "model": str(meta.get("model") or r.get("model") or ""),
            "provider": str(meta.get("provider") or r.get("provider") or ""),
            "rounds": int(got or 0),
            "turns": len(_turn_list(r)),
            "points": pts,
            "evidence_items": len(ev),
            # the module's own grounding counts survive verbatim — dropping them
            # would publish a debate with no record of what was struck from it
            **{k: v for k, v in meta.items() if k not in _META_OWN},
            "note": note[:400],
        },
    }


# ----------------------------------------------------------------------- bake
def main():
    ap = argparse.ArgumentParser(
        description="Bake the bull-vs-bear debate for the public site (server-side LLM, "
                    "static output — the site never calls a model)")
    ap.add_argument("--top", type=int, default=100,
                    help="how many top board stocks to debate (this board is LLM-priced)")
    ap.add_argument("--codes", default="", help="extra codes to debate, comma separated")
    ap.add_argument("--rounds", type=int, default=3, help="debate rounds per company (LLM calls)")
    ap.add_argument("--limit", type=int, default=0, help="cap (testing)")
    ap.add_argument("--provider", default="", help="pin an LLM provider (default: first credentialled)")
    ap.add_argument("--skip-existing", dest="skip_existing", action="store_true", default=True,
                    help="skip codes already debated today (DEFAULT ON; stamp read from the file)")
    ap.add_argument("--force", dest="skip_existing", action="store_false",
                    help="re-debate codes already done today — this costs tokens again")
    ap.add_argument("--skip-any", dest="skip_any", action="store_true",
                    help="coverage pass: extend the universe past the board into every "
                         "baked company and skip anything ALREADY debated on any day")
    ap.add_argument("--board", default=str(ROOT / "docs" / "data" / "technofunda.json"))
    ap.add_argument("--fundamental", default=str(ROOT / "docs" / "data" / "fundamental"))
    ap.add_argument("--docs", default=str(ROOT / "docs" / "data" / "docs"))
    ap.add_argument("--sectors", default=str(ROOT / "docs" / "data" / "sector_tailwind.json"))
    ap.add_argument("--out", default=str(ROOT / "docs" / "data" / "debate"))
    ap.add_argument("--max-minutes", type=float, default=0,
                    help="stop after N minutes (0=no limit) so the cloud commits incrementally")
    args = ap.parse_args()

    providers = have_llm()
    if not providers:
        print("[debate] no LLM credentials configured (GEMINI_API_KEY / OPENAI_API_KEY / "
              "ANTHROPIC_API_KEY / DEEPSEEK_API_KEY / a local Ollama) — skipping the debate bake")
        return 0

    codes, rows = universe(_read_board(Path(args.board)), args.top, args.codes)
    if args.skip_any:
        codes = extend_universe(codes, Path(args.fundamental), args.top)
    if args.limit:
        codes = codes[:args.limit]
    if not codes:
        print(f"[debate] nothing to bake (board empty/unreadable: {args.board}, no --codes)")
        return 0

    try:
        from earnings_intel.data import debate as db      # lazy: sibling module, may lag
    except Exception as e:  # noqa: BLE001
        print(f"[debate] earnings_intel/data/debate.py not importable "
              f"({type(e).__name__}: {e}) — skipping the debate bake")
        return 0

    tailwind = _read_json(Path(args.sectors))
    fdir, ddir = Path(args.fundamental), Path(args.docs)
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    today = time.strftime("%Y-%m-%d")
    done = fail = skipped = thin = nobundle = busy = 0
    start = time.time()
    print(f"[debate] {len(codes)} companies, {args.rounds} rounds, provider "
          f"{args.provider or providers[0]} (credentialled: {', '.join(providers)})"
          f"{' — re-debating everything (--force)' if not args.skip_existing else ''}")

    for i, code in enumerate(codes, 1):
        spent = (time.time() - start) / 60.0
        if args.max_minutes and spent > args.max_minutes:
            print(f"[debate] time budget {args.max_minutes:.0f}min reached at "
                  f"{i}/{len(codes)} — committing what is baked"); break
        bf = out / f"{code}.json"
        if _skip_baked(bf, today, args.skip_existing, args.skip_any):
            skipped += 1; continue
        bundle = company_bundle(_read_json(fdir / f"{code}.json"), code,
                                board_field(rows, code, "name"))
        if not bundle:
            nobundle += 1
            if nobundle <= 8:
                print(f"  [{i}/{len(codes)}] no fundamental bundle for {code} — nothing to argue over")
            continue
        try:
            filings = filings_for(_read_json(ddir / f"{code}.json"))
            sec_name = board_field(rows, code, "sector")
            row = sector_row(tailwind, sec_name)
            ev = evidence_for(db, bundle, filings=filings, sector=row)
            t0 = time.time()
            result = run_debate(db, bundle, filings=filings, sector=row,
                                rounds=args.rounds, provider=args.provider or None)
            took = time.time() - t0
            b = build_bundle(code, bundle.get("name"), result, sector=sector_brief(row, sec_name),
                             evidence=ev, rounds=args.rounds, today=today)
            if not b["_meta"]["points"]:
                # never publish an empty debate over a good one — keep last good
                thin += 1
                if _is_quota(b["_meta"]["note"]):
                    busy += 1
                print(f"  [{i}/{len(codes)}] no debate for {code}"
                      f"{': ' + b['_meta']['note'][:70] if b['_meta']['note'] else ''}")
                continue
            _atomic(bf, json.dumps(b, separators=(",", ":")))
            done += 1
            print(f"  [{i}/{len(codes)}] {code}: {b['_meta']['turns']} turns over "
                  f"{b['_meta']['rounds']} rounds, {b['_meta']['evidence_items']} evidence, "
                  f"{took:.1f}s — {done} baked / {spent:.1f}min spent")
        except Exception as e:  # noqa: BLE001
            fail += 1
            if _is_quota(f"{type(e).__name__} {e}"):
                busy += 1
            print(f"  [{i}/{len(codes)}] FAIL {code}: {type(e).__name__}")
        time.sleep(0.15)

    mins = (time.time() - start) / 60.0
    covered = write_index(out)
    print(f"[debate] baked {done}, skipped {skipped}, no-debate {thin}, no-bundle {nobundle}, "
          f"failed {fail}{f' ({busy} rate-limited/out of quota)' if busy else ''} in "
          f"{mins:.1f}min -> {out} ({covered} companies have a debate)")
    if nobundle and not _sid():
        print("[debate] tip: the missing companies have no docs/data/fundamental/<CODE>.json — "
              "run scripts/refresh_fundamentals.py first (SCREENER_SESSIONID is not set either)")

    # A handful of companies genuinely produce nothing. EVERY company producing
    # nothing means something is wrong upstream — but WHICH thing decides whether
    # the daily build should go red. See _zero_verdict.
    attempted = done + thin + fail
    verdict = _zero_verdict(done, attempted, busy, covered)
    if verdict == "transient":
        print(f"[debate] WARNING: {attempted} companies attempted and NOT ONE produced a "
              f"debate — {busy} hit the provider's quota/overload ceiling. This is the free "
              f"tier's daily cap, not a code fault: {covered} companies already have a debate "
              f"and stay live on the site. Exiting 0 so the daily build is not red every day "
              f"for a condition that clears on its own — raise the quota or add a second "
              f"provider key to bake more per run.")
        return 0
    if verdict == "outage":
        why = ("the provider is refusing every request AND no company has ever been debated"
               if busy else "the model chain is broken, not the data")
        print(f"[debate] ERROR: {attempted} companies attempted and NOT ONE produced a "
              f"debate — {why}. Check the failures above.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
