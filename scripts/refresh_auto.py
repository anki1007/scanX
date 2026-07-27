"""
Auto sales board — vehicle REGISTRATIONS from the government Vahan dashboard
(vahan.parivahan.gov.in), the same public source the financiallyfree "AutoTool"
uses. One run produces three independent boards:

  docs/data/auto.json           company view   — monthly volumes per maker, N calendar years
  docs/data/auto_category.json  category view  — yearly volumes per maker, split by vehicle
                                                 category and by vehicle-category GROUP (VCG)
  docs/data/auto_industry.json  industry view  — long history of all-India totals per vehicle
                                                 category, with YoY and a partial-year run-rate

Vahan is a JSF/PrimeFaces app, so a pass is: GET the report view for a
ViewState -> replay the four dropdown change events (Y axis, X axis, Calendar
Year, <year>) -> press refresh -> page through the table 25 rows at a time (the
paginator rejects any other page size). Only the X axis changes between the
company pass ("Month Wise") and the category passes ("Vehicle Category"/"VCG"),
so the column headers are read off the table instead of assumed to be 12 months.

    python scripts/refresh_auto.py                        # all three boards
    python scripts/refresh_auto.py --years 10             # deeper company history
    python scripts/refresh_auto.py --skip-category --skip-industry
    python scripts/refresh_auto.py --max-minutes 45       # stop between years, write what we have
    python scripts/refresh_auto.py --max-pages 4          # quick test

Each board is written independently with keep-last-good, so a pass that fails
never blanks a board another pass just refreshed.

Registrations != factory dispatches: the numbers track RTO registrations, so
they run slightly behind a company's reported wholesale volumes.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

IST = timezone(timedelta(hours=5, minutes=30))
URL = ("https://vahan.parivahan.gov.in/vahan4dashboard/vahan/view/reportview.xhtml")
FORM = "masterLayout_formlogin"
REFRESH_BTN = "j_idt67"
REFRESH_RENDER = "VhCatg norms fuel VhClass combTablePnl groupingTable msg vhCatgPnl"
PAGE_ROWS = 25                     # the paginator silently returns nothing for any other size
MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
          "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
SOURCE = "Vahan dashboard (RTO registrations, all-India)"

# X-axis options are exactly: Vehicle Category | Norms | Fuel | VCG |
# Financial Year | Calendar Year | Month Wise. Y axis is Maker for the boards,
# Vehicle Category for the industry like-for-like base.
AXIS_MONTH = "Month Wise"
AXIS_CATEGORY = "Vehicle Category"
AXIS_GROUP = "VCG"
Y_MAKER = "Maker"

# Vahan refuses connections from datacenter ranges — the NORMAL outcome in CI.
BLOCKED_MARKERS = ("SSLError", "ConnectionError", "Connection reset", "curl: (35)",
                   "curl: (56)", "Max retries", "timed out", "Timeout")
# Failures that mean the dashboard itself is unusable: every remaining pass
# would fail identically, so the run stops instead of hammering the site.
FATAL_MARKERS = ("no ViewState", "layout changed", "no rows parsed")

# Vahan spells makers out in full; map the listed ones onto Screener codes so
# rows can deep-link into the Fundamental page. Matched by substring, longest
# first, so "MAHINDRA & MAHINDRA LIMITED (SWARAJ" wins over "MAHINDRA".
MAKER_CODES = {
    "HERO MOTOCORP": "HEROMOTOCO",
    "BAJAJ AUTO": "BAJAJ-AUTO",
    "TVS MOTOR": "TVSMOTOR",
    "MARUTI SUZUKI": "MARUTI",
    "ROYAL-ENFIELD": "EICHERMOT",
    "EICHER": "EICHERMOT",
    "MAHINDRA & MAHINDRA": "M&M",
    "MAHINDRA LAST MILE": "M&M",
    "MAHINDRA ELECTRIC": "M&M",
    "TATA MOTORS": "TATAMOTORS",
    "TATA PASSENGER": "TATAMOTORS",
    "ASHOK LEYLAND": "ASHOKLEY",
    "OLA ELECTRIC": "OLAELEC",
    "ATHER ENERGY": "ATHERENERG",
    "ESCORTS KUBOTA": "ESCORTS",
    "FORCE MOTORS": "FORCEMOT",
    "ATUL AUTO": "ATULAUTO",
    "SML ISUZU": "SMLISUZU",
    "VE COMMERCIAL": "EICHERMOT",
    "GREAVES ELECTRIC": "GREAVESCOT",
    "HYUNDAI MOTOR INDIA": "HYUNDAI",
    "JCB INDIA": None,             # unlisted in India
}


def _atomic(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def code_for(maker: str):
    """Screener code for a listed maker, else None (Honda/Kia/Toyota/... are unlisted here)."""
    up = (maker or "").upper()
    for key in sorted(MAKER_CODES, key=len, reverse=True):
        if key in up:
            return MAKER_CODES[key]
    return None


def _text(s: str) -> str:
    """Cell/header text: tags out, entities and runs of whitespace collapsed."""
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = s.replace("&nbsp;", " ").replace("&amp;", "&")
    return re.sub(r"\s+", " ", s).strip()


def _key(s: str) -> str:
    """Loose label key — 'S No.' == 'SNo', 'Two Wheeler(NT)' == 'TWO WHEELER (NT)'."""
    return re.sub(r"[^A-Z0-9]+", "", (s or "").upper())


# Columns that are never a period: the row-number/label columns, the trailing
# total, and the SPANNING group header that carries the X-axis name.
SKIP_HEADERS = {_key(x) for x in (
    "S No", "Sr No", "Sl No", "Maker", "Total",
    "Month Wise", "Vehicle Category", "VCG", "Norms", "Fuel",
    "Vehicle Class", "Financial Year", "Calendar Year")}


def _num(s):
    s = re.sub(r"<[^>]+>", "", s or "").replace(",", "").strip()
    if not s or s in {"-", "NA"}:
        return None
    try:
        return int(float(s))
    except ValueError:
        return None


def parse_header(html: str) -> list[str]:
    """Period column labels of the grouping table, in order.

    The header is a PrimeFaces column group: the X-axis name ("Month Wise",
    "Vehicle Category") is a header SPANNING the period columns, and the leaf
    header row carries the real ones — JAN..<latest month> for the month axis,
    category names for the category axes. So a column is a period column when it
    is neither a spanning header nor structural (S.No / Maker / TOTAL), which is
    what lets the row parser stop assuming 12 months.
    """
    head = re.search(r"<thead[^>]*>(.*?)</thead>", html, re.S | re.I)
    scope = head.group(1) if head else html
    out: list[str] = []
    for attrs, inner in re.findall(r"<th\b([^>]*)>(.*?)</th>", scope, re.S | re.I):
        span = re.search(r'colspan\s*=\s*"?(\d+)', attrs, re.I)
        if span and int(span.group(1)) > 1:
            continue                                   # group header, not a column
        label = _text(inner)
        if not label or _key(label) in SKIP_HEADERS:
            continue
        out.append(label)
    return out


def parse_rows(html: str, cols: int = 12) -> list[tuple[str, list]]:
    """(row label, [one value per period column]) from a partial-response fragment.

    Data columns are: S.No | <label> | <period columns> | TOTAL, where the label
    is the maker (or the vehicle category, when that is the Y axis) and the
    period block is everything between the label and the trailing total. Only
    ELAPSED/present periods render, so `cols` right-pads (and trims) to a fixed
    width — 12 for the month axis; pass 0 to keep whatever Vahan sent, which is
    what the category axes need since their width is `len(parse_header(...))`.
    """
    out = []
    for tr in re.findall(r"<tr[^>]*data-ri=[^>]*>(.*?)</tr>", html, re.S):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
        if len(cells) < 4:
            continue
        label = _text(cells[1])
        if not label:
            continue
        vals = [_num(c) for c in cells[2:-1]]
        if cols > 0:
            vals = (vals[:cols] + [None] * cols)[:cols]
        out.append((label, vals))
    return out


@dataclass
class Deadline:
    """Wall-clock budget for a whole run; checked between years, never mid-year."""
    limit_s: float = 0.0
    start: float = field(default_factory=time.monotonic)

    @property
    def expired(self) -> bool:
        return bool(self.limit_s) and (time.monotonic() - self.start) >= self.limit_s

    @property
    def left_min(self) -> float:
        return max(0.0, (self.limit_s - (time.monotonic() - self.start)) / 60) if self.limit_s else 0.0


class Vahan:
    def __init__(self, timeout: int = 60):
        try:
            from curl_cffi import requests as cr          # Chrome TLS: plain requests gets blocked
            self.s = cr.Session(impersonate="chrome")
        except Exception:  # noqa: BLE001
            import requests as cr
            self.s = cr.Session()
            self.s.headers["User-Agent"] = (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
        self.timeout = timeout
        self.vs = ""

    def _get(self, url: str, tries: int = 4):
        """Vahan resets connections on datacenter IPs — back off and retry."""
        last = None
        for n in range(tries):
            try:
                return self.s.get(url, timeout=self.timeout)
            except Exception as e:  # noqa: BLE001
                last = e
                wait = 3 * (n + 1)
                print(f"[auto] fetch failed ({type(e).__name__}) — retry {n + 1}/{tries - 1} in {wait}s")
                time.sleep(wait)
        raise last  # type: ignore[misc]

    def open(self) -> None:
        html = self._get(URL).text
        m = re.search(r'name="javax\.faces\.ViewState"[^>]*value="([^"]+)"', html)
        if not m:
            raise RuntimeError("no ViewState — Vahan layout changed or the request was blocked")
        self.vs = m.group(1)

    def _ajax(self, source: str, render: str, state: dict, extra: dict | None = None) -> str:
        data = {
            "javax.faces.partial.ajax": "true",
            "javax.faces.source": source,
            "javax.faces.partial.execute": source,
            "javax.faces.partial.render": render,
            source: source,
            FORM: FORM,
            "javax.faces.ViewState": self.vs,
        }
        data.update(state)
        data.update(extra or {})
        headers = {"Faces-Request": "partial/ajax",
                   "X-Requested-With": "XMLHttpRequest", "Referer": URL}
        r, last = None, None
        for n in range(3):
            try:
                r = self.s.post(URL, data=data, timeout=self.timeout, headers=headers)
                break
            except Exception as e:  # noqa: BLE001
                last = e
                time.sleep(3 * (n + 1))
        if r is None:
            raise last  # type: ignore[misc]
        nvs = re.search(r'ViewState:0"><!\[CDATA\[(.*?)\]\]>', r.text, re.S)
        if nvs:
            self.vs = nvs.group(1)
        return r.text

    def scrape(self, axis: str, y: int, max_pages: int = 0, pause: float = 0.4,
               yaxis: str = Y_MAKER, label: str = "") -> tuple[list[str], dict[str, list]]:
        """(column headers, {row label: [one value per column]}) for one axis/year pass."""
        tag = label or f"{axis} {y}"
        state = {"yaxisVar_input": yaxis, "xaxisVar_input": axis,
                 "selectedYearType_input": "C", "selectedYear_input": str(y)}
        for src, render in (("yaxisVar", "xaxisVar"), ("xaxisVar", "multipleYear"),
                            ("selectedYearType", "selectedYear"), ("selectedYear", "selectedYear")):
            self._ajax(src, render, state)
        first_page = self._ajax(REFRESH_BTN, REFRESH_RENDER, state)

        headers = parse_header(first_page)
        total = 0
        m = re.search(r"rowCount:(\d+)", first_page)
        if m:
            total = int(m.group(1))
        data = dict(parse_rows(first_page, cols=0))
        pages = (total + PAGE_ROWS - 1) // PAGE_ROWS if total else 1
        if max_pages:
            pages = min(pages, max_pages)
        for p in range(1, pages):
            html = self._ajax("groupingTable", "groupingTable", state, {
                "javax.faces.behavior.event": "page",
                "javax.faces.partial.event": "page",
                "groupingTable_pagination": "true",
                "groupingTable_first": str(p * PAGE_ROWS),
                "groupingTable_rows": str(PAGE_ROWS),
                "groupingTable_skipChildren": "true",
                "groupingTable_encodeFeature": "true",
            })
            rows = parse_rows(html, cols=0)
            if not rows:
                print(f"[auto]   page {p + 1}/{pages} empty — stopping early")
                break
            data.update(dict(rows))
            if p % 10 == 0:
                print(f"[auto]   {tag}: page {p + 1}/{pages} ({len(data)} rows)")
            time.sleep(pause)
        return headers, data

    def year(self, y: int, max_pages: int = 0, pause: float = 0.4) -> dict:
        """{maker: [12 monthly registrations]} for calendar year `y`."""
        _, rows = self.scrape(AXIS_MONTH, y, max_pages=max_pages, pause=pause, label=str(y))
        return {maker: (vals[:12] + [None] * 12)[:12] for maker, vals in rows.items()}


@dataclass
class Session:
    """Lazily-opened, shared Vahan handle — one ViewState for the whole run.

    Kept lazy so a pass that is skipped (or monkeypatched in tests) never opens
    a connection, and so a site that is down is diagnosed once, not per pass.
    """
    _v: Vahan | None = None
    _err: Exception | None = None

    def get(self) -> Vahan:
        if self._err is not None:
            raise self._err
        if self._v is None:
            try:
                v = Vahan()
                v.open()
            except Exception as e:  # noqa: BLE001
                self._err = e
                raise
            self._v = v
        return self._v


def collect(session: Session | None, axis: str, years, max_pages: int = 0,
            deadline: Deadline | None = None, yaxis: str = Y_MAKER,
            label: str = "scan") -> dict[int, tuple[list[str], dict[str, list]]]:
    """{year: (headers, rows)} — one scrape pass per year, newest first."""
    v = (session or Session()).get()
    years = sorted({int(y) for y in years}, reverse=True)
    scan: dict[int, tuple[list[str], dict[str, list]]] = {}
    for i, y in enumerate(years):
        if deadline is not None and deadline.expired and scan:
            print(f"[auto] {label}: time budget spent — stopping after {len(scan)}/{len(years)} year(s)")
            break
        print(f"[auto] {label} {i + 1}/{len(years)}: {axis} {y} …")
        headers, rows = v.scrape(axis, y, max_pages=max_pages, yaxis=yaxis, label=f"{label} {y}")
        if not rows:
            print(f"[auto] {label}: {y} returned no rows — skipping the year")
            continue
        scan[y] = (headers, rows)
        # ASCII only: a Windows console is cp1252 and an arrow would raise here.
        print(f"[auto] {label}: {y} gave {len(rows)} rows over {len(headers)} columns")
    return scan


def build(years: list[int], max_pages: int, top: int, session: Session | None = None,
          deadline: Deadline | None = None) -> dict:
    """COMPANY view: {"<year>": {"makers": [...]}} — one month-wise pass per year."""
    v = (session or Session()).get()
    out: dict = {}
    years = list(years)
    for i, y in enumerate(years):
        if deadline is not None and deadline.expired and out:
            print(f"[auto] company: time budget spent — stopping after {len(out)}/{len(years)} year(s)")
            break
        print(f"[auto] company {i + 1}/{len(years)}: fetching {y} …")
        raw = v.year(y, max_pages=max_pages)
        makers = []
        for maker, months in raw.items():
            tot = sum(x for x in months if x)
            if not tot:
                continue
            makers.append({"maker": maker, "code": code_for(maker),
                           "months": months, "total": tot})
        makers.sort(key=lambda r: -r["total"])
        out[str(y)] = {"makers": makers[:top] if top else makers}
        print(f"[auto] {y}: {len(makers)} makers with volume (keeping {len(out[str(y)]['makers'])})")
    return out


def _column_order(scan: dict) -> list[str]:
    """Union of period columns across years, newest year's order first."""
    order: list[str] = []
    seen: set[str] = set()
    for y in sorted(scan, reverse=True):
        for h in scan[y][0]:
            if _key(h) not in seen:
                seen.add(_key(h))
                order.append(h)
    return order


def _pivot(scan: dict, top: int = 0) -> tuple[list[str], dict]:
    """(columns, {column: {"years": [...], "makers": [...]}}) from {year: (headers, rows)}.

    Column POSITIONS move between years (a category Vahan did not render in 2022
    shifts everything after it), so every year is indexed by its own header row,
    never by the newest year's positions.
    """
    years = sorted(scan)
    cols = _column_order(scan)
    idx = {y: {_key(h): i for i, h in enumerate(scan[y][0])} for y in years}
    out: dict = {}
    for col in cols:
        per_maker: dict[str, list] = {}
        for yi, y in enumerate(years):
            i = idx[y].get(_key(col))
            if i is None:
                continue
            for maker, vals in scan[y][1].items():
                per_maker.setdefault(maker, [None] * len(years))[yi] = (
                    vals[i] if i < len(vals) else None)
        recs = []
        for maker, vals in per_maker.items():
            tot = sum(x for x in vals if x)
            if not tot:
                continue
            recs.append({"maker": maker, "code": code_for(maker),
                         "values": vals, "total": tot})
        recs.sort(key=lambda r: -r["total"])
        out[col] = {"years": [str(y) for y in years],
                    "makers": recs[:top] if top else recs}
    return cols, out


def category_payload(cat_scan: dict, grp_scan: dict, top: int = 0) -> dict:
    """CATEGORY view: makers pivoted by vehicle category and by category group."""
    cats, by_cat = _pivot(cat_scan, top)
    groups, by_grp = _pivot(grp_scan, top)
    return {"categories": cats, "groups": groups,
            "by_category": by_cat, "by_group": by_grp}


def aggregate_columns(headers: list[str], rows: dict[str, list]) -> dict[str, int]:
    """{column: all-maker total} — the industry line for one year."""
    tot: dict[str, int] = {}
    for vals in rows.values():
        for i, h in enumerate(headers):
            v = vals[i] if i < len(vals) else None
            if v:
                tot[h] = tot.get(h, 0) + v
    return {h: t for h, t in tot.items() if t}


def months_elapsed(rows: dict[str, list]) -> int:
    """How many month columns of a month-wise pass carry data (0 if none do)."""
    n = 0
    for vals in rows.values():
        for i, v in enumerate(vals[:12]):
            if v:
                n = max(n, i + 1)
    return n


def industry_series(totals: dict[int, dict[str, int]], partial_year: int | None = None,
                    elapsed: int = 12, prior_ytd: dict[str, int] | None = None) -> dict:
    """INDUSTRY view: {"series": {category: [{year,total,yoy,partial,...}]}}.

    YoY is like-for-like or it is null: the current (partial) year is compared
    against `prior_ytd` — last year's SAME months — and never against last
    year's full total, which would print a fake -40% every January.
    """
    prior = {_key(k): v for k, v in (prior_ytd or {}).items() if v}
    order: list[str] = []
    for y in sorted(totals, reverse=True):
        for c in totals[y]:
            if c not in order:
                order.append(c)

    elapsed = max(1, min(12, int(elapsed or 12)))
    series: dict[str, list[dict]] = {}
    for cat in order:
        points = []
        for y in sorted(totals):
            tot = totals[y].get(cat)
            if not tot:
                continue
            if y == partial_year:
                base = prior.get(_key(cat))
                points.append({
                    "year": y, "total": tot,
                    "yoy": round((tot / base - 1) * 100, 1) if base else None,
                    "partial": True,
                    "extrapolated": round(tot / elapsed * 12),
                    "months_elapsed": elapsed,
                })
            else:
                prev = None if (y - 1) == partial_year else totals.get(y - 1, {}).get(cat)
                points.append({
                    "year": y, "total": tot,
                    "yoy": round((tot / prev - 1) * 100, 1) if prev else None,
                    "partial": False,
                })
        if points:
            series[cat] = points
    return {"series": series}


def industry_payload(scan: dict, base: dict | None = None, now: datetime | None = None) -> dict:
    """Glue: aggregate the maker×category scan, then bolt on the like-for-like base.

    `base` is a month-wise pass with Vehicle Category on the Y axis for the
    current and previous year — the only way to get last year's Jan..<current
    month> per category, which is what the partial year must be compared with.
    """
    now = now or datetime.now(IST)
    totals = {y: aggregate_columns(h, r) for y, (h, r) in scan.items()}
    totals = {y: t for y, t in totals.items() if t}
    cur = now.year
    base = base or {}
    elapsed, prior = 0, None
    if cur in base:
        elapsed = months_elapsed(base[cur][1])
        if cur - 1 in base:
            prior = {cat: sum(x for x in vals[:elapsed] if x)
                     for cat, vals in base[cur - 1][1].items()} if elapsed else None
    if not elapsed:
        elapsed = now.month                      # calendar fallback; YoY stays null
    return industry_series(totals, partial_year=cur if cur in totals else None,
                           elapsed=elapsed, prior_ytd=prior)


def _write(path: Path, payload: dict, note: str) -> None:
    _atomic(path, json.dumps(payload, separators=(",", ":")))
    print(f"[auto] wrote {path.name} — {note}")


def _meta(out: Path, now: datetime, patch: dict) -> None:
    """Merge into auto_meta.json so a single-pass run still refreshes the stamp."""
    cur: dict = {}
    p = out / "auto_meta.json"
    if p.exists():
        try:
            cur = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            cur = {}
    cur["generated_at_ist"] = now.strftime("%Y-%m-%d %H:%M:%S IST")
    cur["source"] = SOURCE
    cur.update(patch)
    _atomic(p, json.dumps(cur, indent=2))


def _has(msg: str, markers) -> bool:
    return any(x in msg for x in markers)


def main() -> int:
    ap = argparse.ArgumentParser(description="Vahan registrations by maker, category and industry")
    ap.add_argument("--years", type=int, default=6, help="company view: calendar years back (incl. current)")
    ap.add_argument("--cat-years", type=int, default=5, help="category view: calendar years back")
    ap.add_argument("--industry-years", type=int, default=15, help="industry view: calendar years back")
    ap.add_argument("--top", type=int, default=300, help="keep the N largest makers per year (0=all)")
    ap.add_argument("--cat-top", type=int, default=100, help="keep the N largest makers per category (0=all)")
    ap.add_argument("--max-pages", type=int, default=0, help="cap pages per year (testing)")
    ap.add_argument("--max-minutes", type=float, default=0.0,
                    help="stop between years once spent and write what was gathered (0=no limit)")
    ap.add_argument("--skip-company", action="store_true", help="leave auto.json alone")
    ap.add_argument("--skip-category", action="store_true", help="leave auto_category.json alone")
    ap.add_argument("--skip-industry", action="store_true", help="leave auto_industry.json alone")
    ap.add_argument("--out", default=str(ROOT / "docs" / "data"))
    args = ap.parse_args()

    now = datetime.now(IST)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    dl = Deadline(max(0.0, args.max_minutes) * 60)
    sess = Session()

    years = [now.year - i for i in range(max(1, args.years))]
    cat_years = [now.year - i for i in range(max(1, args.cat_years))]
    ind_years = [now.year - i for i in range(max(1, args.industry_years))]
    cat_scan: dict = {}                     # shared between the category and industry passes

    def _company() -> None:
        data = build(years, args.max_pages, args.top, session=sess, deadline=dl)
        if not any(v["makers"] for v in data.values()):
            raise RuntimeError("no rows parsed for the company pass")
        _write(out / "auto.json",
               {"months": MONTHS, "years": data, "latest_year": max(data)},
               f"{len(data[max(data)]['makers'])} makers for {max(data)}, "
               f"{len(data)} year(s)")
        _meta(out, now, {"years": sorted(data, reverse=True),
                         "makers": len(data[max(data)]["makers"])})

    def _category() -> None:
        want = [y for y in cat_years if y not in cat_scan]
        if want:
            cat_scan.update(collect(sess, AXIS_CATEGORY, want, args.max_pages,
                                    deadline=dl, label="category"))
        scan = {y: cat_scan[y] for y in cat_years if y in cat_scan}
        grp = collect(sess, AXIS_GROUP, cat_years, args.max_pages, deadline=dl, label="group")
        if not scan and not grp:
            raise RuntimeError("no rows parsed for the category pass")
        payload = category_payload(scan, grp, args.cat_top)
        _write(out / "auto_category.json", payload,
               f"{len(payload['categories'])} categories, {len(payload['groups'])} groups, "
               f"{len(scan)} year(s)")
        _meta(out, now, {"category_years": [str(y) for y in sorted(scan, reverse=True)],
                         "categories": len(payload["categories"]),
                         "groups": len(payload["groups"])})

    def _industry() -> None:
        want = [y for y in ind_years if y not in cat_scan]
        if want:
            cat_scan.update(collect(sess, AXIS_CATEGORY, want, args.max_pages,
                                    deadline=dl, label="industry"))
        scan = {y: cat_scan[y] for y in ind_years if y in cat_scan}
        if not scan:
            raise RuntimeError("no rows parsed for the industry pass")
        base: dict = {}
        try:
            # Vehicle Category on the Y axis, months across: last year's same
            # months, which the yearly category pass cannot give.
            base = collect(sess, AXIS_MONTH, [now.year, now.year - 1], args.max_pages,
                           yaxis=AXIS_CATEGORY, label="industry base")
        except Exception as e:  # noqa: BLE001
            print(f"[auto] industry: like-for-like base unavailable "
                  f"({type(e).__name__}) — partial-year YoY stays null")
        payload = industry_payload(scan, base=base, now=now)
        _write(out / "auto_industry.json", payload,
               f"{len(payload['series'])} categories over {len(scan)} year(s)")
        _meta(out, now, {"industry_years": [str(y) for y in sorted(scan, reverse=True)],
                         "industry_categories": len(payload["series"])})

    passes = [
        ("company", "auto.json", _company, args.skip_company),
        ("category", "auto_category.json", _category, args.skip_category),
        ("industry", "auto_industry.json", _industry, args.skip_industry),
    ]
    failed: list[tuple[str, bool, bool]] = []       # (name, blocked, previous data on disk)
    abort = False
    for name, fname, fn, skip in passes:
        if skip:
            print(f"[auto] {name}: skipped (--skip-{name})")
            continue
        if abort:
            print(f"[auto] {name}: not attempted — the dashboard is unavailable this run")
            continue
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            # keep-last-good, per file: nothing is written on failure, so a pass
            # that dies never blanks a board another pass just refreshed.
            msg = f"{type(e).__name__}: {e}"
            prev = (out / fname).exists()
            blocked = _has(msg, BLOCKED_MARKERS)
            print(f"[auto] {name} FAILED ({msg[:140]}) — "
                  f"{'keeping previous ' + fname if prev else 'no previous ' + fname + ' to keep'}")
            failed.append((name, blocked, prev))
            if blocked or _has(msg, FATAL_MARKERS):
                abort = True                    # the site is down/changed, not this pass

    if not failed:
        return 0
    if any(b for _, b, _ in failed):
        # Vahan refuses connections from datacenter ranges, so this is the
        # NORMAL outcome on a GitHub runner. Failing the build daily would
        # train everyone to ignore red runs — the exact blindness that let
        # the June price freeze go unnoticed. Warn loudly, stay green, and
        # let the VPS-side run (residential/Indian IP) supply fresh data.
        print("[auto] NOTE: Vahan blocks datacenter IPs — this is expected in CI. "
              "Previous data retained; run scripts/refresh_auto.py from the VPS "
              "or a desktop for a fresh scrape.")
    if all(blocked and prev for _, blocked, prev in failed):
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
