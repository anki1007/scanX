"""
Banking Data publisher.

Builds per-bank key-metric heatmap matrices across fiscal years from the
per-company fundamental bundles scanX has already baked
(docs/data/fundamental/<CODE>.json) — no network, no session needed.
Replicates the core of financiallyfree.in's Looker banking dashboard.

Writes docs/data/banking.json (per-metric matrices: banks x fiscal years)
plus docs/data/banking_meta.json. Consumed by docs/banking.html.

    python scripts/refresh_banking.py
"""
from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IST = timezone(timedelta(hours=5, minutes=30))

# Curated bank/NBFC universe (code, group). Codes must have a baked bundle in
# docs/data/fundamental/ — missing ones are skipped with a log line.
UNIVERSE = [
    # Private banks
    ("HDFCBANK", "PRIVATE"), ("ICICIBANK", "PRIVATE"), ("AXISBANK", "PRIVATE"),
    ("KOTAKBANK", "PRIVATE"), ("INDUSINDBK", "PRIVATE"), ("FEDERALBNK", "PRIVATE"),
    ("IDFCFIRSTB", "PRIVATE"), ("YESBANK", "PRIVATE"), ("BANDHANBNK", "PRIVATE"),
    ("RBLBANK", "PRIVATE"), ("CSBBANK", "PRIVATE"), ("KARURVYSYA", "PRIVATE"),
    ("CUB", "PRIVATE"), ("SOUTHBANK", "PRIVATE"), ("J&KBANK", "PRIVATE"),
    ("KTKBANK", "PRIVATE"), ("DCBBANK", "PRIVATE"), ("TMB", "PRIVATE"),
    # PSU banks
    ("SBIN", "PSU"), ("BANKBARODA", "PSU"), ("PNB", "PSU"), ("CANBK", "PSU"),
    ("UNIONBANK", "PSU"), ("INDIANB", "PSU"), ("IOB", "PSU"), ("MAHABANK", "PSU"),
    ("UCOBANK", "PSU"), ("CENTRALBK", "PSU"), ("PSB", "PSU"), ("IDBI", "PSU"),
    # Small finance banks
    ("AUBANK", "SFB"), ("UJJIVANSFB", "SFB"), ("EQUITASBNK", "SFB"),
    # NBFCs / HFCs
    ("BAJFINANCE", "NBFC"), ("CHOLAFIN", "NBFC"), ("SHRIRAMFIN", "NBFC"),
    ("LICHSGFIN", "NBFC"), ("M&MFIN", "NBFC"), ("SUNDARMFIN", "NBFC"),
    ("MUTHOOTFIN", "NBFC"), ("MANAPPURAM", "NBFC"), ("PFC", "NBFC"),
    ("RECLTD", "NBFC"), ("IREDA", "NBFC"), ("CANFINHOME", "NBFC"),
    ("PNBHOUSING", "NBFC"), ("ABCAPITAL", "NBFC"), ("POONAWALLA", "NBFC"),
]

GROUPS = ["PRIVATE", "PSU", "SFB", "NBFC"]

# (key, label, unit). Screener's "Financing Margin %" = Financing Profit /
# Revenue (banks' financing-spread proxy), NOT the classic NIM-on-assets.
METRICS = [
    ("fin_margin", "Financing Margin %", "%"),
    ("np", "Net Profit", "₹ Cr"),
    ("np_yoy", "Net Profit YoY %", "%"),
    ("eps", "EPS", "₹"),
    ("rev_yoy", "Revenue YoY %", "%"),
    ("roe", "ROE %", "%"),
    ("roa", "ROA % (NP / Total Assets)", "%"),
    ("np_margin", "Net Profit Margin %", "%"),
    ("q_np_yoy", "Latest Qtr NP YoY %", "%"),
]

_MONTHS = {"Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
           "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12}
_NUM_RE = re.compile(r"^-?\d+(?:\.\d+)?$")
_FY_RE = re.compile(r"^([A-Za-z]{3})\s+(\d{4})$")   # full FY only, not 'Mar 2017 9m'


def _atomic(path, text):
    tmp = path.with_suffix(path.suffix + ".tmp"); tmp.write_text(text, encoding="utf-8"); os.replace(tmp, path)


def parse_num(v):
    """Screener cell -> float|None. Handles '48,470', '14%', '-6,547', '', None."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "").replace("₹", "").replace("%", "").strip()
    if not s or s in {"-", "—", "–"}:
        return None
    if not _NUM_RE.match(s):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def growth_pct(cur, prev):
    """YoY growth %, None-safe; None when prev is missing or zero."""
    if cur is None or prev is None or prev == 0:
        return None
    return round((cur - prev) / abs(prev) * 100.0, 1)


def is_fy_label(label):
    """True for clean fiscal-year labels ('Mar 2015'); False for partial
    periods like 'Mar 2017 9m' that newly-listed banks carry."""
    return bool(_FY_RE.match(str(label).strip()))


def year_key(label):
    """'Mar 2015' -> (2015, 3) for chronological ordering; unknowns sort last."""
    m = _FY_RE.match(str(label).strip())
    if not m or m.group(1) not in _MONTHS:
        return (9999, 99)
    return (int(m.group(2)), _MONTHS[m.group(1)])


def _table(fund, key):
    t = fund.get(key) or {}
    return (t.get("headers") or [], t.get("rows") or {})


def _row_map(headers, rows, label):
    vals = rows.get(label)
    if not vals:
        return {}
    return {y: parse_num(v) for y, v in zip(headers, vals) if is_fy_label(y)}


def yoy_map(m, headers):
    """YoY growth per fiscal year; only between consecutive fiscal years so a
    dropped partial period ('Mar 2017 9m') never yields a 2-year 'YoY'."""
    hs = [h for h in headers if is_fy_label(h)]
    out = {}
    for i in range(1, len(hs)):
        if year_key(hs[i])[0] - year_key(hs[i - 1])[0] != 1:
            continue
        g = growth_pct(m.get(hs[i]), m.get(hs[i - 1]))
        if g is not None:
            out[hs[i]] = g
    return out


def extract_bank(bundle, code, group):
    """Bundle dict -> {code, name, group, series:{metric:{year:val}}}.

    Tolerates missing tables/rows — a metric with no inputs is simply empty.
    """
    fund = (bundle or {}).get("fundamental") or {}
    name = fund.get("name") or code
    ph, prows = _table(fund, "profit_loss")
    fin = _row_map(ph, prows, "Financing Margin %")
    rev = _row_map(ph, prows, "Revenue")
    np_ = _row_map(ph, prows, "Net Profit")
    eps = _row_map(ph, prows, "EPS in Rs")
    bh, brows = _table(fund, "balance_sheet")
    ta = _row_map(bh, brows, "Total Assets")
    rh, rrows = _table(fund, "ratios")
    roe = _row_map(rh, rrows, "ROE %")

    roa = {}
    for y, npv in np_.items():
        t = ta.get(y)
        if npv is not None and t:
            roa[y] = round(npv / t * 100.0, 2)
    npm = {}
    for y, npv in np_.items():
        r = rev.get(y)
        if npv is not None and r:
            npm[y] = round(npv / r * 100.0, 1)

    qh, qrows = _table(fund, "quarters")
    qnp = [parse_num(v) for v in (qrows.get("Net Profit") or [])]
    q_yoy = {}
    if len(qnp) >= 5:
        g = growth_pct(qnp[-1], qnp[-5])          # same quarter last year
        if g is not None:
            q_yoy["Latest Qtr"] = g

    series = {"fin_margin": fin, "np": np_, "np_yoy": yoy_map(np_, ph),
              "eps": eps, "rev_yoy": yoy_map(rev, ph), "roe": roe,
              "roa": roa, "np_margin": npm, "q_np_yoy": q_yoy}
    series = {k: {y: v for y, v in m.items() if v is not None} for k, m in series.items()}
    return {"code": code, "name": name, "group": group, "series": series,
            "latest_q": qh[-1] if qh else None}


def build_matrices(banks, max_years=10):
    """Per-metric matrix: {metric,label,unit,years,banks:[{code,name,group,values}]}."""
    out = []
    for key, label, unit in METRICS:
        if key == "q_np_yoy":
            years = ["Latest Qtr"]
        else:
            yset = set()
            for b in banks:
                yset.update(b["series"].get(key, {}))
            years = sorted(yset, key=year_key)[-max_years:]
        rows = []
        for b in banks:
            m = b["series"].get(key, {})
            vals = [m.get(y) for y in years]
            if any(v is not None for v in vals):
                rows.append({"code": b["code"], "name": b["name"],
                             "group": b["group"], "values": vals})
        if years and rows:
            out.append({"metric": key, "label": label, "unit": unit,
                        "years": years, "banks": rows})
    return out


def main():
    ap = argparse.ArgumentParser(description="Banking heatmap feed (local bundles, no network)")
    ap.add_argument("--out", default=str(ROOT / "docs" / "data"))
    ap.add_argument("--src", default=str(ROOT / "docs" / "data" / "fundamental"))
    ap.add_argument("--years", type=int, default=10, help="max fiscal years per matrix")
    args = ap.parse_args()

    src = Path(args.src)
    banks, skipped = [], []
    for code, group in UNIVERSE:
        p = src / f"{code}.json"
        if not p.exists():
            skipped.append(code); print(f"[bank] skip {code} (no bundle)"); continue
        try:
            bundle = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            skipped.append(code); print(f"[bank] skip {code} (bad bundle: {e})"); continue
        b = extract_bank(bundle, code, group)
        if not any(b["series"].values()):
            skipped.append(code); print(f"[bank] skip {code} (no usable tables)"); continue
        banks.append(b)

    matrices = build_matrices(banks, max_years=args.years)
    now = datetime.now(IST)
    data = {"generated_at_ist": now.strftime("%Y-%m-%d %H:%M:%S IST"),
            "metrics": matrices, "groups": GROUPS,
            "generated_from": "fundamental bundles"}
    meta = {"generated_at_ist": now.strftime("%Y-%m-%d %H:%M:%S IST"),
            "banks": len(banks), "skipped": len(skipped), "skipped_codes": skipped,
            "metrics": len(matrices),
            "source": "docs/data/fundamental/<CODE>.json (Screener bundles already baked by scanX)"}
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    _atomic(out / "banking.json", json.dumps(data, separators=(",", ":")))
    _atomic(out / "banking_meta.json", json.dumps(meta, indent=2))
    print(f"[bank] {len(banks)} banks | {len(matrices)} metrics | skipped {len(skipped)}")


if __name__ == "__main__":
    main()
