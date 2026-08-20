"""The left rail.

Reported as "clicking a sub-menu opens another heading or hides and opens
something else". It was not one bug:

  * the pages disagreed on the ORDER of the groups -- 15 had Tools before
    Market Pulse and 8 had it after. vendor/nav.js remembers which groups are
    open (localStorage, keyed by heading text), so an expanded group jumped to
    a different position mid-navigation and read as a different heading
    opening by itself.
  * vendor/nav.js selected only `aside.nav`, so breadth.html and stdrl.html --
    which use `<nav class="nav">` -- silently kept a flat, always-expanded
    rail while every other page collapsed.
  * industries.html never loaded the script at all.
  * index.html marked TWO items active, because Home and PEAD Board are the
    same page.

The rail is progressive enhancement over plain markup, so these assert the
markup is identical everywhere and that every page can actually enhance it.
"""
import re
import sys
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DOCS = ROOT / "docs"
PAGES = sorted(p for p in DOCS.glob("*.html"))
# Every container variant in use. nav.js must match all of them.
CONTAINERS = "aside.nav, nav.nav, aside.navrail, .navrail"


def _nav(page: Path):
    soup = BeautifulSoup(page.read_text(encoding="utf-8"), "html.parser")
    return soup.select_one(CONTAINERS)


def _structure(box):
    """(heading, label, href) per item, in document order."""
    out, cur = [], "(top)"
    for el in box.find_all(["div", "a"]):
        cls = el.get("class") or []
        if "grpL" in cls:
            cur = el.get_text(strip=True)
        elif "nav-i" in cls:
            label = re.sub(r"^[^\w]+", "", el.get_text(" ", strip=True)).strip()
            out.append((cur, label, el.get("href")))
    return out


@pytest.fixture(scope="module")
def navs():
    return {p.name: _nav(p) for p in PAGES}


def test_there_are_pages_to_check():
    assert len(PAGES) >= 20


def test_every_page_has_exactly_one_rail(navs):
    missing = [n for n, b in navs.items() if b is None]
    assert not missing, f"no nav container on: {missing}"


def test_every_page_has_the_same_menu(navs):
    """The defect: two different group orders across the site. The rail
    remembers open groups across pages, so a moving group reads as a different
    heading opening by itself."""
    structures = {n: _structure(b) for n, b in navs.items()}
    canonical = structures["index.html"]
    differing = {n: s for n, s in structures.items() if s != canonical}
    assert not differing, (
        "these pages have a different menu than index.html: "
        + ", ".join(sorted(differing))
    )


def test_the_group_order_is_fixed(navs):
    heads = []
    for h, _, _ in _structure(navs["index.html"]):
        if not heads or heads[-1] != h:
            heads.append(h)
    assert heads == ["(top)", "Screens", "Intrinsic Value", "Tools", "Market Pulse"]


def test_every_link_points_at_a_page_that_exists(navs):
    names = {p.name for p in PAGES}
    broken = set()
    for page, box in navs.items():
        for _, _, href in _structure(box):
            target = (href or "").split("#")[0].split("?")[0]
            if target and not target.startswith(("http", "mailto")) and target not in names:
                broken.add(f"{page} -> {href}")
    assert not broken, f"dead menu links: {sorted(broken)}"


def test_each_page_highlights_itself_exactly_once(navs):
    """index.html marked both Home and PEAD Board, which are the same page."""
    wrong = {}
    for page, box in navs.items():
        active = [a for a in box.select(".nav-i.active")]
        hrefs = [a.get("href") for a in active]
        if len(active) != 1 or hrefs[0] != page:
            wrong[page] = hrefs
    assert not wrong, f"wrong active marker: {wrong}"


def test_every_page_loads_the_shared_rail_script():
    """industries.html did not, so it alone showed a flat expanded menu."""
    missing = [p.name for p in PAGES
               if "vendor/nav.js" not in p.read_text(encoding="utf-8")]
    assert not missing, f"no vendor/nav.js on: {missing}"


def test_the_rail_script_matches_every_container_variant():
    """It selected only `aside.nav`; two pages use `<nav class="nav">` and one
    uses `.navrail`, and on those the script returned early and did nothing."""
    js = (DOCS / "vendor" / "nav.js").read_text(encoding="utf-8")
    m = re.search(r'var nav = document\.querySelector\("([^"]+)"\)', js)
    assert m, "could not find the container lookup in nav.js"
    selector = m.group(1)
    used = set()
    for p in PAGES:
        box = _nav(p)
        cls = ".".join(box.get("class") or [])
        used.add(f"{box.name}.{cls}")
    for variant in used:
        tag, _, cls = variant.partition(".")
        assert (f"{tag}.{cls}" in selector or f".{cls}" in selector), (
            f"nav.js does not match {variant}; it would silently skip that page")


def test_a_group_heading_is_never_left_without_items(navs):
    """An empty heading is a click target that does nothing."""
    for page, box in navs.items():
        counts = {}
        for h, _, _ in _structure(box):
            counts[h] = counts.get(h, 0) + 1
        empty = [h for h, n in counts.items() if n == 0]
        assert not empty, f"{page}: headings with no items: {empty}"


def test_the_top_items_sit_outside_every_group(navs):
    """nav.js only groups links that FOLLOW a heading. Home and Market Breadth
    are meant to stay visible while everything is collapsed."""
    top = [(h, l) for h, l, _ in _structure(navs["index.html"]) if h == "(top)"]
    assert [l for _, l in top] == ["Home", "Market Breadth"]
