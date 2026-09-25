"""Payer-name resolution (ADR-0006): normalized exact match, never a guess."""

import logging
from pathlib import Path

import pytest

from gym_ops.db.connection import get_readonly_connection
from gym_ops.extractor.resolve import MemberDirectory, normalize_name, resolve_member


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [
        ("Dana Smith", "dana smith"),
        ("DANA SMITH", "dana smith"),
        ("  Dana \t  Smith\n", "dana smith"),
        ("José Pérez", "jose perez"),
        ("JOSÉ PÉREZ", "jose perez"),  # combining accents (NFD input)
        ("\uff24\uff41\uff4e\uff41 \uff33\uff4d\uff49\uff54\uff48", "dana smith"),  # fullwidth
        ("Straße", "strasse"),  # casefold, not lower()
        ("", ""),
    ],
)
def test_normalize_name(raw: str, normalized: str) -> None:
    assert normalize_name(raw) == normalized


@pytest.mark.parametrize(
    ("payer", "member_id"),
    [
        ("Dana Smith", 1),
        ("DANA SMITH", 1),
        ("dana   smith", 1),
        (" Dana Smith ", 1),
        ("José Pérez", 2),
        ("Jose Perez", 2),
        ("JOSE PEREZ", 2),
        ("Diana Reed", 5),
    ],
)
def test_resolves_case_spacing_and_accent_variants(
    members_db: Path, payer: str, member_id: int
) -> None:
    conn = get_readonly_connection(members_db)
    try:
        assert resolve_member(conn, payer) == member_id
    finally:
        conn.close()


@pytest.mark.parametrize(
    "payer", ["Dana Smyth", "Dana", "Smith Dana", "Dana Smith Jr", "", "  ", None]
)
def test_no_match_is_unknown_payer(members_db: Path, payer: str | None) -> None:
    """No fuzzy matching: a near miss is an unknown payer, never the closest member."""
    conn = get_readonly_connection(members_db)
    try:
        assert resolve_member(conn, payer) is None
    finally:
        conn.close()


def test_duplicate_names_are_never_guessed(
    members_db: Path, caplog: pytest.LogCaptureFixture
) -> None:
    conn = get_readonly_connection(members_db)
    try:
        with caplog.at_level(logging.WARNING, logger="gym_ops.extractor.resolve"):
            assert resolve_member(conn, "christopher WILLIAMS") is None
    finally:
        conn.close()
    [record] = caplog.records
    assert record.__dict__["event"] == "payer_ambiguous"
    assert record.__dict__["candidate_member_ids"] == [3, 4]
    assert "christopher" not in record.getMessage().lower()  # untrusted name not logged


def test_directory_loads_once(members_db: Path) -> None:
    conn = get_readonly_connection(members_db)
    try:
        directory = MemberDirectory.load(conn)
    finally:
        conn.close()
    assert directory.by_name["christopher williams"] == (3, 4)
    assert directory.resolve("Dana Smith") == 1
