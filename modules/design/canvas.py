"""د کانواس ترکیب — د خبر انځور + ټمپلیټ لایرونه.

Canvas assembly: base → news photo (top, full width) → template overlay.
The location and text layers are added later by the renderer.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PIL import Image

from ..config import DesignConfig

logger = logging.getLogger("design.canvas")


def prepare_news_image(path: str | Path, dcfg: DesignConfig):
    """د خبر انځور د 1650×1309 اندازې ته چمتو کړه (stretch یا cover).

    Resize the news photo to the photo box. ``stretch`` fills the box (default,
    nothing cropped); ``cover`` preserves aspect ratio but crops the edges.
    """
    w, h = dcfg.photo_width, dcfg.photo_height
    img = Image.open(path).convert("RGBA")
    if min(img.width, img.height) < 800:
        logger.warning("د خبر انځور کوچنی دی (%dx%d) — کیفیت ممکن ښکته وي", img.width, img.height)

    if dcfg.photo_fit == "cover":
        src_r, dst_r = img.width / img.height, w / h
        if src_r > dst_r:
            nh, nw = h, int(h * src_r)
        else:
            nw, nh = w, int(w / src_r)
        img = img.resize((nw, nh), Image.LANCZOS)
        left, top = (nw - w) // 2, (nh - h) // 2
        return img.crop((left, top, left + w, top + h))

    # ډیفالټ: stretch — بشپړ انځور، هیڅ برخه نه پرې کیږي.
    return img.resize((w, h), Image.LANCZOS)


def build_canvas(news_image_path, template_path, dcfg: DesignConfig):
    """بنسټ + د خبر انځور (پورته-منځ) + ټمپلیټ (پر مخ) ترکیب کړه.

    Returns an RGBA canvas ready for the location and text layers.
    """
    size = dcfg.canvas_size
    canvas = Image.new("RGBA", (size, size), (255, 255, 255, 255))

    # ۱. د خبر انځور — پورته-منځ (عرض = بشپړ کانواس نو x=0).
    if news_image_path and Path(news_image_path).exists():
        photo = prepare_news_image(news_image_path, dcfg)
        canvas.paste(photo, (0, 0), photo)
    else:
        logger.warning("د خبر انځور نشته — پرته له انځور دوام")

    # ۲. ټمپلیټ — پر مخ (شفافیت لري).
    tpl = Image.open(template_path).convert("RGBA")
    canvas = Image.alpha_composite(canvas, tpl)
    return canvas
