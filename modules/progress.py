"""ژوندۍ کتنه — یو پیغام چې بیا-بیا تازه (edit) کیږي.

Live progress: send one message and edit it in place instead of spamming the
group. Edits are spaced by at least ``min_interval`` seconds to avoid Telegram
flood control, and "message is not modified" errors are swallowed.
"""

from __future__ import annotations

import asyncio
import logging
import time

logger = logging.getLogger("progress")

PROGRESS_STEPS = [
    ("📥", "پکیج ترلاسه شو"),
    ("🤖", "جیمنای تحلیل کوي…"),
    ("🖼", "د خبر انځور چمتو کیږي…"),
    ("🎨", "پښتو ډیزاین جوړیږي…"),
    ("🎨", "فارسي ډیزاین جوړیږي…"),
    ("🎨", "انګلیسي ډیزاین جوړیږي…"),
    ("📦", "انځورونه فشارول کیږي…"),
    ("📤", "ګروپ ته لیږل کیږي…"),
    ("✅", "بشپړ شو"),
]

# د عاجل خبر پرمختګ — بې AI/جیمنای (عنوان په کوډ کې چمتو کیږي).
URGENT_PROGRESS_STEPS = [
    ("📥", "پکیج ترلاسه شو"),
    ("📝", "سرلیکونه چمتو کیږي…"),
    ("🎨", "پښتو ډیزاین جوړیږي…"),
    ("🎨", "فارسي ډیزاین جوړیږي…"),
    ("🎨", "انګلیسي ډیزاین جوړیږي…"),
    ("📤", "ګروپ ته لیږل کیږي…"),
    ("✅", "بشپړ شو"),
]

# د نقل قول پرمختګ — Gemini نوم/رتبه/نقل قول راباسي.
QUOTE_PROGRESS_STEPS = [
    ("📥", "پکیج ترلاسه شو"),
    ("🤖", "نقل قول استخراج کیږي…"),
    ("🎨", "پښتو ډیزاین جوړیږي…"),
    ("🎨", "فارسي ډیزاین جوړیږي…"),
    ("🎨", "انګلیسي ډیزاین جوړیږي…"),
    ("📤", "ګروپ ته لیږل کیږي…"),
    ("✅", "بشپړ شو"),
]


class Progress:
    """د یوه پیغام له لارې د پرمختګ ښودنه.  Single-message progress tracker."""

    def __init__(self, bot, chat_id: int, min_interval: float = 1.0, steps=None):
        self.bot = bot
        self.chat_id = chat_id
        self.msg_id: int | None = None
        self.min_interval = min_interval
        self._last_edit = 0.0
        self.steps = steps or PROGRESS_STEPS

    async def start(self, reply_markup=None) -> None:
        """د پرمختګ پیغام ولیږه.

        ``reply_markup`` د بوټ-کیبورډ بیا-ټاکلو لپاره دی: د دورې پای کې بېل
        پیغام نه لیږو، نو منو همدلته بیرته اصلي حالت ته ځي (ټلګرام کیبورډ یوازې
        د یوه پیغام سره بدلولی شي).
        """
        try:
            m = await self.bot.send_message(self.chat_id, "⏳ پیل کیږي…", reply_markup=reply_markup)
            self.msg_id = m.message_id
            self._last_edit = time.monotonic()
        except Exception as err:  # noqa: BLE001
            logger.warning("د پرمختګ پیغام پیل ناکام: %s", err)

    async def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_edit
        if elapsed < self.min_interval:
            await asyncio.sleep(self.min_interval - elapsed)

    async def step(self, idx: int) -> None:
        if self.msg_id is None:
            return
        lines = []
        for i, (_icon, label) in enumerate(self.steps):
            if i < idx:
                lines.append(f"✅ {label}")
            elif i == idx:
                lines.append(f"⏳ *{label}*")
            else:
                lines.append(f"⬜ {label}")
        await self._throttle()
        try:
            await self.bot.edit_message_text(
                chat_id=self.chat_id, message_id=self.msg_id,
                text="\n".join(lines), parse_mode="Markdown",
            )
        except Exception as err:  # noqa: BLE001 - "not modified"/flood عادي دي
            logger.debug("د پرمختګ تازه کول: %s", err)
        self._last_edit = time.monotonic()

    async def finish(self, ok: bool = True, err: str | None = None) -> None:
        if self.msg_id is None:
            return
        txt = "✅ *ټول بشپړ شو*" if ok else f"❌ *ناکام*\n`{err}`"
        await self._throttle()
        try:
            await self.bot.edit_message_text(
                chat_id=self.chat_id, message_id=self.msg_id,
                text=txt, parse_mode="Markdown",
            )
        except Exception as err2:  # noqa: BLE001
            logger.debug("د پرمختګ پای: %s", err2)
        self._last_edit = time.monotonic()
