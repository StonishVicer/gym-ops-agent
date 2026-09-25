import hashlib
from pathlib import Path

import pytest

FONT_DIR = Path(__file__).resolve().parents[2] / "assets" / "fonts"

# DejaVu fonts 2.37, from the official release asset dejavu-fonts-ttf-2.37.tar.bz2
# (github.com/dejavu-fonts/dejavu-fonts). A changed font changes every image's bytes.
EXPECTED_SHA256 = {
    "DejaVuSans.ttf": "7da195a74c55bef988d0d48f9508bd5d849425c1770dba5d7bfc6ce9ed848954",
    "DejaVuSans-Bold.ttf": "e6476c1b80502924294eed40894c5b18e06c181444ca953e5334262df9c27724",
    "LICENSE-DejaVu": "7a083b136e64d064794c3419751e5c7dd10d2f64c108fe5ba161eae5e5958a93",
}


@pytest.mark.parametrize(("name", "digest"), sorted(EXPECTED_SHA256.items()))
def test_bundled_font_unchanged(name: str, digest: str) -> None:
    assert hashlib.sha256((FONT_DIR / name).read_bytes()).hexdigest() == digest


def test_no_unexpected_font_files() -> None:
    assert {p.name for p in FONT_DIR.iterdir()} == set(EXPECTED_SHA256)
