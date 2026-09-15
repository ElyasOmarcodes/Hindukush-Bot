"""د پکیج جوړول، ساتل او اعتبار.

Package model: the in-memory buffer, validation rules and saving a confirmed
package (one image + up to three texts) to a timestamped folder on disk.
"""

from __future__ import annotations

import shutil
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config
from .utils import ensure_dirs, timestamp_folder

# د منو د اړیکو د یادولو حد — که کارونکی هیڅکله پکیج بشپړ نکړي.
_MAX_CHATTER = 40


@dataclass
class BufferItem:
    """د بفر یو توکی — انځور یا متن.  One buffered item, image or text."""

    type: str  # "image" | "text"
    message_id: int
    content: str = ""      # د متن لپاره / text body
    path: str | None = None  # د انځور لپاره د پینډنګ فایل لار / pending image path


@dataclass
class BotState:
    """د رباټ روان حالت — بفر، البوم-بفر، د پروسس بیرغ.

    Shared runtime state stored on ``application.bot_data``.
    """

    config: Config
    # آیا رباټ اوس د پیغامونو غوږ نیسي؟ په default غیر فعال — د /on سره فعالیږي.
    # Whether the bot is currently listening. Off by default; /on enables it.
    listening: bool = False
    # د کار حالت: "news" (۱ انځور + ۳ متن) یا "urgent" (فقط ۳ متن، بې انځور).
    mode: str = "news"
    buffer: list[BufferItem] = field(default_factory=list)
    # د البوم برخو راټولول د media_group_id پر بنسټ.
    media_buffer: dict = field(default_factory=lambda: defaultdict(list))
    media_tasks: dict = field(default_factory=dict)
    processing: bool = False
    # د منو د اړیکو پیغامونه (کارونکی + رباټ) — د بریالۍ دورې وروسته ړنګیږي.
    # د پکیج **محتوا** پکې نشته، نو راپورونه او انځور خوندي پاتې کیږي.
    chatter: list[int] = field(default_factory=list)
    # د وروستي «منو» پیغام ID — د reset سره **نه** پاکیږي (کیبورډ یې راوړی).
    menu_msg_id: int | None = None
    # د وروستي «شمېره» پیغام ID — هغه چې د «✅ بشپړول | ۲» بټن یې راوړی. هر نوی
    # توکی یې تازه کوي (نوی لیږو، زوړ ړنګوو). لکه `menu_msg_id`، د `reset` سره
    # **نه** پاکیږي — نه‌نو زوړ پیغام به بې-څښتنه پاتې شي.
    count_msg_id: int | None = None
    # ── د وروستي (بریالي) خبر-پکیج یادښت — د موقعیت د لاسي بدلون لپاره ──
    # د پکیج فولډر (result.json + image.jpg پکې دي)، د ریپلای هدف، او هغه
    # موقعیت چې اوس کارول شوی. دا د `reset` سره **نه** پاکیږي.
    last_pkg_dir: str | None = None
    last_reply_to: int | None = None
    last_location: str = "auto"
    last_detected_location: str = ""
    location_msg_id: int | None = None

    def reset(self) -> None:
        """د دورې وروسته بشپړ پاکول.  Full reset after a cycle."""
        # د پینډنګ انځورونه پاک کړه چې کثافت پاتې نشي.
        for item in self.buffer:
            if item.type == "image" and item.path:
                Path(item.path).unlink(missing_ok=True)
        self.buffer.clear()
        self.media_buffer.clear()
        for task in list(self.media_tasks.values()):
            task.cancel()
        self.media_tasks.clear()
        self.processing = False
        # ⚠️ `chatter` دلته **نه** پاکوو — هغه پیغامونه لا په چټ کې دي او باید
        # د بریالۍ دورې وروسته (په _purge_chatter کې) ړنګ شي. که دلته یې پاک
        # کړو، د «جوړول → عاجل» ترمنځ reset یې ورکوي او بیا نه ړنګیږي.
        del self.chatter[:-_MAX_CHATTER]  # یوازې ډیر زاړه وغورځوه

    def counts(self) -> tuple[int, int]:
        """(انځورونه, متنونه) شمیر.  Return (image_count, text_count)."""
        imgs = sum(1 for i in self.buffer if i.type == "image")
        txts = sum(1 for i in self.buffer if i.type == "text")
        return imgs, txts


def can_add(state: BotState, item_type: str) -> tuple[bool, str]:
    """آیا نوی توکی اضافه کیدلی شي؟  Whether a new item may be added.

    Returns ``(ok, reason)`` — ``reason`` is a Pashto message when rejected.
    """
    cfg = state.config
    imgs, txts = state.counts()
    if len(state.buffer) >= cfg.max_items:
        return False, f"د توکو حد ({cfg.max_items}) بشپړ شو"
    if item_type == "image" and imgs >= cfg.max_images:
        return False, f"یوازې {cfg.max_images} انځور منل کیږي"
    if item_type == "text" and txts >= cfg.max_texts:
        return False, f"یوازې {cfg.max_texts} متنونه منل کیږي"
    return True, ""


def validate_package(state: BotState) -> list[str]:
    """د پروسس مخکې د بفر اعتبار وګوره.  Validate the buffer before processing.

    Returns a list of Pashto error strings — empty means valid.
    """
    cfg = state.config
    imgs, txts = state.counts()
    errors: list[str] = []
    if len(state.buffer) > cfg.max_items:
        errors.append(f"تر {cfg.max_items} توکو زیات دي")
    if imgs > cfg.max_images:
        errors.append(f"یوازې {cfg.max_images} انځور منل کیږي")
    if txts > cfg.max_texts:
        errors.append(f"یوازې {cfg.max_texts} متنونه منل کیږي")
    if txts == 0:
        errors.append("لږ تر لږه یو متني راپور اړین دی")
    return errors


def save_package(state: BotState) -> Path:
    """تایید شوی بفر ډیسک ته وساته او د پکیج فولډر لار راوګرځوه.

    Persist the confirmed buffer to ``<packages_dir>/<timestamp>/`` with
    ``image.jpg`` and ``text_1.txt`` … and return the folder path.
    """
    base = Path(state.config.packages_dir)
    pkg_dir = base / timestamp_folder()
    ensure_dirs(pkg_dir)

    text_index = 0
    for item in state.buffer:
        if item.type == "text":
            text_index += 1
            (pkg_dir / f"text_{text_index}.txt").write_text(
                item.content, encoding="utf-8"
            )
        elif item.type == "image" and item.path:
            # اصلي (بشپړ کیفیت) انځور دننه ور واستوه.
            shutil.move(item.path, str(pkg_dir / "image.jpg"))
            item.path = str(pkg_dir / "image.jpg")

    return pkg_dir


def collect_texts(state: BotState) -> list[str]:
    """د بفر ټول متني راپورونه په ترتیب سره راوباسه.  Ordered text bodies."""
    return [i.content for i in state.buffer if i.type == "text"]


def first_text_message_id(state: BotState) -> int | None:
    """د پکیج د **لومړي متني پیغام** ID — چې خروجي انځورونه ورته ریپلای شي.

    The rendered images are sent as a reply to the package's first text message
    (usually the Pashto report), so they stay threaded to their source.
    """
    for item in state.buffer:
        if item.type == "text" and item.message_id:
            return item.message_id
    return None
