"""Vahan auto-registrations parsing (scripts/refresh_auto.py) — pure functions, no network."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import refresh_auto as ra  # noqa: E402


def _row(sno, maker, months, total):
    tds = "".join(f"<td role=\"gridcell\"><label>{v}</label></td>" for v in [sno, maker] + months + [total])
    return f'<tr data-ri="{sno}" class="ui-widget-content" role="row">{tds}</tr>'


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
