"""Payer name -> member_id resolution (ADR-0006, SPEC Q-9).

Both sides are normalized the same way (Unicode NFKD, accents stripped, casefold,
whitespace collapsed) and compared for equality. Exactly one match resolves; zero
or several resolve to None. There is no fuzzy matching: a near-miss that picked the
wrong member would silently credit a payment to someone else, while None only
surfaces the transfer as `unknown_payer` for the operator (ADR-0005).

Production (the extractor), the FR-4 oracle test and the FR-16 eval comparison all
use this module, so they cannot disagree about what "the same name" means.
"""

import logging
import sqlite3
import unicodedata
from collections import defaultdict
from dataclasses import dataclass

logger = logging.getLogger(__name__)

ALL_MEMBER_NAMES = "SELECT member_id, full_name FROM members ORDER BY member_id"


def normalize_name(name: str) -> str:
    """`'  JOSÉ   Pérez '` -> `'jose perez'`."""
    decomposed = unicodedata.normalize("NFKD", name)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(stripped.casefold().split())


@dataclass(frozen=True)
class MemberDirectory:
    """Normalized full name -> member ids, loaded once per batch."""

    by_name: dict[str, tuple[int, ...]]

    @classmethod
    def load(cls, conn: sqlite3.Connection) -> "MemberDirectory":
        index: dict[str, list[int]] = defaultdict(list)
        for member_id, full_name in conn.execute(ALL_MEMBER_NAMES):
            index[normalize_name(full_name)].append(member_id)
        return cls({name: tuple(ids) for name, ids in index.items()})

    def resolve(self, payer_name: str | None) -> int | None:
        if payer_name is None:
            return None
        key = normalize_name(payer_name)
        if not key:
            return None
        ids = self.by_name.get(key, ())
        if len(ids) == 1:
            return ids[0]
        if len(ids) > 1:
            # Never guess between namesakes; ids only, the name is untrusted receipt text.
            logger.warning(
                "payer name matches several members; left unresolved",
                extra={"event": "payer_ambiguous", "candidate_member_ids": list(ids)},
            )
        return None


def resolve_member(conn: sqlite3.Connection, payer_name: str | None) -> int | None:
    """Resolve one payer name. For many names, load a `MemberDirectory` once instead."""
    return MemberDirectory.load(conn).resolve(payer_name)
