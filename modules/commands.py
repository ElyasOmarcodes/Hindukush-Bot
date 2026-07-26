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
from telegram.ext import ApplicationHandlerStop, ContextTypes

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
from .progress import QUOTE_PROGRESS_STEPS, URGENT_PROGRESS_STEPS, Progress

logger = logging.getLogger("commands")

# د هرې ژبې د پرمختګ ګام (index) — خبر او عاجل جلا لیستونه لري.
_LANG_STEP = {"ps": 3, "fa": 4, "en": 5}
_URGENT_LANG_STEP = {"ps": 2, "fa": 3, "en": 4}

# ─────────────── د بوټ کیبورډ بټنې (منو) ───────────────
# د دستورونو پر ځای ښکلې بټنې — چې غیر مسلکي کسان هم اسانه کار وکړي.
# یادونه: ټلګرام د کیبورډ بټنو رنګ نه مني — نو د «سور» لپاره 🔴 ایموجي کاروو.
BTN_CREATE = "🛠 جوړول"
BTN_NEWS = "📰 خبر"
BTN_URGENT = "🔴 عاجل"
BTN_QUOTE = "💬 نقل قول"
BTN_BACK = "◀️ بیرته شاته"
BTN_COMPLETE = "✅ بشپړول"
BTN_CLEAN = "🗑 پاکول"
BTN_CANCEL = "🔴 لغوه"

# ټول د بټنو لیبلونه — چې monitor یې د پکیج محتوا ونه ګڼي.
BUTTON_LABELS = [
    BTN_CREATE, BTN_NEWS, BTN_URGENT, BTN_QUOTE,
    BTN_BACK, BTN_COMPLETE, BTN_CLEAN, BTN_CANCEL,
]


def _kb(rows: list[list[str]]) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[KeyboardButton(b) for b in row] for row in rows],
        resize_keyboard=True,
        is_persistent=True,
    )


def main_keyboard() -> ReplyKeyboardMarkup:
    """اصلي منو — یوازې «جوړول»."""
    return _kb([[BTN_CREATE]])


def create_keyboard() -> ReplyKeyboardMarkup:
    """د «جوړول» فرعي منو."""
    return _kb([[BTN_NEWS, BTN_URGENT], [BTN_QUOTE], [BTN_BACK]])


def active_keyboard() -> ReplyKeyboardMarkup:
    """د «خبر» فعال حالت منو."""
    return _kb([[BTN_COMPLETE], [BTN_CLEAN, BTN_CANCEL], [BTN_BACK]])


# د ژبو پښتو نومونه — د «کومه ژبه ونه لیږل شوه» خبرتیا لپاره.
LANG_NAMES = {"ps": "پښتو", "fa": "فارسي", "en": "انګلیسي"}
ALL_LANGS = ("ps", "fa", "en")


def missing_langs(done) -> list[str]:
    """هغه ژبې چې راپور یې نه دی راغلی — د پښتو نومونو لیست."""
    have = set(done)
    return [LANG_NAMES[code] for code in ALL_LANGS if code not in have]


async def _notify_missing(context: ContextTypes.DEFAULT_TYPE, chat_id: int, done) -> None:
    """پکیج تل ۳ متني راپورونو ته اړتیا لري — د نشتو ژبو خبرتیا ورکړه.

    Languages with no report are simply not designed; tell the user which ones
    were missing so nothing fails silently.
    """
    names = missing_langs(done)
    if not names:
        return
    logger.info("د دې ژبو راپور نه و راغلی: %s", "، ".join(names))
    await context.bot.send_message(
        chat_id,
        "ℹ️ د «" + "» او «".join(names) + "» ژبې راپور مو نه دی راستولی — "
        "نو د هغې ژبې ډیزاین جوړ نشو.",
    )


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
        "🤖 ښه راغلاست! د پیل لپاره «🛠 جوړول» ووهئ.",
        reply_markup=main_keyboard(),
    )


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """د بوټ کیبورډ بټنو اداره — د دستورونو پر ځای."""
    text = (update.effective_message.text or "").strip()
    state = _state(context)

    if text == BTN_CREATE:
        await update.effective_message.reply_text(
            "یو انتخاب وکړئ:", reply_markup=create_keyboard()
        )
    elif text == BTN_NEWS:
        state.reset()
        state.mode = "news"
        state.listening = True
        logger.info("د خبر حالت فعال شو (بټن)")
        await update.effective_message.reply_text(
            "📰 د خبر حالت فعال شو — اوس انځور او متنونه ولیږئ، بیا «✅ بشپړول» ووهئ.",
            reply_markup=active_keyboard(),
        )
    elif text == BTN_URGENT:
        state.reset()
        state.mode = "urgent"
        state.listening = True
        logger.info("د عاجل حالت فعال شو (بټن)")
        await update.effective_message.reply_text(
            "🔴 د عاجل حالت فعال شو — یوازې تر ۳ متني راپورونه ولیږئ (بې انځوره)، بیا «✅ بشپړول» ووهئ.",
            reply_markup=active_keyboard(),
        )
    elif text == BTN_QUOTE:
        state.reset()
        state.mode = "quote"
        state.listening = True
        logger.info("د نقل قول حالت فعال شو (بټن)")
        await update.effective_message.reply_text(
            "💬 د نقل قول حالت فعال شو — د شخص انځور + تر ۳ راپورونه ولیږئ، بیا «✅ بشپړول» ووهئ.",
            reply_markup=active_keyboard(),
        )
    elif text == BTN_BACK:
        state.reset()
        state.listening = False
        state.mode = "news"
        await update.effective_message.reply_text(
            "🏠 اصلي منو", reply_markup=main_keyboard()
        )
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
    if not state.buffer:
        await update.effective_message.reply_text(f"🎧 حالت: {mode}\n📭 بفر خالي دی.")
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
    await update.effective_message.reply_text("\n".join(lines))


async def cmd_clean(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _state(context)
    state.reset()
    logger.info("بفر پاک شو (/clean)")
    await update.effective_message.reply_text(f"{state.config.reaction_clean} بفر پاک شو.")


async def cmd_cnl(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _state(context)
    state.reset()
    logger.info("عملیات لغوه شو (/cnl)")
    await update.effective_message.reply_text(f"{state.config.reaction_reject} عملیات لغوه شو، بفر پاک شو.")


async def cmd_cmp(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """د بفر خلاصه وښیه او د تایید شیشه‌یي بټن ورکړه."""
    state = _state(context)
    if not _is_authorized(state, update):
        return
    if state.processing:
        await update.effective_message.reply_text("⏳ یو پروسس دمخه روان دی، صبر وکړئ.")
        return
    if not state.buffer:
        await update.effective_message.reply_text("⚠️ بفر خالي دی — لومړی محتوا ولیږئ.")
        return

    imgs, txts = state.counts()
    text = (
        "📦 *د پکیج خلاصه*\n\n"
        f"🖼 انځورونه: *{imgs}*\n"
        f"📝 متنونه: *{txts}*\n"
        f"📊 ټول توکي: *{len(state.buffer)}/{state.config.max_items}*\n\n"
        "تایید کوئ چې پروسس پیل شي؟"
    )
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ تایید — پیل کړه", callback_data="cmp_confirm"),
        InlineKeyboardButton("❌ لغوه", callback_data="cmp_cancel"),
    ]])
    await update.effective_message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """د شیشه‌یي بټن ځواب — تایید یا لغوه."""
    q = update.callback_query
    await q.answer()  # ⚠️ حتماً — که نه، بټن "loading" پاتې کیږي
    state = _state(context)

    if q.data == "cmp_cancel":
        await q.edit_message_text("❌ لغوه شو.")
        return

    if q.data == "cmp_confirm":
        if state.processing:
            await q.edit_message_text("⏳ یو پروسس دمخه روان دی.")
            return
        errors = validate_package(state)
        if errors:
            await q.edit_message_text("⚠️ د اعتبار ستونزه:\n- " + "\n- ".join(errors))
            return
        await q.edit_message_text("⏳ پروسس پیل شو…")
        await run_pipeline(context, q.message.chat_id)


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

    if reply_to:
        try:
            await context.bot.send_media_group(
                chat_id=chat_id, media=_media(), reply_to_message_id=reply_to,
            )
            return
        except Exception as err:  # noqa: BLE001 - ریپلای ناکام؛ بې ریپلای یې ولیږه
            logger.warning("ریپلای (msg %s) ناکام: %s — بې ریپلای لیږل کیږي", reply_to, err)
    await context.bot.send_media_group(chat_id=chat_id, media=_media())


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
    prog = Progress(context.bot, chat_id, min_interval=state.config.design.progress_min_interval)
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
        await _send_designs(context, chat_id, paths, reply_to)
        logger.info("%d انځورونه ګروپ ته ولیږل شول", len(paths))
        await prog.finish(ok=True)
        done = [lg["code"] for lg in LANGS if (data.get(lg["title_key"]) or "").strip()]
        await _notify_missing(context, chat_id, done)
    except Exception as err:  # noqa: BLE001
        logger.error("د pipeline خطا: %s", err, exc_info=True)
        await prog.finish(ok=False, err=str(err))
    finally:
        # د دورې وروسته بشپړ reset او بیرته اصلي پاڼې (منو) ته ورګرځي.
        state.reset()
        state.listening = False
        state.mode = "news"
        await context.bot.send_message(
            chat_id, "✅ بشپړ شو — د نوي پکیج لپاره «🛠 جوړول» ووهئ.",
            reply_markup=main_keyboard(),
        )


async def run_urgent_pipeline(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    """د عاجل خبر pipeline: ساتل → سرلیکونه → عاجل ډیزاین → لیږل → منو."""
    state = _state(context)
    state.processing = True
    prog = Progress(
        context.bot, chat_id,
        min_interval=state.config.design.progress_min_interval,
        steps=URGENT_PROGRESS_STEPS,
    )
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
        await _send_designs(context, chat_id, paths, reply_to)
        logger.info("%d عاجل انځورونه ولیږل شول", len(paths))
        await prog.finish(ok=True)
        await _notify_missing(context, chat_id, titles.keys())
    except Exception as err:  # noqa: BLE001
        logger.error("د عاجل pipeline خطا: %s", err, exc_info=True)
        await prog.finish(ok=False, err=str(err))
    finally:
        state.reset()
        state.listening = False
        state.mode = "news"
        await context.bot.send_message(
            chat_id, "✅ بشپړ شو — د نوي پکیج لپاره «🛠 جوړول» ووهئ.",
            reply_markup=main_keyboard(),
        )


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
    )
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
        await _send_designs(context, chat_id, paths, reply_to)
        logger.info("%d نقل قول انځورونه ولیږل شول", len(paths))
        await prog.finish(ok=True)
        await _notify_missing(context, chat_id, quotes.keys())
    except Exception as err:  # noqa: BLE001
        logger.error("د نقل قول pipeline خطا: %s", err, exc_info=True)
        await prog.finish(ok=False, err=str(err))
    finally:
        state.reset()
        state.listening = False
        state.mode = "news"
        await context.bot.send_message(
            chat_id, "✅ بشپړ شو — د نوي پکیج لپاره «🛠 جوړول» ووهئ.",
            reply_markup=main_keyboard(),
        )
