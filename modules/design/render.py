"""د ډیزاین اصلي ننوتنه — درې ژبې جوړول.

Renderer: compose photo → template → location → source → title for each
language and export a JPEG. Each language is isolated in try/except so one
failure never stops the others (spec §9, §15).
"""

from __future__ import annotations

import logging
from pathlib import Path

from PIL import Image

from ..config import Config
from .canvas import build_canvas
from .export import export_image
from .layout import DEFAULT_LOCATION, LANGS, VALID_LOCATIONS
from .location import load_location
from .text_render import draw_source, draw_title

logger = logging.getLogger("design.render")


def _normalize_location(value: str) -> str:
    value = (value or "").strip()
    return value if value in VALID_LOCATIONS else DEFAULT_LOCATION


def render_one(lang: dict, data: dict, news_image_path, out_dir, cfg: Config) -> Path:
    """د یوې ژبې انځور جوړ کړه او د فایل لار راوګرځوه."""
    dcfg = cfg.design
    template = Path(dcfg.templates_dir) / lang["template"]

    # ۱+۲. بنسټ + د خبر انځور + ټمپلیټ
    # `smart_crop` د پکیج پر بنسټ دی (کارونکی یې په بټن ټاکي)، نو له `data`
    # سره سفر کوي — نو د موقعیت د بیا-جوړولو پر مهال هم پاتې کیږي.
    canvas = build_canvas(news_image_path, template, dcfg,
                          smart=bool(data.get("smart_crop")))

    # ۳. د موقعیت لایر
    loc_value = _normalize_location(data.get("location", ""))
    loc_img = load_location(dcfg.locations_dir, lang["code"], loc_value, dcfg.canvas_size)
    if loc_img is not None:
        canvas = Image.alpha_composite(canvas, loc_img)

    # ۴+۵. منبع + عنوان (که نه وي، هغه برخه پریښودل کیږي)
    title_font = str(Path(dcfg.fonts_dir) / dcfg.title_font)
    source_font = str(Path(dcfg.fonts_dir) / dcfg.source_font)
    title = (data.get(lang["title_key"]) or "").strip()
    source = (data.get(lang["source_key"]) or "").strip()

    if title:
        draw_title(canvas, title, title_font, lang["title_box"], lang["is_english"], dcfg)
    else:
        logger.warning("[%s] عنوان خالي — پریښودل شو", lang["code"])
    if source:
        # د سرچینې وروسته نښه (لکه «:») ور اضافه کړه که دمخه یې نه لري.
        if dcfg.source_suffix and not source.endswith(dcfg.source_suffix):
            source = source + dcfg.source_suffix
        draw_source(canvas, source, source_font, lang["source_box"], lang["is_english"], dcfg)
    else:
        logger.warning("[%s] منبع خالي — پریښودل شوه", lang["code"])

    # ۶. Export
    name_seed = title or f"news_{lang['code']}"
    path = export_image(canvas, out_dir, name_seed, dcfg)
    logger.info("[%s] انځور جوړ شو → %s", lang["code"], path.name)
    return path


def render_all(data: dict, news_image_path, out_dir, cfg: Config, progress_cb=None) -> list[Path]:
    """درې واړه ژبې جوړ کړه؛ د هرې ناکامي جلا اداره.

    Build all three languages. ``progress_cb(lang_code)`` — if given — is called
    before each language so callers can report progress.
    """
    paths: list[Path] = []
    for lang in LANGS:
        # یوازې هغه ژبې چې عنوان لري ډیزاین کیږي.
        if not (data.get(lang["title_key"]) or "").strip():
            logger.info("[%s] عنوان نشته — دا ژبه پریښودل شوه", lang["code"])
            continue
        try:
            if progress_cb is not None:
                progress_cb(lang["code"])
            paths.append(render_one(lang, data, news_image_path, out_dir, cfg))
        except Exception as err:  # noqa: BLE001 - یوه ژبه نه باید نور ودروي
            logger.error("[%s] د ډیزاین ناکامي: %s", lang["code"], err, exc_info=True)
    return paths
