"""مرستندویه فنکشنونه — د فایل نوم پاکول، لاګ، Markdown escape، نیټه.

Utility helpers: filename sanitizing, logging setup, Markdown escaping and
timestamped folder names. Kept dependency-free so it is easy to unit-test.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from datetime import datetime
from pathlib import Path

# پټ / کنټرول Unicode حروف چې د فایل نوم ماتوي (خاص د پښتو ZWNJ).
# Hidden/control Unicode characters that break file names (notably the
# Pashto/Persian ZWNJ). Removing them avoids "file not found" surprises.
_HIDDEN_CHARS = "‌‍​‎‏﻿‪‫‬‭‮"

# هغه حروف چې د Markdown (v1) معنا لري او باید escape شي.
_MARKDOWN_SPECIALS = r"_*[]()~`>#+-=|{}.!"


def sanitize_filename(name: str, max_len: int = 150) -> str:
    """د فایل نوم خوندي کړه: پټ حروف لرې، normalize، ناروا حروف لرې.

    Return a filesystem-safe file name: strip hidden characters, NFC-normalize
    and drop characters that are illegal on common filesystems.
    """
    if not name:
        return "untitled"
    for ch in _HIDDEN_CHARS:
        name = name.replace(ch, "")
    name = unicodedata.normalize("NFC", name)
    # ناروا فایل-سیسټم حروف او کنټرول حروف لرې کړه.
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name).strip()
    name = re.sub(r"\s+", "_", name).strip("_.")
    name = name[:max_len].rstrip("_")
    return name or "untitled"


def escape_markdown(text: str) -> str:
    """د Markdown ځانګړي حروف escape کړه چې پارس نه ماتیږي.

    Escape Markdown special characters so Pashto/Persian bodies containing
    ``*``/``_``/``[`` do not break message formatting.
    """
    if not text:
        return ""
    return "".join("\\" + c if c in _MARKDOWN_SPECIALS else c for c in text)


def timestamp_folder(now: datetime | None = None) -> str:
    """د پکیج لپاره د نیټې پر بنسټ فولډر نوم: ``2026-06-27_1530``."""
    now = now or datetime.now()
    return now.strftime("%Y-%m-%d_%H%M%S")


def ensure_dirs(*paths: str | Path) -> None:
    """ورکړل شوي فولډرونه جوړ کړه که نه وي.  Create directories if missing."""
    for p in paths:
        Path(p).mkdir(parents=True, exist_ok=True)


def setup_logging(log_file: str = "logs/bot.log") -> None:
    """د لاګ کولو معیاري تنظیم — فایل + کنسول، UTF-8.

    Configure logging to both a UTF-8 file and the console, and quiet the
    very chatty telegram/httpx loggers down to WARNING.
    """
    ensure_dirs(Path(log_file).parent)
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        level=logging.INFO,
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
