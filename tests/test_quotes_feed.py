"""The shared quote feed.

Reported as "% change is not showing live, is the Upstox token broken?". The
token was fine. Three separate faults made a working feed look dead:

  * Only data/quotes.json was read. It carries ~517 BSE names; the Upstox pass
    over the whole universe lands in data/quotes_wide.json with ~5,650. So the
    day change resolved for 9% of the board -- and for none of the small and
    mid caps that sort to the top of a growth screen.
  * The poller only reached datasets that existed when it fired. Switch to a
    screen that loads its rows afterwards and every row showed a dash until
    the next tick a minute later, while the header still read "updated".
  * Several boards with a price column had no poller at all.

vendor/quotes.js caches the merged feed so a board can be priced the moment
its rows arrive, rather than waiting for a poll it already missed.
"""
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DOCS = ROOT / "docs"
FEED = DOCS / "vendor" / "quotes.js"

# Pages that display a price and must therefore show a live one.
PRICED_PAGES = ("index.html", "technofunda.html", "sector.html", "fairvalue.html")


@pytest.fixture(scope="module")
def js():
    return FEED.read_text(encoding="utf-8")


def test_the_shared_feed_exists():
    assert FEED.exists(), "vendor/quotes.js is missing; every board goes stale"


def test_it_reads_both_files(js):
    assert "data/quotes.json" in js and "data/quotes_wide.json" in js


def test_the_direct_read_wins_a_contested_code(js):
    """quotes.json is the BSE per-scrip read; quotes_wide.json is the Upstox
    pass with Yahoo filling gaps. Wide must be laid down FIRST so narrow
    overwrites it, not the other way round."""
    wide = js.index("fresh(wide)")
    narrow = js.index("fresh(narrow)")
    assert wide < narrow, "the wide feed would overwrite the direct exchange read"


def test_a_stale_file_is_dropped_on_its_own(js):
    """One stale file must not suppress the other, and nothing older than the
    window is applied at all -- yesterday's move shown as today's is worse
    than showing no move."""
    assert "MAX_AGE" in js
    m = re.search(r"MAX_AGE\s*=\s*(\d+)", js)
    assert m and int(m.group(1)) == 5400


def test_absent_is_not_zero(js):
    """A 0 renders as "unchanged today", which is a claim about the stock
    rather than about the feed."""
    assert "q.pct != null" in js.replace("q.pct!=null", "q.pct != null")


def test_the_feed_is_cached_so_a_late_board_can_be_priced(js):
    """The whole point: apply() works off the cached feed, so a screen that
    loads after the last poll is priced immediately."""
    assert "function apply(" in js
    assert "onUpdate" in js and "start" in js


def test_every_page_that_shows_a_price_loads_the_feed():
    missing = [p for p in PRICED_PAGES
               if 'src="vendor/quotes.js"' not in (DOCS / p).read_text(encoding="utf-8")]
    assert not missing, f"no shared quote feed on: {missing}"


def test_the_feed_loads_before_the_page_script_that_calls_it():
    """A page whose inline script calls scanXQuotes at parse time throws a
    ReferenceError if the module is loaded after it."""
    bad = []
    for name in PRICED_PAGES:
        html = (DOCS / name).read_text(encoding="utf-8")
        if "scanXQuotes" not in html:
            continue
        load_at = html.index('src="vendor/quotes.js"')
        # first *unguarded* use in an inline script
        uses = [m.start() for m in re.finditer(r"(?<!window\.)scanXQuotes\.", html)]
        if uses and min(uses) < load_at:
            bad.append(name)
    assert not bad, f"quotes.js is loaded after it is used on: {bad}"


@pytest.mark.parametrize("page", PRICED_PAGES)
def test_each_board_prices_its_rows_on_load_not_only_on_poll(page):
    """The reported symptom: the header said "updated" while every row showed
    a dash, because the rows arrived after the poll."""
    html = (DOCS / page).read_text(encoding="utf-8")
    assert "scanXQuotes.apply(" in html, f"{page} never applies the cached feed"
    assert "scanXQuotes.onUpdate(" in html, f"{page} never refreshes"


@pytest.mark.parametrize("page", PRICED_PAGES)
def test_no_board_keeps_a_private_poller(page):
    """Two implementations drift. The bespoke pollers read the narrow file
    only, which is how this shipped."""
    html = (DOCS / page).read_text(encoding="utf-8")
    assert "function livePoll(" not in html, f"{page} still has its own poller"


def test_the_boards_show_a_day_change_column():
    """A live price with no change column still answers "what happened
    today?" with nothing."""
    for page in PRICED_PAGES:
        html = (DOCS / page).read_text(encoding="utf-8")
        assert "% Chg" in html, f"{page} has no day-change column"


def test_the_technofunda_screens_are_all_priced():
    """TF, the four-quarter screen and the pullback screen each carry an LTP
    column; only the first was ever refreshed."""
    html = (DOCS / "technofunda.html").read_text(encoding="utf-8")
    for name in ("DATA", "QP_DATA", "PB_DATA"):
        assert f"scanXQuotes.apply({name})" in html, name


def test_the_page_still_bootstraps():
    """Removing the old poller block took the load() call with it once; the
    board rendered nothing at all."""
    html = (DOCS / "technofunda.html").read_text(encoding="utf-8")
    assert re.search(r"^load\(\);", html, re.M), "technofunda never calls load()"
