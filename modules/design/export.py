"""د انځور Export — د لوړ کیفیت JPEG فشارول + پاک فایل نوم.

Export: save the RGBA canvas as a high-quality JPEG, lowering quality only as
far as needed to hit the size target — never ``quantize`` (it causes banding).
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..config import DesignConfig
from ..utils import sanitize_filename

logger = logging.getLogger("design.export")


def export_image(canvas, out_dir: str | Path, title: str, dcfg: DesignConfig) -> Path:
    """کانواس د JPEG په بڼه وساته او د فایل لار راوګرځوه.

    File name comes from the sanitized title (hidden-unicode safe so it is
    always findable). subsampling=0 (4:4:4) keeps text and the orange highlight
    crisp; progressive is friendlier for Telegram.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = sanitize_filename(title) if title.strip() else "untitled"
    path = out_dir / f"{name}.jpg"

    # وروستۍ اندازه (1920) ته resize — دواړه خبر او عاجل یو شان اندازه.
    out_size = getattr(dcfg, "output_size", 0)
    if out_size and canvas.size != (out_size, out_size):
        from PIL import Image
        canvas = canvas.resize((out_size, out_size), Image.LANCZOS)

    rgb = canvas.convert("RGB")  # JPEG شفافیت نه مني
    target = dcfg.target_kb * 1024
    qualities = list(range(dcfg.jpeg_quality_max, dcfg.jpeg_quality_min - 1, -3))
    if dcfg.jpeg_quality_min not in qualities:
        qualities.append(dcfg.jpeg_quality_min)

    for q in qualities:
        rgb.save(path, "JPEG", quality=q, optimize=True, progressive=True, subsampling=0)
        size = path.stat().st_size
        if size <= target:
            logger.info("[%s] JPEG q=%d: %dKB", path.name, q, size // 1024)
            return path
    logger.info("[%s] JPEG q=%d (min): %dKB", path.name, qualities[-1], path.stat().st_size // 1024)
    return path
