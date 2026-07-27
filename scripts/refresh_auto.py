"""
Auto sales board — monthly vehicle REGISTRATIONS by manufacturer from the
government Vahan dashboard (vahan.parivahan.gov.in), the same public source
the financiallyfree "AutoTool" uses.

Vahan is a JSF/PrimeFaces app, so a run is: GET the report view for a
ViewState -> replay the four dropdown change events (Y axis = Maker,
X axis = Month Wise, Calendar Year, <year>) -> press refresh -> page through
the maker table 25 rows at a time (the paginator rejects any other page size).

    python scripts/refresh_auto.py                  # current + previous year
    python scripts/refresh_auto.py --years 3
    python scripts/refresh_auto.py --max-pages 4    # quick test

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


def _num(s):
    s = re.sub(r"<[^>]+>", "", s or "").replace(",", "").strip()
    if not s or s in {"-", "NA"}:
        return None
    try:
        return int(float(s))
    except ValueError:
        return None


def parse_rows(html: str) -> list[tuple[str, list]]:
    """(maker, [12 monthly ints]) from a partial-response table fragment.

    Data columns are: S.No | Maker | JAN..<latest month> | TOTAL. "Month Wise"
    in the header row is a group header spanning the month columns, not a
    column of its own, and only ELAPSED months of the year are rendered — so
    the month block is everything between the maker and the trailing total,
    right-padded to 12.
    """
    out = []
    for tr in re.findall(r"<tr[^>]*data-ri=[^>]*>(.*?)</tr>", html, re.S):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
        if len(cells) < 4:
            continue
        maker = re.sub(r"<[^>]+>", "", cells[1]).strip()
        if not maker:
            continue
        months = [_num(c) for c in cells[2:-1]][:12]
        months += [None] * (12 - len(months))
        out.append((maker, months))
    return out


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

    def open(self) -> None:
        html = self.s.get(URL, timeout=self.timeout).text
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
        r = self.s.post(URL, data=data, timeout=self.timeout, headers={
            "Faces-Request": "partial/ajax",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": URL,
        })
        nvs = re.search(r'ViewState:0"><!\[CDATA\[(.*?)\]\]>', r.text, re.S)
        if nvs:
            self.vs = nvs.group(1)
        return r.text

    def year(self, y: int, max_pages: int = 0, pause: float = 0.4) -> dict:
        """{maker: [12 monthly registrations]} for calendar year `y`."""
        state = {"yaxisVar_input": "Maker", "xaxisVar_input": "Month Wise",
                 "selectedYearType_input": "C", "selectedYear_input": str(y)}
        for src, render in (("yaxisVar", "xaxisVar"), ("xaxisVar", "multipleYear"),
                            ("selectedYearType", "selectedYear"), ("selectedYear", "selectedYear")):
            self._ajax(src, render, state)
        first_page = self._ajax(REFRESH_BTN, REFRESH_RENDER, state)

        total = 0
        m = re.search(r"rowCount:(\d+)", first_page)
        if m:
            total = int(m.group(1))
        data = dict(parse_rows(first_page))
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
            rows = parse_rows(html)
            if not rows:
                print(f"[auto]   page {p + 1}/{pages} empty — stopping early")
                break
            data.update(dict(rows))
            if p % 10 == 0:
                print(f"[auto]   {y}: page {p + 1}/{pages} ({len(data)} makers)")
            time.sleep(pause)
        return data


def build(years: list[int], max_pages: int, top: int) -> dict:
    v = Vahan()
    v.open()
    out: dict = {}
    for y in years:
        print(f"[auto] fetching {y} …")
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


def main() -> int:
    ap = argparse.ArgumentParser(description="Vahan monthly registrations by maker")
    ap.add_argument("--years", type=int, default=2, help="how many calendar years back (incl. current)")
    ap.add_argument("--top", type=int, default=300, help="keep the N largest makers per year (0=all)")
    ap.add_argument("--max-pages", type=int, default=0, help="cap pages per year (testing)")
    ap.add_argument("--out", default=str(ROOT / "docs" / "data"))
    args = ap.parse_args()

    now = datetime.now(IST)
    years = [now.year - i for i in range(max(1, args.years))]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    try:
        data = build(years, args.max_pages, args.top)
    except Exception as e:  # noqa: BLE001
        # keep-last-good: Vahan blocks datacenter IPs intermittently, and a
        # blocked run must never blank the board.
        print(f"[auto] FAILED ({type(e).__name__}: {str(e)[:120]}) — keeping previous data")
        return 1
    if not any(v["makers"] for v in data.values()):
        print("[auto] no rows parsed — keeping previous data")
        return 1

    payload = {"years": data, "months": MONTHS}
    _atomic(out / "auto.json", json.dumps(payload, separators=(",", ":")))
    latest = data[str(years[0])]["makers"]
    _atomic(out / "auto_meta.json", json.dumps({
        "generated_at_ist": now.strftime("%Y-%m-%d %H:%M:%S IST"),
        "source": "Vahan dashboard (RTO registrations, all-India)",
        "years": [str(y) for y in years],
        "makers": len(latest),
    }, indent=2))
    print(f"[auto] wrote auto.json — {len(latest)} makers for {years[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
