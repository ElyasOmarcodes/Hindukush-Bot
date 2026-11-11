"""د نقل قول ډیزاین — د شخص انځور + نقل قول + نوم + رتبه.

Quote design (1353×1353): a person photo (right, behind the template), then the
template overlay, then three text layers — the quote (kashida-justified, last
line right-aligned, trailing highlight), the person's name and their rank.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PIL import Image, ImageDraw

from ..config import Config
from ..utils import sanitize_filename
from .export import export_image
from .justify import justify_line
from .layout import (
    QUOTE_NAME_BOX,
    QUOTE_PERSON_BOX,
    QUOTE_RANK_BOX,
    QUOTE_TEMPLATES,
    QUOTE_TITLE_BOX,
)
from .text_render import (
    _direction,
    fit_to_box,
    get_font,
    line_height,
    parse_color,
    text_width,
    wrap_text,
)

logger = logging.getLogger("design.quote")


def _prepare_person(path, w: int, h: int):
    """د شخص انځور د باکس (686×1175) اندازې ته cover-fit کړه (بې تحریفه)."""
    img = Image.open(path).convert("RGBA")
    src_r, dst_r = img.width / img.height, w / h
    if src_r > dst_r:
        nh, nw = h, int(h * src_r)
    else:
        nw, nh = w, int(w / src_r)
    img = img.resize((nw, nh), Image.LANCZOS)
    left, top = (nw - w) // 2, (nh - h) // 2
    return img.crop((left, top, left + w, top + h))


def _draw_aligned_line(draw, words, widths, font, box, y, is_english, gi, start_idx, normal, highlight, gap):
    """یوه کرښه — RTL ښي-تنظیم / LTR کیڼ-تنظیم (بې مرکز)، د کلمه‌په‌کلمه هایلایټ سره.

    ``gap`` د کلمو ترمنځ فاصله ده — د justify شوې کرښې لپاره غزیدلې (چې کرښه
    دقیقاً بشپړ عرض ډکه کړي)، نه‌نو طبیعي فاصله.
    """
    bx, _, bw, _ = box
    direction = _direction(is_english)
    if is_english:
        x = bx
        for w, ww in zip(words, widths):
            draw.text((x, y), w, font=font, fill=highlight if gi >= start_idx else normal, direction=direction)
            x += ww + gap
            gi += 1
    else:
        x = bx + bw
        for w, ww in zip(words, widths):
            x -= ww
            draw.text((x, y), w, font=font, fill=highlight if gi >= start_idx else normal, direction=direction)
            x -= gap
            gi += 1
    return gi


def _draw_title(canvas, text: str, font_path: str, dcfg, is_english: bool) -> None:
    """نقل قول ورسموه — پورته-تنظیم، Justify-last-Righted (RTL) / بې justify (LTR)."""
    box = QUOTE_TITLE_BOX
    bx, by, bw, bh = box
    size, lines = fit_to_box(
        text, font_path, bw, bh, dcfg.title_line_gap,
        dcfg.quote_title_size, dcfg.title_abs_min, is_english,
        headroom=dcfg.quote_fill_headroom,
    )
    font = get_font(font_path, size)
    draw = ImageDraw.Draw(canvas)
    lh = line_height(font, dcfg.title_line_gap)
    normal = parse_color(dcfg.quote_title_color_hex)
    highlight = parse_color(dcfg.quote_highlight_color_hex)
    total_words = sum(len(ln.split()) for ln in lines)
    start_idx = int(total_words * (1 - dcfg.quote_highlight_fraction))

    # عمودي پیل: اوږد نقل قول → پورته؛ کوچنی → د پورته او مرکز تر منځ (خالي فضا لږه شي).
    total_h = len(lines) * lh
    if total_h >= dcfg.quote_title_fill_threshold * bh:
        y = by
    else:
        center_y = by + (bh - total_h) // 2
        y = (by + center_y) // 2  # د پورته او مرکز منځ
    space_w = text_width(font, " ", is_english) or font.getlength(" ")
    gi = 0
    for i, line in enumerate(lines):
        is_last = i == len(lines) - 1
        # RTL نا-وروستۍ کرښه → کشیده (justify)؛ وروستۍ کرښه یوازې ښي-تنظیم.
        justify = not is_english and not is_last
        if justify:
            line = justify_line(line, lambda t: text_width(font, t, is_english), bw, seed=i)
        words = line.split()
        widths = [text_width(font, w, is_english) for w in words]
        # ⚠️ فاصله تل طبیعي ده — ډکول یوازې د کشیده کار دی (زموږ قانون).
        gi = _draw_aligned_line(draw, words, widths, font, box, y, is_english, gi, start_idx, normal, highlight, space_w)
        y += lh


def _draw_rc(canvas, text: str, font_path: str, box, target: int, color_hex: str,
             dcfg, is_english: bool, gap: int = 8, valign: str = "center") -> None:
    """نوم/رتبه — بې justify، RTL ښي-تنظیم / LTR کیڼ-تنظیم، شرینک-ټو-فټ.

    ``valign`` = "center" (نوم) یا "top" (رتبه — چې دوه-لاینه رتبه ښکته وغزیږي
    او نوم ته ور نږدې نشي).
    """
    if not text.strip():
        return
    bx, by, bw, bh = box
    floor = dcfg.title_abs_min
    size, lines = target, [text]
    for s in range(target, floor - 1, -1):
        font = get_font(font_path, s)
        wl = wrap_text(text, font_path, s, bw, is_english)
        a, d = font.getmetrics()
        lh = a + d + gap
        if all(text_width(font, ln, is_english) <= bw for ln in wl) and len(wl) * lh - gap <= bh:
            size, lines = s, wl
            break
    else:
        size = floor
        lines = wrap_text(text, font_path, floor, bw, is_english)

    font = get_font(font_path, size)
    a, d = font.getmetrics()
    lh = a + d + gap
    total_h = len(lines) * lh - gap
    color = parse_color(color_hex)
    draw = ImageDraw.Draw(canvas)
    direction = _direction(is_english)
    y = by if valign == "top" else by + (bh - total_h) // 2
    for line in lines:
        if is_english:
            x = bx
        else:
            x = bx + bw - text_width(font, line, is_english)  # ښي-تنظیم
        draw.text((x, y), line, font=font, fill=color, direction=direction)
        y += lh


def render_quote_one(lang: str, quote: str, name: str, rank: str, person_path,
                     config: Config, out_dir) -> Path:
    """د یوې ژبې د نقل قول انځور جوړ کړه."""
    dcfg = config.design
    size = dcfg.quote_canvas_size
    tpl = Path(dcfg.templates_dir) / QUOTE_TEMPLATES[lang]

    # ۱. بنسټ + د شخص انځور (ښي-پورته، تر ټولو لاندې) + ۲. ټمپلیټ پر مخ.
    canvas = Image.new("RGBA", (size, size), (255, 255, 255, 0))
    if person_path and Path(person_path).exists():
        px, py, pw, ph = QUOTE_PERSON_BOX
        person = _prepare_person(person_path, pw, ph)
        canvas.paste(person, (px, py), person)
    else:
        logger.warning("[%s] د شخص انځور نشته", lang)
    template = Image.open(tpl).convert("RGBA").resize((size, size), Image.LANCZOS)
    canvas = Image.alpha_composite(canvas, template)

    is_english = lang == "en"
    fonts = dcfg.fonts_dir
    # ۳. نقل قول / ۴. نوم / ۵. رتبه (ټول د ټمپلیټ پر مخ).
    if quote.strip():
        _draw_title(canvas, quote, str(Path(fonts) / dcfg.quote_title_font), dcfg, is_english)
    _draw_rc(canvas, name, str(Path(fonts) / dcfg.quote_name_font), QUOTE_NAME_BOX,
             dcfg.quote_name_size, dcfg.quote_name_color_hex, dcfg, is_english)
    _draw_rc(canvas, rank, str(Path(fonts) / dcfg.quote_rank_font), QUOTE_RANK_BOX,
             dcfg.quote_rank_size, dcfg.quote_rank_color_hex, dcfg, is_english, valign="top")

    # د فایل نوم = د نقل قول عنوان (لکه خبر/عاجل کې).
    seed = quote or name or f"quote_{lang}"
    path = export_image(canvas, out_dir, seed, dcfg)
    logger.info("[%s] د نقل قول انځور جوړ شو → %s", lang, path.name)
    return path
