"""The market-hours loop and the boards it runs every hour.

GitHub's scheduler fired the 24-a-day quotes cron about twice a weekday, so
"intraday" was two snapshots. One job now loops through the session, and the
announcement-driven boards run inside it hourly. That moves two old defects
from annoying to visible: a board that wrote its empty fetch over a good file
blanked for a night -- run hourly, it would flicker -- and a push retry that
rebased JSON with -X theirs could splice two generations of one file.
"""
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

LOOP = ROOT / "scripts" / "ci_intraday.sh"
QUOTES_WF = ROOT / ".github" / "workflows" / "market-quotes.yml"
NIGHTLY_WF = ROOT / ".github" / "workflows" / "refresh-data.yml"


# ------------------------------------------------------- no blank boards

def test_market_pulse_keeps_a_good_feed_over_an_empty_fetch(tmp_path):
    import refresh_marketpulse as rm
    p = tmp_path / "deals.json"
    p.write_text(json.dumps({"generated_at_ist": "old", "rows": [{"a": 1}]}), encoding="utf-8")
    assert rm.publish(p, [], "new") is False
    assert json.loads(p.read_text(encoding="utf-8"))["rows"] == [{"a": 1}]
    assert rm.publish(p, [{"b": 2}], "new") is True
    assert json.loads(p.read_text(encoding="utf-8"))["rows"] == [{"b": 2}]


def test_market_pulse_writes_an_empty_feed_over_nothing(tmp_path):
    import refresh_marketpulse as rm
    p = tmp_path / "actions.json"
    assert rm.publish(p, [], "now") is True
    assert json.loads(p.read_text(encoding="utf-8"))["rows"] == []


def test_market_pulse_goes_red_when_every_feed_is_empty(tmp_path, monkeypatch):
    import refresh_marketpulse as rm
    monkeypatch.setattr(rm.mp, "fetch_trades", lambda k, sid: [])
    monkeypatch.setattr(rm.mp, "fetch_actions", lambda k, sid: [])
    monkeypatch.setattr(rm.mp, "fetch_announcements", lambda sid: [])
    monkeypatch.setattr(sys, "argv", ["x", "--out", str(tmp_path), "--delay", "0"])
    assert rm.main() == 1


@pytest.mark.parametrize("module,files", [
    ("refresh_special", ["special.json", "special_meta.json"]),
    ("refresh_orders", ["orders.json", "orders_meta.json"]),
    ("refresh_buybacks", ["buybacks.json", "buybacks_meta.json"]),
])
def test_an_empty_fetch_leaves_the_last_good_board(module, files, tmp_path, monkeypatch):
    mod = __import__(module)
    if module == "refresh_special":
        monkeypatch.setattr(mod, "build_rows", lambda *a, **k: [])
    else:
        monkeypatch.setattr(mod, "build_rows", lambda *a, **k: ([], "feed"))
    for f in files:
        (tmp_path / f).write_text('["last good"]', encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["x", "--out", str(tmp_path)])
    assert mod.main() == 1
    for f in files:
        assert (tmp_path / f).read_text(encoding="utf-8") == '["last good"]'


def test_demergers_goes_red_on_no_data(tmp_path, monkeypatch):
    import refresh_demergers as rd
    monkeypatch.setattr(rd, "build_rows", lambda *a, **k: [])
    monkeypatch.setattr(sys, "argv", ["x", "--out", str(tmp_path)])
    assert rd.main() == 1


# --------------------------------------------------------- bake rotation

def test_the_bake_rotates_oldest_first_after_the_board_head():
    import refresh_fundamentals as rf
    baked = {"TOP1": "2026-09-23", "TOP2": "2026-09-23", "OLD": "2026-07-20",
             "MID": "2026-08-15", "NEW": "", "RECENT": "2026-09-22"}
    order = rf.stalest_first(["TOP1", "TOP2", "RECENT", "MID", "OLD", "NEW"], 2, baked.get)
    assert order == ["TOP1", "TOP2", "NEW", "OLD", "MID", "RECENT"]


def test_the_nightly_bake_uses_the_rotation():
    text = NIGHTLY_WF.read_text(encoding="utf-8")
    line = [ln for ln in text.splitlines() if "--skip-existing" in ln and "refresh_fundamentals" in ln]
    assert line and "--stalest-first" in line[0]


# -------------------------------------------------------------- redaction

def test_redaction_can_be_limited_to_named_files(tmp_path, monkeypatch):
    import redact_sources as rs
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    for p in (a, b):
        p.write_text(json.dumps({"source": "screener.in (saved session)"}), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["x", "--data-dir", str(tmp_path), str(a)])
    assert rs.main() == 0
    assert "screener" not in a.read_text(encoding="utf-8").lower()
    assert "screener" in b.read_text(encoding="utf-8").lower(), "only the named file"


# ------------------------------------------------------------ the loop

def _board_files():
    text = LOOP.read_text(encoding="utf-8")
    block = re.search(r'BOARD_FILES="([^"]+)"', text).group(1)
    return block.split()


def _loop_scripts():
    return re.findall(r"python (scripts/\w+\.py)", LOOP.read_text(encoding="utf-8"))


# Which script is expected to write each published file. Matched as a quoted
# name in that one script, so "meta.json" cannot pass on "buybacks_meta.json".
WRITERS = {
    "quotes.json": "refresh_quotes.py", "quotes_wide.json": "refresh_quotes.py",
    "pead.json": "refresh_scanx.py", "pead.csv": "refresh_scanx.py",
    "meta.json": "refresh_scanx.py",
    "deals.json": "refresh_marketpulse.py", "actions.json": "refresh_marketpulse.py",
    "announcements.json": "refresh_marketpulse.py",
    "demergers.json": "refresh_demergers.py", "demergers_meta.json": "refresh_demergers.py",
    "special.json": "refresh_special.py", "special_meta.json": "refresh_special.py",
    "buybacks.json": "refresh_buybacks.py", "buybacks_meta.json": "refresh_buybacks.py",
    "orders.json": "refresh_orders.py", "orders_companies.json": "refresh_orders.py",
    "orders_meta.json": "refresh_orders.py",
}


def test_every_file_the_loop_publishes_is_written_by_a_script_it_runs():
    run = {Path(s).name for s in _loop_scripts()}
    for f in _board_files() + ["docs/data/quotes.json", "docs/data/quotes_wide.json"]:
        name = Path(f).name
        script = WRITERS.get(name)
        assert script, f"{f} is published but has no known writer"
        assert script in run, f"{script} writes {name} but the loop never runs it"
        src = (ROOT / "scripts" / script).read_text(encoding="utf-8")
        assert re.search(r"[\"'/]" + re.escape(name) + r"[\"']", src), (script, name)


def test_the_loop_redacts_everything_it_publishes_every_cycle():
    """Quotes too: the wide feed writes its source line on every cycle."""
    text = LOOP.read_text(encoding="utf-8")
    body = text[text.index("while :; do"):text.index("done\n", text.index("while :; do"))]
    redact, publish = body.index("redact_sources.py $files"), body.index("publish ")
    assert redact < publish
    hourly = body[body.index("if [ $(( now - last_boards"):body.index("  fi\n")]
    assert "redact_sources" not in hourly, "must not be limited to board cycles"


def test_a_capped_loop_dispatches_its_successor():
    text = LOOP.read_text(encoding="utf-8")
    assert "gh workflow run market-quotes.yml" in text
    wf = QUOTES_WF.read_text(encoding="utf-8")
    assert "actions: write" in wf and "GH_TOKEN: ${{ github.token }}" in wf


def test_redaction_blanks_vendor_urls_in_csv(tmp_path, monkeypatch):
    import redact_sources as rs
    p = tmp_path / "pead.csv"
    want = "code,name,url\nABC,Abc,\n"
    p.write_bytes(b"code,name,url\nABC,Abc,https://www.screener.in/company/ABC/\n")
    monkeypatch.setattr(sys, "argv", ["x", "--data-dir", str(tmp_path), str(p)])
    assert rs.main() == 0
    assert p.read_bytes().decode("utf-8").replace("\r\n", "\n") == want
    assert rs.main() == 0, "idempotent"
    assert p.read_bytes().decode("utf-8").replace("\r\n", "\n") == want


def test_every_script_the_loop_runs_exists():
    for s in _loop_scripts():
        assert (ROOT / s).is_file(), s


def test_the_loop_never_merges_json_textually():
    code = "\n".join(ln for ln in LOOP.read_text(encoding="utf-8").splitlines()
                     if not ln.lstrip().startswith("#"))
    assert "-X theirs" not in code and "rebase" not in code
    assert "reset -q --hard origin/main" in code


def test_the_workflow_runs_the_loop_inside_the_job_cap():
    wf = QUOTES_WF.read_text(encoding="utf-8")
    assert "workflow_dispatch" in wf, "an external trigger is how a full session is guaranteed"
    assert "bash scripts/ci_intraday.sh" in wf
    timeout = int(re.search(r"timeout-minutes:\s*(\d+)", wf).group(1))
    loop_max = int(re.search(r'MAX_MINUTES="\$\{MAX_MINUTES:-(\d+)\}"',
                             LOOP.read_text(encoding="utf-8")).group(1))
    assert loop_max < timeout < 360


def test_a_failed_login_does_not_stop_the_quotes():
    wf = QUOTES_WF.read_text(encoding="utf-8")
    login = wf[wf.index("screener_login.py") - 200:wf.index("screener_login.py")]
    assert "continue-on-error: true" in login
