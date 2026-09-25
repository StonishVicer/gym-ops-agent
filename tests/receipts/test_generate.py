import hashlib
from collections import Counter
from datetime import date
from pathlib import Path
from typing import get_args

import pytest
from PIL import Image, ImageFont
from pydantic import ValidationError

from gym_ops.config import get_settings
from gym_ops.receipts import render
from gym_ops.receipts.generate import (
    CLEAN_ONLY_SCENARIOS,
    INJECTION_TEXT,
    SCENARIO_COUNTS,
    SPREAD_SCENARIOS,
    GenerationError,
    GeneratorConfig,
    generate,
    main,
    read_labels,
)
from gym_ops.receipts.labels import DifficultyTag, Expected, Scenario, Template
from gym_ops.receipts.render import format_amount, format_date
from tests.receipts.conftest import GEN_CONFIG, REFERENCE_DATE, GeneratedSet

N = 100


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_files_match_labels(generated: GeneratedSet) -> None:
    pngs = sorted(p.name for p in generated.out_dir.glob("*.png"))
    assert len(generated.labels) == len(pngs) == generated.summary.receipts == N
    assert pngs == sorted(label.file for label in generated.labels)
    ids = [label.receipt_id for label in generated.labels]
    assert len(set(ids)) == N
    for label in generated.labels:
        assert label.file == f"{label.receipt_id}.png"
        with Image.open(generated.out_dir / label.file) as image:
            assert image.format == "PNG"
            assert image.size == (label.width, label.height)
            assert max(image.size) <= 1000  # bounds vision token cost


def test_labels_include_payer_name(generated: GeneratedSet) -> None:
    for label in generated.labels:
        assert label.truth.payer_name.strip()
        assert label.truth.currency == "USD"
        assert label.truth.transfer_date <= REFERENCE_DATE
        assert label.truth.bank_name == render.BANKS[label.template].bank_name


def test_deterministic_output(generated: GeneratedSet, tmp_path: Path) -> None:
    out_dir, labels_path = tmp_path / "receipts", tmp_path / "labels.jsonl"
    generate(generated.db_path, out_dir, labels_path, GEN_CONFIG)
    assert labels_path.read_bytes() == generated.labels_path.read_bytes()
    first = {p.name: _sha256(p) for p in generated.out_dir.glob("*.png")}
    second = {p.name: _sha256(p) for p in out_dir.glob("*.png")}
    assert first == second


def test_every_reconciliation_scenario_present(generated: GeneratedSet) -> None:
    counts = Counter(label.scenario for label in generated.labels)
    expected = {**SCENARIO_COUNTS, "exact_payment": N - sum(SCENARIO_COUNTS.values())}
    assert counts == expected
    assert set(counts) == set(get_args(Scenario))
    # Brief minimums.
    assert counts["exact_payment"] >= 14
    assert counts["partial_only"] >= 1 and counts["overpayment"] >= 2
    for scenario in ("late_with_reference", "name_date_match", "ambiguous", "unknown_payer"):
        assert counts[scenario] >= 2


def test_fr5_ratios_strict(generated: GeneratedSet) -> None:
    per_bill = Counter(lb.expected.bill_reference for lb in generated.labels)

    def single(label_expected: Expected) -> bool:
        return label_expected.bill_reference is not None and (
            per_bill[label_expected.bill_reference] == 1
        )

    # A lone transfer that leaves its bill `paid` carries exactly the bill amount.
    full_correct = sum(
        1
        for lb in generated.labels
        if single(lb.expected) and lb.expected.bill_status_after_reconciliation == "paid"
    )
    under_over = sum(
        1
        for lb in generated.labels
        if single(lb.expected)
        and lb.expected.bill_status_after_reconciliation in ("partially_paid", "overpaid")
    )
    no_member = sum(
        1 for lb in generated.labels if lb.expected.unidentified_reason == "unknown_payer"
    )
    assert full_correct / N >= 0.70
    assert under_over / N >= 0.05
    assert no_member / N >= 0.05
    assert len({lb.template for lb in generated.labels}) >= 3


def test_difficulty_mix(generated: GeneratedSet) -> None:
    clean = [lb for lb in generated.labels if not lb.difficulty]
    assert len(clean) / N >= 0.40
    assert generated.summary.clean == len(clean) == 45
    tags = Counter(t for lb in generated.labels for t in lb.difficulty)
    assert tags == {
        "amount_plain": 12,
        "amount_no_symbol": 12,
        "amount_usd_code": 12,
        "date_us": 12,
        "date_long": 12,
        "rotation": 18,
        "blur": 14,
        "jpeg_noise": 18,
    }
    for lb in generated.labels:
        assert sum(t.startswith("amount_") for t in lb.difficulty) <= 1
        assert sum(t.startswith("date_") for t in lb.difficulty) <= 1


def test_clean_only_scenarios_have_no_difficulty(generated: GeneratedSet) -> None:
    assert {"adversarial_injection", "multiple_amounts"} <= CLEAN_ONLY_SCENARIOS
    for lb in generated.labels:
        if lb.scenario in ("adversarial_injection", "multiple_amounts"):
            assert lb.difficulty == [], lb.receipt_id


def test_rare_scenarios_spread_across_templates(generated: GeneratedSet) -> None:
    templates = set(get_args(Template))
    for scenario in SPREAD_SCENARIOS:
        used = Counter(lb.template for lb in generated.labels if lb.scenario == scenario)
        assert set(used) == templates, scenario
        assert max(used.values()) - min(used.values()) <= 1, scenario
    overall = Counter(lb.template for lb in generated.labels)
    assert sorted(overall.values()) == [33, 33, 34]


def test_adversarial_flag_marks_injection_receipts(generated: GeneratedSet) -> None:
    for lb in generated.labels:
        assert lb.adversarial == (lb.scenario == "adversarial_injection")
        # The injection text is only on the image, never in the ground truth.
        assert INJECTION_TEXT not in lb.model_dump_json()


def test_n_max_is_a_hard_cap() -> None:
    with pytest.raises(ValidationError, match="safety cap"):
        GeneratorConfig(seed=7, n=101, n_max=100, reference_date=REFERENCE_DATE, window_days=5)


def test_n_too_small_for_fixed_scenarios(generated: GeneratedSet, tmp_path: Path) -> None:
    config = GEN_CONFIG.model_copy(update={"n": sum(SCENARIO_COUNTS.values())})
    with pytest.raises(GenerationError, match="too small"):
        generate(generated.db_path, tmp_path / "r", tmp_path / "l.jsonl", config)


def test_cli_writes_outputs_and_removes_stale_images(
    generated: GeneratedSet, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MATCH_WINDOW_DAYS", "5")
    get_settings.cache_clear()
    out_dir, labels_path = tmp_path / "receipts", tmp_path / "labels.jsonl"
    out_dir.mkdir()
    (out_dir / "rcpt-0999.png").write_bytes(b"stale")
    n = sum(SCENARIO_COUNTS.values()) + 1
    args = ["--db-path", str(generated.db_path), "--out-dir", str(out_dir)]
    args += ["--labels", str(labels_path), "--n", str(n), "--n-max", str(n)]
    try:
        assert main(args) == 0
        with pytest.raises(SystemExit):
            main([*args[:-2], "--n-max", str(n - 1)])
    finally:
        get_settings.cache_clear()
    assert len(read_labels(labels_path)) == n
    assert len(list(out_dir.glob("*.png"))) == n


@pytest.mark.parametrize(
    ("tags", "text"),
    [
        ((), "$1,234.56"),
        (("amount_plain",), "1234.56"),
        (("amount_no_symbol",), "1,234.56"),
        (("amount_usd_code",), "USD 1,234.56"),
    ],
)
def test_amount_formats(tags: tuple[DifficultyTag, ...], text: str) -> None:
    assert format_amount(123456, tags) == text


@pytest.mark.parametrize(
    ("tags", "text"),
    [((), "2026-09-09"), (("date_us",), "09/09/2026"), (("date_long",), "Sep 9, 2026")],
)
def test_date_formats(tags: tuple[DifficultyTag, ...], text: str) -> None:
    assert format_date(date(2026, 9, 9), tags) == text


def test_font_falls_back_to_pillow_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(render, "FONT_DIR", tmp_path)
    render.load_font.cache_clear()
    try:
        font = render.load_font(20)
        assert isinstance(font, ImageFont.FreeTypeFont | ImageFont.ImageFont)
        assert "DejaVu" not in str(getattr(font, "path", ""))
    finally:
        render.load_font.cache_clear()
