"""Draw one synthetic bank-transfer receipt with Pillow.

Three fictional banks, each with its own layout and field labels. Every image carries
a visible "SYNTHETIC DOCUMENT — DEMO" watermark. Rendering is a pure function of
`RenderSpec`: no randomness here, so the caller's seed fully determines the bytes.
"""

import io
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from gym_ops.receipts.labels import DifficultyTag, Template

# Long edge <= 1000 px bounds vision tokens (~w*h/750, about 920 per receipt).
IMAGE_WIDTH = 720
IMAGE_HEIGHT = 960

FONT_DIR = Path(__file__).resolve().parents[3] / "assets" / "fonts"
FONT_REGULAR = "DejaVuSans.ttf"
FONT_BOLD = "DejaVuSans-Bold.ttf"

WATERMARK_TEXT = "SYNTHETIC DOCUMENT — DEMO"
FOOTER_TEXT = "Synthetic document for demonstration only. Not a real bank record."

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

_BACKGROUND = (252, 252, 250)
_INK = (28, 28, 32)
_MUTED = (110, 112, 120)
_RULE = (218, 220, 226)
_WATERMARK_RGBA = (150, 150, 150, 64)
_MARGIN = 48

Font = ImageFont.FreeTypeFont | ImageFont.ImageFont
Color = tuple[int, int, int]


@dataclass(frozen=True)
class BankStyle:
    bank_name: str
    accent: Color
    title: str
    payer: str
    amount: str
    reference: str
    date: str
    timestamp: str


BANKS: dict[Template, BankStyle] = {
    "banco_demo": BankStyle(
        bank_name="Banco Demo",
        accent=(24, 52, 110),
        title="Transfer confirmation",
        payer="Payer",
        amount="Amount",
        reference="Reference",
        date="Date",
        timestamp="Transaction time",
    ),
    "banco_ficticio_del_sur": BankStyle(
        bank_name="Banco Ficticio del Sur",
        accent=(22, 110, 72),
        title="Transfer receipt",
        payer="Sent by",
        amount="Total transferred",
        reference="Transaction no.",
        date="Value date",
        timestamp="Processed at",
    ),
    "cooperativa_ejemplo": BankStyle(
        bank_name="Cooperativa Ejemplo",
        accent=(128, 32, 48),
        title="Payment receipt",
        payer="Payer name",
        amount="Payment amount",
        reference="Ref.",
        date="Payment date",
        timestamp="Timestamp",
    ),
}


@dataclass(frozen=True)
class RenderSpec:
    template: Template
    payer_name: str
    amount_cents: int
    currency: str
    reference: str | None
    transfer_date: date
    difficulty: tuple[DifficultyTag, ...]
    time_of_day: tuple[int, int, int]
    memo: str | None = None
    fee_cents: int = 0  # > 0: also print a subtotal and a fee (multiple_amounts)
    rotation_deg: float = 0.0
    blur_radius: float = 0.0
    jpeg_quality: int = 95


def format_amount(cents: int, difficulty: Sequence[DifficultyTag]) -> str:
    """Canonical `$1,234.56`, or the variant named by an `amount_*` tag."""
    dollars, rem = divmod(cents, 100)
    grouped = f"{dollars:,}.{rem:02d}"
    if "amount_plain" in difficulty:
        return f"{dollars}.{rem:02d}"
    if "amount_no_symbol" in difficulty:
        return grouped
    if "amount_usd_code" in difficulty:
        return f"USD {grouped}"
    return f"${grouped}"


def format_date(day: date, difficulty: Sequence[DifficultyTag]) -> str:
    """Canonical ISO `2026-09-19`, or the variant named by a `date_*` tag.

    Month names come from a fixed table, not `strftime("%b")`, which is locale-dependent.
    """
    if "date_us" in difficulty:
        return f"{day.month:02d}/{day.day:02d}/{day.year}"
    if "date_long" in difficulty:
        return f"{_MONTHS[day.month - 1]} {day.day}, {day.year}"
    return day.isoformat()


@lru_cache
def load_font(size: int, bold: bool = False) -> Font:
    """Bundled DejaVu Sans; Pillow's built-in scalable font if the file is missing.

    The existence check matters: given a missing path, `truetype()` silently searches
    the system font directories for the same filename, so output would depend on the
    host's installed fonts instead of falling back deterministically.
    """
    path = FONT_DIR / (FONT_BOLD if bold else FONT_REGULAR)
    if path.is_file():
        return ImageFont.truetype(str(path), size)
    return ImageFont.load_default(size=size)


def _text_width(draw: ImageDraw.ImageDraw, text: str, font: Font) -> float:
    return draw.textlength(text, font=font)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: Font, max_width: float) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if current and _text_width(draw, candidate, font) > max_width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def _fields(spec: RenderSpec, style: BankStyle) -> list[tuple[str, str]]:
    """(label, value) rows in display order; amount rows exclude the hero layout's total."""
    fmt_date = format_date(spec.transfer_date, spec.difficulty)
    hh, mm, ss = spec.time_of_day
    rows: list[tuple[str, str]] = [(style.payer, spec.payer_name)]
    if spec.fee_cents:
        rows.append(
            ("Subtotal", format_amount(spec.amount_cents - spec.fee_cents, spec.difficulty))
        )
        rows.append(("Transfer fee", format_amount(spec.fee_cents, spec.difficulty)))
    rows.append((style.amount, format_amount(spec.amount_cents, spec.difficulty)))
    rows.append(("Currency", spec.currency))
    if spec.reference is not None:
        rows.append((style.reference, spec.reference))
    rows.append((style.date, fmt_date))
    rows.append((style.timestamp, f"{fmt_date} {hh:02d}:{mm:02d}:{ss:02d}"))
    return rows


def _draw_memo(draw: ImageDraw.ImageDraw, memo: str, y: int) -> int:
    label_font, value_font = load_font(20), load_font(22)
    draw.text((_MARGIN, y), "Memo", font=label_font, fill=_MUTED)
    y += 30
    for line in _wrap(draw, memo, value_font, IMAGE_WIDTH - 2 * _MARGIN):
        draw.text((_MARGIN, y), line, font=value_font, fill=_INK)
        y += 30
    return y + 10


def _layout_rows(draw: ImageDraw.ImageDraw, spec: RenderSpec, style: BankStyle) -> None:
    """Banco Demo: solid header band, two-column table with rules."""
    draw.rectangle((0, 0, IMAGE_WIDTH, 120), fill=style.accent)
    draw.text((_MARGIN, 38), style.bank_name, font=load_font(40, bold=True), fill=(255, 255, 255))
    draw.text((_MARGIN, 160), style.title, font=load_font(30, bold=True), fill=_INK)
    label_font, value_font = load_font(22), load_font(24, bold=True)
    y = 240
    for label, value in _fields(spec, style):
        draw.text((_MARGIN, y), label, font=label_font, fill=_MUTED)
        width = _text_width(draw, value, value_font)
        draw.text((IMAGE_WIDTH - _MARGIN - width, y - 2), value, font=value_font, fill=_INK)
        y += 44
        draw.line((_MARGIN, y - 10, IMAGE_WIDTH - _MARGIN, y - 10), fill=_RULE, width=1)
    if spec.memo:
        _draw_memo(draw, spec.memo, y + 20)


def _layout_hero(draw: ImageDraw.ImageDraw, spec: RenderSpec, style: BankStyle) -> None:
    """Banco Ficticio del Sur: bank name in accent color, the total as a large hero figure."""
    draw.text((_MARGIN, 50), style.bank_name, font=load_font(36, bold=True), fill=style.accent)
    draw.text((_MARGIN, 100), style.title, font=load_font(24), fill=_MUTED)
    draw.line((_MARGIN, 145, IMAGE_WIDTH - _MARGIN, 145), fill=style.accent, width=3)
    draw.text((_MARGIN, 175), style.amount, font=load_font(22), fill=_MUTED)
    amount = format_amount(spec.amount_cents, spec.difficulty)
    draw.text((_MARGIN, 205), amount, font=load_font(56, bold=True), fill=_INK)
    label_font, value_font = load_font(21), load_font(23)
    y = 320
    for label, value in _fields(spec, style):
        if label == style.amount:
            continue  # already shown as the hero figure
        draw.text((_MARGIN, y), f"{label}:", font=label_font, fill=_MUTED)
        draw.text((_MARGIN + 230, y), value, font=value_font, fill=_INK)
        y += 42
    if spec.memo:
        _draw_memo(draw, spec.memo, y + 20)


def _layout_stacked(draw: ImageDraw.ImageDraw, spec: RenderSpec, style: BankStyle) -> None:
    """Cooperativa Ejemplo: left accent bar, each value stacked under a small label."""
    draw.rectangle((0, 0, 16, IMAGE_HEIGHT), fill=style.accent)
    draw.text((_MARGIN, 44), style.bank_name.upper(), font=load_font(30, bold=True), fill=_INK)
    draw.text((_MARGIN, 88), style.title, font=load_font(24), fill=style.accent)
    label_font, value_font = load_font(18), load_font(26, bold=True)
    y = 150
    for label, value in _fields(spec, style):
        draw.text((_MARGIN, y), label.upper(), font=label_font, fill=_MUTED)
        draw.text((_MARGIN, y + 24), value, font=value_font, fill=_INK)
        y += 70
    if spec.memo:
        _draw_memo(draw, spec.memo, y + 10)


_LAYOUTS = {
    "banco_demo": _layout_rows,
    "banco_ficticio_del_sur": _layout_hero,
    "cooperativa_ejemplo": _layout_stacked,
}


def _watermark(image: Image.Image) -> Image.Image:
    font = load_font(46, bold=True)
    probe = ImageDraw.Draw(image)
    width = int(_text_width(probe, WATERMARK_TEXT, font)) + 20
    tile = Image.new("RGBA", (width, 70), (0, 0, 0, 0))
    ImageDraw.Draw(tile).text((10, 5), WATERMARK_TEXT, font=font, fill=_WATERMARK_RGBA)
    tile = tile.rotate(30, resample=Image.Resampling.BICUBIC, expand=True)
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    for y in (60, 380, 700):
        overlay.alpha_composite(tile, ((IMAGE_WIDTH - tile.width) // 2, y - tile.height // 4))
    return Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")


def _degrade(image: Image.Image, spec: RenderSpec) -> Image.Image:
    if "rotation" in spec.difficulty:
        image = image.rotate(
            spec.rotation_deg,
            resample=Image.Resampling.BICUBIC,
            expand=False,
            fillcolor=_BACKGROUND,
        )
    if "blur" in spec.difficulty:
        image = image.filter(ImageFilter.GaussianBlur(radius=spec.blur_radius))
    if "jpeg_noise" in spec.difficulty:
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=spec.jpeg_quality)
        buffer.seek(0)
        with Image.open(buffer) as decoded:
            image = decoded.convert("RGB")
    return image


def render_receipt(spec: RenderSpec) -> Image.Image:
    """Return the receipt as an RGB image of IMAGE_WIDTH x IMAGE_HEIGHT."""
    style = BANKS[spec.template]
    image = Image.new("RGB", (IMAGE_WIDTH, IMAGE_HEIGHT), _BACKGROUND)
    draw = ImageDraw.Draw(image)
    _LAYOUTS[spec.template](draw, spec, style)
    draw.text((_MARGIN, IMAGE_HEIGHT - 50), FOOTER_TEXT, font=load_font(16), fill=_MUTED)
    return _degrade(_watermark(image), spec)
