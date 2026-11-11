"""د کانواس ترکیب — د خبر انځور + ټمپلیټ لایرونه.

Canvas assembly: base → news photo (top, full width) → template overlay.
The location and text layers are added later by the renderer.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PIL import Image

from ..config import DesignConfig
from .smartcrop import smart_crop

logger = logging.getLogger("design.canvas")


def prepare_news_image(path: str | Path, dcfg: DesignConfig, smart: bool = False):
    """د خبر انځور د 1650×1309 اندازې ته چمتو کړه.

    درې حالتونه:
      • ``smart=True``  — د ۵:۴ **تر ټولو لوی** مستطیل ومومه چې اصلي سوژه پکې
        پاتې شي، بیا یې اندازه کړه (اختیاري؛ کارونکی یې په بټن ټاکي).
      • ``photo_fit="cover"`` — نسبت ساتي خو یوازې د مرکز پر بنسټ پرې کوي.
      • ډیفالټ ``stretch`` — بشپړ انځور، هیڅ نه پرې کیږي (خو لږ غزیږي).
    """
    w, h = dcfg.photo_width, dcfg.photo_height
    img = Image.open(path).convert("RGBA")
    if min(img.width, img.height) < 800:
        logger.warning("د خبر انځور کوچنی دی (%dx%d) — کیفیت ممکن ښکته وي", img.width, img.height)

    if smart:
        out = smart_crop(img, w, h)
        logger.info("ځیرک کرپ: %dx%d → %dx%d", img.width, img.height, w, h)
        return out

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


def build_canvas(news_image_path, template_path, dcfg: DesignConfig,
                 smart: bool = False):
    """بنسټ + د خبر انځور (پورته-منځ) + ټمپلیټ (پر مخ) ترکیب کړه.

    Returns an RGBA canvas ready for the location and text layers.
    """
    size = dcfg.canvas_size
    canvas = Image.new("RGBA", (size, size), (255, 255, 255, 255))

    # ۱. د خبر انځور — پورته-منځ (عرض = بشپړ کانواس نو x=0).
    if news_image_path and Path(news_image_path).exists():
        photo = prepare_news_image(news_image_path, dcfg, smart=smart)
        canvas.paste(photo, (0, 0), photo)
    else:
        logger.warning("د خبر انځور نشته — پرته له انځور دوام")

    # ۲. ټمپلیټ — پر مخ (شفافیت لري).
    tpl = Image.open(template_path).convert("RGBA")
    canvas = Image.alpha_composite(canvas, tpl)
    return canvas
