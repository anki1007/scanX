"""Sector benchmarks computed from our own constituents — pure, no network."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from earnings_intel.data.sectormedian import (  # noqa: E402
    apply_medians, sector_medians,
)


def _bundles(n, sector_metric_values):
    return {f"C{i}": {"upstox_ratios": {k: {"value": v[i]} for k, v in sector_metric_values.items()}}
            for i in range(n)}


def test_one_median_per_sector_not_one_per_company():
    """Four Chemicals companies carried four different "sector" ROCEs on the
    same day — 70.68, 10.44, 70.67 and 12.94. A sector cannot have four."""
    bundles = _bundles(6, {"roce": [10, 12, 11, 13, 9, 14], "pe": [20, 22, 21, 23, 19, 24]})
    member = {f"C{i}": "Chemicals" for i in range(6)}
    out = sector_medians(bundles, member)
    assert set(out) == {"Chemicals"}
    assert out["Chemicals"]["roce"]["median"] == pytest.approx(11.5)
    assert out["Chemicals"]["roce"]["n"] == 6


def test_the_median_resists_a_single_extreme_reading():
    """One company on a 400x P/E must not drag the benchmark somewhere no member
    of the sector actually is — which a mean would.

    400 rather than 900 deliberately: anything past 500x is treated as a data
    error and filtered before the median ever sees it, so a 900 would test the
    sanity gate instead of the statistic.
    """
    bundles = _bundles(6, {"pe": [20, 21, 22, 23, 24, 400]})
    out = sector_medians(bundles, {f"C{i}": "X" for i in range(6)})
    assert out["X"]["pe"]["n"] == 6
    assert out["X"]["pe"]["median"] == pytest.approx(22.5)   # a mean would be ~85


def test_a_reading_past_the_sanity_bound_is_a_data_error_not_an_outlier():
    bundles = _bundles(6, {"pe": [20, 21, 22, 23, 24, 900]})
    out = sector_medians(bundles, {f"C{i}": "X" for i in range(6)})
    assert out["X"]["pe"]["n"] == 5, "the 900x reading should have been filtered"


def test_a_thin_sector_publishes_nothing_rather_than_an_accident():
    bundles = _bundles(3, {"pe": [20, 21, 22]})
    out = sector_medians(bundles, {f"C{i}": "Tiny" for i in range(3)}, min_members=5)
    assert "Tiny" not in out


def test_negative_multiples_never_enter_the_benchmark():
    """A negative P/E or EV/EBITDA means loss-making, not cheap. Upstox published
    a NEGATIVE sector EV/EBITDA of -2.16 for Chemicals."""
    bundles = _bundles(6, {"pe": [-50, 20, 21, 22, 23, 24],
                           "ev_ebitda": [-2.16, 10, 11, 12, 13, 14]})
    out = sector_medians(bundles, {f"C{i}": "X" for i in range(6)})
    assert out["X"]["pe"]["n"] == 5                # the -50 was excluded
    assert out["X"]["ev_ebitda"]["median"] > 0


def test_a_negative_return_IS_kept_because_it_is_real():
    """Unlike a multiple, a negative ROE is a genuine reading."""
    bundles = _bundles(6, {"roe": [-8, -4, 2, 6, 10, 14]})
    out = sector_medians(bundles, {f"C{i}": "X" for i in range(6)})
    assert out["X"]["roe"]["n"] == 6


def test_apply_medians_replaces_the_benchmark_but_not_the_company_value():
    peers = {"pe": {"value": 14.25, "sector": 22.93, "bias": "positive"}}
    out = apply_medians(peers, "Chemicals", {"Chemicals": {"pe": {"median": 22.59, "n": 170}}})
    assert out["pe"]["value"] == 14.25             # the company's own number is untouched
    assert out["pe"]["sector"] == 22.59
    assert out["pe"]["sector_n"] == 170


def test_an_untrustworthy_benchmark_is_dropped_not_left_stale():
    """Keeping the old Upstox value when we have no median of our own would
    silently preserve exactly the number this module exists to replace."""
    peers = {"roce": {"value": 22.86, "sector": 70.68}}
    out = apply_medians(peers, "Chemicals", {"Chemicals": {}})
    assert "sector" not in out["roce"]
    assert "no sector median" in out["roce"]["sector_basis"]


@pytest.mark.parametrize("junk", [None, {}, {"C1": None}, {"C1": "nope"}])
def test_garbage_never_raises(junk):
    assert sector_medians(junk, {"C1": "X"}) == {}
    assert apply_medians(junk, "X", None) == {}
