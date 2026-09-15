"""دستورونه او د پروسس pipeline.

Slash commands (/on /off /cmp /cnl /clean /status /help), the /cmp inline
confirmation keyboard, its callback, and the end-to-end processing pipeline
(save → Gemini → render 3 designs → send documents → reset).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaDocument,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.error import RetryAfter
from telegram.ext import ApplicationHandlerStop, ContextTypes, filters

from . import funny, gemini
from .design import quote as design_quote
from .design import render as design_render
from .design import urgent as design_urgent
from .design.layout import LANGS
from .package import (
    BotState,
    collect_texts,
    first_text_message_id,
    save_package,
    validate_package,
)
from .progress import (
    QUOTE_PROGRESS_STEPS,
    URGENT_PROGRESS_STEPS,
    Progress,
    fa_digits,
)

logger = logging.getLogger("commands")

# د هرې ژبې د پرمختګ ګام (index) — خبر او عاجل جلا لیستونه لري.
_LANG_STEP = {"ps": 3, "fa": 4, "en": 5}
_URGENT_LANG_STEP = {"ps": 2, "fa": 3, "en": 4}

# ─────────────── د بوټ کیبورډ بټنې (منو) ───────────────
# د دستورونو پر ځای ښکلې بټنې — چې غیر مسلکي کسان هم اسانه کار وکړي.
# یادونه: ټلګرام د کیبورډ بټنو رنګ نه مني — نو د «سور» لپاره 🔴 ایموجي کاروو.
BTN_NEWS = "📰 خبر"
BTN_URGENT = "🔴 عاجل"
BTN_QUOTE = "💬 نقل قول"
BTN_BACK = "◀️ بیرته شاته"
BTN_COMPLETE = "✅ بشپړول"
BTN_CLEAN = "🗑 پاکول"
BTN_CANCEL = "🔴 لغوه"

# ټول د بټنو لیبلونه — چې monitor یې د پکیج محتوا ونه ګڼي.
BUTTON_LABELS = [
    BTN_NEWS, BTN_URGENT, BTN_QUOTE,
    BTN_BACK, BTN_COMPLETE, BTN_CLEAN, BTN_CANCEL,
]

# د «بشپړول» بټن د محتوا شمېره ښیي: «✅ بشپړول | ۲». نو راغلی متن **دقیق**
# سمون نه لري — دلته یې کینونیکل بټن ته اړوو.
_COUNT_SEP = " | "


def complete_label(count: int) -> str:
    """د «بشپړول» بټن متن — د پکیج د محتوا شمېرې سره."""
    return f"{BTN_COMPLETE}{_COUNT_SEP}{fa_digits(count)}"


def match_button(text: str | None) -> str | None:
    """راغلی متن کوم بټن دی؟ (د شمېرې-تاړی له پامه غورځوي) — نه‌نو None."""
    t = (text or "").strip()
    for label in BUTTON_LABELS:
        if t == label or t.startswith(label + _COUNT_SEP):
            return label
    return None


class _ButtonFilter(filters.MessageFilter):
    """د منو بټنې — د «✅ بشپړول | ۲» ډوله شمېرې-تاړي سره هم سمون کوي."""

    def filter(self, message) -> bool:
        return match_button(message.text) is not None


BUTTON_FILTER = _ButtonFilter()


def _kb(rows: list[list[str]]) -> ReplyKeyboardMarkup:
    """د بټنو کیبورډ جوړ کړه.

    ⚠️ `is_persistent` **نه** ټاکو (یعنې false). که true وي، ټلګرام کیبورډ تل
    ښکاره ساتي او د مبایل «بیرته» بټن یې نشي پټولی — نو کارونکی له چټ/ګروپ
    څخه وځي. په false سره چلند نورمال کیږي: «بیرته» لومړی منو پټوي، بیا یې
    دویم ځل چټ پریږدي. منو د کیبورډ د آیکن په وهلو بیا خلاصیږي.
    """
    return ReplyKeyboardMarkup(
        [[KeyboardButton(b) for b in row] for row in rows],
        resize_keyboard=True,
    )


def main_keyboard() -> ReplyKeyboardMarkup:
    """اصلي منو — مستقیم د حالت انتخاب.

    ⚠️ پخوا یو «🛠 جوړول» بټن و چې دا منو یې خلاصوله — یو زیات ګام و او هر ځل
    یې دوه اضافي پیغامونه جوړول. اوس حالتونه سمدلاسه دلته دي.
    """
    return _kb([[BTN_NEWS, BTN_URGENT], [BTN_QUOTE]])


def active_keyboard(count: int = 0) -> ReplyKeyboardMarkup:
    """د فعال حالت منو — «بشپړول» بټن د محتوا شمېره ښیي."""
    return _kb([[complete_label(count)], [BTN_CLEAN, BTN_CANCEL], [BTN_BACK]])


async def refresh_count(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    """د «بشپړول» بټن کې شمېره تازه کړه (کله چې نوی توکی راشي).

    ⚠️ ټلګرام د **ریپلای-کیبورډ** بټنې نشي ایډیټ کولی — یوازې inline کیبورډ
    ایډیټ کیږي. نو نوی پیغام لیږو (چې نوی کیبورډ راوړي) او **بیا** زوړ ړنګوو.
    ترتیب حیاتي دی: د پیغام ړنګول خپل کیبورډ هم بېرته اخلي، نو که لومړی زوړ
    ړنګ کړو، منو به ورکه شي.
    """
    state = _state(context)
    if not state.listening or state.processing:
        return
    count = len(state.buffer)
    old_id = state.count_msg_id
    try:
        m = await context.bot.send_message(
            chat_id,
            f"📦 په پکیج کې: {fa_digits(count)}/{fa_digits(state.config.max_items)}",
            reply_markup=active_keyboard(count),
        )
    except Exception as err:  # noqa: BLE001 - د شمېرې تازه کول حیاتي نه دي
        logger.debug("د شمېرې تازه کول: %s", err)
        return
    state.count_msg_id = m.message_id
    _track(state, m.message_id)
    if old_id:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=old_id)
        except Exception:  # noqa: BLE001
            pass


# د ژبو پښتو نومونه — د «کومه ژبه ونه لیږل شوه» خبرتیا لپاره.
LANG_NAMES = {"ps": "پښتو", "fa": "فارسي", "en": "انګلیسي"}
ALL_LANGS = ("ps", "fa", "en")


def missing_langs(done) -> list[str]:
    """هغه ژبې چې راپور یې نه دی راغلی — د پښتو نومونو لیست."""
    have = set(done)
    return [LANG_NAMES[code] for code in ALL_LANGS if code not in have]


def _summary(count: int, done) -> str:
    """د دورې لنډیز — څو انځورونه او کومې ژبې (د ژوندي پیغام دننه)."""
    have = [LANG_NAMES[c] for c in ALL_LANGS if c in set(done)]
    return f"📤 *{fa_digits(count)}* انځورونه ولیږل شول — {'، '.join(have)}"


def _missing_note(done) -> str | None:
    """که کومه ژبه نه وه راغلې، هغه خبرتیا چې د دورې وروسته پاتې کیږي."""
    names = missing_langs(done)
    if not names:
        return None
    logger.info("د دې ژبو راپور نه و راغلی: %s", "، ".join(names))
    return ("⚠️ د «" + "» او «".join(names) + "» راپور مو نه و راستولی — "
            "ډیزاین یې جوړ نشو.")


# ─────────────── د موقعیت لاسي کنټرول (شیشه‌یي منو) ───────────────
# جیمنای موقعیت پخپله ټاکي، خو ځینې وخت یې سم نه ټاکي. د پکیج د نتیجې سره یوه
# شیشه‌یي منو ورکوو چې کارونکی یې په یوه کلیک بدل کړي — انځورونه بیا جوړیږي.
LOCATION_CHOICES = [
    ("auto", "اتومات"),
    ("Afghanistan", "افغانستان"),
    ("Region", "سیمه"),
    ("Middle East", "منځنی ختیځ"),
    ("World", "نړۍ"),
]
LOCATION_LABELS = dict(LOCATION_CHOICES)


def location_keyboard(active: str = "auto") -> InlineKeyboardMarkup:
    """د موقعیت شیشه‌یي منو — روان انتخاب ✅ نښه لري."""
    def btn(value: str) -> InlineKeyboardButton:
        mark = "✅ " if value == active else ""
        return InlineKeyboardButton(mark + LOCATION_LABELS[value],
                                    callback_data=f"loc:{value}")
    return InlineKeyboardMarkup([
        [btn("auto")],
        [btn("Afghanistan"), btn("Region")],
        [btn("Middle East"), btn("World")],
    ])


def _location_text(active: str, detected: str) -> str:
    if active == "auto":
        return f"📍 موقعیت: *اتومات* — جیمنای «{LOCATION_LABELS.get(detected, detected)}» وټاکه"
    return f"📍 موقعیت: *{LOCATION_LABELS[active]}* (لاسي)"


async def show_location_menu(context: ContextTypes.DEFAULT_TYPE, chat_id: int,
                             detected: str) -> None:
    """د پکیج د نتیجې سره د موقعیت شیشه‌یي منو وښیه."""
    state = _state(context)
    old_id = state.location_msg_id
    try:
        m = await context.bot.send_message(
            chat_id, _location_text(state.last_location, detected),
            reply_markup=location_keyboard(state.last_location),
            parse_mode="Markdown",
        )
    except Exception as err:  # noqa: BLE001 - د موقعیت منو حیاتي نه ده
        logger.debug("د موقعیت منو: %s", err)
        return
    state.location_msg_id = m.message_id
    if old_id:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=old_id)
        except Exception:  # noqa: BLE001
            pass


async def _rerender_location(context: ContextTypes.DEFAULT_TYPE, chat_id: int,
                             value: str) -> str | None:
    """د وروستي پکیج انځورونه د نوي موقعیت سره بیا جوړ او ولیږه.

    خطا (د پښتو پیغام) راګرځوي که بیا جوړول ونشي، نه‌نو None.
    """
    state = _state(context)
    if not state.last_pkg_dir:
        return "⚠️ وروستی پکیج نه دی پاتې — لومړی یو نوی پکیج جوړ کړئ."
    pkg_dir = Path(state.last_pkg_dir)
    result = pkg_dir / "result.json"
    if not result.exists():
        return "⚠️ د پکیج معلومات ورک دي."
    data = json.loads(result.read_text(encoding="utf-8"))
    # «اتومات» = هغه څه چې جیمنای پخپله ټاکلي وو (په result.json کې خوندي دي).
    data["location"] = (state.last_detected_location or data.get("location", "")
                        ) if value == "auto" else value

    news_img = pkg_dir / "image.jpg"
    news_path = str(news_img) if news_img.exists() else None
    loop = asyncio.get_running_loop()
    paths: list[Path] = []
    for lang in LANGS:
        if not (data.get(lang["title_key"]) or "").strip():
            continue
        try:
            paths.append(await loop.run_in_executor(
                None, design_render.render_one, lang, data, news_path, pkg_dir,
                state.config))
        except Exception as err:  # noqa: BLE001
            logger.error("[%s] د بیا-جوړولو ناکامي: %s", lang["code"], err, exc_info=True)
    if not paths:
        return "⚠️ انځورونه بیا جوړ نشول."
    await _send_designs(context, chat_id, paths, state.last_reply_to)
    logger.info("د موقعیت بدلون → %s، %d انځورونه بیا ولیږل شول", value, len(paths))
    return None


# د دورې د پای پیغام — کیبورډ یې راوړي. لنډ ساتل شوی دی.
MENU_RESET_TEXT = "🛠 چمتو یم — د نوي پکیج لپاره یو حالت وټاکئ."


async def _restore_menu(context: ContextTypes.DEFAULT_TYPE, chat_id: int,
                        text: str | None = None) -> None:
    """منو بیرته اصلي حالت ته وګرځوه.

    ⚠️ ټلګرام د بوټ-کیبورډ یوازې د یوه **پاتې کېدونکي** پیغام سره بدلولی شي.
    پخوا مې یو تش پیغام لیږه او سمدلاسه مې ړنګاوه — خو د پیغام ړنګول کیبورډ
    هم بېرته اخلي، نو منو ریسیټ نه کېده. اوس پیغام پاتې کیږي (لنډ دی).
    که ویلو ته څه وي (لکه د نشتې ژبې خبرتیا)، هماغه پیغام کیبورډ وړي — نو
    یو پیغام بس دی.
    """
    state = _state(context)
    old_id = state.menu_msg_id
    try:
        m = await context.bot.send_message(
            chat_id, text or MENU_RESET_TEXT, reply_markup=main_keyboard()
        )
        state.menu_msg_id = m.message_id
        state.count_msg_id = None      # شمېرې-کیبورډ نور نشته
    except Exception as err:  # noqa: BLE001 - منو بیا ټاکل حیاتي نه دي
        logger.debug("د منو بیا ټاکل: %s", err)
        return
    # زوړ منو پیغام اوس ړنګولی شو — نوی یې دمخه کیبورډ ټاکلی دی.
    if old_id:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=old_id)
        except Exception:  # noqa: BLE001
            pass


def _track(state: BotState, *message_ids) -> None:
    """د منو د اړیکو پیغام یاد کړه — د بریالۍ دورې وروسته ړنګیږي."""
    for mid in message_ids:
        if mid:
            state.chatter.append(mid)


async def _purge_chatter(context: ContextTypes.DEFAULT_TYPE, chat_id: int,
                         state: BotState) -> None:
    """د منو ټول اړیکه-پیغامونه ړنګ کړه — د پکیج **محتوا** نه لمس کوي.

    یو `deleteMessages` کال کاروو (نه فی-پیغام)، ځکه د نرخ محدودوونکی هر کال
    ~۳ ثانیې ځنډوي — ۶ جلا کالونه به ~۱۸ ثانیې نیولې وای.
    """
    ids = [m for m in dict.fromkeys(state.chatter) if m]
    if not ids:
        return
    try:
        await context.bot.delete_messages(chat_id=chat_id, message_ids=ids)
        logger.info("د منو %d اړیکه-پیغامونه ړنګ شول", len(ids))
    except Exception as err:  # noqa: BLE001 - ښایي ډیر زاړه وي یا اجازه نه وي
        logger.debug("د منو پیغامونو ړنګول: %s", err)
    state.chatter.clear()


def _state(context: ContextTypes.DEFAULT_TYPE) -> BotState:
    return context.application.bot_data["state"]


def _is_authorized(state: BotState, update: Update) -> bool:
    """که د مجازو لیست خالي وي، هرڅوک اجازه لري؛ نه نو یوازې هغه ID ګانې.

    Empty allow-list means everyone may use the bot.
    """
    allowed = state.config.admin_user_ids
    if not allowed:
        return True
    user = update.effective_user
    return bool(user and user.id in allowed)


# د غیر مجازو کارونکو د ځواب throttle (فی کارونکی) چې flood ونشي.
_FUNNY_COOLDOWN = 8.0


async def gatekeeper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """د مجازو لیست پلي کوي — غیر مجاز کارونکي ته طنزي ځواب، بیا دروي.

    Runs in the first handler group. Authorized users pass through; everyone
    else gets a random funny reply (throttled) and the update stops here.
    """
    state = _state(context)
    if _is_authorized(state, update):
        return  # مجاز — نور handlers دې کار وکړي
    user = update.effective_user
    now = time.monotonic()
    last = context.bot_data.setdefault("_funny_last", {})
    if user and now - last.get(user.id, 0.0) >= _FUNNY_COOLDOWN:
        last[user.id] = now
        try:
            name = user.mention_html() if user else "کاروونکی"
            chat = update.effective_chat
            if chat:
                await context.bot.send_message(chat.id, funny.random_message(name), parse_mode="HTML")
        except Exception as err:  # noqa: BLE001
            logger.debug("د طنزي ځواب لیږل: %s", err)
    if update.callback_query:
        try:
            await update.callback_query.answer("🚫 اجازه نلرې", show_alert=False)
        except Exception:  # noqa: BLE001
            pass
    raise ApplicationHandlerStop


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """اصلي منو وښیه (د بوټ کیبورډ بټنې)."""
    await update.effective_message.reply_text(
        "🤖 ښه راغلاست! یو حالت وټاکئ:",
        reply_markup=main_keyboard(),
    )


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """د بوټ کیبورډ بټنو اداره — د دستورونو پر ځای."""
    msg = update.effective_message
    text = match_button(msg.text) or (msg.text or "").strip()
    state = _state(context)
    _track(state, msg.message_id)          # د کارونکي بټن-پیغام

    async def reply(body: str, **kw):
        sent = await msg.reply_text(body, **kw)
        _track(state, sent.message_id)     # د رباټ ځواب
        return sent

    if text == BTN_NEWS:
        state.reset()
        state.mode = "news"
        state.listening = True
        logger.info("د خبر حالت فعال شو (بټن)")
        await reply(
            "📰 د خبر حالت فعال شو — اوس انځور او متنونه ولیږئ، بیا «✅ بشپړول» ووهئ.",
            reply_markup=active_keyboard(0),
        )
    elif text == BTN_URGENT:
        state.reset()
        state.mode = "urgent"
        state.listening = True
        logger.info("د عاجل حالت فعال شو (بټن)")
        await reply(
            "🔴 د عاجل حالت فعال شو — یوازې تر ۳ متني راپورونه ولیږئ (بې انځوره)، بیا «✅ بشپړول» ووهئ.",
            reply_markup=active_keyboard(0),
        )
    elif text == BTN_QUOTE:
        state.reset()
        state.mode = "quote"
        state.listening = True
        logger.info("د نقل قول حالت فعال شو (بټن)")
        await reply(
            "💬 د نقل قول حالت فعال شو — د شخص انځور + تر ۳ راپورونه ولیږئ، بیا «✅ بشپړول» ووهئ.",
            reply_markup=active_keyboard(0),
        )
    elif text == BTN_BACK:
        state.reset()
        state.listening = False
        state.mode = "news"
        await reply("🏠 اصلي منو", reply_markup=main_keyboard())
    elif text == BTN_COMPLETE:
        await cmd_cmp(update, context)
    elif text == BTN_CLEAN:
        await cmd_clean(update, context)
    elif text == BTN_CANCEL:
        await cmd_cnl(update, context)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "🤖 د دستورونو لیست:\n\n"
        "/on — د پیغامونو غوږ نیول پیل کړه\n"
        "/off — غوږ نیول ودروه (موقت)\n"
        "/cmp — پکیج بشپړ او پروسس کړه\n"
        "/cnl — روان عملیات لغوه کړه\n"
        "/clean — بفر پاک کړه\n"
        "/status — اوسنی حالت وښیه\n"
        "/help — دا مرسته"
    )


async def cmd_on(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """رباټ فعال کړه چې د پیغامونو غوږ شي.  Start listening for messages."""
    state = _state(context)
    if not _is_authorized(state, update):
        return
    if state.listening:
        await update.effective_message.reply_text("👂 رباټ دمخه فعال دی.")
        return
    state.listening = True
    logger.info("غوږ نیول فعال شو (/on)")
    await update.effective_message.reply_text(
        "👂 فعال شوم — اوس د پیغامونو غوږ نیسم. محتوا ولیږئ."
    )


async def cmd_off(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """رباټ موقتاً غیر فعال کړه چې نور پیغامونو ته غوږ نشي.  Pause listening."""
    state = _state(context)
    if not _is_authorized(state, update):
        return
    if not state.listening:
        await update.effective_message.reply_text("🔇 رباټ دمخه غیر فعال دی.")
        return
    state.listening = False
    logger.info("غوږ نیول غیر فعال شو (/off)")
    await update.effective_message.reply_text(
        "🔇 غیر فعال شوم — نور پیغامونو ته غوږ نه یم. د بیا پیل لپاره /on ووهئ."
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _state(context)
    imgs, txts = state.counts()
    mode = "فعال 👂" if state.listening else "غیر فعال 🔇"
    # د کوټې کولډاون — کوم مډل/کیلي اوس پرېښودل کیږي او تر کله.
    blocked = gemini.quota.status()
    quota_line = ""
    if blocked:
        quota_line = "\n\n🚦 د کوټې کولډاون:\n" + "\n".join(f"• {b}" for b in blocked[:6])
        if len(blocked) > 6:
            quota_line += f"\n• … او {len(blocked) - 6} نور"
    if not state.buffer:
        await update.effective_message.reply_text(
            f"🎧 حالت: {mode}\n📭 بفر خالي دی.{quota_line}"
        )
        return
    lines = [f"🎧 حالت: {mode}",
             f"📦 اوسنی بفر — {len(state.buffer)}/{state.config.max_items} توکي",
             f"🖼 انځورونه: {imgs}   📝 متنونه: {txts}", ""]
    for i, item in enumerate(state.buffer, 1):
        if item.type == "text":
            preview = (item.content[:40] + "…") if len(item.content) > 40 else item.content
            lines.append(f"{i}. 📝 {preview}")
        else:
            lines.append(f"{i}. 🖼 (انځور)")
    if state.processing:
        lines.append("\n⏳ پروسس روان دی…")
    await update.effective_message.reply_text("\n".join(lines) + quota_line)


async def _clear_buffer(update: Update, context: ContextTypes.DEFAULT_TYPE,
                       body: str) -> None:
    """بفر پاک کړه او د «بشپړول» بټن شمېره بېرته ۰ ته وګرځوه."""
    state = _state(context)
    old_id = state.count_msg_id
    state.reset()
    sent = await update.effective_message.reply_text(
        body, reply_markup=active_keyboard(0) if state.listening else None
    )
    _track(state, sent.message_id)
    if state.listening:
        state.count_msg_id = sent.message_id
        if old_id and old_id != sent.message_id:
            try:
                await context.bot.delete_message(
                    chat_id=update.effective_chat.id, message_id=old_id)
            except Exception:  # noqa: BLE001
                pass


async def cmd_clean(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info("بفر پاک شو (/clean)")
    await _clear_buffer(update, context,
                        f"{_state(context).config.reaction_clean} بفر پاک شو.")


async def cmd_cnl(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info("عملیات لغوه شو (/cnl)")
    await _clear_buffer(update, context,
                        f"{_state(context).config.reaction_reject} عملیات لغوه شو، بفر پاک شو.")


async def cmd_cmp(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """پکیج **سمدلاسه** پروسس کړه.

    ⚠️ پخوا دلته یو «📦 د پکیج خلاصه» پیغام او شیشه‌یي «✅ تایید / ❌ لغوه»
    بټنې وې. کارونکي یې دوه ځله وهل غوښتل، حال دا چې «✅ بشپړول» پخپله د
    تایید کار کوي — نو هغه ګام لرې شو.
    """
    state = _state(context)
    if not _is_authorized(state, update):
        return
    msg = update.effective_message

    async def warn(body: str) -> None:
        sent = await msg.reply_text(body)
        _track(state, sent.message_id)

    if state.processing:
        await warn("⏳ یو پروسس دمخه روان دی، صبر وکړئ.")
        return
    if not state.buffer:
        await warn("⚠️ بفر خالي دی — لومړی محتوا ولیږئ.")
        return
    errors = validate_package(state)
    if errors:
        await warn("⚠️ د اعتبار ستونزه:\n- " + "\n- ".join(errors))
        return

    logger.info("پکیج پیل شو (بې د تایید ګام) — %d توکي", len(state.buffer))
    await run_pipeline(context, msg.chat_id)


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """د شیشه‌یي بټنو ځواب — اوس یوازې د موقعیت لاسي انتخاب.

    ⚠️ د تایید ګام لرې شوی دی؛ که د **زړو** پیغامونو بټن ووهل شي، یوازې
    ځواب ورکوو او بټنې لرې کوو، نه‌نو بټن تل «loading» پاتې کیږي.
    """
    q = update.callback_query
    if not q:
        return
    data = q.data or ""
    state = _state(context)

    if data.startswith("loc:"):
        value = data[4:]
        if value not in LOCATION_LABELS:
            await q.answer("نامعلوم موقعیت")
            return
        if value == state.last_location:
            await q.answer("دمخه همدا ټاکل شوی دی")
            return
        if state.processing:
            await q.answer("⏳ یو پروسس روان دی", show_alert=True)
            return
        await q.answer("⏳ بیا جوړیږي…")
        state.last_location = value
        detected = state.last_detected_location
        try:
            await q.edit_message_text(
                _location_text(value, detected),
                reply_markup=location_keyboard(value), parse_mode="Markdown")
        except Exception:  # noqa: BLE001
            pass
        err = await _rerender_location(context, q.message.chat_id, value)
        if err:
            try:
                await context.bot.send_message(q.message.chat_id, err)
            except Exception:  # noqa: BLE001
                pass
        return

    await q.answer()
    try:
        await q.edit_message_reply_markup(reply_markup=None)
    except Exception:  # noqa: BLE001 - پیغام ښایي دمخه ړنګ شوی وي
        pass


async def _render_designs(context, data: dict, news_image, pkg_dir: Path, state: BotState, prog: Progress) -> list[Path]:
    """درې ژبې په executor کې جوړ کړه (CPU-bound → loop نه بلاکوي)."""
    loop = asyncio.get_running_loop()
    paths: list[Path] = []
    for lang in LANGS:
        # یوازې هغه ژبې چې عنوان لري (راپور یې لیږل شوی) ډیزاین کیږي.
        if not (data.get(lang["title_key"]) or "").strip():
            logger.info("[%s] عنوان نشته — دا ژبه پریښودل شوه", lang["code"])
            continue
        await prog.step(_LANG_STEP[lang["code"]])
        try:
            path = await loop.run_in_executor(
                None, design_render.render_one, lang, data, news_image, pkg_dir, state.config
            )
            paths.append(path)
        except Exception as err:  # noqa: BLE001 - یوه ژبه نه باید نور ودروي
            logger.error("[%s] د ډیزاین ناکامي: %s", lang["code"], err, exc_info=True)
    return paths


async def _send_designs(context, chat_id: int, paths: list[Path],
                        reply_to: int | None = None) -> None:
    """انځورونه د **سند** په بڼه ولیږه — د پکیج لومړي متني پیغام ته ریپلای.

    Sent as a reply to the package's first text message (usually the Pashto
    report). If that message is gone, Telegram rejects the reply — so we retry
    once without it rather than losing the whole delivery.
    """
    def _media():
        return [InputMediaDocument(media=open(p, "rb"), filename=p.name) for p in paths]

    async def _send(**extra):
        """د flood پر مهال صبر وکړه او بیا هڅه — فایلونه هیڅکله مه غورځوه."""
        last: Exception | None = None
        for attempt in range(1, 4):
            try:
                await context.bot.send_media_group(chat_id=chat_id, media=_media(), **extra)
                return
            except RetryAfter as err:
                last = err
                wait = float(getattr(err, "retry_after", 5)) + 1
                logger.warning("د لیږلو نرخ-حد — %.0f ثانیې صبر، بیا هڅه (%d/۳)",
                               wait, attempt)
                await asyncio.sleep(wait)
        raise last if last else RuntimeError("د فایلونو لیږل ناکام")

    if reply_to:
        try:
            await _send(reply_to_message_id=reply_to)
            return
        except Exception as err:  # noqa: BLE001 - ریپلای ناکام؛ بې ریپلای یې ولیږه
            logger.warning("ریپلای (msg %s) ناکام: %s — بې ریپلای لیږل کیږي", reply_to, err)
    await _send()


async def _render_urgent(context, titles: dict, pkg_dir: Path, state: BotState, prog: Progress) -> list[Path]:
    """د عاجل خبر انځورونه (یوازې عنوان + تاریخ) په executor کې جوړ کړه."""
    loop = asyncio.get_running_loop()
    paths: list[Path] = []
    for lang, title in titles.items():
        await prog.step(_URGENT_LANG_STEP.get(lang, 2))
        try:
            path = await loop.run_in_executor(
                None, design_urgent.render_urgent_one, lang, title, state.config, pkg_dir
            )
            paths.append(path)
        except Exception as err:  # noqa: BLE001
            logger.error("[%s] د عاجل ډیزاین ناکامي: %s", lang, err, exc_info=True)
    return paths


async def run_pipeline(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    """بشپړ pipeline — د حالت پر بنسټ (news یا urgent)."""
    state = _state(context)
    if state.mode == "urgent":
        await run_urgent_pipeline(context, chat_id)
        return
    if state.mode == "quote":
        await run_quote_pipeline(context, chat_id)
        return

    state.processing = True
    prog = Progress(
        context.bot, chat_id,
        min_interval=state.config.design.progress_min_interval,
        title="📰 خبر",
    )
    note: str | None = None
    purge = False
    await prog.start()
    try:
        await prog.step(0)  # پکیج ترلاسه شو
        reply_to = first_text_message_id(state)
        pkg_dir = save_package(state)
        texts = collect_texts(state)
        logger.info("پکیج خوندي شو → %s", pkg_dir)

        await prog.step(1)  # جیمنای
        data = await gemini.analyze(texts, state.config)
        (pkg_dir / "result.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        await prog.step(2)  # د خبر انځور
        news_img = pkg_dir / "image.jpg"
        news_path = str(news_img) if news_img.exists() else None

        paths = await _render_designs(context, data, news_path, pkg_dir, state, prog)
        if not paths:
            raise RuntimeError("هیڅ انځور جوړ نشو")

        await prog.step(7)  # لیږل
        # ⚠️ ژوندی پیغام **مخکې** له لیږلو ړنګوو: د نرخ محدودوونکی هر کال ~۳
        # ثانیې ځنډوي، نو تر فایلونو وروسته باید یوازې **یو** کال (منو) پاتې شي —
        # نه نو منو ناوخته ریسیټ کیږي.
        prog.pause()
        await prog.delete()
        await _send_designs(context, chat_id, paths, reply_to)
        logger.info("%d انځورونه ګروپ ته ولیږل شول", len(paths))
        done = [lg["code"] for lg in LANGS if (data.get(lg["title_key"]) or "").strip()]
        note = _missing_note(done)
        # د موقعیت لاسي کنټرول لپاره پکیج یاد وساته او شیشه‌یي منو وښیه.
        state.last_pkg_dir = str(pkg_dir)
        state.last_reply_to = reply_to
        state.last_location = "auto"
        state.last_detected_location = data.get("location", "")
        await show_location_menu(context, chat_id, state.last_detected_location)
        purge = True
    except Exception as err:  # noqa: BLE001
        logger.error("د pipeline خطا: %s", err, exc_info=True)
        if prog.msg_id is not None:
            await prog.finish(ok=False, err=str(err))
        else:  # ژوندی پیغام دمخه ړنګ شوی — خطا په منو-پیغام کې ووایه
            note = f"❌ ناکام — {err}"
    finally:
        # د دورې وروسته بشپړ reset او بیرته اصلي پاڼې (منو) ته ورګرځي.
        state.reset()
        state.listening = False
        state.mode = "news"
        await _restore_menu(context, chat_id, note)
        if purge:
            await _purge_chatter(context, chat_id, state)


async def run_urgent_pipeline(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    """د عاجل خبر pipeline: ساتل → سرلیکونه → عاجل ډیزاین → لیږل → منو."""
    state = _state(context)
    state.processing = True
    prog = Progress(
        context.bot, chat_id,
        min_interval=state.config.design.progress_min_interval,
        steps=URGENT_PROGRESS_STEPS,
        title="🔴 عاجل خبر",
    )
    note: str | None = None
    purge = False
    await prog.start()
    try:
        await prog.step(0)
        reply_to = first_text_message_id(state)
        pkg_dir = save_package(state)
        texts = collect_texts(state)

        await prog.step(1)  # عاجل — بې AI، عنوان په کوډ کې راوباسه
        titles = gemini.extract_urgent_titles(texts)
        if not titles:
            raise RuntimeError("هیڅ سرلیک ترلاسه نشو")

        paths = await _render_urgent(context, titles, pkg_dir, state, prog)
        if not paths:
            raise RuntimeError("هیڅ عاجل انځور جوړ نشو")

        await prog.step(5)  # لیږل
        prog.pause()
        await prog.delete()
        await _send_designs(context, chat_id, paths, reply_to)
        logger.info("%d عاجل انځورونه ولیږل شول", len(paths))
        note = _missing_note(titles.keys())
        purge = True
    except Exception as err:  # noqa: BLE001
        logger.error("د عاجل pipeline خطا: %s", err, exc_info=True)
        if prog.msg_id is not None:
            await prog.finish(ok=False, err=str(err))
        else:  # ژوندی پیغام دمخه ړنګ شوی — خطا په منو-پیغام کې ووایه
            note = f"❌ ناکام — {err}"
    finally:
        state.reset()
        state.listening = False
        state.mode = "news"
        await _restore_menu(context, chat_id, note)
        if purge:
            await _purge_chatter(context, chat_id, state)


async def _render_quotes(context, quotes: dict, person, pkg_dir: Path, state: BotState, prog: Progress) -> list[Path]:
    """د نقل قول انځورونه (شخص + نقل قول + نوم + رتبه) په executor کې جوړ کړه."""
    loop = asyncio.get_running_loop()
    paths: list[Path] = []
    for lang, d in quotes.items():
        await prog.step(_URGENT_LANG_STEP.get(lang, 2))
        try:
            path = await loop.run_in_executor(
                None, design_quote.render_quote_one,
                lang, d["quote"], d["name"], d["rank"], person, state.config, pkg_dir,
            )
            paths.append(path)
        except Exception as err:  # noqa: BLE001
            logger.error("[%s] د نقل قول ډیزاین ناکامي: %s", lang, err, exc_info=True)
    return paths


async def run_quote_pipeline(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    """د نقل قول pipeline: ساتل → نوم/رتبه/نقل قول → ډیزاین → لیږل → منو."""
    state = _state(context)
    state.processing = True
    prog = Progress(
        context.bot, chat_id,
        min_interval=state.config.design.progress_min_interval,
        steps=QUOTE_PROGRESS_STEPS,
        title="💬 نقل قول",
    )
    note: str | None = None
    purge = False
    await prog.start()
    try:
        await prog.step(0)
        reply_to = first_text_message_id(state)
        pkg_dir = save_package(state)
        texts = collect_texts(state)

        await prog.step(1)  # Gemini — نوم/رتبه/نقل قول
        quotes = await gemini.analyze_quote(texts, state.config)
        if not quotes:
            raise RuntimeError("هیڅ نقل قول ترلاسه نشو")

        news_img = pkg_dir / "image.jpg"
        person = str(news_img) if news_img.exists() else None
        paths = await _render_quotes(context, quotes, person, pkg_dir, state, prog)
        if not paths:
            raise RuntimeError("هیڅ نقل قول انځور جوړ نشو")

        await prog.step(5)  # لیږل
        prog.pause()
        await prog.delete()
        await _send_designs(context, chat_id, paths, reply_to)
        logger.info("%d نقل قول انځورونه ولیږل شول", len(paths))
        note = _missing_note(quotes.keys())
        purge = True
    except Exception as err:  # noqa: BLE001
        logger.error("د نقل قول pipeline خطا: %s", err, exc_info=True)
        if prog.msg_id is not None:
            await prog.finish(ok=False, err=str(err))
        else:  # ژوندی پیغام دمخه ړنګ شوی — خطا په منو-پیغام کې ووایه
            note = f"❌ ناکام — {err}"
    finally:
        state.reset()
        state.listening = False
        state.mode = "news"
        await _restore_menu(context, chat_id, note)
        if purge:
            await _purge_chatter(context, chat_id, state)
