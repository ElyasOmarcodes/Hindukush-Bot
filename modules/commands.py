"""دستورونه او د پروسس pipeline.

Slash commands (/on /off /cmp /cnl /clean /status /help), the /cmp inline
confirmation keyboard, its callback, and the end-to-end processing pipeline
(save → Gemini → render 3 designs → send documents → reset).
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaDocument, Update
from telegram.ext import ContextTypes

from . import gemini
from .design import render as design_render
from .design.layout import LANGS
from .package import BotState, collect_texts, save_package, validate_package
from .progress import Progress

logger = logging.getLogger("commands")

# د هرې ژبې د پرمختګ ګام (د PROGRESS_STEPS index).
_LANG_STEP = {"ps": 3, "fa": 4, "en": 5}


def _state(context: ContextTypes.DEFAULT_TYPE) -> BotState:
    return context.application.bot_data["state"]


def _is_authorized(state: BotState, update: Update) -> bool:
    """که د اډمینانو لیست خالي وي، هرڅوک اجازه لري؛ نه نو یوازې اډمینان.

    Empty admin list means everyone in the group may use commands.
    """
    admins = state.config.admin_user_ids
    if not admins:
        return True
    user = update.effective_user
    return bool(user and user.id in admins)


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
        await prog.step(_LANG_STEP[lang["code"]])
        try:
            path = await loop.run_in_executor(
                None, design_render.render_one, lang, data, news_image, pkg_dir, state.config
            )
            paths.append(path)
        except Exception as err:  # noqa: BLE001 - یوه ژبه نه باید نور ودروي
            logger.error("[%s] د ډیزاین ناکامي: %s", lang["code"], err, exc_info=True)
    return paths


async def _send_designs(context, chat_id: int, paths: list[Path]) -> None:
    """درې انځورونه د **سند** په بڼه ولیږه چې ټلګرام یې فشار نه کړي."""
    media = [InputMediaDocument(media=open(p, "rb"), filename=p.name) for p in paths]
    await context.bot.send_media_group(chat_id=chat_id, media=media)


async def run_pipeline(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    """بشپړ pipeline: ساتل → Gemini → ۳ ډیزاینه → د سند لیږل → reset."""
    state = _state(context)
    state.processing = True
    prog = Progress(context.bot, chat_id, min_interval=state.config.design.progress_min_interval)
    await prog.start()
    try:
        await prog.step(0)  # پکیج ترلاسه شو
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
        await _send_designs(context, chat_id, paths)
        logger.info("%d انځورونه ګروپ ته ولیږل شول", len(paths))
        await prog.finish(ok=True)
    except Exception as err:  # noqa: BLE001
        logger.error("د pipeline خطا: %s", err, exc_info=True)
        await prog.finish(ok=False, err=str(err))
    finally:
        # د دورې وروسته بشپړ reset — حتی که خطا راشي (listening نه بدلوي).
        state.reset()
        await context.bot.send_message(chat_id, "🔄 چمتو — نوی پکیج ولیږئ.")
