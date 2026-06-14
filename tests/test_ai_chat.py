import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from earnings_intel.data import ai_chat as ac


def _fund():
    return {
        "code": "TEST", "name": "Test Industries", "url": "https://www.screener.in/company/TEST/",
        "overview": {"Market Cap": "₹ 1,200 Cr", "Stock P/E": "18.5", "ROCE": "22.1 %"},
        "growth": {"Sales 3Y": "14%", "Profit 3Y": "21%"},
        "pros": ["Healthy ROCE"], "cons": ["Rising receivables"],
        "quarters": {"headers": ["Mar 2025", "Jun 2025"],
                     "rows": {"Sales": ["100", "110"], "Net Profit": ["10", "12"]}},
        "profit_loss": {"headers": ["Mar 2024", "Mar 2025"],
                        "rows": {"Sales": ["350", "420"], "Net Profit": ["30", "41"]}},
        "balance_sheet": {"headers": [], "rows": {}},
        "cash_flow": {"headers": [], "rows": {}},
        "ratios": {"headers": [], "rows": {}},
        "shareholding": {"headers": ["Mar 2025"], "rows": {"Promoters": ["61.2"]}},
    }


def test_tbl_markdown_shape():
    t = ac._tbl({"headers": ["A", "B", "C"], "rows": {"Sales": ["1", "2", "3"]}}, last=2)
    assert t.splitlines()[0] == "| | B | C |"
    assert "| Sales | 2 | 3 |" in t


def test_tbl_empty():
    assert ac._tbl({"headers": [], "rows": {}}) == ""


def test_build_context_contains_key_blocks():
    ctx = ac.build_context(_fund(), price={"technical": {"rs_rating": 71}},
                           sector={"name": "Capital Goods", "label": "TAILWIND", "score": 0.9})
    for needle in ("Test Industries", "KEY METRICS", "QUARTERLY RESULTS",
                   "ANNUAL P&L", "SHAREHOLDING", "TAILWIND", "rs_rating: 71",
                   "PROS", "CONS"):
        assert needle in ctx, needle
    assert "BALANCE SHEET" not in ctx          # empty tables skipped


def test_fold_history_last_n_and_roles():
    h = [{"role": "user", "text": f"q{i}"} for i in range(12)]
    h[-1] = {"role": "ai", "text": "answer"}
    s = ac.fold_history(h, max_turns=4)
    lines = s.splitlines()
    assert len(lines) == 4
    assert lines[-1] == "scanX AI: answer"
    assert ac.fold_history([]) == ""
    assert ac.fold_history([{"role": "user", "text": "  "}]) == ""


def test_build_prompt_order_and_grounding_blocks():
    p = ac.build_prompt("Test Industries", "CTX", "DOCTEXT",
                        [{"role": "user", "text": "hi"}], "What are the red flags?")
    assert p.index("==== COMPANY DATA") < p.index("==== DOCUMENT EXCERPT") \
        < p.index("==== CONVERSATION SO FAR") < p.index("==== QUESTION")
    assert "Test Industries" in p and "red flags" in p
    p2 = ac.build_prompt("X", "CTX", "", None, "q")
    assert "==== DOCUMENT EXCERPT" not in p2 and "==== CONVERSATION" not in p2


def test_answer_requires_question(monkeypatch):
    monkeypatch.setattr(ac.ia, "have_key", lambda: True)
    assert "error" in ac.answer("TEST", "")


def test_answer_no_key(monkeypatch):
    monkeypatch.setattr(ac.ia, "have_key", lambda: False)
    out = ac.answer("TEST", "hello")
    assert "error" in out and "key" in out["error"].lower()


def test_call_llm_dispatch(monkeypatch):
    calls = {}

    class _R:
        status_code = 200
        text = ""
        def json(self):
            return {"choices": [{"message": {"content": "oa-ans"}}],
                    "content": [{"text": "an-ans"}]}

    def fake_post(url, **kw):
        calls["url"] = url
        calls["headers"] = kw.get("headers", {})
        return _R()

    import requests
    monkeypatch.setattr(requests, "post", fake_post)
    assert ac.call_llm("p", "openai", "sk-x") == "oa-ans"
    assert "openai.com" in calls["url"] and calls["headers"]["Authorization"] == "Bearer sk-x"
    assert ac.call_llm("p", "anthropic", "sk-a") == "an-ans"
    assert "anthropic.com" in calls["url"] and calls["headers"]["x-api-key"] == "sk-a"
    # missing keys raise (never silently fall through to gemini)
    import pytest
    with pytest.raises(RuntimeError):
        ac.call_llm("p", "openai", None)
    with pytest.raises(RuntimeError):
        ac.call_llm("p", "anthropic", "")
    # gemini path routes to ia._gemini with server/user key
    monkeypatch.setattr(ac.ia, "_api_key", lambda: "AIzaLOCAL")
    monkeypatch.setattr(ac.ia, "_gemini",
                        lambda prompt, key, model, json_mode=True, temperature=0: f"gm:{key}")
    assert ac.call_llm("p", "gemini", None) == "gm:AIzaLOCAL"
    assert ac.call_llm("p", "gemini", "AIzaUSER") == "gm:AIzaUSER"


def test_market_context_and_general_mode(monkeypatch):
    ctx = ac.market_context()
    assert isinstance(ctx, str) and "SECTOR SIGNALS" in ctx
    assert "MAGIC FORMULA" in ctx
    # general mode (no code) calls the LLM with board data
    seen = {}
    def fake_llm(prompt, provider="gemini", api_key=None, model=None, temperature=0.2):
        seen["prompt"] = prompt
        return "general answer"
    monkeypatch.setattr(ac, "call_llm", fake_llm)
    monkeypatch.setattr(ac.ia, "have_key", lambda: True)
    out = ac.answer("", "Which sectors have tailwind?")
    assert out.get("mode") == "market" and out["answer"] == "general answer"
    assert "scanX BOARD DATA" in seen["prompt"]
