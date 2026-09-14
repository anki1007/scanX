"""The home page.

index.html used to BE the PEAD board, so the site opened on one screen out of
twenty-five and Home and "PEAD Board" were the same link. The board now lives
at pead.html and index.html lists every module with a line on what it does.

The cards are static markup, not injected by script: the page has to be
useful if the stats fail to load, and it is the page a crawler and a social
card read.
"""
import re
import sys
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DOCS = ROOT / "docs"
HOME = DOCS / "index.html"


def _soup(path):
    return BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")


def _nav_modules(soup):
    """(heading, label, href) for every nav entry except Home itself."""
    box = soup.select_one("aside.nav, nav.nav, aside.navrail, .navrail")
    out, cur = [], "(top)"
    for el in box.find_all(["div", "a"]):
        cls = el.get("class") or []
        if "grpL" in cls:
            cur = el.get_text(strip=True)
        elif "nav-i" in cls and el.get("href") != "index.html":
            label = re.sub(r"^[^\w]+", "", el.get_text(" ", strip=True)).strip()
            out.append((cur, label, el.get("href")))
    return out


@pytest.fixture(scope="module")
def home():
    return _soup(HOME)


def test_home_is_not_the_pead_board(home):
    html = HOME.read_text(encoding="utf-8")
    assert "data/pead.json" not in html, "index.html is still the PEAD board"


def test_the_pead_board_has_its_own_page():
    pead = DOCS / "pead.html"
    assert pead.exists()
    assert "data/pead.json" in pead.read_text(encoding="utf-8")


def test_every_module_in_the_menu_has_a_card(home):
    cards = {a.get("href") for a in home.select("a.mod")}
    missing = [f"{label} ({href})" for _, label, href in _nav_modules(home)
               if href not in cards]
    assert not missing, f"modules with no card on home: {missing}"


def test_there_is_no_card_for_a_page_the_menu_does_not_offer(home):
    menu = {href for _, _, href in _nav_modules(home)}
    extra = [a.get("href") for a in home.select("a.mod") if a.get("href") not in menu]
    assert not extra, f"cards that are not in the menu: {extra}"


def test_every_card_says_what_the_module_does(home):
    thin = []
    for a in home.select("a.mod"):
        desc = a.select_one(".mod-d")
        text = desc.get_text(" ", strip=True) if desc else ""
        if len(text) < 30:
            thin.append(a.get("href"))
    assert not thin, f"cards with no real description: {thin}"


def test_every_card_links_to_a_page_that_exists(home):
    dead = [a.get("href") for a in home.select("a.mod")
            if not (DOCS / a.get("href")).exists()]
    assert not dead, f"cards pointing nowhere: {dead}"


def test_cards_are_grouped_like_the_menu(home):
    """Same sections, same order: the home page is a map of the sidebar, so
    the two must not disagree about where a module lives."""
    menu_heads = []
    for head, _, _ in _nav_modules(home):
        if head != "(top)" and head not in menu_heads:
            menu_heads.append(head)
    card_heads = [h.get_text(strip=True) for h in home.select(".sec-h")]
    assert card_heads[-len(menu_heads):] == menu_heads, (card_heads, menu_heads)

    for sec in home.select("section.sec"):
        head = sec.select_one(".sec-h")
        if head is None or head.get_text(strip=True) not in menu_heads:
            continue
        want = [href for h, _, href in _nav_modules(home)
                if h == head.get_text(strip=True)]
        got = [a.get("href") for a in sec.select("a.mod")]
        assert got == want, (head.get_text(strip=True), got, want)


def test_no_page_sends_pead_board_to_the_home_page():
    bad = []
    for p in sorted(DOCS.glob("*.html")):
        for _, label, href in _nav_modules(_soup(p)):
            if label == "PEAD Board" and href != "pead.html":
                bad.append(f"{p.name} -> {href}")
    assert not bad, bad


def test_the_cards_are_in_the_markup_not_injected(home):
    """Stats are fetched; the cards themselves must not depend on script."""
    assert len(home.select("a.mod")) >= 20


def test_home_does_not_name_a_data_vendor():
    text = HOME.read_text(encoding="utf-8").lower()
    for word in ("screener.in", "upstox", "trendlyne"):
        assert word not in text, word


def test_a_stale_module_is_flagged_rather_than_hidden():
    """Several boards went weeks without updating and nothing on the site said
    so. The freshness badge is the only place a reader learns that."""
    html = HOME.read_text(encoding="utf-8")
    assert "STALE_DAYS" in html and "stale" in html
