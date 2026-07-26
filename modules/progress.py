"""ژوندۍ کتنه — یو پیغام چې بیا-بیا تازه (edit) کیږي.

Live progress: send ONE message and edit it in place instead of spamming the
group. The message shows a header (which mode is running), a progress bar with
percent and elapsed time, and a checklist of steps.

⚠️ درې اصله:
  ۱. **یوازې یو پیغام** — د دورې لپاره هیڅ اضافي پیغام نه لیږو.
  ۲. **تازه کول کار نه ځنډوي** — هیڅکله ویده نه کیږو؛ یو د شالید ټیکر پیغام
     ژوندی ساتي. که یو ګام ډیر ژر راشي، ټیکر یې تر یوې دورې دننه ښیي.
  ۳. **جمود نشته** — پخوا به ژر تازه‌کول یوازې *غورځول* کیدل، نو د اوږد ګام
     (لکه د جیمنای تحلیل) پر مهال به پیغام جامد و او بیا به یې په پای کې ټول
     ګامونه یو ځل ټک کړل. اوس ټیکر تېر وخت هم ځغلوي، نو حرکت تل ښکاري.
"""

from __future__ import annotations

import asyncio
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

    def __init__(self, bot, chat_id: int, min_interval: float = 0.8, steps=None,
                 title: str = "📦 پروسس"):
        self.bot = bot
        self.chat_id = chat_id
        self.msg_id: int | None = None
        # `min_interval` = د هرې تازه‌کولو **کمترین واټن** (نوی ګام ژر ښکاره شي).
        # `tick_interval` = د شالید د تازه‌کولو دوره (تېر وخت ځغلوي) — لوړه ده
        # چې د ټلګرام نرخ-حد ته ورنږدې نشو.
        self.min_interval = min_interval
        self.tick_interval = max(1.5, min_interval)
        self.steps = steps or PROGRESS_STEPS
        self.title = title
        self._last_edit = 0.0
        self._last_text = ""
        self._started = 0.0
        self._current = -1          # روان ګام (-1 = لا پیل شوی نه)
        self._notes: dict = {}      # idx → اضافي یادښت (لکه «۳ انځورونه»)
        self._failed_at: int | None = None
        self._ticker: asyncio.Task | None = None

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
        """پیغام تازه کړه — د نرخ-حد په پام کې نیولو سره (کار نه ځنډوي)."""
        if self.msg_id is None:
            return
        now = time.monotonic()
        if not force and now - self._last_edit < self.min_interval:
            return  # ډیر ژر دی — ټیکر به یې تر ۱ دورې دننه ونیسي
        if text == self._last_text and not force:
            return  # هماغه متن — «not modified» ته اړتیا نشته
        self._last_edit = now
        self._last_text = text
        try:
            await self.bot.edit_message_text(
                chat_id=self.chat_id, message_id=self.msg_id,
                text=text, parse_mode="Markdown",
            )
            return
        except Exception as err:  # noqa: BLE001
            msg = str(err)
            low = msg.lower()
            if "not modified" in low:
                return  # عادي — هماغه متن دی
            if "flood" in low or "too many" in low or "429" in low:
                # ټلګرام نرخ محدود کړ — دوره ورو کړه (خو تر ۵ ثانیو زیاته نه).
                self.min_interval = min(self.min_interval * 2, 5.0)
                self.tick_interval = max(self.tick_interval * 2, self.min_interval)
                logger.warning("د ټلګرام نرخ-حد — د تازه کولو دوره %.1f ثانیې شوه",
                               self.tick_interval)
                return
            # ⚠️ دا خطا پخوا په debug کې وه، نو جامد پیغام یې پټ ساتلی و.
            logger.warning("د پرمختګ تازه کول ناکام: %s — بې Markdown بیا هڅه", msg)
        try:  # ښایي د Markdown پارس ستونزه وي — بې فارمټ یې ولیږه
            await self.bot.edit_message_text(
                chat_id=self.chat_id, message_id=self.msg_id, text=text,
            )
        except Exception as err:  # noqa: BLE001
            logger.warning("د پرمختګ تازه کول بیا هم ناکام: %s", err)

    async def start(self) -> None:
        """د پرمختګ پیغام ولیږه.

        ⚠️ دلته **reply_markup نه ورکوو**. که پیغام له ReplyKeyboardMarkup سره
        ولیږل شي، وروسته یې `editMessageText` نه مني — پیغام جامد پاتې کیږي.
        منو جلا بیا ټاکل کیږي (commands._restore_menu).
        """
        self._started = time.monotonic()
        try:
            text = self._render()
            m = await self.bot.send_message(
                self.chat_id, text, parse_mode="Markdown",
            )
            self.msg_id = m.message_id
            self._last_edit = time.monotonic()
            self._last_text = text
        except Exception as err:  # noqa: BLE001
            logger.warning("د پرمختګ پیغام پیل ناکام: %s", err)
            return
        # ژوندی ساتونکی — پخپله هرې دورې پیغام تازه کوي، نو اوږد ګامونه
        # (لکه د جیمنای تحلیل) هم حرکت ښیي: تېر وخت ځغلي او ګامونه سمدلاسه ښکاري.
        try:
            self._ticker = asyncio.create_task(self._tick())
        except RuntimeError:  # pragma: no cover - بې روان loop (ټیسټ)
            self._ticker = None

    async def _tick(self) -> None:
        """د شالید دوره — پیغام ژوندی ساتي (تېر وخت + نوي ګامونه)."""
        try:
            while True:
                await asyncio.sleep(self.tick_interval)
                await self._edit(self._render())
        except asyncio.CancelledError:  # pragma: no cover - عادي پای
            pass
        except Exception as err:  # noqa: BLE001
            logger.debug("د پرمختګ ټیکر: %s", err)

    def _stop_ticker(self) -> None:
        if self._ticker is not None:
            self._ticker.cancel()
            self._ticker = None

    async def delete(self) -> None:
        """ژوندی پیغام ړنګ کړه — د بریالۍ دورې وروسته چټ پاک پاتې شي."""
        self._stop_ticker()
        if self.msg_id is None:
            return
        try:
            await self.bot.delete_message(chat_id=self.chat_id, message_id=self.msg_id)
        except Exception as err:  # noqa: BLE001
            logger.debug("د پرمختګ پیغام ړنګول: %s", err)
        self.msg_id = None

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
        self._stop_ticker()
        if ok:
            self._current = len(self.steps)
        else:
            self._failed_at = max(self._current, 0)
        clean = (err or "").replace("`", "'")[:300] or None
        await self._edit(self._render("ok" if ok else "fail", clean, summary), force=True)
