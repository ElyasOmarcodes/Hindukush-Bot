"""د موقعیت لایر — د ژبې/ارزښت پر بنسټ PNG لوډول.

Location layer: pick the right 1650×1650 transparent PNG for the language and
Gemini location value, tolerating the ``Regin``/``Region`` filename typo.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PIL import Image

from .layout import DEFAULT_LOCATION, LOCATION_MAP

logger = logging.getLogger("design.location")


def load_location(locations_dir: str | Path, lang: str, value: str, canvas_size: int = 1650):
    """د ژبې + موقعیت لپاره PNG راوګرځوه، یا None که ونه موندل شو.

    Returns an RGBA image scaled to ``canvas_size`` (the source files are
    2000×2000), or None (renderer then skips the layer). Unknown values fall
    back to ``World``.
    """
    lang_map = LOCATION_MAP.get(lang, {})
    rel = lang_map.get(value) or lang_map.get(DEFAULT_LOCATION)
    if not rel:
        logger.warning("د موقعیت نقشه د ژبې %s لپاره نشته", lang)
        return None

    path = Path(locations_dir) / rel
    if not path.exists():
        # د Regin/Region ټایپي بدیل هڅه کړه.
        for a, b in (("Regin", "Region"), ("Region", "Regin")):
            alt = Path(str(path).replace(a, b))
            if alt.exists():
                path = alt
                break
        else:
            logger.warning("د موقعیت فایل ونه موندل شو: %s", path)
            return None
    img = Image.open(path).convert("RGBA")
    if img.size != (canvas_size, canvas_size):
        img = img.resize((canvas_size, canvas_size), Image.LANCZOS)
    return img
