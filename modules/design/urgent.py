"""د عاجل خبر ډیزاین — بېل ټمپلیټ، یوازې عنوان + هجري شمسي تاریخ.

Urgent-news design: a separate 1561×1561 template per language with only two
objects — the title (justified, last line centered, trailing red highlight)
and today's Solar-Hijri (Jalali) date. No photo, source or location layer.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PIL import Image, ImageDraw

from ..config import Config
from ..utils import sanitize_filename
from .export import export_image
from .justify import justify_line
from .layout import URGENT_DATE_BOX, URGENT_TEMPLATES, URGENT_TITLE_BOX
from .text_render import (
    _direction,
    fit_to_box,
    get_font,
    line_height,
    parse_color,
    text_width,
)

logger = logging.getLogger("design.urgent")


def current_jalali_date() -> str:
    """د نننۍ ورځې هجري شمسي نیټه — yyyy/mm/d."""
    from persiantools.jdatetime import JalaliDate

    j = JalaliDate.today()
    return f"{j.year}/{j.month:02d}/{j.day}"


def _draw_line(draw, words, widths, font, box, y, is_english, justify, gi, start_idx, normal, highlight):
    """یوه کرښه ورسموه — justify (بشپړ عرض) یا مرکز، د کلمه‌په‌کلمه هایلایټ سره."""
    bx, _, bw, _ = box
    direction = _direction(is_english)
    space_w = text_width(font, " ", is_english) or font.getlength(" ")
    n = len(words)
    if justify and n > 1:
        gap = (bw - sum(widths)) / (n - 1)
        offset = 0.0
    else:  # وروستۍ کرښه یا یوه‌کلمه‌یي → مرکز
        gap = space_w
        offset = (bw - (sum(widths) + space_w * (n - 1))) / 2

    if is_english:
        x = bx + offset
        for w, ww in zip(words, widths):
            col = highlight if gi >= start_idx else normal
            draw.text((x, y), w, font=font, fill=col, direction=direction)
            x += ww + gap
            gi += 1
    else:
        x = bx + bw - offset
        for w, ww in zip(words, widths):
            col = highlight if gi >= start_idx else normal
            x -= ww
            draw.text((x, y), w, font=font, fill=col, direction=direction)
            x -= gap
            gi += 1
    return gi


def _draw_title(canvas, text: str, font_path: str, dcfg, is_english: bool) -> None:
    """عنوان ورسموه — عمودي مرکز + Justify-last-Centered + اخیري برخه سره."""
    box = URGENT_TITLE_BOX
    bx, by, bw, bh = box
    size, lines = fit_to_box(
        text, font_path, bw, bh, dcfg.title_line_gap,
        dcfg.urgent_title_size, dcfg.title_abs_min, is_english,
    )
    font = get_font(font_path, size)
    draw = ImageDraw.Draw(canvas)
    lh = line_height(font, dcfg.title_line_gap)
    normal = parse_color(dcfg.urgent_title_color_hex)
    highlight = parse_color(dcfg.urgent_highlight_color_hex)
    total_words = sum(len(ln.split()) for ln in lines)
    start_idx = int(total_words * (1 - dcfg.urgent_highlight_fraction))

    y = by + (bh - len(lines) * lh) // 2  # عمودي مرکز
    gi = 0
    for i, line in enumerate(lines):
        is_last = i == len(lines) - 1
        # RTL (پښتو/فارسي) نا-وروستۍ کرښه → کشیده justify (زموږ منطق).
        # انګلیسي → هیڅ justify نه؛ ټول لاینونه مرکز (center-center).
        if not is_english and not is_last:
            line = justify_line(line, lambda t: text_width(font, t, is_english), bw, seed=i)
        words = line.split()
        widths = [text_width(font, w, is_english) for w in words]
        gi = _draw_line(
            draw, words, widths, font, box, y, is_english,
            justify=False,  # انګلیسي justify نه کوي؛ RTL کشیده دمخه ډک کړی
            gi=gi, start_idx=start_idx, normal=normal, highlight=highlight,
        )
        y += lh


def _draw_date(canvas, date_str: str, font_path: str, dcfg) -> None:
    """تاریخ ورسموه — چپ-مرکز، سور، بولډ (couriernew regular دی نو faux-bold)."""
    bx, by, bw, bh = URGENT_DATE_BOX
    size = dcfg.urgent_date_size
    font = get_font(font_path, size)
    draw = ImageDraw.Draw(canvas)
    color = parse_color(dcfg.urgent_date_color_hex)
    # couriernew.ttf regular دی — د بولډ لپاره stroke_width ورکوو.
    stroke = max(1, round(size / 28))
    draw.text(
        (bx, by + bh // 2), date_str, font=font, fill=color, anchor="lm",
        stroke_width=stroke, stroke_fill=color,
    )


def render_urgent_one(lang: str, title: str, config: Config, out_dir) -> Path:
    """د یوې ژبې د عاجل خبر انځور جوړ کړه."""
    dcfg = config.design
    size = dcfg.urgent_canvas_size
    tpl = Path(dcfg.templates_dir) / URGENT_TEMPLATES[lang]
    canvas = Image.open(tpl).convert("RGB").resize((size, size), Image.LANCZOS)

    is_english = lang == "en"
    title_font = str(Path(dcfg.fonts_dir) / dcfg.urgent_title_font)
    date_font = str(Path(dcfg.fonts_dir) / dcfg.urgent_date_font)

    if title.strip():
        _draw_title(canvas, title, title_font, dcfg, is_english)
    _draw_date(canvas, current_jalali_date(), date_font, dcfg)

    name = sanitize_filename(title) if title.strip() else f"urgent_{lang}"
    path = export_image(canvas, out_dir, name, dcfg)
    logger.info("[%s] د عاجل انځور جوړ شو → %s", lang, path.name)
    return path
