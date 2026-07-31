"""STDRL bake + environment — no torch, no GPU, no network.

The environment maths is exercised through PortfolioEnv directly, which needs
only numpy; make_env/train_and_evaluate are the parts that import gymnasium and
stable-baselines3, and they are not touched here.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from earnings_intel.data.rlenv import PortfolioEnv  # noqa: E402
import refresh_stdrl as rs  # noqa: E402


def _basket(n=60):
    return {"UP":   [{"close": 100 * (1.02 ** i)} for i in range(n)],
            "DOWN": [{"close": 100 * (0.98 ** i)} for i in range(n)]}


# ------------------------------------------------------- environment corrections
def test_transaction_costs_are_actually_charged():
    """The stated objective was to include them; the original environment traded
    free, which is what makes a churning policy look optimal to an RL agent."""
    free, paid = PortfolioEnv(_basket(), cost_bps=0), PortfolioEnv(_basket(), cost_bps=100)
    for env in (free, paid):
        env.reset()
        for _ in range(30):
            env.step(np.array([0.5, -0.5]))
    assert paid.costs_paid > 0 and free.costs_paid == 0
    assert paid.net_worth < free.net_worth


def test_reward_is_the_change_in_net_worth_not_the_level():
    """`reward = net_worth - initial_balance` pays the agent at every step for
    wealth it already had, so a losing step still scores strongly positive while
    the book is up overall."""
    env = PortfolioEnv(_basket(), cost_bps=0)
    env.reset()
    rewards = [env.step(np.array([1.0, 0.0]))[1] for _ in range(6)]
    assert all(abs(r) < 1.0 for r in rewards), "rewards look like levels, not deltas"


def test_cash_never_goes_negative():
    env = PortfolioEnv(_basket(), cost_bps=25)
    env.reset()
    for _ in range(40):
        env.step(np.array([1.0, 1.0]))          # buy everything, every step
        assert env.cash >= -1e-6


def test_the_basket_is_aligned_from_the_most_recent_end():
    """A 30-year name and a 3-year name should overlap on the window they SHARE,
    not on unrelated decades."""
    basket = {"LONG": [{"close": float(i)} for i in range(1, 101)],
              "SHORT": [{"close": float(i)} for i in range(1, 41)]}
    env = PortfolioEnv(basket, cost_bps=0)
    assert env.T == 40
    assert env.closes[-1][0] == 100.0 and env.closes[-1][1] == 40.0


def test_garbage_actions_do_not_crash_the_environment():
    env = PortfolioEnv(_basket(), cost_bps=25)
    env.reset()
    for action in (np.array([np.nan, np.inf]), np.array([9.0, -9.0]), np.array([0.0, 0.0])):
        obs, reward, done, trunc, info = env.step(action)
        assert np.all(np.isfinite(obs)) and np.isfinite(reward)


# ----------------------------------------------------------------- the bake
def test_monthly_bars_annualise_over_twelve_not_252():
    """The bundles hold a MONTHLY return heatmap. Annualising monthly data with
    the daily constant overstates every Sharpe by sqrt(252/12) = 4.6x."""
    assert rs.PERIODS_PER_YEAR == 12


def test_history_comes_from_the_heatmap_the_bundles_actually_hold():
    """RELIANCE has ~31 years of monthly returns on disk; there is no daily
    close series in the bundles at all."""
    hist = rs.load_history("RELIANCE")
    assert len(hist) >= 36
    assert {"period", "ret_pct", "close"} <= set(hist[0])
    assert all(h["close"] > 0 for h in hist)


def test_a_company_with_too_little_history_is_dropped_not_padded():
    assert rs.load_history("__NOT_A_REAL_CODE__") == []


def test_the_payload_ranks_and_names_a_best_agent():
    curves = {
        "GOOD": [100000 * (1.01 ** i) for i in range(60)],
        "BAD":  [100000 * (0.99 ** i) for i in range(60)],
    }
    out = rs.build_payload(curves, meta={"periods_per_year": 12, "risk_free": 0.0})
    assert out["best_agent"] == "GOOD"
    assert [a["agent"] for a in out["agents"]] == ["GOOD", "BAD"]
    assert out["agents"][0]["sharpe"] > out["agents"][1]["sharpe"]
    assert set(out["equity"]) == {"GOOD", "BAD"}


def test_the_payload_survives_having_no_results():
    out = rs.build_payload({}, meta={})
    assert out["agents"] == [] and out["best_agent"] is None


# ----------------------------------------------------------------- the page
def test_the_page_is_linked_from_every_page_and_reads_the_baked_file():
    docs = ROOT / "docs"
    missing = [p.name for p in sorted(docs.glob("*.html"))
               if 'href="stdrl.html"' not in p.read_text(encoding="utf-8")]
    assert not missing, f"STDRL not linked from: {missing}"
    page = (docs / "stdrl.html").read_text(encoding="utf-8")
    assert "data/stdrl.json" in page
    assert "function esc(" in page          # agent names land in innerHTML


def test_the_page_does_not_claim_to_train_anything():
    """A static page cannot run torch; it renders what the bake produced."""
    page = (ROOT / "docs" / "stdrl.html").read_text(encoding="utf-8")
    assert "not personalized investment advice" in page
    for banned in ("stable_baselines3", "torch", "model.learn"):
        assert banned not in page


# ------------------------------------------- out of sample + the benchmark
def test_the_split_is_chronological_never_random():
    """Shuffling market data lets the model see the future."""
    from earnings_intel.data.rlenv import split_basket
    basket = {"A": [{"close": float(i)} for i in range(1, 101)]}
    train, test = split_basket(basket, 0.7)
    assert [r["close"] for r in train["A"]] == list(map(float, range(1, 71)))
    assert [r["close"] for r in test["A"]] == list(map(float, range(71, 101)))
    assert train["A"][-1]["close"] < test["A"][0]["close"]      # strictly earlier


def test_a_basket_too_short_to_split_yields_nothing():
    """Better an empty result than a one-month 'out of sample' claim."""
    from earnings_intel.data.rlenv import split_basket
    train, test = split_basket({"A": [{"close": 1.0}] * 10}, 0.7)
    assert train == {} and test == {}


def test_the_benchmark_buys_once_and_holds():
    """It must not churn — the point is the do-nothing baseline."""
    from earnings_intel.data.rlenv import buy_and_hold
    rising = {"A": [{"close": 100 * (1.01 ** i)} for i in range(40)],
              "B": [{"close": 100 * (1.02 ** i)} for i in range(40)]}
    curve = buy_and_hold(rising, cost_bps=25)
    assert len(curve) == 40
    assert curve[-1] > curve[0]


def test_the_published_ranking_includes_the_benchmark():
    """An agent's return is meaningless without it: over this window Indian
    large caps compounded hard, and the first out-of-sample run had buy-and-hold
    beating every agent on Sharpe (1.32 against PPO's 1.04)."""
    curves = {"PPO": [100000 * (1.02 ** i) for i in range(60)],
              "Buy & hold": [100000 * (1.015 ** i) for i in range(60)]}
    out = rs.build_payload(curves, meta={"periods_per_year": 12})
    assert "Buy & hold" in {a["agent"] for a in out["agents"]}


# ------------------------------------------------- the buy / hold / exit book
def _cand(code, score, **kw):
    row = {"code": code, "name": code, "score": score, "ltp": 100.0,
           "sector": "X", "sector_signal": "TAILWIND", "mcap": 9000.0}
    row.update(kw)
    return row


def test_a_stopped_out_name_is_not_re_bought_in_the_same_pass():
    """The first version sold AAA at -22% and bought it straight back — which is
    not a stop loss, it is a round trip that pays costs twice and leaves the
    position exactly where the rule said it must not be."""
    from earnings_intel.data.portfolio import rebalance
    prev = [{"code": "AAA", "entry_price": 100.0, "entry_date": "2026-01-01", "score": 7.0}]
    cands = [_cand("AAA", 7.0, ltp=78.0), _cand("NEW", 8.1, ltp=50.0)]
    book = rebalance(prev, cands, size=5, today="2026-07-31")
    assert [e["code"] for e in book["exits"]] == ["AAA"]
    assert "AAA" not in [e["code"] for e in book["entries"]]
    assert "NEW" in [e["code"] for e in book["entries"]]


def test_the_cooloff_survives_into_the_next_pass():
    from earnings_intel.data.portfolio import rebalance
    prev = [{"code": "AAA", "entry_price": 100.0, "entry_date": "2026-01-01", "score": 7.0}]
    cands = [_cand("AAA", 8.5, ltp=78.0)]
    first = rebalance(prev, cands, size=5, today="2026-07-31")
    again = rebalance(first["holdings"] + first["exits"], cands, size=5, today="2026-08-05")
    assert "AAA" not in [e["code"] for e in again["entries"]], "cool-off ignored"


@pytest.mark.parametrize("score,row,trigger", [
    (3.9, {}, "score fell"),
    (7.0, {"sector_signal": "HEADWIND"}, "headwind"),
    (7.0, {"pe": 200.0, "pe_sector": 20.0}, "stretched"),
    (7.0, {"ltp": 70.0}, "stop hit"),
])
def test_each_exit_rule_fires_on_its_own(score, row, trigger):
    """An exit needs only ONE trigger — the asymmetry with entry is the point.

    `score` is passed separately because _cand takes it positionally; supplying
    it through **kw as well is a TypeError, which is how this test first failed.
    """
    from earnings_intel.data.portfolio import exit_signal
    holding = {"code": "A", "entry_price": 100.0}
    verdict = exit_signal(holding, _cand("A", score, **row))
    assert verdict["ok"], f"no trigger fired for {row}"
    assert any(trigger in t for t in verdict["triggers"]), verdict["triggers"]


def test_a_name_that_leaves_the_screen_is_sold_not_silently_kept():
    from earnings_intel.data.portfolio import exit_signal
    verdict = exit_signal({"code": "GONE", "entry_price": 100.0, "last_price": 90.0}, None)
    assert verdict["ok"] and "no longer in the screened universe" in verdict["triggers"]


def test_entry_needs_every_gate_but_still_reports_why_it_failed():
    """The watchlist shows 'would qualify except for X', not a bare no."""
    from earnings_intel.data.portfolio import entry_signal
    sig = entry_signal(_cand("A", 8.8, mcap=160.0))
    assert not sig["ok"]
    assert any("market cap" in b for b in sig["blockers"])
    assert any("8.8" in r for r in sig["reasons"])       # the good part is kept


def test_a_loss_making_company_never_qualifies():
    from earnings_intel.data.portfolio import entry_signal
    assert not entry_signal(_cand("A", 8.0, pe=-12.0))["ok"]
    assert not entry_signal(_cand("A", 8.0, health={"ocf_np": {"value": -0.5}}))["ok"]


def test_exits_free_seats_for_entries_in_the_same_pass():
    """Entries first would cap the book while holding names already on the way out."""
    from earnings_intel.data.portfolio import rebalance
    prev = [{"code": f"OLD{i}", "entry_price": 100.0, "score": 7.0} for i in range(3)]
    cands = [_cand(f"OLD{i}", 3.0) for i in range(3)] + [_cand(f"N{i}", 8.0) for i in range(3)]
    book = rebalance(prev, cands, size=3, today="2026-07-31")
    assert len(book["exits"]) == 3 and len(book["entries"]) == 3
    assert len(book["holdings"]) == 3


def test_the_book_is_deterministic():
    from earnings_intel.data.portfolio import rebalance
    cands = [_cand(f"C{i}", 6.0 + i * 0.1) for i in range(20)]
    a = rebalance([], cands, size=5, today="2026-07-31")
    b = rebalance([], cands, size=5, today="2026-07-31")
    assert [h["code"] for h in a["holdings"]] == [h["code"] for h in b["holdings"]]


def test_the_page_renders_the_book_and_the_rules():
    page = (ROOT / "docs" / "stdrl.html").read_text(encoding="utf-8")
    assert "data/portfolio.json" in page
    for el in ('id="hold"', 'id="exits"', 'id="watch"', 'id="rules"'):
        assert el in page, f"missing {el}"
