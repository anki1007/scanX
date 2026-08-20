"""Link previews.

A shared link is the first thing most people see of scanX, and the failure is
silent: the tags either render a card or they don't, and nothing in the app
breaks either way. So these pin the parts that actually decide it.

The card image is 1200x630 (1.91:1). The source artwork is 3:2, and every
platform crops toward 1.91:1 -- on the raw file that removes 220px of height,
taking the logo off the top or the icon strip off the bottom. It is letterboxed
onto the card instead, padded with the artwork's own edge colour.
"""
import re
import sys
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DOCS = ROOT / "docs"
PAGES = sorted(DOCS.glob("*.html"))
BASE = "https://anki1007.github.io/scanX/"
CARD = DOCS / "og-scanX.png"


def _soup(p):
    return BeautifulSoup(p.read_text(encoding="utf-8"), "html.parser")


def _meta(soup, key):
    tag = (soup.find("meta", attrs={"property": key})
           or soup.find("meta", attrs={"name": key}))
    return tag.get("content") if tag else None


def test_the_card_image_exists():
    assert CARD.exists(), "og-scanX.png is missing; every preview would be blank"


def test_the_card_is_the_aspect_ratio_platforms_crop_toward():
    Image = pytest.importorskip("PIL.Image", reason="Pillow not installed")
    with Image.open(CARD) as im:
        assert im.size == (1200, 630), f"card is {im.size}, not 1200x630"


def test_the_card_is_small_enough_to_be_fetched():
    """X rejects over 5MB, and a slow image is often skipped on first scrape."""
    mb = CARD.stat().st_size / (1024 * 1024)
    assert mb < 5, f"card is {mb:.1f} MB"


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_every_page_declares_a_preview(page):
    s = _soup(page)
    for key in ("og:title", "og:description", "og:image", "og:url", "og:type",
                "twitter:card", "twitter:title", "twitter:image"):
        assert _meta(s, key), f"{page.name} is missing {key}"


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_preview_urls_are_absolute(page):
    """A relative og:image is the classic reason a card renders blank: the
    crawler resolves it against its own host, not the site."""
    s = _soup(page)
    for key in ("og:image", "og:url", "twitter:image"):
        v = _meta(s, key)
        assert v.startswith("https://"), f"{page.name} {key} is not absolute: {v}"


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_the_image_url_resolves_to_a_file_we_ship(page):
    s = _soup(page)
    rel = _meta(s, "og:image")[len(BASE):]
    assert (DOCS / rel).exists(), f"{page.name} points at a file that is not published: {rel}"


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_each_page_declares_its_own_url(page):
    s = _soup(page)
    expected = BASE if page.name == "index.html" else BASE + page.name
    assert _meta(s, "og:url") == expected
    canon = s.find("link", rel="canonical")
    assert canon is not None and canon.get("href") == expected


def test_the_declared_dimensions_match_the_real_image():
    """A wrong width/height makes some crawlers letterbox or skip the image."""
    Image = pytest.importorskip("PIL.Image", reason="Pillow not installed")
    s = _soup(DOCS / "index.html")
    with Image.open(CARD) as im:
        assert int(_meta(s, "og:image:width")) == im.size[0]
        assert int(_meta(s, "og:image:height")) == im.size[1]


def test_the_large_card_is_requested_not_the_thumbnail():
    for p in PAGES:
        assert _meta(_soup(p), "twitter:card") == "summary_large_image", p.name


def test_no_data_vendor_is_named_in_the_preview_copy():
    """Same rule the pages follow: the card must not advertise where the
    numbers come from."""
    # The vendor IDENTITY, not the English word: "Fundamental Screener" is a
    # feature name and has always been one. redact.py draws the same line --
    # it rewrites "screener_note" and "screener flags a", never bare "screener".
    banned = ("screener.in", "upstox", "trendlyne", "dhan")
    for p in PAGES:
        s = _soup(p)
        for key in ("og:title", "og:description", "og:image:alt",
                    "twitter:title", "twitter:description", "description"):
            v = (_meta(s, key) or "").lower()
            for word in banned:
                assert word not in v, f"{p.name} {key} names {word}"


def test_the_headline_is_not_left_as_one_pages_narrow_title():
    """The root link represents the whole app, not the board that happens to
    live at /."""
    s = _soup(DOCS / "index.html")
    assert _meta(s, "og:title") != s.title.string
    assert "scanX" in _meta(s, "og:title")


def test_the_alt_text_describes_the_image():
    s = _soup(DOCS / "index.html")
    alt = _meta(s, "og:image:alt")
    assert alt and len(alt) > 20, "alt text is what screen readers announce"
