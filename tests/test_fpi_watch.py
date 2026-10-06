"""The FPI watch fetches only while the latest closed fortnight is missing."""
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import fpi_due as fd  # noqa: E402


def test_the_latest_closed_fortnight():
    assert fd.latest_closed_fortnight(date(2026, 10, 7)) == date(2026, 9, 30)
    assert fd.latest_closed_fortnight(date(2026, 10, 15)) == date(2026, 9, 30)
    assert fd.latest_closed_fortnight(date(2026, 10, 16)) == date(2026, 10, 15)
    assert fd.latest_closed_fortnight(date(2026, 3, 1)) == date(2026, 2, 28)


def test_the_published_file_is_read(tmp_path):
    p = tmp_path / "fpi.json"
    p.write_text('{"rows":[{"end":"2026-09-30"}]}', encoding="utf-8")
    assert "2026-09-30" in fd.have_ends(p)
