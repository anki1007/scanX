import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from earnings_intel.data import company as co

PERIODS = ["Mar 2014","Mar 2015","Mar 2016","Mar 2017","Mar 2018","Mar 2019",
           "Mar 2020","Mar 2021","Mar 2022","Mar 2023","Mar 2024","Mar 2025"]


def _table(rows_html):
    th = "".join(f"<th>{p}</th>" for p in PERIODS)
    return (f'<div id="yearly-insights"><table>'
            f'<thead><tr><th></th>{th}</tr></thead><tbody>{rows_html}</tbody></table></div>')


def _row(metric, unit, vals):
    tds = "".join(f"<td>{v}</td>" for v in vals)
    return (f'<tr><td><div>{metric}</div><div class="sub">{unit} · Standalone data</div></td>{tds}</tr>')


def _soup(html):
    from bs4 import BeautifulSoup
    return BeautifulSoup(html, "lxml")


def test_insights_clean_premium_grid():
    cap = [30,34,37,34,43,43,43,46,46,46.04,51,55.40]
    prod = [300.25,304.41,285.74,340.05,355.80,323.90,314.90,341.10,421.90,408.20,412.20,440.00]
    html = _table(_row("Iron Ore Mining Capacity","MTPA",cap)
                  + _row("Iron Ore Production","Lakh Tonnes",prod))
    ins = co._insights(_soup(html))
    assert "yearly" in ins
    y = ins["yearly"]
    assert y["periods"] == PERIODS
    assert len(y["rows"]) == 2
    r0 = y["rows"][0]
    assert r0["metric"] == "Iron Ore Mining Capacity" and r0["unit"] == "MTPA"
    assert r0["values"]["Mar 2014"] == "30" and r0["values"]["Mar 2025"] == "55.4"
    assert y["rows"][1]["metric"] == "Iron Ore Production"
    assert y["rows"][1]["values"]["Mar 2025"] == "440.0"


def test_insights_masked_session_yields_nothing():
    masked = ["xx.xx"] * 12
    html = _table(_row("Iron Ore Mining Capacity","MTPA",masked)
                  + _row("Iron Ore Production","Lakh Tonnes",masked))
    ins = co._insights(_soup(html))
    assert ins == {}      # nothing kept -> UI hides the panel


def test_insights_absent_section():
    assert co._insights(_soup("<div>no insights here</div>")) == {}
