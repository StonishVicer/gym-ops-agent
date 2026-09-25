"""Evaluate frozen runs and render `eval/reports/<run>.{json,md}` and `eval/results.md`.

Reports are byte-reproducible: they contain no wall-clock time (only the timestamps
recorded in each run's metadata), keys are sorted, and every list is in a fixed order.
"""

import json
import re
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final

from pydantic import BaseModel, ConfigDict

from gym_ops.config import get_settings
from gym_ops.eval.e2e import E2EMetrics, e2e_metrics
from gym_ops.eval.gates import FIELD_ACCURACY_MIN, Gate, gates_for
from gym_ops.eval.integrity import load_run
from gym_ops.eval.metrics import (
    FIELDS,
    Calibration,
    CostLatency,
    ExtractionMetrics,
    ReceiptScore,
    SecurityMetrics,
    WrongField,
    calibration,
    cost_latency,
    extraction_metrics,
    score_run,
    security_metrics,
    wrong_fields,
)
from gym_ops.eval.stats import Proportion, n_for_wilson_lower_bound, proportion

HEADLINE_RUN: Final = "v2-holdout"
RUN_ORDER: Final = ("v1-baseline", "v2-dev", "v2-holdout")
DECIMAL_COMMA: Final = re.compile(r"\d,\d{2}$")  # "50,00", "$50,00": comma as decimal mark
DEGRADED: Final = frozenset({"rotation", "blur", "jpeg_noise"})
# SPEC §9 escalation estimate: Claude Sonnet 5 list price per 1M tokens (Anthropic direct,
# not OpenRouter; the introductory $2/$10 became the standard price).
# Source: https://platform.claude.com/docs/en/about-claude/pricing (retrieved 2026-09-25).
ESCALATION_PRICE_SOURCE: Final = (
    "https://platform.claude.com/docs/en/about-claude/pricing, retrieved 2026-09-25"
)
ESCALATION_INPUT_USD_PER_MTOK: Final = 2.0
ESCALATION_OUTPUT_USD_PER_MTOK: Final = 10.0
# Same source: Claude 4.7+ models use a newer tokenizer, ~30% more tokens for the same text.
ESCALATION_TOKENIZER_FACTOR: Final = 1.3

# One-time billing check (v1-baseline only): OpenRouter `GET /api/v1/key` limit_remaining
# immediately before and after the v1 full run, 2026-09-25 (no other calls in between).
V1_BILLING_CHECK_BEFORE_USD: Final = 1.989578
V1_BILLING_CHECK_AFTER_USD: Final = 1.661588


class RunReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    run: str
    split: str
    headline: bool
    provenance: dict[str, Any]
    extraction: ExtractionMetrics
    wrong_fields: list[WrongField]
    security: SecurityMetrics
    e2e: E2EMetrics
    cost_latency: CostLatency
    calibration: Calibration
    gated: bool  # only the headline run's gates decide the exit code (FR-17)
    gates: list[Gate]
    gates_passed: bool
    decimal_comma_receipts: list[str]
    failures_by_template: dict[str, Proportion]
    scores: list[ReceiptScore]


def evaluate_run(run_dir: Path, workdir: Path) -> RunReport:
    run = load_run(run_dir, workdir)  # integrity first: raises before any scoring
    scores = score_run(run)
    meta = run.meta
    extraction = extraction_metrics(scores)
    cost = cost_latency(scores)
    gates = gates_for(cost, extraction)
    by_template: dict[str, list[ReceiptScore]] = {}
    for s in scores:
        by_template.setdefault(s.template, []).append(s)
    return RunReport(
        run=meta.name,
        split=meta.split,
        headline=meta.name == HEADLINE_RUN,
        provenance={
            "model_id": meta.model_id,
            "prompt_version": meta.prompt_version,
            "prompt_sha256": meta.prompt_sha256,
            "tool_schema_sha256": meta.tool_schema_sha256,
            "git_sha": meta.git_sha,
            "receipts_seed": meta.receipts_seed,
            "db_seed": meta.db_seed,
            "db_members": meta.db_members,
            "reference_date": meta.reference_date,
            "receipts": meta.receipts,
            "extractions_sha256": meta.extractions_sha256,
            "labels_sha256": meta.labels_sha256,
            "run_started_at": meta.run_started_at,
            "run_finished_at": meta.run_finished_at,
            "integrity": "verified: extractions hash, regenerated labels hash, id coverage",
        },
        extraction=extraction,
        wrong_fields=wrong_fields(run),
        security=security_metrics(scores),
        e2e=e2e_metrics(
            run, get_settings().MATCH_WINDOW_DAYS, {s.receipt_id for s in scores if s.wrong}
        ),
        cost_latency=cost,
        calibration=calibration(scores),
        gated=meta.name == HEADLINE_RUN,
        gates=gates,
        gates_passed=all(g.passed for g in gates),
        decimal_comma_receipts=sorted(
            s.receipt_id for s in scores if s.raw_amount and DECIMAL_COMMA.search(s.raw_amount)
        ),
        failures_by_template={
            t: proportion(sum(1 for s in group if not s.ok), len(group))
            for t, group in sorted(by_template.items())
        },
        scores=scores,
    )


def evaluate(runs_dir: Path, names: Sequence[str]) -> list[RunReport]:
    with tempfile.TemporaryDirectory(prefix="gym-ops-eval-") as tmp:
        return [evaluate_run(runs_dir / name, Path(tmp)) for name in names]


# --- rendering ------------------------------------------------------------------------


def to_json(report: RunReport) -> str:
    return (
        json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True, ensure_ascii=False)
        + "\n"
    )


def _p(p: Proportion) -> str:
    return p.fmt()


def _table(header: Sequence[str], rows: Sequence[Sequence[Any]]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return out


def gates_table(report: RunReport) -> list[str]:
    return _table(
        ["Gate", "Metric", "Threshold", "Measured", "Wilson 95%", "Result"],
        [
            [
                g.nfr,
                g.metric,
                g.threshold,
                g.measured,
                g.interval or "—",
                "PASS" if g.passed else "**FAIL**",
            ]
            for g in report.gates
        ],
    )


def run_markdown(r: RunReport) -> str:
    e, s, c, cal, x = r.extraction, r.security, r.cost_latency, r.calibration, r.e2e
    pv = r.provenance
    lines = [
        f"# Eval report: `{r.run}` ({r.split}{', headline' if r.headline else ''})",
        "",
        f"Model `{pv['model_id']}`, prompt `{pv['prompt_version']}`, git `{pv['git_sha'][:7]}`, "
        f"receipts seed {pv['receipts_seed']}, DB seed {pv['db_seed']}; "
        f"run {pv['run_started_at']} → {pv['run_finished_at']}. Integrity: {pv['integrity']}.",
        "",
        f"n = {e.n} receipts. Every rate shows a 95% Wilson interval and k/n. "
        "Failed extractions are wrong on every field and stay in every denominator.",
        "",
        "## Extraction",
        "",
        *_table(["Field", "Accuracy"], [[f, _p(e.per_field[f])] for f in FIELDS]),
        f"| **all six fields** | **{_p(e.all_six)}** |",
        "",
        f"Failures after retry: {len(e.failures)} ({', '.join(e.failures) or 'none'}).",
        "",
        "### All-six-correct rate by difficulty tag (a receipt counts under each of its tags)",
        "",
        *_table(
            ["Difficulty", "All six correct"], [[k, _p(v)] for k, v in e.by_difficulty.items()]
        ),
        "",
        "### By bank template",
        "",
        *_table(["Template", "All six correct"], [[k, _p(v)] for k, v in e.by_template.items()]),
        "",
        "### By scenario",
        "",
        *_table(["Scenario", "All six correct"], [[k, _p(v)] for k, v in e.by_scenario.items()]),
        "",
        "### Every wrong field",
        "",
        *(
            _table(
                ["Receipt", "Field", "Expected", "Got", "Difficulty", "Template", "Note"],
                [
                    [
                        w.receipt_id,
                        w.field,
                        w.expected,
                        w.got,
                        ", ".join(w.difficulty) or "clean",
                        w.template,
                        "extraction failed: " + (w.error or "") if w.failed_extraction else "",
                    ]
                    for w in r.wrong_fields
                ],
            )
            if r.wrong_fields
            else ["None."]
        ),
        "",
        "## Security (FR-9; measured only through `injection_detected`)",
        "",
        *_table(
            ["Metric", "Result"],
            [
                ["Detection on adversarial receipts", _p(s.detection)],
                ["False positives on all other receipts", _p(s.false_positive)],
                ["Amount correct on adversarial receipts", _p(s.adversarial_amount_correct)],
            ],
        ),
        "",
        "## End-to-end reconciliation (months " + ", ".join(x.months) + ")",
        "",
        *_table(
            ["Per receipt", "Result"],
            [
                ["Linked to the right bill (or correctly unlinked)", _p(x.bill_link)],
                ["Final bill status correct", _p(x.bill_status)],
                ["Unidentified reason correct", _p(x.unidentified_reason)],
                ["All three correct", _p(x.all_three)],
            ],
        ),
        "",
        f"Stored transfers: {x.stored}. Resolver vs recorded `member_id` mismatches: "
        f"{len(x.resolver_mismatches)}. Bills touched without a label: {len(x.leaked_bills)}.",
        "",
        *(
            [
                f"- Bill `{b.bill_reference}`: expected `{b.expected_status}`, observed "
                f"`{b.observed_status}`; failed extraction(s): "
                f"{', '.join(b.failed_receipts) or '—'}; "
                f"stored-but-wrong: {', '.join(b.wrong_receipts) or '—'}."
                for b in x.bill_errors
            ]
            or ["- No bill ends with a wrong status."]
        ),
        "",
        "## Cost and latency",
        "",
        *_table(
            ["Metric", "Value"],
            [
                [
                    "Mean input / output tokens",
                    f"{c.mean_input_tokens:g} / {c.mean_output_tokens:g}",
                ],
                [
                    "Cost per extraction: mean / max",
                    f"${c.mean_cost_usd:.6f} / ${c.max_cost_usd:.6f} ({c.max_cost_receipt})",
                ],
                ["Total cost", f"${c.total_cost_usd:.6f}"],
                ["Projected cost per 1,000 receipts", f"${c.projected_cost_per_1000_usd:.2f}"],
                [
                    "Latency p50 / p95 / max",
                    f"{c.latency_p50_ms} / {c.latency_p95_ms} / {c.latency_max_ms} ms",
                ],
                ["Percentile method", c.percentile_method],
                [
                    "Slowest receipt",
                    f"{c.slowest_receipt} ({c.slowest_http_attempts} HTTP attempt(s))",
                ],
                [
                    "Validation retries / HTTP retries",
                    f"{len(c.validation_retries)} / {len(c.http_retries)}",
                ],
            ],
        ),
        "",
        "## Confidence calibration (reported only; nothing is tuned with it)",
        "",
        f"Mean confidence: all six correct {cal.mean_confidence_all_correct} "
        f"(n={cal.n_all_correct}); "
        f"any field wrong {cal.mean_confidence_any_wrong} (n={cal.n_any_wrong}); "
        f"excluded (no reading): {cal.excluded_no_reading}.",
        "",
        "## Gates "
        "("
        + ("decide the exit code" if r.gated else "informational; only the headline run is gated")
        + ")",
        "",
        *gates_table(r),
        "",
    ]
    return "\n".join(lines)


def _by_id(report: RunReport) -> dict[str, ReceiptScore]:
    return {s.receipt_id: s for s in report.scores}


def results_markdown(reports: Sequence[RunReport]) -> str:
    by = {r.run: r for r in reports}
    h = by[HEADLINE_RUN]
    e, s, x, c = h.extraction, h.security, h.e2e, h.cost_latency
    n_needed = n_for_wilson_lower_bound(0.98, FIELD_ACCURACY_MIN)
    worst = min(e.per_field.values(), key=lambda p: (p.rate or 0.0, p.k))

    # Failure analysis, computed (not asserted) across all runs.
    comma_rows: list[list[Any]] = []
    comma_tot: dict[str, tuple[int, int]] = {}
    templates = sorted({t for r in reports for t in r.failures_by_template})
    for r in reports:
        ids = _by_id(r)
        row: list[Any] = [r.run]
        for t in templates:
            k = sum(1 for rid in r.decimal_comma_receipts if ids[rid].template == t)
            n = r.failures_by_template[t].n
            row.append(f"{k}/{n}")
            ck, cn = comma_tot.get(t, (0, 0))
            comma_tot[t] = (ck + k, cn + n)
        comma_rows.append(row)
    comma_rows.append(
        [
            "**combined (receipt-runs)**",
            *[f"**{comma_tot[t][0]}/{comma_tot[t][1]}**" for t in templates],
        ]
    )
    all_failures = [(r.run, sid) for r in reports for sid in r.extraction.failures]
    failures_all_comma = all(sid in by[run].decimal_comma_receipts for run, sid in all_failures)
    comma_not_failed = [
        (r.run, rid)
        for r in reports
        for rid in r.decimal_comma_receipts
        if rid not in r.extraction.failures
    ]
    hold_ids = _by_id(h)
    comma_templates = [t for t in templates if comma_tot[t][0] > 0]
    e2e_only_failures = set(x.receipts_wrong) <= set(e.failures) and all(
        not b.wrong_receipts for b in x.bill_errors
    )
    dollar_misreads = [
        rid for rid in h.decimal_comma_receipts if (hold_ids[rid].raw_amount or "").startswith("$")
    ]

    v1, v2 = _by_id(by["v1-baseline"]), _by_id(by["v2-dev"])
    fixed_rows = []
    for rid in by["v1-baseline"].extraction.failures:
        degraded = sorted(set(v1[rid].difficulty) & DEGRADED)
        fixed_rows.append(
            [
                rid,
                ", ".join(v1[rid].difficulty) or "clean",
                ", ".join(degraded) or "none",
                "fixed" if v2[rid].ok and not v2[rid].wrong else "still fails",
            ]
        )
    clean_fixed = sum(1 for row in fixed_rows if row[2] == "none" and row[3] == "fixed")
    clean_n = sum(1 for row in fixed_rows if row[2] == "none")
    deg_fixed = sum(1 for row in fixed_rows if row[2] != "none" and row[3] == "fixed")
    deg_n = sum(1 for row in fixed_rows if row[2] != "none")

    fail_rate = len(e.failures) / e.n
    esc_cost = (
        c.mean_input_tokens * ESCALATION_INPUT_USD_PER_MTOK
        + c.mean_output_tokens * ESCALATION_OUTPUT_USD_PER_MTOK
    ) / 1_000_000
    esc_added = fail_rate * esc_cost
    esc_cost_tok = esc_cost * ESCALATION_TOKENIZER_FACTOR
    esc_added_tok = fail_rate * esc_cost_tok
    v1_estimate = by["v1-baseline"].cost_latency.total_cost_usd
    v1_billed = V1_BILLING_CHECK_BEFORE_USD - V1_BILLING_CHECK_AFTER_USD

    def cmp_row(label: str, get: Any) -> list[Any]:
        return [label, *[get(by[name]) for name in RUN_ORDER]]

    lines = [
        "# gym-ops-agent: extraction results",
        "",
        "Receipt extraction with Claude Haiku 4.5 (`anthropic/claude-haiku-4.5` via OpenRouter), "
        "prompt **v2**, "
        "scored on 100% synthetic data. **Headline numbers come from the held-out set only** "
        "(`v2-holdout`: 100 receipts, seed 8, run once after the prompt was frozen on the dev "
        "set). "
        "**n = 100 per set, so every result is indicative**: each rate carries a 95% Wilson "
        "interval and k/n. "
        "Failed extractions count as wrong on every field and stay in every denominator. "
        "Every number is recomputed by `make eval` from frozen, hash-verified snapshots in "
        "`eval/runs/` "
        "(per-run detail: `eval/reports/`).",
        "",
        "## Headline (v2-holdout)",
        "",
        *_table(
            ["Field", "Accuracy [Wilson 95%] (k/n)"], [[f, _p(e.per_field[f])] for f in FIELDS]
        ),
        f"| **all six fields** | **{_p(e.all_six)}** |",
        "",
        f"Failures after retry: {len(e.failures)} ({', '.join(e.failures) or 'none'}); "
        + (
            "every other receipt is right on all six fields."
            if e.all_six.k == e.n - len(e.failures)
            else f"{e.n - len(e.failures) - e.all_six.k} stored receipt(s) have a wrong field."
        ),
        "",
        "## Gates (v2-holdout; thresholds from SPEC §4, never tuned)",
        "",
        *gates_table(h),
        "",
        f"**Overall: {'PASS' if h.gates_passed else 'FAIL'}.** "
        "NFR-3 passes on the measured value, as the SPEC defines the gate, but it is **not "
        "statistically "
        f"established at n = 100**: the lowest field, {worst.k}/{worst.n}, has a Wilson 95% lower "
        "bound of "
        f"{(worst.low or 0) * 100:.1f}%, below the 95% threshold. If the true rate were 98%, the "
        "lower bound would "
        f"clear 95% at about n ≈ {n_needed} receipts. NFR-2 gates p50 only (SPEC Q-6); the tail is "
        "reported below.",
        "",
        "## Security (prompt injection, FR-9)",
        "",
        *_table(
            ["Metric (v2-holdout)", "Result"],
            [
                ["Injection detected on adversarial receipts", _p(s.detection)],
                ["False positives on all other receipts", _p(s.false_positive)],
                [
                    "Amount correct on adversarial receipts (injection asked for 999999)",
                    _p(s.adversarial_amount_correct),
                ],
            ],
        ),
        "",
        "Measured only through the boolean `injection_detected`, never through `notes`. With 3 "
        "adversarial "
        "receipts the detection interval is wide; it shows that detection works here, not how "
        "often it would.",
        "",
        "## End-to-end reconciliation (the system-level result)",
        "",
        "Each run's frozen extractions are written through the production path (resolver + upsert) "
        "into a fresh "
        "database built from the seed, and reconciled for July to September with the real rules "
        "(ADR-0005).",
        "",
        *_table(
            ["Per receipt (v2-holdout)", "Result"],
            [
                ["Linked to the right bill (or correctly unlinked)", _p(x.bill_link)],
                ["Final bill status correct", _p(x.bill_status)],
                ["Unidentified reason correct", _p(x.unidentified_reason)],
            ],
        ),
        "",
        f"Resolver mismatches: {len(x.resolver_mismatches)}; transfers leaking onto unlabelled "
        f"bills: {len(x.leaked_bills)}. "
        "How the failed extractions propagate: a failure is never stored (FR-7), so its transfer "
        "is simply absent.",
        "",
        *[
            f"- `{b.failed_receipts[0] if b.failed_receipts else b.receipts[0]}`: bill "
            f"`{b.bill_reference}` ends "
            f'`{b.observed_status}` instead of `{b.expected_status}` (a false "unpaid" the '
            "operator would chase)."
            for b in x.bill_errors
        ],
        *[
            f"- `{rid}` ({hold_ids[rid].scenario}): no bill changes, but the transfer is missing "
            "from the operator's "
            "list of unidentified transfers."
            for rid in e.failures
            if not any(rid in b.failed_receipts for b in x.bill_errors)
        ],
        "",
        (
            "No stored extraction produced a wrong link or status: every end-to-end error traces "
            "to a "
            "failed extraction."
            if e2e_only_failures
            else "Some end-to-end errors come from stored extractions with wrong fields; see "
            "`eval/reports/v2-holdout.md`."
        ),
        "",
        "## v1 → v2-dev → v2-holdout",
        "",
        *_table(
            ["", "v1-baseline (dev)", "v2-dev", "**v2-holdout**"],
            [
                cmp_row(
                    "Prompt / git",
                    lambda r: f"{r.provenance['prompt_version']} / `{r.provenance['git_sha'][:7]}`",
                ),
                cmp_row(
                    "Receipts (seed)",
                    lambda r: f"{r.extraction.n} ({r.provenance['receipts_seed']})",
                ),
                cmp_row("Failures after retry", lambda r: len(r.extraction.failures)),
                cmp_row("All six fields correct", lambda r: _p(r.extraction.all_six)),
                cmp_row("Amount correct", lambda r: _p(r.extraction.per_field["amount_cents"])),
                cmp_row(
                    "Injection detection",
                    lambda r: f"{r.security.detection.k}/{r.security.detection.n}",
                ),
                cmp_row(
                    "False positives",
                    lambda r: f"{r.security.false_positive.k}/{r.security.false_positive.n}",
                ),
                cmp_row("E2E all three correct", lambda r: _p(r.e2e.all_three)),
                cmp_row("Mean cost", lambda r: f"${r.cost_latency.mean_cost_usd:.6f}"),
                cmp_row(
                    "Latency p50 / p95 / max (ms)",
                    lambda r: (
                        f"{r.cost_latency.latency_p50_ms} / {r.cost_latency.latency_p95_ms} / "
                        f"{r.cost_latency.latency_max_ms}"
                    ),
                ),
            ],
        ),
        "",
        "v1 and v2-dev are the same 100 dev receipts; the dev set was used to choose v2 and is not "
        "an unbiased "
        "estimate. v1 → v2 changed one prompt line (keep digits and separators as printed); no "
        "receipt that v1 got "
        "right became wrong.",
        "",
        "## Cost and latency (v2-holdout)",
        "",
        *_table(
            ["Metric", "Value"],
            [
                [
                    "Tokens per receipt (mean input / output)",
                    f"{c.mean_input_tokens:g} / {c.mean_output_tokens:g}",
                ],
                [
                    "Cost per extraction (mean / max)",
                    f"${c.mean_cost_usd:.6f} / ${c.max_cost_usd:.6f}",
                ],
                ["Total (100 receipts)", f"${c.total_cost_usd:.4f}"],
                ["Projected per 1,000 receipts", f"${c.projected_cost_per_1000_usd:.2f}"],
                [
                    "Latency p50 / p95 / max",
                    f"{c.latency_p50_ms} / {c.latency_p95_ms} / {c.latency_max_ms} ms "
                    f"(nearest-rank, n={c.n})",
                ],
                [
                    "Slowest receipt",
                    f"`{c.slowest_receipt}` ({c.slowest_http_attempts} HTTP attempt: upstream tail "
                    "latency, not a retry)",
                ],
                [
                    "Validation / HTTP retries",
                    f"{len(c.validation_retries)} / {len(c.http_retries)}",
                ],
                [
                    "Mean confidence: all correct vs any wrong",
                    f"{h.calibration.mean_confidence_all_correct} "
                    f"(n={h.calibration.n_all_correct}) vs "
                    f"{h.calibration.mean_confidence_any_wrong} (n={h.calibration.n_any_wrong}); "
                    f"{h.calibration.excluded_no_reading} without a reading",
                ],
            ],
        ),
        "",
        (
            "Model confidence does not separate right from wrong readings here (means within "
            "0.02), so it "
            "cannot be used to route receipts for review."
            if h.calibration.mean_confidence_all_correct is not None
            and h.calibration.mean_confidence_any_wrong is not None
            and abs(
                h.calibration.mean_confidence_all_correct - h.calibration.mean_confidence_any_wrong
            )
            < 0.02
            else "Model confidence differs between right and wrong readings; "
            "see the per-run report."
        ),
        "",
        "Cost is estimated from each response's `usage` times configured prices "
        "($1 / $5 per MTok), "
        "not billed amounts. "
        "Latency is end-to-end from the developer's machine through OpenRouter, sequential calls "
        "(SPEC A-6).",
        "",
        "**Billing check (one-time, v1-baseline only).** OpenRouter's `limit_remaining` dropped "
        f"by ${v1_billed:.3f} across the v1 run (${V1_BILLING_CHECK_BEFORE_USD:.6f} to "
        f"${V1_BILLING_CHECK_AFTER_USD:.6f}, read via `GET /api/v1/key` right before and after) "
        f"against a usage-based estimate of ${v1_estimate:.3f}: the estimate was "
        f"{v1_estimate / v1_billed - 1:.0%} above the billed amount, i.e. conservative. Not "
        "repeated for v2-dev or v2-holdout.",
        "",
        "## Failure analysis",
        "",
        f"**1. Every failure is a decimal-comma misread** ({len(all_failures)} failures across the "
        "three runs; "
        f"{'all' if failures_all_comma else 'NOT all'} have a raw amount like `50,00`; "
        f"misreads that did not fail: {len(comma_not_failed)}). Decimal-comma misreads by template "
        "(k/n per template):",
        "",
        *_table(["Run", *templates], comma_rows),
        "",
        f"Misreads occur only on: {', '.join(f'`{t}`' for t in comma_templates) or 'none'} "
        f"({sum(comma_tot[t][0] for t in comma_templates)}/"
        f"{sum(comma_tot[t][1] for t in comma_templates)} "
        f"receipt-runs); the other templates show "
        f"{sum(comma_tot[t][0] for t in templates if t not in comma_templates)}/"
        f"{sum(comma_tot[t][1] for t in templates if t not in comma_templates)}. "
        "Combined counts are receipt-runs: v1 and v2-dev score the same 100 dev receipts, so they "
        "are not "
        "independent.",
        "",
        "**2. A missing currency symbol is refuted as the cause.** "
        f"`{', '.join(dollar_misreads) or '—'}` printed "
        "`$` and the model still returned a decimal comma (raw amount `"
        + ", ".join(hold_ids[rid].raw_amount or "" for rid in dollar_misreads)
        + "`).",
        "",
        '**3. Hypothesis, not verified:** the Spanish-sounding bank name ("Banco Demo") nudges the '
        "model toward "
        "European number formatting. It is consistent with the misreads clustering on that one "
        "template, but it "
        "is weak as stated: **all three fictional banks have Spanish-sounding names** (Banco Demo, "
        "Banco "
        "Ficticio del Sur, Cooperativa Ejemplo), and the other two show no misreads. Whatever "
        "drives it is "
        "specific to the `banco_demo` template, whose name, layout (`_layout_rows` in `render.py`; "
        "the others use `_layout_hero` and "
        "`_layout_stacked`) and field labels all differ from the others; the data cannot separate "
        "these. Test with an ablation on the "
        "dev set: render the same `banco_demo` receipts changing one factor at a time (bank name → "
        "a neutral "
        "English name; then the layout; then the field labels), everything else byte-identical, "
        "and compare "
        "decimal-comma rates. Not run in this phase.",
        "",
        "**4. The v2 prompt fixed the clean case, not the degraded ones** (v1-baseline failures, "
        "rescored in v2-dev): "
        f"clean {clean_fixed}/{clean_n} fixed, degraded {deg_fixed}/{deg_n} fixed. With 3 receipts "
        "this is a "
        "description, not a rate.",
        "",
        *_table(["v1 failure", "Difficulty", "Degradation", "In v2-dev"], fixed_rows),
        "",
        f"**5. Tail latency.** `{c.slowest_receipt}` took {c.latency_max_ms} ms on a single HTTP "
        "attempt: an upstream "
        f"slow response, not a retry. p50 {c.latency_p50_ms} ms passes NFR-2; p95 "
        f"{c.latency_p95_ms} ms and max "
        f"{c.latency_max_ms} ms are reported next to it and are not gated.",
        "",
        "**6. Mitigation (SPEC §9 Future work, not implemented).** Escalating only failed "
        "readings to a larger model "
        "would target exactly this pattern. At the holdout's failure rate "
        f"({len(e.failures)}/{e.n}) and its mean "
        f"tokens, one escalation costs about ${esc_cost:.4f} at the Claude Sonnet 5 list price "
        f"($2 / $10 per MTok; source: {ESCALATION_PRICE_SOURCE}), adding "
        f"~${esc_added:.5f} per receipt ({esc_added / c.mean_cost_usd:+.0%} on "
        f"${c.mean_cost_usd:.5f}). The same page notes that Claude 4.7 and later models use a "
        "tokenizer producing ~30% more tokens; with the Haiku token counts scaled by "
        f"{ESCALATION_TOKENIZER_FACTOR}, that is ~${esc_cost_tok:.4f} per escalation and "
        f"~${esc_added_tok:.5f} per receipt ({esc_added_tok / c.mean_cost_usd:+.0%}). Both stay "
        "well within NFR-1. Adding it "
        "would require a new prompt/system version, a dev run and a **new** holdout set.",
        "",
    ]
    return "\n".join(lines)


def write_reports(
    reports: Sequence[RunReport], out_dir: Path, results_path: Path | None
) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for r in reports:
        for suffix, text in ((".json", to_json(r)), (".md", run_markdown(r))):
            path = out_dir / f"{r.run}{suffix}"
            path.write_text(text, encoding="utf-8")
            written.append(path)
    if results_path is not None and {r.run for r in reports} >= set(RUN_ORDER):
        results_path.write_text(results_markdown(reports), encoding="utf-8")
        written.append(results_path)
    return written
