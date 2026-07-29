"""Evidence graph (earnings_intel/data/evidence_graph.py) — pure, no network, no key.

The graph decides what two debate agents ARGUE ABOUT, so a false edge is not a
cosmetic defect: it sends both sides to dispute something that is not in
disagreement, and that round is paid for in tokens against a free-tier ceiling.
Most of these tests came from running the graph over real bundles and finding
edges that should never have fired.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from earnings_intel.data.evidence_graph import (  # noqa: E402
    build_graph, clashes, direction_of, metric_of, new_nodes, subgraph, topic_of,
)


def item(id_, fact, value="", *, family="valuation", weight=3, side="neutral", quote=""):
    return {"id": id_, "fact": fact, "value": value, "family": family,
            "weight": weight, "side_hint": side, "quote": quote}


# ------------------------------------------------------------------ classifying
def test_sharpe_ratio_is_not_a_pe():
    """Real bug: "pe ratio" matches inside "Sharpe ratio" as a bare substring, so
    "The Sharpe ratio is 0.56" was filed as a P/E and then reported as a source
    conflict against the genuine P/E of 46.1."""
    assert metric_of(item("E1", "The Sharpe ratio is 0.56", "0.56")) == ""
    assert metric_of(item("E2", "Trades at a P/E of 46.1", "46.1x")) == "pe"


def test_plurals_still_match_after_the_boundary_fix():
    """Only a LEFT boundary is required — margin/margins must both classify."""
    assert metric_of(item("E1", "Operating margin expanded", "14%")) == "opm"
    assert metric_of(item("E2", "Operating margins expanded", "14%")) == "opm"
    assert topic_of(item("E3", "Order book at 12,000 cr", "12000")) == "orders"
    assert topic_of(item("E4", "Order books are full", "")) == "orders"


def test_direction_reads_words_before_numbers():
    """"margin contracted to 12%" is negative even though 12 is positive."""
    assert direction_of(item("E1", "Operating margin contracted to 12%", "12%")) == -1
    assert direction_of(item("E2", "Operating margin expanded to 12%", "12%")) == 1
    assert direction_of(item("E3", "Sales growth", "-8%")) == -1


def test_direction_falls_back_to_the_packs_own_view():
    assert direction_of(item("E1", "Something neutral", "5", side="bull")) == 1
    assert direction_of(item("E2", "Something neutral", "5", side="bear")) == -1
    assert direction_of(item("E3", "Something neutral", "")) == 0


# ----------------------------------------------------------------- false clashes
def test_two_different_metrics_are_not_a_contradiction():
    """Real bug: DLF put "P/E 47.5" and "P/B 2.74 vs sector 6.3" both under
    topic=valuation with opposite directions, and the graph called it a dispute.
    P/E versus P/B is two measurements, not a disagreement."""
    g = build_graph([
        item("E1", "Trades at a P/E of 47.5", "47.5x", side="bear"),
        item("E2", "P/B of 2.74x against a sector benchmark of 6.3x", "2.74x",
             family="peer_ratio", side="bull"),
    ])
    assert [e for e in g["edges"] if e["type"] == "contradicts"] == []


def test_same_metric_different_numbers_is_a_source_conflict_not_a_debate():
    """DLF carries P/E 47.5 (Screener, standalone) beside P/E 28.25 (Upstox peer
    basis). Two agents arguing which is real produces nothing, so it must never
    reach the clash list."""
    g = build_graph([
        item("E1", "Trades at a P/E of 47.5", "47.5x", side="bear"),
        item("E2", "P/E of 28.25x against a sector benchmark of 33.04x", "28.25x",
             family="peer_ratio", side="bull"),
    ])
    kinds = {e["type"] for e in g["edges"]}
    assert "source_conflict" in kinds
    assert clashes(g) == []


def test_a_computed_label_is_not_a_management_commitment():
    """Real bug: "insight" carries computed verdicts like FUNDAMENTALS-LED, and
    treating those as forward promises made "P/E 46.1" contradict "profit grew
    10% vs price 5%" — a ratio beside an unrelated computed label."""
    g = build_graph([
        item("E1", "Trades at a P/E of 46.1", "46.1x", side="bear"),
        item("E2", "FUNDAMENTALS-LED - profit grew 10% vs price 5%",
             "FUNDAMENTALS-LED", family="insight", side="bull"),
    ])
    assert [e for e in g["edges"] if e["type"] == "contradicts"] == []


def test_a_real_management_commitment_against_a_reported_number_does_clash():
    g = build_graph([
        item("E1", "Management guided to margin expansion this year", "",
             family="commitment", side="bull", quote="we expect margins to expand"),
        item("E2", "Operating margin contracted 180bp", "-180bp",
             family="margin", side="bear"),
    ])
    assert any(e["type"] == "contradicts" for e in g["edges"])


def test_a_light_disagreement_is_not_worth_a_round():
    """A dispute needs at least one item the pack itself considers heavyweight."""
    g = build_graph([
        item("E1", "Sales growth improved", "8%", family="growth", weight=1, side="bull"),
        item("E2", "Sales growth declined", "-3%", family="growth", weight=1, side="bear"),
    ])
    assert [e for e in g["edges"] if e["type"] == "contradicts"] == []


# ------------------------------------------------------------------- real clashes
def test_a_genuine_dispute_is_found_and_ranked():
    g = build_graph([
        item("E1", "Compounded sales growth over 10 years is 16%", "16%",
             family="growth", side="bull"),
        item("E2", "Screener flags a negative: poor sales growth", "",
             family="screener_note", side="bear"),
    ])
    got = clashes(g)
    assert got and got[0]["type"] == "contradicts"
    assert got[0]["topic"] or got[0]["why"]
    assert got[0]["strength"] >= 3


def test_clashes_are_ordered_by_combined_weight():
    g = build_graph([
        item("E1", "ROCE is 21%", "21%", family="returns", weight=3, side="bull"),
        item("E2", "Screen reason: low ROCE", "", family="screener_note", weight=1, side="bear"),
        item("E3", "Sales growth is 16%", "16%", family="growth", weight=3, side="bull"),
        item("E4", "Sales growth is poor", "", family="growth", weight=3, side="bear"),
    ])
    got = clashes(g)
    assert got == sorted(got, key=lambda c: (-c["strength"], c["src"]))


def test_every_edge_names_the_rule_that_fired():
    """An unexplained edge cannot be audited, only guessed at."""
    g = build_graph([
        item("E1", "Trades at a P/E of 46.1", "46.1x", side="bear"),
        item("E2", "P/E of 31.9x against a sector benchmark of 30x", "31.9x",
             family="peer_ratio", side="bull"),
    ])
    assert g["edges"]
    for e in g["edges"]:
        assert e["why"] and e["type"] and e["src"] and e["dst"]


def test_a_pair_never_yields_the_same_edge_twice():
    g = build_graph([item(f"E{i}", "Trades at a P/E of 46.1", "46.1x") for i in range(1, 6)])
    keys = [(e["src"], e["dst"], e["type"]) for e in g["edges"]]
    assert len(keys) == len(set(keys))


# ------------------------------------------------------------------ prompt slice
def test_subgraph_returns_the_neighbourhood_and_includes_the_seeds():
    g = build_graph([
        item("E1", "Trades at a P/E of 46.1", "46.1x", side="bear"),
        item("E2", "P/E of 31.9x against a sector benchmark of 30x", "31.9x",
             family="peer_ratio", side="bull"),
        item("E3", "Promoter holding is 75%", "75%", family="ownership"),
    ])
    got = subgraph(g, ["E1"], hops=1)
    assert "E1" in got and "E2" in got
    assert "E3" not in got          # unrelated topic stays out of the prompt


def test_subgraph_respects_its_limit_and_survives_junk():
    # alternating direction + family so the items are actually connected;
    # 29 IDENTICAL items correctly produce no edges at all and would prove nothing
    g = build_graph([
        item(f"E{i}", "Sales growth was strong" if i % 2 else "Sales growth was weak",
             f"{i}%", family="growth" if i % 3 else "screener_note",
             side="bull" if i % 2 else "bear")
        for i in range(1, 30)])
    assert g["edges"], "fixture must be connected for this test to mean anything"
    assert len(subgraph(g, ["E1"], hops=3, limit=5)) == 5
    assert subgraph(None, ["E1"]) == []
    assert subgraph(g, None) == []


def test_new_nodes_drives_convergence():
    assert new_nodes(["E1", "E2"], ["E2", "E7"]) == {"E7"}
    assert new_nodes(["E1", "E2"], ["E1", "E2"]) == set()   # nobody said anything new
    assert new_nodes(None, None) == set()


# ------------------------------------------------------------------- robustness
@pytest.mark.parametrize("junk", [None, [], [None], ["nope"], [{}], [{"fact": "no id"}]])
def test_garbage_never_raises(junk):
    g = build_graph(junk)
    assert g["nodes"] == [] and g["edges"] == []
    assert clashes(g) == []


def test_graph_is_deterministic_for_a_given_pack():
    items = [item("E1", "Trades at a P/E of 46.1", "46.1x", side="bear"),
             item("E2", "P/E of 31.9x against a sector benchmark of 30x", "31.9x",
                  family="peer_ratio", side="bull"),
             item("E3", "Sales growth is 16%", "16%", family="growth", side="bull")]
    assert build_graph(items) == build_graph(items)


def test_build_graph_does_not_mutate_its_input():
    import copy
    items = [item("E1", "Trades at a P/E of 46.1", "46.1x")]
    snapshot = copy.deepcopy(items)
    build_graph(items)
    assert items == snapshot


def test_subgraph_never_returns_an_id_the_graph_does_not_hold():
    """Grounding strips claims citing ids the pack never issued. If subgraph
    invented an id here it would launder a stale seed straight past that check
    and into a prompt."""
    g = build_graph([item("E1", "Trades at a P/E of 46.1", "46.1x")])
    assert subgraph(g, ["E1", "E999"]) == ["E1"]
    assert subgraph(g, ["E999"]) == []
