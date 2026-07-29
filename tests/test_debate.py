"""BULL vs BEAR debate engine: evidence pack, grounding, scorecard, contract.

The model is injected everywhere — no network, no API key, no provider import
that needs credentials. `evidence_pack` and `scorecard` are pure, so they are
asserted on exact values; `run_debate` is asserted on the contract it promises.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from earnings_intel.data import debate as D            # noqa: E402
from earnings_intel.llm import LLMResponse             # noqa: E402


# ------------------------------------------------------------------- fixtures
def _cols(*vals):
    return list(vals)


YEARS = ["Mar 2023", "Mar 2024", "Mar 2025", "Mar 2026"]
QTRS = ["Jun 2025", "Sep 2025", "Dec 2025", "Mar 2026", "Jun 2026"]
SHQ = ["Sep 2024", "Dec 2024", "Mar 2025", "Jun 2025",
       "Sep 2025", "Dec 2025", "Mar 2026", "Jun 2026"]

BUNDLE = {
    "generated_at": "2026-07-29",
    "fundamental": {
        "code": "TESTCO",
        "name": "Test Industries Ltd",
        "url": "https://www.screener.in/company/TESTCO/",
        "overview": {
            "Market Cap": "₹ 12,500 Cr.", "Current Price": "₹ 640",
            "High / Low": "₹ 780 / 410", "Stock P/E": "18.4",
            "Book Value": "₹ 210", "Dividend Yield": "1.10 %",
            "ROCE": "21.5 %", "ROE": "18.2 %", "Face Value": "₹ 2.00",
        },
        "growth": {
            "Compounded Sales Growth": {"10 Years": "12%", "5 Years": "17%",
                                        "3 Years": "9%", "TTM": "4%"},
            "Compounded Profit Growth": {"10 Years": "19%", "5 Years": "22%",
                                         "3 Years": "6%", "TTM": "-3%"},
            "Stock Price CAGR": {"10 Years": "16%", "5 Years": "28%",
                                 "3 Years": "11%", "1 Year": "-18%"},
            "Return on Equity": {"10 Years": "15%", "5 Years": "17%",
                                 "3 Years": "18%", "Last Year": "18%"},
        },
        "quarters": {"headers": QTRS, "rows": {
            "Sales": _cols("420", "445", "460", "470", "455"),
            "OPM": _cols("18%", "19%", "17%", "16%", "15%"),
            "Net Profit": _cols("41", "44", "38", "35", "30"),
        }},
        "profit_loss": {"headers": YEARS, "rows": {
            "Sales": _cols("1,410", "1,560", "1,700", "1,795"),
            "OPM %": _cols("20%", "19%", "18%", "16%"),
            "Net Profit": _cols("152", "164", "158", "147"),
        }},
        "balance_sheet": {"headers": YEARS, "rows": {
            "Borrowings": _cols("310", "340", "390", "455"),
            "CWIP": _cols("60", "80", "95", "210"),
        }},
        "cash_flow": {"headers": YEARS, "rows": {
            "Cash from Operating Activity": _cols("180", "150", "120", "95"),
            "Free Cash Flow": _cols("90", "60", "20", "-15"),
            "CFO/OP": _cols("95%", "82%", "70%", "55%"),
        }},
        "ratios": {"headers": YEARS, "rows": {
            "Debtor Days": _cols("52", "58", "63", "71"),
        }},
        "shareholding": {"headers": SHQ, "rows": {
            "Promoters": _cols("58.20%", "58.20%", "57.10%", "56.40%",
                               "55.90%", "55.00%", "54.60%", "54.10%"),
            "FIIs": _cols("6.10%", "6.40%", "6.20%", "5.80%",
                          "5.20%", "4.90%", "4.40%", "4.10%"),
            "DIIs": _cols("9.10%", "9.30%", "9.80%", "10.20%",
                          "10.60%", "11.10%", "11.40%", "11.90%"),
            "Pledged": _cols("0.00%", "0.00%", "0.00%", "2.10%",
                             "2.10%", "4.40%", "6.50%", "6.50%"),
            "Public": _cols("26.60%", "26.10%", "26.90%", "27.60%",
                            "28.30%", "29.00%", "29.60%", "29.90%"),
        }},
        "pros": ["Company has delivered good profit growth of 22.0% CAGR over "
                 "last 5 years"],
        "cons": ["Promoter holding has decreased over last 3 years",
                 "Working capital days have increased from 52 to 71 days"],
        "analysis": {
            "trends": {
                "yearly": {"OPM%": {"label": "Decreasing", "n": 3, "unit": "yrs"},
                           "Sales": {"label": "Increasing", "n": 4, "unit": "yrs"}},
                "quarterly": {"OPM%": {"label": "Decreasing", "n": 4, "unit": "qtrs"}},
            },
            "cyclical": {"label": "CYCLICAL", "positive_quarters": ["Mar"],
                         "negative_quarters": ["Jun", "Sep"]},
            "growth_insight": {
                "label": "PRICE-LED",
                "long": "Price grew 28% vs profit 22% (5Y) — stock ahead of fundamentals.",
                "recent": "Recently, 1-year price growth trails profit growth.",
            },
            "money_flow": {"label": "NEGATIVE MONEY FLOW", "change": -0.42,
                           "note": "Tracks institutional (FII+DII) holding change."},
            "dcf": {
                "ok": True,
                "inputs": {"earnings": 147, "growth": 12.0, "discount": 10,
                           "terminal_growth": 2, "years": 10, "terminal_multiple": 12.8},
                "reverse": {"implied_growth": 21.5, "total_pv": 12500},
                "intrinsic_total": 10160, "intrinsic_per_share": 520,
                "current_price": 640.0, "market_cap": 12500.0,
                "margin_of_safety": -23.1,
            },
            "health": {
                "current_ratio": {"value": 1.12, "bias": "neutral", "year": "Mar 2026",
                                  "note": "1.12x (Mar 2026): adequate liquidity.",
                                  "source": "upstox:balance-sheet"},
                "ocf_np": {"value": 0.62, "year": "Mar 2026", "bias": "negative",
                           "note": "0.62x (Mar 2026): weak cash conversion"},
                "debt_equity": {"value": 0.94, "year": "Mar 2026", "bias": "negative",
                                "note": "0.94x (Mar 2026): leveraged"},
                "cwip": {"latest": 210.0, "prev": 95.0, "pct_change": 121.1,
                         "year": "Mar 2026", "bias": "neutral",
                         "note": "CWIP up 121.1%: capex build-up phase"},
                "peers": {
                    "pe": {"value": 18.4, "unit": "x", "sector": 24.1,
                           "source": "upstox:key-ratios", "bias": "positive"},
                    "pb": {"value": 3.05, "unit": "x", "sector": 2.4,
                           "source": "upstox:key-ratios", "bias": "negative"},
                    "roa": {"value": 9.4, "unit": "pct", "sector": 6.1,
                            "source": "upstox:key-ratios", "bias": "positive"},
                    "roe": {"value": 18.2, "unit": "pct", "sector": 13.0,
                            "source": "upstox:key-ratios", "bias": "positive"},
                    "roce": {"value": 21.5, "unit": "pct", "sector": 15.4,
                             "source": "upstox:key-ratios", "bias": "positive"},
                    "ev_ebitda": {"value": 12.9, "unit": "x", "sector": 15.5,
                                  "source": "upstox:key-ratios", "bias": "positive"},
                },
            },
        },
    },
    "prices": {
        "ok": True, "ticker": "TESTCO.NS",
        "risk": {"avg_weekly": 0.21, "weekly_std": 5.3, "ann_vol": 38.4,
                 "max_drawdown": -61.2, "pct_positive": 49.1, "sharpe": 0.44,
                 "sortino": 0.71},
        "technical": {"ret_3m": -4.2, "ret_12m": -18.3, "rs_rating": 38,
                      "price": 640.0, "above_50dma": False, "above_200dma": False,
                      "golden_cross": False, "pos_52w": 62.2,
                      "dist_52w_high": -17.9, "benchmark": "Nifty 500"},
    },
    "signal": {
        "label": "HOLD", "composite": 54, "confidence": "Medium",
        "reasons_pos": ["Sales +9% YoY", "ROCE above 20%"],
        "reasons_neg": ["margin compression four quarters running",
                        "promoter holding falling"],
        "bias_check": {"risk": "MEDIUM", "source": "Insider-Bias checklist",
                       "flags": [{"level": "warn", "title": "Sector headwind",
                                  "note": "Chemicals is in a HEADWIND (score -0.42).",
                                  "lesson": "Focus on cycles and change."}]},
    },
    "upstox_ratios": {"pe": {"value": 18.4, "unit": "x", "sector": 24.1,
                             "source": "upstox:key-ratios"}},
}

# only an overview — every other family is genuinely absent
SPARSE = {
    "generated_at": "2026-07-29",
    "fundamental": {"code": "SPARSE", "name": "Sparse Ltd",
                    "url": "https://www.screener.in/company/SPARSE/",
                    "overview": {"Stock P/E": "11.2", "ROCE": "9.0 %"},
                    "pros": [], "cons": []},
}

QUOTE_CAPACITY = ("We have commissioned the new line and annual capacity now "
                  "stands at 240,000 tonnes.")
QUOTE_RISK = ("Input cost inflation and freight remain the key headwinds we are "
              "watching into FY27.")
QUOTE_COMMIT = "We remain committed to becoming net-debt free by FY28."

FILINGS = {
    "code": "TESTCO", "name": "Test Industries Ltd", "generated_at": "2026-07-29",
    "documents": [{"kind": "concall_transcript", "date": "2026-05-01",
                   "title": "Q4 FY26 Earnings Call Transcript",
                   "url": "https://example.com/q4fy26.pdf", "source": "screener"}],
    "analysis": {
        "summary": "Capacity expanded; management flagged input costs.",
        "themes": {
            "guidance": [],
            "demand_outlook": [],
            "capex_expansion": [],
            "margins_costs": [],
            "orders_capacity": [
                {"claim": "Annual capacity now stands at 240,000 tonnes",
                 "quote": QUOTE_CAPACITY, "doc_kind": "concall_transcript",
                 "doc_date": "2026-05-01", "url": "https://example.com/q4fy26.pdf"},
            ],
            "capital_allocation": [],
            "risks_headwinds": [
                {"claim": "Input cost inflation and freight flagged as headwinds",
                 "quote": QUOTE_RISK, "doc_kind": "concall_transcript",
                 "doc_date": "2026-05-01", "url": "https://example.com/q4fy26.pdf"},
            ],
        },
        "management_commitments": [
            {"claim": "Management committed to net-debt-free status by FY28",
             "quote": QUOTE_COMMIT, "timeframe": "FY28",
             "doc_kind": "concall_transcript", "doc_date": "2026-05-01",
             "url": "https://example.com/q4fy26.pdf"},
        ],
    },
    "_meta": {"model": "fake", "grounded": True},
}

SECTOR = {"sector": "Chemicals", "sector_code": "IN0201", "signal": "HEADWIND",
          "score": -0.42, "median_profit_var": -3.1, "median_sales_var": 2.4,
          "median_roce": 11.8}


# ------------------------------------------------------------- fake LLM layer
class FakeModel:
    """Injectable `complete`. Records prompts; replays a script or auto-answers."""

    def __init__(self, script=None, ok=True, error=""):
        self.prompts = []
        self.script = list(script or [])
        self.ok = ok
        self.error = error
        self.calls = 0

    def __call__(self, prompt, *, provider=None, json_mode=False,
                 temperature=0.0, max_tokens=None):
        self.prompts.append(prompt)
        self.calls += 1
        if not self.ok:
            return LLMResponse.failure(provider or "gemini", "fake-1",
                                       self.error or "no LLM provider configured")
        text = self.script.pop(0) if self.script else self._auto(prompt)
        return LLMResponse(text=text, model="fake-1", provider=provider or "fake",
                           ok=True)

    @staticmethod
    def _auto(prompt):
        side = "bull" if prompt.startswith("You are the BULL") else "bear"
        m = re.search(r"ROUND (\d+) of (\d+)", prompt)
        rnd = m.group(1) if m else "0"
        ids = ("[E1]", "[E4]") if side == "bull" else ("[E2]", "[E5]")
        return (f"The {side} case in round {rnd} rests on {ids[0]}. "
                f"It is reinforced by {ids[1]}.")


def _run(bundle=BUNDLE, **kw):
    kw.setdefault("filings", FILINGS)
    kw.setdefault("sector", SECTOR)
    model = kw.pop("model", None) or FakeModel()
    out = D.run_debate(bundle, complete=model, **kw)
    return out, model


# --------------------------------------------------------------- evidence pack
def test_pack_covers_every_family_present():
    pack = D.evidence_pack(BUNDLE, filings=FILINGS, sector=SECTOR)
    families = {i["family"] for i in pack}
    expected = {"valuation", "peer_ratio", "profitability", "growth", "margin",
                "cash", "balance_sheet", "capex", "ownership", "flow", "dcf",
                "technical", "risk", "insight", "screener_note", "signal",
                "sector", "filing", "commitment"}
    assert families == expected, sorted(expected ^ families)
    # every family we emit must carry a weight we actually declared
    assert all(i["weight"] == D.FAMILY_WEIGHT[i["family"]] for i in pack)


def test_pack_reads_the_real_numbers():
    pack = D.evidence_pack(BUNDLE, filings=FILINGS, sector=SECTOR)
    blob = " || ".join(f"{i['family']}:{i['fact']}" for i in pack)
    assert "P/E of 18.4" in blob                                  # overview
    assert "sector benchmark of 24.1x" in blob                    # peers
    assert "17%" in blob and "22%" in blob                        # sales/profit CAGR
    assert "16%" in blob and "-4 percentage points" in blob       # OPM trend
    assert "0.62x" in blob                                        # cash conversion
    assert "0.94x" in blob and "1.12x" in blob                    # debt / current
    assert "CWIP" in blob and "+121.1%" in blob                   # capex
    assert "Promoters hold 54.10%" in blob                        # promoter trend
    assert "Promoter pledge stands at 6.50%" in blob              # pledge
    assert "Foreign institutions hold 4.10%" in blob              # FII flow
    assert "intrinsic value at Rs 520" in blob                    # DCF
    assert "implies 21.5% earnings growth" in blob                # reverse DCF
    assert "62% of the way up its 52-week range" in blob          # technical
    assert "below its 50-day moving average" in blob              # moving averages
    assert "-61.2%" in blob                                       # drawdown
    assert "Working capital days have increased" in blob          # Screener cons
    assert "margin compression four quarters running" in blob     # signal reasons
    assert "Sector headwind" in blob                              # bias check
    assert "Chemicals reads HEADWIND" in blob                     # sector
    assert "240,000 tonnes" in blob                               # filing fact
    assert "net-debt-free status by FY28" in blob                 # commitment


def test_pack_invents_nothing_that_is_absent():
    pack = D.evidence_pack(SPARSE)                    # no filings, no sector
    families = {i["family"] for i in pack}
    assert families == {"valuation", "profitability"}
    for gone in ("dcf", "technical", "risk", "flow", "growth", "cash", "capex",
                 "ownership", "balance_sheet", "margin", "peer_ratio",
                 "screener_note", "signal", "sector", "filing", "commitment"):
        assert gone not in families
    # and nothing was defaulted to a zero the agents could then cite
    assert not any(i["value"].strip() in ("0", "0.0", "0%", "0.00x") for i in pack)


def test_pack_degrades_on_junk_instead_of_raising():
    assert D.evidence_pack(None) == []
    assert D.evidence_pack({}) == []
    assert D.evidence_pack({"fundamental": {"overview": "not-a-dict"}}) == []


def test_ids_are_stable_sequential_and_reproducible():
    a = D.evidence_pack(BUNDLE, filings=FILINGS, sector=SECTOR)
    b = D.evidence_pack(BUNDLE, filings=FILINGS, sector=SECTOR)
    assert [i["id"] for i in a] == [f"E{n}" for n in range(1, len(a) + 1)]
    assert len({i["id"] for i in a}) == len(a)
    assert a == b                                     # pure: same in, same out
    for item in a:
        assert set(item) >= {"id", "fact", "value", "source", "url", "side_hint"}
        assert item["side_hint"] in ("bull", "bear", "neutral")
        assert item["fact"]


def test_filing_facts_carry_their_verbatim_quote_and_url():
    pack = D.evidence_pack(BUNDLE, filings=FILINGS, sector=SECTOR)
    filed = [i for i in pack if i["family"] in ("filing", "commitment")]
    assert len(filed) == 3
    assert all(i["quote"] and i["url"].startswith("https://") for i in filed)
    quotes = {i["quote"] for i in filed}
    assert QUOTE_CAPACITY in quotes and QUOTE_RISK in quotes and QUOTE_COMMIT in quotes
    assert {i["source"] for i in filed} == {"concall transcript - 2026-05-01"}
    sides = {i["fact"][:20]: i["side_hint"] for i in filed}
    assert sides["Input cost inflation"] == "bear"      # risks_headwinds


def test_pack_accepts_a_flattened_bundle_too():
    flat = dict(BUNDLE["fundamental"])
    flat["prices"] = BUNDLE["prices"]
    flat["signal"] = BUNDLE["signal"]
    pack = D.evidence_pack(flat)
    assert pack and {i["family"] for i in pack} >= {"valuation", "dcf", "technical"}


# ---------------------------------------------------------------------- prompt
def test_prompt_states_the_house_rules():
    pack = D.evidence_pack(BUNDLE, filings=FILINGS, sector=SECTOR)
    prompt = D._build_prompt("bull", "Test Industries Ltd (TESTCO)", pack, 1, 3, "")
    low = prompt.lower()
    assert "argue only from the numbered evidence" in low
    assert "square brackets" in low
    assert "never invent" in low
    assert "no price target" in low and "buy/sell/hold" in low
    assert "concede" in low
    assert "[E1]" in prompt and f"[E{len(pack)}]" in prompt
    assert "open your case" in low                       # round 1 is an opening


def test_round_one_is_an_opening_and_later_rounds_are_rebuttals():
    out, model = _run(rounds=2)
    assert len(model.prompts) == 4                       # bull, bear, bull, bear
    bull_r1, bear_r1, bull_r2, bear_r2 = model.prompts
    assert "rebut" not in bull_r1.lower() and "rebut" not in bear_r1.lower()
    for p in (bull_r2, bear_r2):
        assert "REBUT that turn" in p
        assert "do not restate your own" in p.lower()
    assert out["_meta"]["rounds"] == 2


def test_rebuttal_prompt_carries_the_opponents_previous_turn_verbatim():
    script = [
        "Bull opening leans on [E1]. And on [E4].",
        "Bear opening leans on [E2]. And on [E5].",
        "Bull rebuttal answers [E2]. Conceded: [E5] is fair.",
        "Bear rebuttal answers [E1]. It also answers [E4].",
    ]
    out, model = _run(rounds=2, model=FakeModel(script))
    bull_r2, bear_r2 = model.prompts[2], model.prompts[3]
    assert "Bear opening leans on [E2]. And on [E5]." in bull_r2   # opponent, verbatim
    assert "Bull opening leans on [E1]. And on [E4]." not in bull_r2
    assert "Bull rebuttal answers [E2]. Conceded: [E5] is fair." in bear_r2
    assert "THE BEAR JUST ARGUED, VERBATIM" in bull_r2
    assert "THE BULL JUST ARGUED, VERBATIM" in bear_r2
    # round 1 must not leak the other side's opening
    assert "Bull opening" not in model.prompts[1]
    assert out["rounds"][2]["conceded"] is True


# -------------------------------------------------------------------- rounds
def test_both_sides_speak_in_every_round():
    out, _ = _run(rounds=3)
    assert [(t["round"], t["side"]) for t in out["rounds"]] == [
        (1, "bull"), (1, "bear"), (2, "bull"), (2, "bear"),
        (3, "bull"), (3, "bear")]
    assert all(t["cites"] and t["text"] for t in out["rounds"])
    assert "error" not in out
    assert out["_meta"]["turns_dropped"] == 0
    assert out["_meta"]["cites_invalid"] == 0
    assert out["_meta"]["model"] == "fake-1"
    assert out["code"] == "TESTCO" and out["name"] == "Test Industries Ltd"
    assert out["generated_at"] == "2026-07-29"
    assert set(out) >= {"code", "name", "generated_at", "evidence", "rounds",
                        "scorecard", "_meta"}


def test_rounds_are_clamped_to_something_sane():
    out, model = _run(rounds=99)
    assert out["_meta"]["rounds"] == D._MAX_ROUNDS
    assert len(model.prompts) == 2 * D._MAX_ROUNDS
    out2, model2 = _run(rounds=0)
    assert out2["_meta"]["rounds"] == 1 and len(model2.prompts) == 2


# ------------------------------------------------------------------- grounding
def test_claim_citing_an_unknown_id_is_stripped_and_counted():
    script = [
        "Returns beat the sector [E1]. A phantom claim rests on [E999]. "
        "Margins are the swing factor [E4].",
        "Bear opening leans on [E2]. And on [E5].",
    ]
    out, _ = _run(rounds=1, model=FakeModel(script))
    bull = out["rounds"][0]
    assert "phantom" not in bull["text"]
    assert "E999" not in bull["text"]
    assert bull["cites"] == ["E1", "E4"]
    assert out["_meta"]["cites_invalid"] == 1
    assert out["_meta"]["cites_invalid_ids"] == ["E999"]
    assert out["_meta"]["claims_stripped"] == 1
    assert out["_meta"]["turns_dropped"] == 0


def test_turn_with_no_valid_citation_is_dropped_and_counted():
    script = [
        "Everything I say rests on [E999]. And on [E1000].",     # bull, all invented
        "Bear opening leans on [E2]. And on [E5].",
    ]
    out, _ = _run(rounds=1, model=FakeModel(script))
    assert [(t["round"], t["side"]) for t in out["rounds"]] == [(1, "bear")]
    assert out["_meta"]["turns_dropped"] == 1
    assert out["_meta"]["cites_invalid"] == 2
    assert out["_meta"]["cites_invalid_ids"] == ["E999", "E1000"]
    assert "error" not in out                       # one side surviving is still a debate


def test_turn_with_no_citation_at_all_is_dropped():
    script = ["I simply believe the business is wonderful.",
              "Bear opening leans on [E2]. And on [E5]."]
    out, _ = _run(rounds=1, model=FakeModel(script))
    assert [t["side"] for t in out["rounds"]] == ["bear"]
    assert out["_meta"]["turns_dropped"] == 1
    assert out["_meta"]["cites_invalid"] == 0


def test_citation_forms_are_all_understood():
    assert D._ids_in("a [E7] b [E12, E3] c [e9] d") == ["E7", "E12", "E3", "E9"]
    assert D._ids_in("no citations here [2026] at all") == []
    assert D._ids_in("") == []
    assert D._ids_in("repeat [E5] and [E5]") == ["E5"]


def test_invented_quotation_is_flagged_against_the_evidence_corpus():
    script = [
        'Management said "we will double revenue by FY30" [E1]. Capacity is real [E4].',
        f'Management said "{QUOTE_CAPACITY}" [E2]. That is on the record [E5].',
    ]
    out, _ = _run(rounds=1, model=FakeModel(script))
    bull, bear = out["rounds"][0], out["rounds"][1]
    assert bull["quotes_unverified"] == ["we will double revenue by FY30"]
    assert "quotes_unverified" not in bear          # that one really is in the pack
    assert out["_meta"]["quotes_unverified"] == 1


# ------------------------------------------------------------------- scorecard
SMALL = [
    {"id": "E1", "fact": "ROCE is high.", "value": "21%", "source": "s",
     "url": "", "side_hint": "bull", "family": "profitability", "weight": 3},
    {"id": "E2", "fact": "Debt is rising.", "value": "0.94x", "source": "s",
     "url": "", "side_hint": "bear", "family": "balance_sheet", "weight": 3},
    {"id": "E3", "fact": "Sector reads HEADWIND.", "value": "-0.42", "source": "s",
     "url": "", "side_hint": "bear", "family": "sector", "weight": 2},
    {"id": "E4", "fact": "Screen reason.", "value": "", "source": "s",
     "url": "", "side_hint": "bull", "family": "signal", "weight": 1},
    {"id": "E5", "fact": "Pledge at 6.5%.", "value": "6.50%", "source": "s",
     "url": "", "side_hint": "bear", "family": "ownership", "weight": 2},
    {"id": "E6", "fact": "Nobody mentions this.", "value": "", "source": "s",
     "url": "", "side_hint": "neutral", "family": "insight", "weight": 2},
]
SMALL_TURNS = [
    {"round": 1, "side": "bull", "text": "x", "cites": ["E1", "E4"]},
    {"round": 1, "side": "bear", "text": "y", "cites": ["E2", "E3", "E1"]},
    {"round": 2, "side": "bull", "text": "z", "cites": ["E1", "E2"]},
]


def test_scorecard_blind_spots_are_exactly_the_unused_evidence():
    sc = D.scorecard(SMALL, SMALL_TURNS)
    assert sc["blind_spots"] == ["E5", "E6"]
    assert [b["id"] for b in sc["blind_spot_facts"]] == ["E5", "E6"]
    assert sc["blind_spot_facts"][0]["fact"] == "Pledge at 6.5%."
    assert sc["evidence_total"] == 6 and sc["evidence_used"] == 4
    assert sc["coverage_pct"] == round(100 * 4 / 6, 1)
    assert sc["contested"] == ["E1", "E2"]


def test_scorecard_weighs_the_heavier_evidence():
    sc = D.scorecard(SMALL, SMALL_TURNS)
    assert sc["bull"]["unique_ids"] == ["E1", "E4", "E2"]
    assert sc["bull"]["cites"] == 4 and sc["bull"]["unique_cites"] == 3
    assert sc["bull"]["weight"] == 3 + 1 + 3
    assert sc["bull"]["high_weight_cites"] == 2          # E1, E2
    assert sc["bear"]["unique_ids"] == ["E2", "E3", "E1"]
    assert sc["bear"]["weight"] == 3 + 2 + 3
    assert sc["bear"]["high_weight_cites"] == 2
    # high-weight ties, so the tie-break falls to total weight -> bear
    assert sc["evidence_edge"] == "bear"


def test_scorecard_is_deterministic_and_ignores_unknown_ids():
    assert D.scorecard(SMALL, SMALL_TURNS) == D.scorecard(SMALL, SMALL_TURNS)
    noisy = SMALL_TURNS + [{"round": 3, "side": "bull", "text": "q",
                            "cites": ["E999"]},
                           {"round": 3, "side": "sideline", "text": "q",
                            "cites": ["E5"]}]
    sc = D.scorecard(SMALL, noisy)
    assert sc["blind_spots"] == ["E5", "E6"]             # E5 came from no real side
    assert sc["bull"]["unique_cites"] == 3
    assert D.scorecard([], [])["evidence_edge"] == "tie"
    assert D.scorecard([], [])["coverage_pct"] == 0.0


def test_scorecard_on_a_real_run_matches_the_surviving_turns():
    out, _ = _run(rounds=2)
    used = {c for t in out["rounds"] for c in t["cites"]}
    ids = [e["id"] for e in out["evidence"]]
    assert out["scorecard"]["blind_spots"] == [i for i in ids if i not in used]
    assert out["scorecard"]["evidence_total"] == len(ids)


def test_whole_debate_is_reproducible_for_a_fixed_model():
    a, _ = _run(rounds=2, model=FakeModel())
    b, _ = _run(rounds=2, model=FakeModel())
    assert a == b


# ------------------------------------------------------------- failure paths
def test_no_key_returns_a_contract_shaped_error_without_raising():
    out, model = _run(rounds=2, model=FakeModel(ok=False,
                                                error="no LLM provider configured"))
    assert "error" in out and "no LLM provider configured" in out["error"]
    assert out["rounds"] == []
    assert set(out) >= {"code", "name", "generated_at", "evidence", "rounds",
                        "scorecard", "_meta", "error"}
    assert out["evidence"]                            # the pack still stands alone
    assert out["_meta"]["turns_failed"] == 4
    assert out["scorecard"]["blind_spots"] == [e["id"] for e in out["evidence"]]
    assert model.calls == 4


def test_default_complete_is_the_provider_layer(monkeypatch):
    from earnings_intel import llm

    seen = {"prompts": [], "providers": []}

    def fake_complete(prompt, **kw):
        seen["prompts"].append(prompt)
        seen["providers"].append(kw.get("provider"))
        return LLMResponse.failure("gemini", "", "no LLM provider configured")

    monkeypatch.setattr(llm, "complete", fake_complete)
    out = D.run_debate(BUNDLE, filings=FILINGS, sector=SECTOR, rounds=1,
                       provider="gemini")
    assert seen["providers"] == ["gemini", "gemini"]
    assert seen["prompts"][0].startswith("You are the BULL")
    assert seen["prompts"][1].startswith("You are the BEAR")
    assert "error" in out and out["rounds"] == []


def test_a_model_that_explodes_never_escapes():
    def boom(prompt, **kw):
        raise RuntimeError("provider on fire")

    out = D.run_debate(BUNDLE, filings=FILINGS, sector=SECTOR, rounds=1,
                       complete=boom)
    assert "error" in out and "provider on fire" in out["error"]
    assert out["rounds"] == [] and out["_meta"]["turns_failed"] == 2


def test_a_narrow_complete_signature_still_works():
    calls = []

    def narrow(prompt):                       # no keyword arguments at all
        calls.append(prompt)
        return "Narrow but valid [E1]. And [E4]."

    out = D.run_debate(BUNDLE, filings=FILINGS, rounds=1, complete=narrow)
    assert len(calls) == 2
    assert [t["side"] for t in out["rounds"]] == ["bull", "bear"]


def test_empty_bundle_reports_no_evidence_rather_than_debating():
    model = FakeModel()
    out = D.run_debate({}, complete=model)
    assert "error" in out and "no evidence" in out["error"]
    assert out["rounds"] == [] and out["evidence"] == []
    assert model.calls == 0                   # never bother the model with nothing


def test_run_debate_survives_garbage_input():
    for junk in (None, [], "not-a-bundle", {"fundamental": 7}):
        out = D.run_debate(junk, complete=FakeModel())
        assert isinstance(out, dict) and "error" in out
        assert set(out) >= {"code", "name", "generated_at", "evidence",
                            "rounds", "scorecard", "_meta"}
