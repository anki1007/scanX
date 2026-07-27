"""Tests for the Market Mood board — score bands, breadth counting, RSI.

Pure-function tests only (no network): the yfinance download path is
exercised in production by refresh_marketmood.py itself.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import refresh_marketmood as mm   # noqa: E402


# ------------------------------------------------------------- mood score
def test_mood_score_neutral_market():
    # half below 50/200 DMA + flat day -> exactly 50
    assert mm.mood_score(50, 50, 0.0) == 50.0


def test_mood_score_strong_market():
    s = mm.mood_score(10, 20, 1.5)
    # 0.4*90 + 0.3*80 + 0.3*65 = 36+24+19.5
    assert s == 79.5


def test_mood_score_weak_market():
    s = mm.mood_score(90, 80, -2.0)
    # 0.4*10 + 0.3*20 + 0.3*30 = 4+6+9
    assert s == 19.0


def test_mood_score_clamps_avg_pct():
    # avg move +20% would blow past the clamp: 0.3 leg maxes at 30
    assert mm.mood_score(0, 0, 20.0) == 100.0
    assert mm.mood_score(100, 100, -20.0) == 0.0


def test_mood_score_none_inputs_are_neutral():
    assert mm.mood_score(None, None, None) == 50.0


def test_mood_label_bands():
    assert mm.mood_label(80) == "Ex Strong"
    assert mm.mood_label(75) == "Ex Strong"
    assert mm.mood_label(74.9) == "Strong"
    assert mm.mood_label(50) == "Strong"
    assert mm.mood_label(49.9) == "Weak"
    assert mm.mood_label(25) == "Weak"
    assert mm.mood_label(24.9) == "Ex Weak"
    assert mm.mood_label(0) == "Ex Weak"


# ---------------------------------------------------------------- breadth
def _fixture_quotes():
    return {
        "AAA": {"ltp": 100, "pct": 0.5},
        "BBB": {"ltp": 100, "pct": -1.2},
        "CCC": {"ltp": 100, "pct": 3.5},     # up3
        "DDD": {"ltp": 100, "pct": -4.0},    # down3
        "EEE": {"ltp": 100, "pct": 6.0},     # up3 + up5
        "FFF": {"ltp": 100, "pct": -11.0},   # down3 + down5 + down10
        "GGG": {"ltp": 100, "pct": 12.0},    # up3 + up5 + up10
        "HHH": {"ltp": 100, "pct": 0.0},     # flat
        "III": {"ltp": 100, "pct": None},    # ignored
        "JJJ": "garbage",                    # ignored
    }


def test_breadth_counts_from_fixture():
    b = mm.breadth_from_quotes(_fixture_quotes())
    assert b["n"] == 8
    assert b["up"] == 4 and b["down"] == 3 and b["flat"] == 1
    assert b["up3"] == 3 and b["down3"] == 2
    assert b["up5"] == 2 and b["down5"] == 1
    assert b["up10"] == 1 and b["down10"] == 1
    # (0.5-1.2+3.5-4+6-11+12+0)/8 = 0.725 -> 0.72/0.73 by rounding
    assert abs(b["avg_pct"] - 0.72) <= 0.01


def test_breadth_empty_quotes():
    b = mm.breadth_from_quotes({})
    assert b["n"] == 0 and b["up"] == 0 and b["down"] == 0
    assert b["avg_pct"] == 0.0


# -------------------------------------------------------------------- RSI
def test_rsi_all_gains_is_100():
    assert mm.rsi14([float(x) for x in range(1, 40)]) == 100.0


def test_rsi_all_losses_is_0():
    assert mm.rsi14([float(x) for x in range(40, 1, -1)]) == 0.0


def test_rsi_mixed_series_in_middle():
    # alternating +1/-1 -> gains ~ losses -> RSI near 50
    series, v = [], 100.0
    for i in range(60):
        v += 1.0 if i % 2 == 0 else -1.0
        series.append(v)
    r = mm.rsi14(series)
    assert 40.0 <= r <= 60.0


def test_rsi_too_short_returns_none():
    assert mm.rsi14([1.0, 2.0, 3.0]) is None


def test_rsi_wilder_reference_value():
    # deterministic check: uptrend with one dip keeps RSI high but < 100
    closes = [100 + i for i in range(20)]
    closes[10] = closes[9] - 2.0     # single down day
    r = mm.rsi14(closes)
    assert r is not None and 70.0 < r < 100.0


# ------------------------------------------------------------ ticker map
def test_yf_symbol_mapping_matches_refresh_scanx():
    assert mm.yf_symbol("RELIANCE") == "RELIANCE.NS"
    assert mm.yf_symbol("543320") == "543320.BO"
    assert mm.yf_symbol(" tcs ") == "TCS.NS"


def test_clamp():
    assert mm.clamp(120, 0, 100) == 100
    assert mm.clamp(-5, 0, 100) == 0
    assert mm.clamp(42, 0, 100) == 42


# ------------------------------------------------- main() (no network)
def _quotes_file(tmp_path, date="2026-07-25"):
    import json
    p = tmp_path / "quotes.json"
    p.write_text(json.dumps({"date": date, "quotes": _fixture_quotes()}),
                 encoding="utf-8")
    return p


def test_main_keeps_previous_file_when_yahoo_fails(tmp_path, monkeypatch):
    import json
    out = tmp_path / "out"
    out.mkdir()
    keep = {"history": [{"date": "2026-07-24", "score": 61.0, "label": "Strong"}]}
    (out / "marketmood.json").write_text(json.dumps(keep), encoding="utf-8")
    monkeypatch.setattr(mm, "QUOTES", _quotes_file(tmp_path))
    monkeypatch.setattr(mm, "_download_closes", lambda codes, chunk=100: {})
    monkeypatch.setattr(sys, "argv", ["refresh_marketmood.py", "--out", str(out)])
    assert mm.main() == 1
    # previous file untouched — never blank the site
    assert json.loads((out / "marketmood.json").read_text(encoding="utf-8")) == keep


def test_main_appends_and_replaces_history_by_date(tmp_path, monkeypatch):
    import json
    import pandas as pd
    out = tmp_path / "out"
    out.mkdir()
    prev = {"history": [
        {"date": "2026-07-24", "score": 61.0, "label": "Strong"},
        {"date": "2026-07-25", "score": 10.0, "label": "Ex Weak"},   # replaced
    ]}
    (out / "marketmood.json").write_text(json.dumps(prev), encoding="utf-8")
    frames = {c: pd.Series([100.0 + i for i in range(60)])
              for c in ("AAA", "BBB", "CCC")}
    monkeypatch.setattr(mm, "QUOTES", _quotes_file(tmp_path, date="2026-07-25"))
    monkeypatch.setattr(mm, "_download_closes", lambda codes, chunk=100: frames)
    monkeypatch.setattr(sys, "argv", ["refresh_marketmood.py", "--out", str(out)])
    assert mm.main() == 0
    got = json.loads((out / "marketmood.json").read_text(encoding="utf-8"))
    dates = [h["date"] for h in got["history"]]
    assert dates == ["2026-07-24", "2026-07-25"]          # replaced, not duped
    assert got["latest"]["date"] == "2026-07-25"
    assert got["latest"]["score"] != 10.0                 # recomputed entry
    assert got["latest"]["breadth"]["n"] == 8
    assert got["latest"]["resolved"] == 3
    meta = json.loads((out / "marketmood_meta.json").read_text(encoding="utf-8"))
    assert meta["universe"] == 10 and meta["resolved"] == 3
