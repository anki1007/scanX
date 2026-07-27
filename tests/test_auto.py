"""Vahan auto-registrations parsing (scripts/refresh_auto.py) — pure functions, no network."""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import refresh_auto as ra  # noqa: E402


def _row(sno, maker, months, total):
    tds = "".join(f"<td role=\"gridcell\"><label>{v}</label></td>" for v in [sno, maker] + months + [total])
    return f'<tr data-ri="{sno}" class="ui-widget-content" role="row">{tds}</tr>'


def _head(cols, group):
    """PrimeFaces column group: S.No/Maker/TOTAL span both rows, the axis name
    spans the period columns, and the leaf row carries the real ones."""
    lead = ('<th rowspan="2" class="ui-state-default"><span class="ui-column-title">S No.</span></th>'
            '<th rowspan="2"><span class="ui-column-title">Maker</span></th>'
            f'<th colspan="{len(cols)}"><span class="ui-column-title">{group}</span></th>'
            '<th rowspan="2"><span class="ui-column-title">TOTAL</span></th>')
    leaf = "".join('<th colspan="1"><span class="ui-column-title">'
                   f'{c}</span><span class="ui-sortable-column-icon ui-icon"></span></th>'
                   for c in cols)
    return (f'<thead id="groupingTable_head"><tr role="row">{lead}</tr>'
            f'<tr role="row">{leaf}</tr></thead>')


def _table(cols, rows, group="Vehicle Category"):
    """Full fragment: header + one <tr> per (label, values)."""
    body = "".join(_row(i + 1, label, [f"{v:,}" if v is not None else "-" for v in vals],
                        f"{sum(v for v in vals if v):,}")
                   for i, (label, vals) in enumerate(rows))
    return f'<table id="groupingTable">{_head(cols, group)}<tbody>{body}</tbody></table>'


def test_parse_rows_maps_elapsed_months_and_ignores_total():
    # real shape: S.No | Maker | JAN..JUL | TOTAL  (only elapsed months render)
    html = _row(1, "ATHER ENERGY LTD",
                ["23,100", "21,359", "36,614", "28,634", "28,578", "31,428", "23,585"], "193,298")
    rows = ra.parse_rows(html)
    assert len(rows) == 1
    maker, months = rows[0]
    assert maker == "ATHER ENERGY LTD"
    assert months[:7] == [23100, 21359, 36614, 28634, 28578, 31428, 23585]
    assert months[7:] == [None] * 5          # right-padded to 12
    assert sum(x for x in months if x) == 193298   # the trailing TOTAL is NOT double counted


def test_parse_rows_full_year_uses_all_twelve():
    html = _row(2, "SOME MAKER LTD", [str(i) for i in range(1, 13)], "78")
    _, months = ra.parse_rows(html)[0]
    assert months == list(range(1, 13))


def test_parse_rows_skips_short_and_nameless_rows():
    assert ra.parse_rows('<tr data-ri="1"><td>1</td><td>X</td></tr>') == []
    assert ra.parse_rows(_row(3, "", ["1"], "1")) == []


def test_num_handles_commas_blanks_and_dashes():
    assert ra._num("1,23,456") == 123456
    assert ra._num("<label>42</label>") == 42
    assert ra._num("") is None
    assert ra._num("-") is None
    assert ra._num("NA") is None


def test_code_for_maps_listed_makers():
    assert ra.code_for("HERO MOTOCORP LTD") == "HEROMOTOCO"
    assert ra.code_for("MARUTI SUZUKI INDIA LTD") == "MARUTI"
    assert ra.code_for("TVS MOTOR COMPANY LTD") == "TVSMOTOR"
    assert ra.code_for("ROYAL-ENFIELD (UNIT OF EICHER LTD)") == "EICHERMOT"


def test_code_for_longest_key_wins_for_mahindra_divisions():
    # every Mahindra division rolls up to the listed parent
    assert ra.code_for("MAHINDRA & MAHINDRA LIMITED (SWARAJ DIVISION)") == "M&M"
    assert ra.code_for("MAHINDRA LAST MILE MOBILITY LTD") == "M&M"
    assert ra.code_for("MAHINDRA ELECTRIC AUTOMOBILE LTD") == "M&M"


def test_code_for_unlisted_makers_are_none():
    for m in ("HONDA MOTORCYCLE AND SCOOTER INDIA (P) LTD",
              "TOYOTA KIRLOSKAR MOTOR PVT LTD",
              "KIA INDIA PRIVATE LIMITED",
              "INDIA YAMAHA MOTOR PVT LTD",
              "JCB INDIA LIMITED"):
        assert ra.code_for(m) is None, m


def test_code_for_tolerates_empty():
    assert ra.code_for("") is None
    assert ra.code_for(None) is None

def test_blocked_vahan_keeps_data_and_stays_green(monkeypatch, tmp_path, capsys):
    """Vahan refuses datacenter IPs, so a blocked CI run is EXPECTED.

    It must keep the previous board and exit 0 — failing daily would train
    everyone to ignore red builds, which is how the June price freeze stayed
    invisible for six weeks.
    """
    (tmp_path / "auto.json").write_text('{"years":{}}', encoding="utf-8")

    def _boom(*a, **kw):
        raise RuntimeError("SSLError: curl: (35) Recv failure: Connection reset by peer")

    monkeypatch.setattr(ra, "build", _boom)
    monkeypatch.setattr("sys.argv", ["refresh_auto.py", "--out", str(tmp_path)])
    assert ra.main() == 0
    assert "blocks datacenter IPs" in capsys.readouterr().out
    assert (tmp_path / "auto.json").read_text(encoding="utf-8") == '{"years":{}}'


def test_unexpected_failure_still_fails_the_build(monkeypatch, tmp_path):
    """A layout change must NOT be silently swallowed like an IP block."""
    (tmp_path / "auto.json").write_text('{"years":{}}', encoding="utf-8")

    def _boom(*a, **kw):
        raise RuntimeError("no ViewState - Vahan layout changed")

    monkeypatch.setattr(ra, "build", _boom)
    monkeypatch.setattr("sys.argv", ["refresh_auto.py", "--out", str(tmp_path)])
    assert ra.main() == 1


def test_blocked_with_no_previous_data_still_fails(monkeypatch, tmp_path):
    """Nothing to keep means nothing to publish — that IS a failure."""
    def _boom(*a, **kw):
        raise RuntimeError("SSLError: Connection reset by peer")

    monkeypatch.setattr(ra, "build", _boom)
    monkeypatch.setattr("sys.argv", ["refresh_auto.py", "--out", str(tmp_path)])
    assert ra.main() == 1


# ---------------------------------------------------------------- header parsing

def test_parse_header_reads_month_columns_and_drops_the_group_header():
    """'Month Wise' spans the month columns — it is not a column itself."""
    html = _table(["JAN", "FEB", "MAR"], [("HERO MOTOCORP LTD", [3, 2, 1])], group="Month Wise")
    assert ra.parse_header(html) == ["JAN", "FEB", "MAR"]


def test_parse_header_reads_category_columns_not_months():
    """The category axis puts category names where the months used to be."""
    cols = ["TWO WHEELER(NT)", "THREE WHEELER(T)", "LIGHT MOTOR VEHICLE"]
    html = _table(cols, [("TVS MOTOR COMPANY LTD", [10, 20, 30])])
    assert ra.parse_header(html) == cols          # S No / Maker / TOTAL are structural


def test_parse_header_reads_vcg_group_columns():
    html = _table(["2WN", "2WT", "3WN"], [("BAJAJ AUTO LTD", [1, 2, 3])], group="VCG")
    assert ra.parse_header(html) == ["2WN", "2WT", "3WN"]


def test_parse_header_handles_a_flat_single_row_header():
    html = ('<thead><tr><th>S No</th><th>Maker</th>'
            '<th>TWO WHEELER(NT)</th><th>TOTAL</th></tr></thead>')
    assert ra.parse_header(html) == ["TWO WHEELER(NT)"]


def test_parse_header_ignores_the_y_axis_label_column():
    """With Vehicle Category on the Y axis the label column repeats the axis name."""
    html = ('<thead><tr><th>S No</th><th>Vehicle Category</th>'
            '<th colspan="2">Month Wise</th><th>TOTAL</th></tr>'
            '<tr><th>JAN</th><th>FEB</th></tr></thead>')
    assert ra.parse_header(html) == ["JAN", "FEB"]


# ---------------------------------------------------------------- row parsing

def test_parse_rows_cols_zero_keeps_the_category_width():
    """Category passes have as many columns as the header says — never 12."""
    cols = ["TWO WHEELER(NT)", "THREE WHEELER(T)", "LIGHT MOTOR VEHICLE"]
    html = _table(cols, [("TVS MOTOR COMPANY LTD", [11, 22, 33])])
    rows = ra.parse_rows(html, cols=0)
    assert rows == [("TVS MOTOR COMPANY LTD", [11, 22, 33])]
    assert len(rows[0][1]) == len(ra.parse_header(html))


def test_parse_rows_keeps_gaps_inside_a_category_row():
    html = _table(["A", "B", "C"], [("SOME MAKER LTD", [5, None, 7])])
    assert ra.parse_rows(html, cols=0) == [("SOME MAKER LTD", [5, None, 7])]


def test_parse_rows_row_label_can_be_a_vehicle_category():
    """The industry base pass puts categories, not makers, in the label column."""
    html = _table(["JAN", "FEB"], [("TWO WHEELER(NT)", [100, 200])], group="Month Wise")
    assert ra.parse_rows(html, cols=0) == [("TWO WHEELER(NT)", [100, 200])]


def test_year_pads_month_columns_to_twelve(monkeypatch):
    """The month-wise pass still fills a fixed 12-slot board (no network)."""
    v = ra.Vahan.__new__(ra.Vahan)                       # no HTTP session created
    monkeypatch.setattr(v, "scrape", lambda *a, **kw: (["JAN", "FEB"], {"X LTD": [5, 6]}))
    assert v.year(2026) == {"X LTD": [5, 6] + [None] * 10}


# ---------------------------------------------------------------- category pivot

def _scan(year_cols_rows):
    return {y: (cols, rows) for y, cols, rows in year_cols_rows}


def test_pivot_indexes_each_year_by_its_own_headers():
    """A category Vahan did not render in 2024 shifts every later column —
    indexing 2024 by 2026's positions would silently mis-file the numbers."""
    scan = _scan([
        (2026, ["2WN", "3WN", "4WN"], {"HERO MOTOCORP LTD": [900, 10, 5]}),
        (2024, ["2WN", "4WN"], {"HERO MOTOCORP LTD": [700, 3]}),      # no 3WN that year
    ])
    cols, by = ra._pivot(scan)
    assert cols == ["2WN", "3WN", "4WN"]
    assert by["2WN"]["years"] == ["2024", "2026"]
    assert by["2WN"]["makers"][0]["values"] == [700, 900]
    assert by["4WN"]["makers"][0]["values"] == [3, 5]                 # not 700
    assert by["3WN"]["makers"][0]["values"] == [None, 10]


def test_category_payload_shape_matches_the_contract():
    cat = _scan([(2026, ["TWO WHEELER(NT)"], {"HERO MOTOCORP LTD": [900],
                                              "TVS MOTOR COMPANY LTD": [500]})])
    grp = _scan([(2026, ["2WN"], {"HERO MOTOCORP LTD": [900]})])
    p = ra.category_payload(cat, grp)
    assert set(p) == {"categories", "groups", "by_category", "by_group"}
    assert p["categories"] == ["TWO WHEELER(NT)"] and p["groups"] == ["2WN"]
    rec = p["by_category"]["TWO WHEELER(NT)"]["makers"][0]
    assert rec == {"maker": "HERO MOTOCORP LTD", "code": "HEROMOTOCO",
                   "values": [900], "total": 900}                     # biggest first
    assert p["by_category"]["TWO WHEELER(NT)"]["years"] == ["2026"]   # years are strings


def test_category_payload_top_cut_and_zero_rows():
    cat = _scan([(2026, ["A"], {"BIG LTD": [900], "SMALL LTD": [5], "ZERO LTD": [None]})])
    p = ra.category_payload(cat, {}, top=1)
    assert [r["maker"] for r in p["by_category"]["A"]["makers"]] == ["BIG LTD"]
    assert p["groups"] == [] and p["by_group"] == {}                  # a missing pass is empty, not fatal


# ---------------------------------------------------------------- industry series

def test_aggregate_columns_sums_every_maker():
    headers = ["2WN", "3WN"]
    rows = {"A LTD": [10, 1], "B LTD": [20, None], "C LTD": [5]}      # ragged: C has no 3WN cell
    assert ra.aggregate_columns(headers, rows) == {"2WN": 35, "3WN": 1}


def test_months_elapsed_counts_rendered_months():
    assert ra.months_elapsed({"TWO WHEELER(NT)": [1, 2, 3, None, None]}) == 3
    assert ra.months_elapsed({"A": [1], "B": [1, 2, 3, 4, 5, 6, 7]}) == 7
    assert ra.months_elapsed({"A": [None] * 12}) == 0


def test_industry_series_full_years_chain_yoy():
    totals = {2023: {"2WN": 100}, 2024: {"2WN": 150}, 2025: {"2WN": 300}}
    pts = ra.industry_series(totals)["series"]["2WN"]
    assert [p["year"] for p in pts] == [2023, 2024, 2025]
    assert pts[0]["yoy"] is None                                      # nothing to compare against
    assert pts[1]["yoy"] == 50.0 and pts[2]["yoy"] == 100.0
    assert all(p["partial"] is False and "extrapolated" not in p for p in pts)


def test_industry_series_partial_year_extrapolates_and_compares_like_for_like():
    totals = {2025: {"2WN": 1200}, 2026: {"2WN": 700}}
    pts = ra.industry_series(totals, partial_year=2026, elapsed=7,
                             prior_ytd={"2WN": 500})["series"]["2WN"]
    cur = pts[-1]
    assert cur["partial"] is True and cur["months_elapsed"] == 7
    assert cur["extrapolated"] == 1200                                # 700 / 7 * 12
    assert cur["yoy"] == 40.0                                         # 700 vs 500, NOT vs 1200
    assert pts[0]["yoy"] is None and pts[0]["partial"] is False


def test_industry_series_partial_year_yoy_is_null_without_a_like_for_like_base():
    """A 7-month total against a 12-month total would print a fake collapse."""
    totals = {2025: {"2WN": 1200}, 2026: {"2WN": 700}}
    cur = ra.industry_series(totals, partial_year=2026, elapsed=7)["series"]["2WN"][-1]
    assert cur["yoy"] is None
    assert cur["total"] == 700 and cur["extrapolated"] == 1200


def test_industry_series_never_compares_a_full_year_against_a_partial_one():
    totals = {2026: {"2WN": 700}, 2027: {"2WN": 1300}}
    pts = ra.industry_series(totals, partial_year=2026, elapsed=7)["series"]["2WN"]
    assert pts[-1]["year"] == 2027 and pts[-1]["yoy"] is None


def test_industry_series_matches_categories_case_insensitively():
    totals = {2026: {"Two Wheeler(NT)": 700}}
    cur = ra.industry_series(totals, partial_year=2026, elapsed=7,
                             prior_ytd={"TWO WHEELER (NT)": 700})["series"]["Two Wheeler(NT)"]
    assert cur[-1]["yoy"] == 0.0


def test_industry_series_skips_categories_with_no_volume():
    totals = {2026: {"2WN": 700, "DEAD": 0}}
    assert list(ra.industry_series(totals)["series"]) == ["2WN"]


def test_industry_payload_derives_elapsed_and_prior_from_the_month_base():
    from datetime import datetime
    now = datetime(2026, 7, 27, tzinfo=ra.IST)
    scan = _scan([(2025, ["2WN"], {"A LTD": [600], "B LTD": [600]}),
                  (2026, ["2WN"], {"A LTD": [400], "B LTD": [300]})])
    base = _scan([
        (2026, ra.MONTHS[:3], {"2WN": [300, 200, 200]}),               # 3 months rendered
        (2025, ra.MONTHS, {"2WN": [100, 100, 100] + [100] * 9}),       # same 3 months = 300
    ])
    pts = ra.industry_payload(scan, base=base, now=now)["series"]["2WN"]
    cur = pts[-1]
    assert cur["year"] == 2026 and cur["total"] == 700
    assert cur["months_elapsed"] == 3 and cur["extrapolated"] == 2800
    assert cur["yoy"] == round((700 / 300 - 1) * 100, 1)               # like-for-like, not 700 vs 1200


def test_industry_payload_without_a_base_falls_back_to_the_calendar():
    from datetime import datetime
    now = datetime(2026, 7, 27, tzinfo=ra.IST)
    scan = _scan([(2025, ["2WN"], {"A LTD": [1200]}), (2026, ["2WN"], {"A LTD": [700]})])
    cur = ra.industry_payload(scan, base={}, now=now)["series"]["2WN"][-1]
    assert cur["months_elapsed"] == 7 and cur["yoy"] is None
    assert cur["extrapolated"] == 1200


# ---------------------------------------------------------------- run control

class _FakeVahan:
    """Records what was asked for; never touches the network."""

    def __init__(self):
        self.calls = []

    def scrape(self, axis, y, max_pages=0, pause=0.4, yaxis="Maker", label=""):
        self.calls.append((axis, y))
        return ["2WN"], {"HERO MOTOCORP LTD": [y]}

    def year(self, y, max_pages=0, pause=0.4):
        self.calls.append(("Month Wise", y))
        return {"HERO MOTOCORP LTD": [1] + [None] * 11}


class _FakeSession:
    def __init__(self, v):
        self.v = v

    def get(self):
        return self.v


def _spent():
    return ra.Deadline(limit_s=60, start=time.monotonic() - 3600)


def test_max_minutes_stops_the_company_pass_between_years():
    fv = _FakeVahan()
    data = ra.build([2026, 2025, 2024], 0, 0, session=_FakeSession(fv), deadline=_spent())
    assert fv.calls == [("Month Wise", 2026)]            # stopped between years …
    assert list(data) == ["2026"]                        # … and kept what it gathered


def test_max_minutes_stops_the_category_pass_between_years():
    fv = _FakeVahan()
    scan = ra.collect(_FakeSession(fv), ra.AXIS_CATEGORY, [2024, 2025, 2026],
                      deadline=_spent(), label="category")
    assert list(scan) == [2026]                          # newest year first
    assert fv.calls == [(ra.AXIS_CATEGORY, 2026)]


def test_deadline_without_a_limit_never_expires():
    assert ra.Deadline(0).expired is False
    assert _spent().expired is True


# ---------------------------------------------------------------- per-file keep-last-good

_COMPANY = {"2026": {"makers": [{"maker": "HERO MOTOCORP LTD", "code": "HEROMOTOCO",
                                 "months": [1] + [None] * 11, "total": 1}]}}


def _fake_collect(session, axis, years, max_pages=0, deadline=None, yaxis=ra.Y_MAKER, label=""):
    ys = sorted({int(y) for y in years}, reverse=True)
    if axis == ra.AXIS_MONTH:                            # the industry like-for-like base
        return {y: (ra.MONTHS[:2], {"TWO WHEELER(NT)": [10, 20]}) for y in ys}
    cols = ["TWO WHEELER(NT)"] if axis == ra.AXIS_CATEGORY else ["2WN"]
    return {y: (cols, {"HERO MOTOCORP LTD": [100 + (y - 2000)]}) for y in ys}


def test_category_failure_never_blanks_the_company_board(monkeypatch, tmp_path):
    """Each board is written on its own — one broken pass must not take the rest down."""
    (tmp_path / "auto_category.json").write_text('{"old":"cat"}', encoding="utf-8")
    (tmp_path / "auto_industry.json").write_text('{"old":"ind"}', encoding="utf-8")

    def _boom(*a, **kw):
        raise ValueError("category axis returned garbage")

    monkeypatch.setattr(ra, "build", lambda *a, **kw: _COMPANY)
    monkeypatch.setattr(ra, "collect", _boom)
    monkeypatch.setattr("sys.argv", ["refresh_auto.py", "--out", str(tmp_path)])
    assert ra.main() == 1                                        # the run is not green …
    fresh = json.loads((tmp_path / "auto.json").read_text(encoding="utf-8"))
    assert fresh["latest_year"] == "2026" and fresh["months"] == ra.MONTHS
    assert (tmp_path / "auto_category.json").read_text(encoding="utf-8") == '{"old":"cat"}'
    assert (tmp_path / "auto_industry.json").read_text(encoding="utf-8") == '{"old":"ind"}'


def test_company_failure_still_refreshes_the_other_boards(monkeypatch, tmp_path):
    (tmp_path / "auto.json").write_text('{"old":"company"}', encoding="utf-8")

    def _boom(*a, **kw):
        raise ValueError("month axis returned garbage")

    monkeypatch.setattr(ra, "build", _boom)
    monkeypatch.setattr(ra, "collect", _fake_collect)
    monkeypatch.setattr("sys.argv", ["refresh_auto.py", "--out", str(tmp_path),
                                     "--cat-years", "2", "--industry-years", "3"])
    assert ra.main() == 1
    assert (tmp_path / "auto.json").read_text(encoding="utf-8") == '{"old":"company"}'
    cat = json.loads((tmp_path / "auto_category.json").read_text(encoding="utf-8"))
    assert cat["categories"] == ["TWO WHEELER(NT)"] and cat["groups"] == ["2WN"]
    ind = json.loads((tmp_path / "auto_industry.json").read_text(encoding="utf-8"))
    assert len(ind["series"]["TWO WHEELER(NT)"]) == 3             # --industry-years 3


def test_skip_flags_leave_every_board_alone(monkeypatch, tmp_path):
    def _boom(*a, **kw):
        raise AssertionError("nothing should be scraped")

    monkeypatch.setattr(ra, "build", _boom)
    monkeypatch.setattr(ra, "collect", _boom)
    monkeypatch.setattr("sys.argv", ["refresh_auto.py", "--out", str(tmp_path),
                                     "--skip-company", "--skip-category", "--skip-industry"])
    assert ra.main() == 0
    assert list(tmp_path.iterdir()) == []


def test_a_blocked_run_skips_the_remaining_passes(monkeypatch, tmp_path, capsys):
    """One connection reset means every pass would reset — do not hammer Vahan."""
    for name in ("auto.json", "auto_category.json", "auto_industry.json"):
        (tmp_path / name).write_text("{}", encoding="utf-8")

    def _blocked(*a, **kw):
        raise RuntimeError("SSLError: curl: (35) Recv failure: Connection reset by peer")

    def _never(*a, **kw):
        raise AssertionError("the category/industry passes must not be attempted")

    monkeypatch.setattr(ra, "build", _blocked)
    monkeypatch.setattr(ra, "collect", _never)
    monkeypatch.setattr("sys.argv", ["refresh_auto.py", "--out", str(tmp_path)])
    assert ra.main() == 0                                        # expected in CI, stay green
    out = capsys.readouterr().out
    assert "not attempted" in out and "blocks datacenter IPs" in out


def test_company_pass_stamps_meta_with_every_year(monkeypatch, tmp_path):
    data = {"2026": _COMPANY["2026"], "2025": _COMPANY["2026"]}
    monkeypatch.setattr(ra, "build", lambda *a, **kw: data)
    monkeypatch.setattr(ra, "collect", _fake_collect)
    monkeypatch.setattr("sys.argv", ["refresh_auto.py", "--out", str(tmp_path),
                                     "--cat-years", "1", "--industry-years", "2"])
    assert ra.main() == 0
    meta = json.loads((tmp_path / "auto_meta.json").read_text(encoding="utf-8"))
    assert meta["years"] == ["2026", "2025"] and meta["makers"] == 1
    assert meta["categories"] == 1 and meta["industry_categories"] == 1
    assert all(isinstance(y, str) for y in meta["category_years"] + meta["industry_years"])
    assert "IST" in meta["generated_at_ist"]


def test_progress_output_survives_a_cp1252_console():
    """A Windows console is cp1252: one '->' in a progress line aborts the pass
    mid-scrape, and the board it was building never gets written."""
    src = (ROOT / "scripts" / "refresh_auto.py").read_text(encoding="utf-8")
    for i, line in enumerate(src.splitlines(), 1):
        try:
            line.encode("cp1252")
        except UnicodeEncodeError as e:
            raise AssertionError(f"refresh_auto.py:{i} is not printable on cp1252: {e}") from None
