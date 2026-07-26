"""ژوندۍ کتنه — یو پیغام چې بیا-بیا تازه (edit) کیږي.

Live progress: send ONE message and edit it in place instead of spamming the
group. The message shows a header (which mode is running), a progress bar with
percent and elapsed time, and a checklist of steps.

⚠️ دوه اصله:
  ۱. **یوازې یو پیغام** — د دورې لپاره هیڅ اضافي پیغام نه لیږو.
  ۲. **تازه کول کار نه ځنډوي** — که وخت لږ وي، هغه تازه کول *پریږدو* (نه دا چې
     ویده شو)، خو وروستی حالت تل په زور ښکاره کیږي.
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger("progress")

PROGRESS_STEPS = [
    ("📥", "پکیج ترلاسه شو"),
    ("🤖", "جیمنای تحلیل کوي"),
    ("🖼", "د خبر انځور چمتو کیږي"),
    ("🎨", "پښتو ډیزاین"),
    ("🎨", "فارسي ډیزاین"),
    ("🎨", "انګلیسي ډیزاین"),
    ("📦", "انځورونه فشارول کیږي"),
    ("📤", "ګروپ ته لیږل"),
]

# د عاجل خبر پرمختګ — بې AI/جیمنای (عنوان په کوډ کې چمتو کیږي).
URGENT_PROGRESS_STEPS = [
    ("📥", "پکیج ترلاسه شو"),
    ("📝", "سرلیکونه چمتو کیږي"),
    ("🎨", "پښتو ډیزاین"),
    ("🎨", "فارسي ډیزاین"),
    ("🎨", "انګلیسي ډیزاین"),
    ("📤", "ګروپ ته لیږل"),
]

# د نقل قول پرمختګ — Gemini نوم/رتبه/نقل قول راباسي.
QUOTE_PROGRESS_STEPS = [
    ("📥", "پکیج ترلاسه شو"),
    ("🤖", "نقل قول استخراج کیږي"),
    ("🎨", "پښتو ډیزاین"),
    ("🎨", "فارسي ډیزاین"),
    ("🎨", "انګلیسي ډیزاین"),
    ("📤", "ګروپ ته لیږل"),
]

_BAR_LEN = 10
_BAR_FULL = "▰"
_BAR_EMPTY = "▱"
_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def fa_digits(value) -> str:
    """لاتین شمېرې پښتو/فارسي شمېرو ته واړوه (۱۲۳ نه 123)."""
    return str(value).translate(_DIGITS)


def human_time(seconds: float) -> str:
    """تېر وخت — «۱۲ ثانیې» یا «۲:۰۵ دقیقې»."""
    s = int(seconds)
    if s < 60:
        return f"{fa_digits(s)} ثانیې"
    return f"{fa_digits(s // 60)}:{fa_digits(f'{s % 60:02d}')} دقیقې"


class Progress:
    """د یوه پیغام له لارې د پرمختګ ژوندۍ ښودنه.  Single-message live progress."""

    def __init__(self, bot, chat_id: int, min_interval: float = 0.7, steps=None,
                 title: str = "📦 پروسس"):
        self.bot = bot
        self.chat_id = chat_id
        self.msg_id: int | None = None
        self.min_interval = min_interval
        self.steps = steps or PROGRESS_STEPS
        self.title = title
        self._last_edit = 0.0
        self._started = 0.0
        self._current = -1          # روان ګام (-1 = لا پیل شوی نه)
        self._notes: dict = {}      # idx → اضافي یادښت (لکه «۳ انځورونه»)
        self._failed_at: int | None = None

    # ─────────────── رسمول ───────────────

    def _bar(self, done: int) -> str:
        total = len(self.steps)
        filled = round(_BAR_LEN * done / total) if total else 0
        pct = round(100 * done / total) if total else 0
        return f"{_BAR_FULL * filled}{_BAR_EMPTY * (_BAR_LEN - filled)} {fa_digits(pct)}٪"

    def _render(self, state: str = "running", err: str | None = None,
                summary: str | None = None) -> str:
        """بشپړ متن جوړ کړه — سرلیک + پټه + چیک‌لیسټ (+ لنډیز/خطا).

        ⚠️ د پای کې هم چیک‌لیسټ **نه پاکیږي** — کارونکی ګوري چې څه ترسره شول.
        """
        done = len(self.steps) if state == "ok" else max(self._current, 0)
        head_state = {"ok": "✅ بشپړ شو", "fail": "❌ ناکام"}.get(state, "⏳ روان دی")
        elapsed = human_time(time.monotonic() - self._started) if self._started else ""

        lines = [f"*{self.title}* — {head_state}", f"{self._bar(done)} · {elapsed}", ""]
        for i, (icon, label) in enumerate(self.steps):
            note = self._notes.get(i, "")
            note = f"  ‹{note}›" if note else ""
            if self._failed_at == i:
                mark = "❌"
            elif i < self._current or state == "ok":
                mark = "✅"
            elif i == self._current:
                mark = "⏳"
            else:
                mark = "⬜"
            body = f"*{label}*" if (mark == "⏳" and state == "running") else label
            lines.append(f"{mark} {icon} {body}{note}")

        if summary:
            lines += ["", summary]
        if err:
            lines += ["", f"⚠️ `{err}`"]
        return "\n".join(lines)

    # ─────────────── لیږل / تازه کول ───────────────

    async def _edit(self, text: str, force: bool = False) -> None:
        """پیغام تازه کړه — که وخت لږ وي، **پریږده** (نه ویده کیږو)."""
        if self.msg_id is None:
            return
        now = time.monotonic()
        if not force and now - self._last_edit < self.min_interval:
            return  # ډیر ژر دی — دا تازه کول پریږده، کار ونه ځنډوو
        self._last_edit = now
        try:
            await self.bot.edit_message_text(
                chat_id=self.chat_id, message_id=self.msg_id,
                text=text, parse_mode="Markdown",
            )
        except Exception as err:  # noqa: BLE001 - "not modified"/flood عادي دي
            logger.debug("د پرمختګ تازه کول: %s", err)

    async def start(self, reply_markup=None) -> None:
        """د پرمختګ پیغام ولیږه.

        ``reply_markup`` د بوټ-کیبورډ بیا-ټاکلو لپاره دی: د دورې پای کې بېل
        پیغام نه لیږو، نو منو همدلته بیرته اصلي حالت ته ځي (ټلګرام کیبورډ یوازې
        د یوه پیغام سره بدلولی شي).
        """
        self._started = time.monotonic()
        try:
            m = await self.bot.send_message(
                self.chat_id, self._render(), parse_mode="Markdown",
                reply_markup=reply_markup,
            )
            self.msg_id = m.message_id
            self._last_edit = time.monotonic()
        except Exception as err:  # noqa: BLE001
            logger.warning("د پرمختګ پیغام پیل ناکام: %s", err)

    def note(self, idx: int, text: str) -> None:
        """یوه ګام ته اضافي یادښت ورکړه (لکه د ژبې نوم یا شمېر)."""
        self._notes[idx] = text

    async def step(self, idx: int, note: str | None = None) -> None:
        """روان ګام وټاکه او پیغام تازه کړه."""
        self._current = idx
        if note:
            self._notes[idx] = note
        await self._edit(self._render())

    async def finish(self, ok: bool = True, err: str | None = None,
                     summary: str | None = None) -> None:
        """وروستی حالت — چیک‌لیسټ پاتې کیږي، لنډیز/خطا ورسره."""
        if ok:
            self._current = len(self.steps)
        else:
            self._failed_at = max(self._current, 0)
        clean = (err or "").replace("`", "'")[:300] or None
        await self._edit(self._render("ok" if ok else "fail", clean, summary), force=True)
