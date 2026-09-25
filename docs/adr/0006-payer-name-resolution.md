# ADR-0006: Resolve payer names by normalized exact match, never fuzzy

- **Status:** Accepted
- **Date:** 2026-09-25
- **Resolves:** SPEC Q-9

## Context

Reconciliation (ADR-0005, SPEC FR-14) links a reference-less transfer to a bill through the transfer's `member_id`. The extractor must therefore turn the payer name read off a receipt into a `member_id`, or `NULL` when it cannot.

The name the model copies "as printed" can differ from `members.full_name` in harmless ways: case (`CHRISTOPHER WILLIAMS`), spacing (`Christopher  Williams`), accents (`José` vs `Jose`), or Unicode form (a precomposed `é` vs `e` + combining accent). The seeded data also holds one duplicate full name (two members named *Christopher Williams*), so a name can be ambiguous even when it's read perfectly.

A resolution error isn't symmetric:

- **False negative** (`NULL` for a real member): the transfer shows up as `unknown_payer` in `reconcile_payments`, and the operator matches it by hand. It's visible, cheap, and safe.
- **False positive** (the wrong member): the payment is credited to someone else. The payer's bill stays `unpaid` and the other member's bill may show `paid` or `overpaid`. Nothing flags it, and money is misattributed. This is the failure the system must not produce.

## Options considered

| Option | Complexity | Cost | Security | Portability |
| --- | --- | --- | --- | --- |
| A. Exact `full_name = ?` in SQL | Lowest | None | No false positives; misses case, spacing and accent variants | Plain SQL |
| **B. Normalize both sides (NFKD, strip accents, casefold, collapse whitespace), then exact match; one match only** | Low: ~20 lines of pure Python, members loaded once per batch | None | No false positives beyond true namesakes, which are refused | Pure Python; `unicodedata` is stdlib |
| C. Fuzzy match (edit distance, token-set ratio) with a threshold | Medium: threshold tuning, tie-breaking | None in tokens | **False positives by design**: `Dana Smith` ≈ `Diana Smith`; the threshold trades misattributed money for fewer manual matches | Needs a library or custom scoring |
| D. Ask the LLM to pick the member from a candidate list | High: a second call per receipt, member PII sent to the provider | Extra tokens per receipt | Injection-exposed: receipt text could steer the choice | Couples resolution to the model |

## Decision

**Option B.** `gym_ops.extractor.resolve`:

1. `normalize_name(s)`: Unicode **NFKD**, drop combining marks (accents), **casefold** (so `ß` becomes `ss`, which `lower()` doesn't do), and collapse runs of whitespace. NFKD also folds compatibility forms such as fullwidth letters.
2. `MemberDirectory.load(conn)` indexes every `members.full_name` under its normalized key, using a single parametrized `SELECT` (normalization happens in Python, never in SQL).
3. `resolve(payer_name)`:
   - **exactly one** member → that `member_id`;
   - **none** → `NULL` (`unknown_payer`);
   - **two or more** → `NULL`, with a `payer_ambiguous` warning log that lists the candidate ids (never the untrusted name). **The system never guesses between namesakes**, the same principle as ADR-0005's `ambiguous` rule.
4. **No fuzzy matching.** A near miss (`Dana Smyth`, `Dana`, `Smith Dana`) resolves to `NULL`.
5. **One code path.** The extractor, the FR-4 oracle test (`tests/receipts/test_oracle_reconciliation.py`) and the FR-16 eval comparison of `payer_name` all use this module. The oracle still reproduces every label's expected reconciliation exactly (100%).

The FR-16 `payer_name` comparison uses `normalize_name` too, so the eval scores payer names by the same notion of "same name" that production uses to credit payments. A looser eval rule would overstate accuracy, and a stricter one would count as wrong names that resolve correctly.

## Consequences

**Easier**
- Case, spacing, accent and Unicode-form variants resolve without any tuning.
- The failure mode is always visible: an unresolved payer surfaces as `unknown_payer` for the operator, never as a silent misattribution.
- Deterministic and pure, so the resolver is unit-tested exhaustively and shared by the tests, production and the eval.

**Harder**
- Legitimate variants beyond normalization, such as nicknames (`Chris`), a missing middle name, reordered names or OCR-style misreadings (`Wi11iams`), stay unresolved and need a manual match. Reference matches (ADR-0005 rule 1) cover most transfers, so this only affects reference-less ones.
- Namesakes can never be resolved by name, even when only one of them has an open bill. Resolution deliberately ignores bills: it answers "who paid", and reconciliation answers "for what".

**Revisit at scale**
- If manual matching of reference-less transfers becomes a real burden, add a *suggestion* channel: fuzzy candidates shown to the operator for confirmation, never written as `member_id`.
- With many members, namesakes become common. Richer identity signals (payer account number, a member-specific reference) are then better than any name-matching improvement.
