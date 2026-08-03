"""Advanced fundamental checklist, run against a baked bundle.

A checklist of the things a careful reader looks for in a balance sheet, an
income statement and a cash-flow statement -- capex intent, deleveraging,
receivable quality, margin stability, operating leverage, interest cover,
depreciation games, cash conversion.

Two rules govern this module.

FIRST: a check we cannot compute says so. It returns "na" with the reason,
never a neutral-looking pass. Several items on the source checklist need lines
the statements here simply do not carry -- a cash balance, a raw-material cost,
an employee cost, the split of investments between financial assets, associates
and subsidiaries. Reporting those as "fine" because the data is missing would
be the most damaging thing this file could do, since the whole point is to
surface what is wrong.

SECOND: a proxy is labelled as a proxy. There is no gross margin here, because
there is no raw-material line -- what is computed is the OPERATING margin, and
it is named that way in the output rather than quietly relabelled.

Pure: no I/O, no network, no clock. Never raises on a malformed bundle.
"""
from __future__ import annotations

import math
import re
import statistics
from typing import Any, Mapping, Sequence

__all__ = ["evaluate", "CHECKS", "GROUPS", "PASS", "WARN", "FAIL", "NA"]

PASS, WARN, FAIL, NA = "pass", "warn", "fail", "na"

GROUPS = ("Balance sheet", "Income statement", "Cash flow", "Nature")

#: Thresholds, all in one place so the screen can be argued with.
T = {
    "roe_min": 15.0,            # a business that cannot earn 15% on equity
    "icr_min": 7.0,             # ebit/interest; below this a shock compounds
    "cfo_conv_min": 70.0,       # CFO as % of operating profit
    "recv_inv_share_max": 50.0,  # receivables+inventory as % of total assets
    "capex_must_read": 2.0,     # CWIP >= 2x net block: a company being rebuilt
    "capex_heavy": 0.5,
    "margin_cv_stable": 0.15,   # coefficient of variation of OPM
    "margin_cv_cyclical": 0.35,
    "dep_cv_flag": 0.30,        # volatile depreciation = asset-life games
    "fast_growth": 15.0,
    "other_income_max": 25.0,   # other income as % of PBT
    "asset_turn_high": 1.0,
}


# --------------------------------------------------------------------- utils

def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(float(value)) else None
    text = str(value).replace(",", "").replace("%", "").replace("₹", "").strip()
    if not text or text in {"-", "--", "NA", "n/a"}:
        return None
    m = re.search(r"-?\d+\.?\d*", text)
    if not m:
        return None
    try:
        out = float(m.group())
    except ValueError:
        return None
    return out if math.isfinite(out) else None


def _series(statement: Mapping | None, *names: str) -> list[float]:
    """Numeric series for the first row whose label starts with any of `names`."""
    rows = (statement or {}).get("rows") if isinstance(statement, Mapping) else None
    if not isinstance(rows, Mapping):
        return []
    wanted = [n.lower() for n in names]
    for label, values in rows.items():
        low = str(label).strip().lower()
        if any(low.startswith(w) for w in wanted):
            if isinstance(values, Sequence) and not isinstance(values, str):
                return [v for v in (_num(x) for x in values) if v is not None]
    return []


def _series_by_year(statement: Mapping | None, *names: str) -> dict[str, float]:
    """{period label -> value}, so two statements can be compared by YEAR.

    This matters more than it looks. The statements do not share a period
    range: on a real bundle the profit and loss ran Mar 2016..Mar 2026 plus a
    TTM column while the balance sheet ran Mar 2015..Mar 2026. Pairing them by
    list index therefore divides one year's depreciation by a different year's
    asset base. TTM is dropped, being a rolling window rather than a year.
    """
    if not isinstance(statement, Mapping):
        return {}
    headers = statement.get("headers")
    rows = statement.get("rows")
    if not isinstance(headers, Sequence) or not isinstance(rows, Mapping):
        return {}
    wanted = [n.lower() for n in names]
    for label, values in rows.items():
        low = str(label).strip().lower()
        if not any(low.startswith(w) for w in wanted):
            continue
        if not isinstance(values, Sequence) or isinstance(values, str):
            continue
        out: dict[str, float] = {}
        for header, value in zip(headers, values):
            key = str(header).strip()
            if key.upper() == "TTM":
                continue
            v = _num(value)
            if v is not None:
                out[key] = v
        return out
    return {}


def _ratio_by_year(num_stmt, num_names, den_stmt, den_names) -> list[float]:
    """Numerator over denominator, matched on the period label they share."""
    num = _series_by_year(num_stmt, *num_names)
    den = _series_by_year(den_stmt, *den_names)
    order = [k for k in num if k in den]
    return [num[k] / den[k] for k in order if abs(den[k]) > 1e-9]


def _last(xs: Sequence[float], n: int = 1):
    return xs[-n] if len(xs) >= n else None


def _cv(xs: Sequence[float]) -> float | None:
    """Coefficient of variation -- spread relative to level, so a 2% swing on a
    4% margin counts as the violent thing it is."""
    vals = [x for x in xs if x is not None]
    if len(vals) < 3:
        return None
    mean = statistics.fmean(vals)
    if abs(mean) < 1e-9:
        return None
    return statistics.pstdev(vals) / abs(mean)


def _trend(xs: Sequence[float], window: int = 4) -> float | None:
    """Fractional change from the start to the end of the recent window.

    ALWAYS clamped to [-100%, +500%]. Two different ways the raw ratio becomes
    nonsense, and both occur in the real data:

      * the series crosses zero. Working-capital days going from +40 to -35 is
        a genuinely good outcome -- the business collects before it pays -- but
        "falling 189%" is not a sentence about anything.
      * the base is tiny but not zero. A metric starting at 0.001 produced a
        5,722,469% "change" on a real bundle. The near-zero guard below only
        catches an exact zero; it cannot catch a small one.

    Past a full 100% the direction is the information and the magnitude is an
    artefact of the denominator. Clamping does not move any verdict: every
    threshold in this module sits between -25% and +25%.
    """
    vals = [x for x in xs if x is not None][-window:]
    if len(vals) < 2 or abs(vals[0]) < 1e-9:
        return None
    first, last = vals[0], vals[-1]
    change = (last - first) / abs(first)
    return max(-1.0, min(5.0, change))


#: Reported values are clamped to this magnitude.
REPORT_CAP = 9999.0


def _chk(key, group, label, verdict, value=None, detail="") -> dict:
    """One checklist row.

    The reported VALUE is clamped; the verdict is computed before this and is
    untouched. Every ratio here has a denominator that can approach zero, and
    the real data duly produced a 6,588,781% cash conversion and a 112,166%
    margin variation. Both verdicts were defensible -- cash did vastly exceed a
    near-zero operating profit -- but the numbers are noise from the divisor,
    and a screen that prints them is not believed about anything else.
    """
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if math.isfinite(value):
            value = max(-REPORT_CAP, min(REPORT_CAP, value))
        else:
            value = None
    return {"key": key, "group": group, "label": label,
            "verdict": verdict, "value": value, "detail": detail}


def _na(key, group, label, why) -> dict:
    return _chk(key, group, label, NA, None, why)


# ------------------------------------------------------------ balance sheet

def _balance_sheet(bs, pl, ratios) -> list[dict]:
    g = "Balance sheet"
    out: list[dict] = []

    fixed = _series(bs, "fixed asset")
    cwip = _series(bs, "cwip", "capital work")
    total_assets = _series(bs, "total asset")
    borrow = _series(bs, "borrowing")
    equity = _series(bs, "equity capital")
    reserves = _series(bs, "reserves")
    invest = _series(bs, "investment")
    sales = _series(pl, "sales", "revenue")

    # Capex intent. CWIP is money already committed to plant not yet producing,
    # so CWIP against the existing net block is the cleanest read on whether a
    # company is being expanded or merely maintained.
    nb, wip = _last(fixed), _last(cwip)
    if nb and wip is not None and nb > 0:
        ratio = wip / nb
        if ratio >= T["capex_must_read"]:
            v, d = PASS, f"CWIP is {ratio:.1f}x the existing net block - the plant is being more than doubled"
        elif ratio >= T["capex_heavy"]:
            v, d = PASS, f"CWIP is {ratio:.1f}x net block - a heavy build is under way"
        else:
            v, d = WARN, f"CWIP is {ratio:.2f}x net block - maintenance capex, no step change coming"
        out.append(_chk("capex_intensity", g, "Capex under way (CWIP vs net block)", v, round(ratio, 2), d))
    else:
        out.append(_na("capex_intensity", g, "Capex under way (CWIP vs net block)",
                       "no fixed-asset or CWIP line"))

    # Asset turn. Capex only predicts revenue if the existing assets already
    # turn over; a heavy build on a low-turn base predicts depreciation.
    ta, sl = _last(total_assets), _last(sales)
    if ta and sl and ta > 0:
        turn = sl / ta
        v = PASS if turn >= T["asset_turn_high"] else WARN
        out.append(_chk("asset_turnover", g, "Asset turnover (sales / total assets)", v, round(turn, 2),
                        f"{turn:.2f}x - " + ("assets convert to sales, so new capex should convert too"
                                             if v == PASS else
                                             "a low-turn base; new capex adds depreciation before it adds revenue")))
    else:
        out.append(_na("asset_turnover", g, "Asset turnover (sales / total assets)", "no sales or total-asset line"))

    # Deleveraging.
    bt = _trend(borrow)
    if bt is not None:
        v = PASS if bt <= -0.10 else (WARN if bt <= 0.25 else FAIL)
        out.append(_chk("deleveraging", g, "Borrowings trend", v, round(bt * 100, 1),
                        f"borrowings {'down' if bt < 0 else 'up'} {abs(bt)*100:.0f}% over the window"))
    else:
        out.append(_na("deleveraging", g, "Borrowings trend", "no borrowings history"))

    # Rising equity base.
    if equity and reserves and len(reserves) >= 2:
        base = [(equity[i] if i < len(equity) else equity[-1]) + reserves[i]
                for i in range(len(reserves))]
        et = _trend(base)
        if et is not None:
            v = PASS if et > 0.05 else (WARN if et > -0.05 else FAIL)
            out.append(_chk("equity_base", g, "Equity base (capital + reserves)", v, round(et * 100, 1),
                            f"net worth {'up' if et > 0 else 'down'} {abs(et)*100:.0f}% over the window"))
    if not any(c["key"] == "equity_base" for c in out):
        out.append(_na("equity_base", g, "Equity base (capital + reserves)", "no reserves history"))

    # Receivables + inventory as a share of assets. The statements carry no
    # receivable or inventory LINE, so this is reconstructed from the day
    # counts: receivables ~ sales x debtor-days / 365. Approximate, and marked
    # as such, but a company carrying half its balance sheet in unpaid bills
    # and unsold stock is worth flagging even approximately.
    dd, idays = _last(_series(ratios, "debtor day")), _last(_series(ratios, "inventory day"))
    if ta and sl and (dd is not None or idays is not None) and ta > 0:
        recv = sl * (dd or 0) / 365.0
        inv = sl * (idays or 0) / 365.0
        share = (recv + inv) / ta * 100
        v = FAIL if share >= T["recv_inv_share_max"] else (WARN if share >= 35 else PASS)
        out.append(_chk("recv_inv_share", g, "Receivables + inventory as % of assets", v, round(share, 1),
                        f"~{share:.0f}% of the balance sheet (derived from debtor/inventory days, not a filed line)"))
    else:
        out.append(_na("recv_inv_share", g, "Receivables + inventory as % of assets",
                       "needs sales, total assets and day counts"))

    # Debtor days direction.
    ddt = _trend(_series(ratios, "debtor day"))
    if ddt is not None:
        v = PASS if ddt <= -0.05 else (WARN if ddt <= 0.20 else FAIL)
        out.append(_chk("debtor_days_trend", g, "Debtor days trend", v, round(ddt * 100, 1),
                        f"debtor days {'falling' if ddt < 0 else 'rising'} {abs(ddt)*100:.0f}% - "
                        + ("cash arrives sooner" if ddt < 0 else "cash arrives later")))
    else:
        out.append(_na("debtor_days_trend", g, "Debtor days trend", "no debtor-day history"))

    # Investments as a share of assets -- reported, not judged. The checklist
    # wants the split into financial assets (<20%), associates (20-50%) and
    # subsidiaries (>50%), and that split is a holding-percentage disclosure in
    # the annual report, not a balance-sheet line. Judging it from the total
    # would be inventing the answer.
    iv = _last(invest)
    if iv is not None and ta and ta > 0:
        out.append(_chk("investments_share", g, "Investments as % of assets", NA, round(iv / ta * 100, 1),
                        f"{iv/ta*100:.0f}% of assets. The financial-asset / associate / subsidiary split is a "
                        f"holding-percentage disclosure in the annual report, not a balance-sheet line"))
    else:
        out.append(_na("investments_share", g, "Investments as % of assets", "no investments line"))

    out.append(_na("cash_pile", g, "Excess cash on the balance sheet",
                   "these statements carry no separate cash line - only 'Other Assets'"))
    return out


# -------------------------------------------------------- income statement

def _income(pl, bs, upstox, overview) -> list[dict]:
    g = "Income statement"
    out: list[dict] = []

    sales = _series(pl, "sales", "revenue")
    expenses = _series(pl, "expenses")
    opm = _series(pl, "opm")
    op = _series(pl, "operating profit")
    interest = _series(pl, "interest")
    dep = _series(pl, "depreciation")
    other_inc = _series(pl, "other income")
    pbt = _series(pl, "profit before tax")
    npf = _series(pl, "net profit")

    # ROE.
    roe = None
    row = (upstox or {}).get("roe")
    if isinstance(row, Mapping):
        roe = _num(row.get("value"))
    if roe is None:
        roe = _num((overview or {}).get("ROE"))
    if roe is not None:
        v = PASS if roe >= T["roe_min"] else (WARN if roe >= 10 else FAIL)
        out.append(_chk("roe", g, f"ROE above {T['roe_min']:.0f}%", v, round(roe, 1),
                        f"{roe:.1f}% return on equity"))
    else:
        out.append(_na("roe", g, f"ROE above {T['roe_min']:.0f}%", "no ROE available"))

    # Margin stability. Low spread = secular; high spread = cyclical, which is
    # not a failure, it is a different game (buy at support P/B, not on margin).
    cv = _cv(opm)
    if cv is not None:
        if cv <= T["margin_cv_stable"]:
            v, d = PASS, f"operating margin is steady (variation {cv:.0%}) - a secular business"
        elif cv <= T["margin_cv_cyclical"]:
            v, d = WARN, f"operating margin moves about (variation {cv:.0%})"
        else:
            v, d = FAIL, f"operating margin swings hard (variation {cv:.0%}) - cyclical; price it on P/B, not on peak margin"
        out.append(_chk("margin_stability", g, "Operating-margin stability", v, round(cv * 100, 1), d))
    else:
        out.append(_na("margin_stability", g, "Operating-margin stability", "needs at least three years of OPM"))

    # Margin direction. Named OPERATING margin, not gross: there is no
    # raw-material line in these statements, so a true gross margin cannot be
    # computed and will not be implied.
    mt = _trend(opm)
    if mt is not None:
        v = PASS if mt > 0.05 else (WARN if mt > -0.05 else FAIL)
        out.append(_chk("margin_trend", g, "Operating margin direction", v, round(mt * 100, 1),
                        f"margin {'expanding' if mt > 0 else 'compressing'} {abs(mt)*100:.0f}% over the window"
                        " (operating margin - no raw-material line, so gross margin is not available)"))
    else:
        out.append(_na("margin_trend", g, "Operating margin direction", "no OPM history"))

    # Operating leverage: sales growing faster than the cost base.
    st, et = _trend(sales), _trend(expenses)
    if st is not None and et is not None:
        gap = st - et
        v = PASS if gap > 0.02 else (WARN if gap > -0.02 else FAIL)
        out.append(_chk("operating_leverage", g, "Operating leverage", v, round(gap * 100, 1),
                        f"sales {st*100:+.0f}% vs expenses {et*100:+.0f}% - "
                        + ("costs held while sales grew" if gap > 0 else "costs grew faster than sales")))
    else:
        out.append(_na("operating_leverage", g, "Operating leverage", "needs sales and expense history"))

    # Interest cover.
    ebit, intr = _last(op), _last(interest)
    if ebit is not None and intr is not None:
        if intr <= 0:
            out.append(_chk("interest_cover", g, f"Interest cover above {T['icr_min']:.0f}x", PASS, None,
                            "no meaningful interest cost"))
        else:
            icr = ebit / intr
            v = PASS if icr >= T["icr_min"] else (WARN if icr >= 3 else FAIL)
            out.append(_chk("interest_cover", g, f"Interest cover above {T['icr_min']:.0f}x", v, round(icr, 1),
                            f"EBIT covers interest {icr:.1f}x"
                            + ("" if v == PASS else " - a shock here compounds fast")))
    else:
        out.append(_na("interest_cover", g, f"Interest cover above {T['icr_min']:.0f}x",
                       "no operating-profit or interest line"))

    # Depreciation games: a company that keeps changing asset lives to flatter
    # profit leaves the fingerprint in a jumpy depreciation charge.
    #
    # Measured against the ASSET BASE, not on the raw charge. A growing company
    # depreciates more every year simply because it owns more -- on a real
    # bundle the charge went 8,590 to 17,171 over the window, which the raw
    # measure called a 30% "red flag" when it was just capex working. What a
    # revised asset life actually looks like is the charge moving while the
    # asset base does not.
    dep_rate = _ratio_by_year(pl, ("depreciation",), bs, ("fixed asset",))
    dcv = _cv(dep_rate)
    if dcv is not None:
        v = FAIL if dcv >= T["dep_cv_flag"] else PASS
        rate = dep_rate[-1] * 100
        out.append(_chk("depreciation_volatility", g, "Depreciation consistency", v, round(dcv * 100, 1),
                        f"charge is {rate:.1f}% of the net block and that rate varies {dcv:.0%}"
                        + (" - check whether asset lives were revised" if v == FAIL else "")))
    else:
        out.append(_na("depreciation_volatility", g, "Depreciation consistency",
                       "needs depreciation and a fixed-asset base over the same years"))

    # Reliance on other income -- where repeated "exceptional" items hide.
    oi, pb = _last(other_inc), _last(pbt)
    if oi is not None and pb and pb > 0:
        share = oi / pb * 100
        v = PASS if share <= T["other_income_max"] else (WARN if share <= 50 else FAIL)
        out.append(_chk("other_income_reliance", g, "Reliance on other income", v, round(share, 1),
                        f"other income is {share:.0f}% of pre-tax profit"
                        + (" - read what sits inside it" if v != PASS else "")))
    else:
        out.append(_na("other_income_reliance", g, "Reliance on other income", "no other-income or PBT line"))

    # Zone of danger: peak margin AND a rich multiple at the same time.
    # A scraped P/E of 2,615,022 turned up in the real board. Outside a sane
    # band it is a data error, and treating it as "rich" would put a company in
    # the danger zone on the strength of a broken number.
    pe = _num((overview or {}).get("Stock P/E"))
    if pe is not None and not (0 < pe <= 500):
        pe = None
    if opm and len(opm) >= 4 and pe:
        at_peak = opm[-1] >= max(opm) - 1e-9
        rich = pe >= 40
        if at_peak and rich:
            v, d = FAIL, f"margin at its own peak ({opm[-1]:.0f}%) on a P/E of {pe:.0f} - both have to keep going right"
        elif at_peak:
            v, d = WARN, f"margin at its own peak ({opm[-1]:.0f}%), but the multiple is not stretched"
        else:
            v, d = PASS, f"margin is below its peak ({opm[-1]:.0f}% vs {max(opm):.0f}%) - room to recover"
        out.append(_chk("zone_of_danger", g, "Peak margin on a peak multiple", v, round(pe, 1), d))
    else:
        out.append(_na("zone_of_danger", g, "Peak margin on a peak multiple", "needs OPM history and a P/E"))

    out.append(_na("volume_vs_value", g, "Volume growth vs price growth",
                   "volume is not in the statements - it is disclosed in the annual report and concall"))
    out.append(_na("inventory_writeoff", g, "Inventory write-offs / inventory gains",
                   "needs the raw-material and inventory notes from the annual report"))
    return out


# ------------------------------------------------------------- cash flow

def _cash(cf, ratios, pl) -> list[dict]:
    g = "Cash flow"
    out: list[dict] = []

    cfo = _series(cf, "cash from operating")
    fcf = _series(cf, "free cash flow")
    conv = _series(cf, "cfo/op")

    c = _last(conv)
    if c is not None:
        v = PASS if c >= T["cfo_conv_min"] else (WARN if c >= 40 else FAIL)
        out.append(_chk("cfo_conversion", g, f"Profit converts to cash (CFO/OP above {T['cfo_conv_min']:.0f}%)",
                        v, round(c, 1), f"{c:.0f}% of operating profit arrived as cash"))
    else:
        out.append(_na("cfo_conversion", g, "Profit converts to cash", "no CFO/OP line"))

    ct = _trend(cfo)
    if ct is not None:
        v = PASS if ct > 0.05 else (WARN if ct > -0.20 else FAIL)
        out.append(_chk("cfo_trend", g, "Operating cash-flow trend", v, round(ct * 100, 1),
                        f"CFO {'rising' if ct > 0 else 'falling'} {abs(ct)*100:.0f}% over the window"))
    else:
        out.append(_na("cfo_trend", g, "Operating cash-flow trend", "no CFO history"))

    recent = [x for x in fcf][-3:]
    if recent:
        pos = sum(1 for x in recent if x > 0)
        v = PASS if pos == len(recent) else (WARN if pos else FAIL)
        out.append(_chk("free_cash_flow", g, "Free cash flow", v, round(recent[-1], 1),
                        f"positive in {pos} of the last {len(recent)} years"))
    else:
        out.append(_na("free_cash_flow", g, "Free cash flow", "no free-cash-flow line"))

    wct = _trend(_series(ratios, "working capital day"))
    if wct is not None:
        v = PASS if wct <= 0 else (WARN if wct <= 0.25 else FAIL)
        out.append(_chk("working_capital", g, "Working-capital days trend", v, round(wct * 100, 1),
                        f"working-capital days {'falling' if wct < 0 else 'rising'} {abs(wct)*100:.0f}% - "
                        + ("releases cash" if wct < 0 else "absorbs cash")))
    else:
        out.append(_na("working_capital", g, "Working-capital days trend", "no working-capital-day history"))
    return out


# ---------------------------------------------------------------- nature

def _nature(pl, analysis, upstox) -> list[dict]:
    g = "Nature"
    out: list[dict] = []

    sales = _series(pl, "sales", "revenue")
    st = _trend(sales)
    if st is not None and len(sales) >= 2:
        years = min(len(sales), 4) - 1
        cagr = ((1 + st) ** (1 / years) - 1) * 100 if years > 0 and st > -1 else None
        if cagr is not None:
            v = PASS if cagr >= T["fast_growth"] else (WARN if cagr >= 8 else FAIL)
            out.append(_chk("growth", g, f"Sales growth above {T['fast_growth']:.0f}%", v, round(cagr, 1),
                            f"{cagr:.0f}% a year over the window"))
    if not any(c["key"] == "growth" for c in out):
        out.append(_na("growth", g, "Sales growth", "no sales history"))

    cyc = (analysis or {}).get("cyclical")
    if isinstance(cyc, Mapping) and cyc.get("label"):
        # Reported, not scored: cyclical is not worse than secular, it is a
        # different game -- buy it at support P/B, not on a peak margin.
        label = str(cyc.get("label"))
        pos = ", ".join(str(x) for x in (cyc.get("positive_quarters") or []))
        out.append(_chk("cyclical", g, "Cyclicality", NA, None,
                        label + (f" - strongest in {pos}" if pos else "")))
    elif isinstance(cyc, str) and cyc:
        out.append(_chk("cyclical", g, "Cyclicality", NA, None, cyc))
    else:
        out.append(_na("cyclical", g, "Cyclicality", "not assessed"))

    out.append(_na("corporate_actions", g, "Auditor exits, warrants, promoter selling, concall gaps",
                   "needs a per-company filing history; the boards here carry only a "
                   "market-wide snapshot of recent actions"))
    return out


#: Every check this module can emit, for the page to render a stable table.
CHECKS = ("capex_intensity", "asset_turnover", "deleveraging", "equity_base",
          "recv_inv_share", "debtor_days_trend", "investments_share", "cash_pile",
          "roe", "margin_stability", "margin_trend", "operating_leverage",
          "interest_cover", "depreciation_volatility", "other_income_reliance",
          "zone_of_danger", "volume_vs_value", "inventory_writeoff",
          "cfo_conversion", "cfo_trend", "free_cash_flow", "working_capital",
          "growth", "cyclical", "corporate_actions")


def evaluate(bundle: Mapping | None) -> dict:
    """Run the checklist over one baked bundle. PURE, never raises.

    Returns {score, counts, checks[]}. `score` is the share of DECIDABLE checks
    that passed, so a company whose statements are thin is not punished for it
    -- it simply has fewer checks behind its score, which `counts["decided"]`
    makes visible.
    """
    fundamental: Mapping = {}
    if isinstance(bundle, Mapping):
        inner = bundle.get("fundamental")
        fundamental = inner if isinstance(inner, Mapping) else bundle

    def sec(name):
        v = fundamental.get(name) if isinstance(fundamental, Mapping) else None
        return v if isinstance(v, Mapping) else {}

    pl, bs, cf = sec("profit_loss"), sec("balance_sheet"), sec("cash_flow")
    ratios, overview = sec("ratios"), sec("overview")
    analysis = sec("analysis")
    upstox = bundle.get("upstox_ratios") if isinstance(bundle, Mapping) else None
    upstox = upstox if isinstance(upstox, Mapping) else {}

    checks: list[dict] = []
    for fn, args in ((_balance_sheet, (bs, pl, ratios)),
                     (_income, (pl, bs, upstox, overview)),
                     (_cash, (cf, ratios, pl)),
                     (_nature, (pl, analysis, upstox))):
        try:
            checks.extend(fn(*args))
        except Exception:  # noqa: BLE001
            # One broken section must not cost the whole checklist.
            continue

    counts = {v: sum(1 for c in checks if c["verdict"] == v) for v in (PASS, WARN, FAIL, NA)}
    decided = counts[PASS] + counts[WARN] + counts[FAIL]
    counts["decided"] = decided
    score = round((counts[PASS] + 0.5 * counts[WARN]) / decided * 100, 1) if decided else None

    return {"score": score, "counts": counts, "checks": checks}
