"""Boards that fall back to the bundles when the screen scrape fails.

In September 2026 a renamed column header made every screen read as empty.
TechnoFunda survived because it unions the screen with the bundles on disk;
the sector board, the three IV boards that read its universe, and Magic
Formula froze for three weeks, and most of them still exited 0.

These pin the fallback: a board built from bundles when the scrape gives
nothing, a run that still goes red when it does, and no double-counting of a
company held under both its BSE number and its NSE symbol.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from earnings_intel import magicformula as mf  # noqa: E402
from earnings_intel.data import boarduniverse as bu  # noqa: E402
from earnings_intel.data import sectors as S  # noqa: E402


def _bundle(name="Acme Ltd", industry="Chemicals", mcap=1000, price=250, roce=18.0,
            pe=20.0, feed_ev=None, bank=False, pl_year=2026, q_last="Jun 2026",
            pbt=100.0, interest=10.0, dep=20.0, debt=200.0, pos_52w=70.0, rs=60):
    pl_rows = {"Profit before tax": [80.0, pbt], "Interest": [5.0, interest],
               "Depreciation": [15.0, dep]}
    if bank:
        pl_rows["Financing Profit"] = [1.0, 2.0]
    b = {
        "fundamental": {
            "name": name,
            "classification": {"industry": industry},
            "overview": {"Market Cap": f"₹ {mcap:,} Cr.", "Current Price": f"₹ {price}",
                         "Stock P/E": str(pe), "ROCE": f"{roce} %"},
            "quarters": {"headers": ["Jun 2025", "Sep 2025", "Dec 2025", "Mar 2026", q_last],
                         "rows": {"Sales +": [100, 1, 1, 1, 120],
                                  "Net Profit +": [10, 1, 1, 1, 13]}},
            "profit_loss": {"headers": [f"Mar {pl_year - 1}", f"Mar {pl_year}"], "rows": pl_rows},
            "balance_sheet": {"headers": ["Mar 2025", "Mar 2026"],
                              "rows": {"Borrowings": [150.0, debt]}},
            "shareholding": {"rows": {"FIIs +": [3.7, 3.98]}},
        },
        "prices": {"ok": True, "technical": {"rs_rating": rs, "pos_52w": pos_52w}},
    }
    if feed_ev is not None:
        b["upstox_ratios"] = {"ev_ebitda": {"value": feed_ev}}
    return b


# ------------------------------------------------------------ dual listings

def test_a_dual_listing_is_counted_once_and_the_first_code_wins():
    rows = [{"code": "HINDMOTORS", "name": "Hindustan Motors Ltd"},
            {"code": "500500", "name": "Hindustan Motors Limited"}]
    assert [r["code"] for r in bu.dedupe_listings(rows)] == ["HINDMOTORS"]
    assert [r["code"] for r in bu.dedupe_listings(rows[::-1])] == ["500500"]


def test_two_different_companies_with_one_name_both_stay():
    """Only a BSE-number / symbol pair is a dual listing. Two symbols that
    normalise to the same name are two companies until proven otherwise."""
    rows = [{"code": "ABC", "name": "ABC Ltd"}, {"code": "ABCX", "name": "ABC Limited"},
            {"code": "500001", "name": "Other Co"}, {"code": "500002", "name": "Other Co"}]
    assert len(bu.dedupe_listings(rows)) == 4


# ------------------------------------------------------- sector membership

def test_membership_snapshot_beats_the_bundle_classification():
    pairs = [("SRF", _bundle("SRF Ltd", industry="Chemicals"))]
    rows, unplaced = bu.sector_rows_from_bundles(pairs, {"SRF": "Capital Goods"})
    assert rows[0]["sector"] == "Capital Goods"
    assert rows[0]["sector_code"] == S.CODE_OF["Capital Goods"]
    assert rows[0]["src"] == "bundle" and unplaced == 0


def test_a_new_listing_is_placed_by_its_industry_and_an_alias_is_honoured():
    pairs = [("NEWCO", _bundle("NewCo", industry="Fast Moving Consumer Goods"))]
    rows, _ = bu.sector_rows_from_bundles(pairs, {})
    assert rows[0]["sector"] == "FMCG"


def test_a_company_with_no_sector_is_left_out_not_grouped_as_unknown():
    pairs = [("X", _bundle(industry="")), ("Y", _bundle(industry="Chemicals"))]
    rows, unplaced = bu.sector_rows_from_bundles(pairs, {})
    assert [r["code"] for r in rows] == ["Y"] and unplaced == 1


def test_macro_sector_is_not_mistaken_for_an_industry():
    """classification['sector'] is a different 12-way taxonomy."""
    b = _bundle(industry="")
    b["fundamental"]["classification"]["sector"] = "Commodities"
    assert bu.sector_rows_from_bundles([("X", b)], {})[0] == []


def test_bundle_rows_carry_breadth_and_strength_inputs():
    rows, _ = bu.sector_rows_from_bundles([("A", _bundle(pos_52w=70.0, rs=60))], {})
    assert rows[0]["pos_52w"] == 70.0 and rows[0]["rs_rating"] == 60
    assert rows[0]["fii_chg"] == 0.28


# ------------------------------------------------------------ EV / EBITDA

def test_the_ratio_feed_leads_for_ev_ebitda():
    assert bu.ev_ebitda_of(_bundle(feed_ev=12.34), 1000) == (12.34, "feed")


def test_ev_ebitda_is_derived_from_statements_without_a_feed_value():
    # EV 1000 + 200 debt over EBITDA 100 + 10 + 20
    value, src = bu.ev_ebitda_of(_bundle(), 1000)
    assert src == "derived" and value == pytest.approx(1200 / 130, abs=0.01)


def test_ev_ebitda_is_never_derived_for_a_lender():
    """Statement-derived EV/EBITDA made every bank look several times cheaper."""
    assert bu.ev_ebitda_of(_bundle(bank=True), 1000) == (None, None)
    assert bu.is_bank_format(_bundle(bank=True))


def test_stale_statements_derive_nothing():
    assert bu.ev_ebitda_of(_bundle(pl_year=2019), 1000) == (None, None)
    assert bu.ev_ebitda_of(_bundle(q_last="Jun 2019"), 1000) == (None, None)


def test_a_lender_is_flagged_financial_by_its_statements():
    rows = [dict(bu.magic_row_from_bundle("HOLDCO", _bundle("XYZ Holdings", bank=True,
                                                            feed_ev=5.0)))]
    assert mf.compute(rows)[0]["fin"] == 1


# ------------------------------------------------------ Magic Formula script

def _write(fdir: Path, code: str, bundle: dict):
    fdir.mkdir(parents=True, exist_ok=True)
    (fdir / f"{code}.json").write_text(json.dumps(bundle), encoding="utf-8")


def test_magic_formula_bundle_rows_apply_the_screen_conditions(tmp_path):
    import refresh_magicformula as rm
    fdir = tmp_path / "fundamental"
    _write(fdir, "GOOD", _bundle("Good Ltd", feed_ev=8.0))
    _write(fdir, "NEGROCE", _bundle("Neg Ltd", roce=-3.0, feed_ev=8.0))
    _write(fdir, "BANK", _bundle("Some Bank", bank=True))          # no EV at all
    _write(fdir, "HINDMOTORS", _bundle("Hindustan Motors Ltd", feed_ev=9.0))
    _write(fdir, "500500", _bundle("Hindustan Motors Limited", feed_ev=9.0))
    rows = rm.bundle_rows(fdir, prefer={"HINDMOTORS"})
    assert sorted(r["code"] for r in rows) == ["GOOD", "HINDMOTORS"]


def _magic_dir(tmp_path, n=6):
    fdir = tmp_path / "fundamental"
    for i in range(n):
        _write(fdir, f"C{i}", _bundle(f"Company {i}", roce=10 + i, feed_ev=5 + i))
    return fdir


def test_magic_formula_rebuilds_from_bundles_and_still_goes_red(tmp_path, monkeypatch):
    import refresh_magicformula as rm
    from earnings_intel.data import screener as sc

    def boom(self, query, max_pages=200):
        raise sc.ScreenLayoutError("header unrecognised")
    monkeypatch.setattr(sc.ScreenerClient, "fetch_screen", boom)
    out = tmp_path / "out"
    rc = rm.main(["--fundamental", str(_magic_dir(tmp_path)), "--out", str(out),
                  "--min-rows", "3"])
    assert rc == 1, "a fallback board must not pass for a healthy run"
    meta = json.loads((out / "magicformula_meta.json").read_text(encoding="utf-8"))
    assert meta["screen_ok"] is False and meta["ranked"] == 6
    assert meta["ev_ebitda_from"] == {"feed": 6}
    text = json.dumps(meta).lower()
    assert "screener" not in text and "upstox" not in text


def test_magic_formula_bundles_mode_is_a_clean_run(tmp_path):
    import refresh_magicformula as rm
    rc = rm.main(["--source", "bundles", "--fundamental", str(_magic_dir(tmp_path)),
                  "--out", str(tmp_path / "out"), "--min-rows", "3"])
    assert rc == 0


def test_magic_formula_refuses_a_thin_bundle_board(tmp_path):
    import refresh_magicformula as rm
    out = tmp_path / "out"
    rc = rm.main(["--source", "bundles", "--fundamental", str(_magic_dir(tmp_path, 2)),
                  "--out", str(out), "--min-rows", "3"])
    assert rc == 1 and not (out / "magicformula.json").exists()


# ------------------------------------------------------------ sector script

def _sector_dir(tmp_path):
    """One company per sector, so every one of the 22 is present."""
    fdir = tmp_path / "fundamental"
    for i, (code, name) in enumerate(S.SECTORS.items()):
        _write(fdir, f"S{i:02d}", _bundle(f"Sector co {i}", industry=name,
                                          pos_52w=30.0 + i, rs=40 + i))
    return fdir


def _run_sectors(tmp_path, *extra):
    import refresh_sectors as rs
    out = tmp_path / "out"; out.mkdir(exist_ok=True)
    return rs.main(["--fundamental", str(_sector_dir(tmp_path)), "--out", str(out),
                    "--cache", str(tmp_path / "cache"), "--min-rows", "10", *extra]), out


def test_sector_board_builds_from_bundles_when_the_pages_fail(tmp_path, monkeypatch):
    from earnings_intel.data import screener as sc

    def boom(*a, **k):
        raise sc.ScreenLayoutError("header unrecognised")
    monkeypatch.setattr(S, "fetch_sectors", boom)
    rc, out = _run_sectors(tmp_path)
    assert rc == 1, "published from bundles, and still red"
    res = json.loads((out / "sector_tailwind.json").read_text(encoding="utf-8"))
    assert res["full_market"]["sectors"] == 22
    assert res["source"] == {"mode": "bundles", "screen_ok": False, "screen_rows": 0,
                             "bundle_rows": 22, "unplaced": 0}
    assert all(s["breadth_pct"] is not None for s in res["sectors"])
    uni = json.loads((tmp_path / "cache" / "universe.json").read_text(encoding="utf-8"))
    assert len(uni) == 22 and {r["src"] for r in uni} == {"bundle"}
    hist = json.loads((out / "sector_history.json").read_text(encoding="utf-8"))
    assert hist[-1]["source"] == "bundles"


def test_sector_board_bundles_mode_is_a_clean_run(tmp_path):
    rc, out = _run_sectors(tmp_path, "--source", "bundles")
    assert rc == 0 and (out / "sector_stocks.json").exists()


def _screen_rows(skip=()):
    """One live row per sector, for the same codes the bundles hold."""
    return [{"code": f"S{i:02d}", "name": f"Sector co {i}", "cmp": 999.0, "mcap": 5000.0,
             "sector": name, "sector_code": code}
            for i, (code, name) in enumerate(S.SECTORS.items()) if name not in skip]


def test_the_pages_win_a_contested_code(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "fetch_sectors", lambda *a, **k: _screen_rows())
    rc, out = _run_sectors(tmp_path)
    assert rc == 0
    res = json.loads((out / "sector_tailwind.json").read_text(encoding="utf-8"))
    assert res["source"]["mode"] == "screen" and res["source"]["screen_ok"] is True
    stocks = json.loads((out / "sector_stocks.json").read_text(encoding="utf-8"))
    s00 = [r for r in stocks["sectors"]["Chemicals"] if r["code"] == "S00"][0]
    assert s00["ltp"] == 999.0, "the live price, not the bundle's"


def test_a_scrape_that_missed_sectors_is_partial_and_red(tmp_path, monkeypatch):
    """Bundles fill the missing sectors, so the board looks whole -- which is
    exactly why the run has to say it was not."""
    monkeypatch.setattr(S, "fetch_sectors",
                        lambda *a, **k: _screen_rows(skip=("Power", "Realty")))
    rc, out = _run_sectors(tmp_path)
    assert rc == 1
    res = json.loads((out / "sector_tailwind.json").read_text(encoding="utf-8"))
    assert res["full_market"]["sectors"] == 22
    assert res["source"]["mode"] == "partial" and res["source"]["screen_ok"] is False
    assert sorted(res["source"]["filled_sectors"]) == ["Power", "Realty"]


def test_a_dual_listing_matches_across_abbreviated_and_full_names(tmp_path):
    """The pages abbreviate ("Permanent Magnet"), bundles spell it out."""
    import refresh_sectors as rs
    pairs = [("PERMAGN", _bundle("Permanent Magnets Ltd")),
             ("504132", _bundle("Permanent Magnets Ltd"))]
    screen = [{"code": "PERMAGN", "name": "Permanent Magnet", "cmp": 1.0, "mcap": 1.0,
               "sector": "Chemicals", "sector_code": "IN0101"}]
    rows, stats = rs.build_universe(screen, pairs, {})
    assert [r["code"] for r in rows] == ["PERMAGN"]
    assert stats == {"screen_rows": 1, "bundle_rows": 0, "unplaced": 0}


def test_growth_from_a_long_dead_quarter_table_is_blanked():
    """One large cap's consolidated quarters ended in March 2015."""
    old = _bundle(q_last="Mar 2015")
    old["fundamental"]["quarters"]["headers"] = ["Mar 2014", "Jun 2014", "Sep 2014",
                                                 "Dec 2014", "Mar 2015"]
    fresh = _bundle()
    cutoff = bu.quarter_index("Dec 2025")
    rows, _ = bu.sector_rows_from_bundles([("OLD", old), ("NEW", fresh)], {},
                                          min_quarter=cutoff)
    by = {r["code"]: r for r in rows}
    assert by["OLD"]["profit_var"] is None and by["OLD"]["sales_var"] is None
    assert by["OLD"]["mcap"] == 1000.0 and by["OLD"]["pos_52w"] == 70.0, "the row stays"
    assert by["NEW"]["profit_var"] == 30.0


def test_a_board_missing_a_sector_is_not_published(tmp_path):
    import refresh_sectors as rs
    fdir = _sector_dir(tmp_path)
    (fdir / "S00.json").unlink()
    out = tmp_path / "out"; out.mkdir()
    rc = rs.main(["--source", "bundles", "--fundamental", str(fdir), "--out", str(out),
                  "--cache", str(tmp_path / "cache"), "--min-rows", "10"])
    assert rc == 1 and not (out / "sector_tailwind.json").exists()


def test_only_writes_nothing(tmp_path):
    rc, out = _run_sectors(tmp_path, "--source", "bundles", "--only", "IN0101")
    assert rc == 0 and not (out / "sector_tailwind.json").exists()
    assert not (tmp_path / "cache" / "universe.json").exists()


def test_membership_is_read_from_the_last_drill_down(tmp_path):
    import refresh_sectors as rs
    p = tmp_path / "sector_stocks.json"
    p.write_text(json.dumps({"sectors": {"Power": [{"code": "ntpc"}],
                                         "Unknown": [{"code": "X"}]}}), encoding="utf-8")
    assert rs.membership(p) == {"NTPC": "Power"}


def test_problems_name_a_dead_breadth_input():
    import refresh_sectors as rs
    rows = [{"code": f"C{i}", "sector": name, "profit_var": i, "sales_var": i,
             "roce": i, "fii_chg": i, "pos_52w": None}
            for i, name in enumerate(list(S.SECTORS.values()) * 3)]
    problems = rs.problems_with(rows, min_rows=10)
    assert any("pos_52w" in p for p in problems)


# ------------------------------------------------------ TechnoFunda's cache

def test_technofunda_ignores_a_bundle_built_universe(tmp_path, monkeypatch):
    import refresh_technofunda as rt
    from earnings_intel.data import screener as sc
    (tmp_path / ".cache").mkdir()
    (tmp_path / ".cache" / "universe.json").write_text(
        json.dumps([{"code": "A", "src": "bundle"}]), encoding="utf-8")
    monkeypatch.setattr(rt, "ROOT", tmp_path)
    monkeypatch.setattr(sc.ScreenerClient, "fetch_screen",
                        lambda self, q, max_pages=1: [{"code": "LIVE"}])
    assert rt.universe(None, "q", 1) == [{"code": "LIVE"}]


def test_technofunda_takes_only_the_screened_rows_of_a_mixed_universe(tmp_path, monkeypatch):
    import refresh_technofunda as rt
    (tmp_path / ".cache").mkdir()
    (tmp_path / ".cache" / "universe.json").write_text(json.dumps(
        [{"code": "A", "src": "screen"}, {"code": "B", "src": "bundle"}, {"code": "C"}]),
        encoding="utf-8")
    monkeypatch.setattr(rt, "ROOT", tmp_path)
    assert [r["code"] for r in rt.universe(None, "q", 1)] == ["A", "C"]
