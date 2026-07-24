"""د متن رسمول — د Pillow اصلي raqm shaping (RTL)، wrap، اتومات سایز، هایلایټ.

Text rendering. The provided FModam font ships no Arabic presentation forms,
so classic reshaper output would be tofu. Pillow's bundled raqm/harfbuzz does
real OpenType shaping instead — we pass base Unicode with ``direction="rtl"``
and the font joins letters natively. English is drawn left-to-right.
"""

from __future__ import annotations

import logging

from PIL import ImageDraw, ImageFont

from ..config import DesignConfig

logger = logging.getLogger("design.text")

_font_cache: dict = {}


def _direction(is_english: bool) -> str:
    return "ltr" if is_english else "rtl"


def parse_color(hx: str) -> tuple[int, int, int]:
    """د رنګ hex پارس کړه — د ARGB (۸ رقمه) ملاتړ سره.

    Accept ``RRGGBB`` or ``AARRGGBB`` (leading alpha dropped) with/without ``#``.
    """
    hx = hx.lstrip("#").strip()
    if len(hx) == 8:  # ARGB → لومړی ۲ رقمه (alpha) پرې کړه
        hx = hx[2:]
    return tuple(int(hx[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def get_font(path: str, size: int) -> ImageFont.FreeTypeFont:
    key = (path, size)
    if key not in _font_cache:
        _font_cache[key] = ImageFont.truetype(path, size)
    return _font_cache[key]


def text_width(font: ImageFont.FreeTypeFont, text: str, is_english: bool) -> float:
    """د shaped متن عرض (raqm)."""
    if not text:
        return 0.0
    return font.getlength(text, direction=_direction(is_english))


def line_height(font: ImageFont.FreeTypeFont, gap: int) -> int:
    """د لاین لوړوالی = د فونټ طبیعي لوړوالی (ascent+descent) + فاصله (px)."""
    ascent, descent = font.getmetrics()
    return ascent + descent + gap


def wrap_text(text: str, font_path: str, size: int, max_w: int, is_english: bool) -> list[str]:
    """متن د باکس عرض ته wrap کړه (منطقي کرښې راګرځوي).

    Wrap to the box width using shaped measurement. Lines stay in logical
    order; raqm handles shaping and bidi at draw time.
    """
    font = get_font(font_path, size)
    lines: list[str] = []
    for paragraph in text.split("\n"):
        words = paragraph.split()
        cur: list[str] = []
        for w in words:
            test = cur + [w]
            if text_width(font, " ".join(test), is_english) <= max_w or not cur:
                cur = test
            else:
                lines.append(" ".join(cur))
                cur = [w]
        if cur:
            lines.append(" ".join(cur))
    return lines or [""]


def find_best_size(text: str, font_path: str, box: tuple, is_english: bool, dcfg: DesignConfig):
    """له TARGET پیل، تر min پورې کوچنی کوه چې متن د باکس دننه ځایېږي."""
    _, _, box_w, box_h = box
    min_size = dcfg.title_extended_min if is_english else dcfg.title_normal_min
    for size in range(dcfg.title_target_size, min_size - 1, -1):
        lines = wrap_text(text, font_path, size, box_w, is_english)
        lh = line_height(get_font(font_path, size), dcfg.title_line_gap)
        if len(lines) * lh <= box_h:
            return size, lines
    lines = wrap_text(text, font_path, min_size, box_w, is_english)
    logger.warning("عنوان په %dpx کې هم ونه ځایېد — %d کرښې", min_size, len(lines))
    return min_size, lines


def draw_title(canvas, text: str, font_path: str, box: tuple, is_english: bool, dcfg: DesignConfig) -> None:
    """عنوان ورسموه — اتومات سایز + کلمه‌په‌کلمه هایلایټ (وروستۍ برخه نارنجي).

    RTL words are placed from the right edge; each word is shaped by raqm.
    """
    if not text.strip():
        return
    size, lines = find_best_size(text, font_path, box, is_english, dcfg)
    font = get_font(font_path, size)
    draw = ImageDraw.Draw(canvas)
    bx, by, bw, bh = box
    lh = line_height(font, dcfg.title_line_gap)
    direction = _direction(is_english)
    space_w = text_width(font, " ", is_english) or font.getlength(" ")

    highlight = parse_color(dcfg.highlight_color_hex)
    normal = parse_color(dcfg.normal_color_hex)
    total_words = sum(len(ln.split()) for ln in lines)
    start_idx = int(total_words * (1 - dcfg.highlight_fraction))

    if dcfg.title_valign == "middle":
        y = by + (bh - len(lines) * lh) // 2
    else:
        y = by

    gi = 0
    for line in lines:
        words = line.split()
        if is_english:
            x = bx
            for w in words:
                col = highlight if gi >= start_idx else normal
                draw.text((x, y), w, font=font, fill=col, direction=direction)
                x += text_width(font, w, is_english) + space_w
                gi += 1
        else:
            x = bx + bw  # د ښي څنډې څخه پیل (RTL)
            for w in words:
                col = highlight if gi >= start_idx else normal
                ww = text_width(font, w, is_english)
                x -= ww
                draw.text((x, y), w, font=font, fill=col, direction=direction)
                x -= space_w
                gi += 1
        y += lh


def fit_source(text: str, font_path: str, box: tuple, is_english: bool, dcfg: DesignConfig) -> ImageFont.FreeTypeFont:
    """د منبع لپاره سایز ومومه — تر یوې کرښې پورې، تر min ښکته کیدلی شي."""
    _, _, box_w, box_h = box
    for size in range(dcfg.source_size, dcfg.source_min_size - 1, -1):
        font = get_font(font_path, size)
        if text_width(font, text, is_english) <= box_w and size * 1.3 <= box_h:
            return font
    return get_font(font_path, dcfg.source_min_size)


def draw_source(canvas, text: str, font_path: str, box: tuple, is_english: bool, dcfg: DesignConfig) -> None:
    """منبع ورسموه — سپینه، یو لاین، عمودي مرکز؛ RTL=ښي-مرکز، LTR=کیڼ-مرکز.

    Anchored so the source sits exactly right-centre (RTL) or left-centre (LTR)
    inside its box.
    """
    if not text.strip():
        return
    font = fit_source(text, font_path, box, is_english, dcfg)
    draw = ImageDraw.Draw(canvas)
    bx, by, bw, bh = box
    color = parse_color(dcfg.source_color_hex)
    cy = by + bh // 2
    if is_english:
        draw.text((bx, cy), text, font=font, fill=color, direction="ltr", anchor="lm")
    else:
        draw.text((bx + bw, cy), text, font=font, fill=color, direction="rtl", anchor="rm")
