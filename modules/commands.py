"""دستورونه او د پروسس pipeline.

Slash commands (/cmp /cnl /clean /status /help), the /cmp inline confirmation
keyboard, its callback, and the end-to-end processing pipeline (save →
Gemini → send result → reset).
"""

from __future__ import annotations

import json
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from . import gemini
from .package import BotState, collect_texts, save_package, validate_package
from .utils import escape_markdown

logger = logging.getLogger("commands")


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
        "/cmp — پکیج بشپړ او پروسس کړه\n"
        "/cnl — روان عملیات لغوه کړه\n"
        "/clean — بفر پاک کړه\n"
        "/status — اوسنی حالت وښیه\n"
        "/help — دا مرسته"
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _state(context)
    imgs, txts = state.counts()
    if not state.buffer:
        await update.effective_message.reply_text("📭 بفر خالي دی.")
        return
    lines = [f"📦 اوسنی بفر — {len(state.buffer)}/{state.config.max_items} توکي",
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


async def run_pipeline(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    """بشپړ pipeline: ساتل → Gemini → پایله → reset."""
    state = _state(context)
    state.processing = True
    try:
        pkg_dir = save_package(state)
        logger.info("پکیج خوندي شو → %s", pkg_dir)

        texts = collect_texts(state)
        data = await gemini.analyze(texts, state.config)

        # پایله د JSON فایل په بڼه وساته.
        result_path = pkg_dir / "result.json"
        result_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

        # ---- پایله د متن په بڼه (Markdown escape شوی) ----
        loc = escape_markdown(data.get("location", ""))
        result_text = (
            "✅ *تحلیل بشپړ شو*\n\n"
            f"🌍 موقعیت: `{loc}`\n\n"
            f"🇦🇫 *پښتو*\nسرچینه: {escape_markdown(data.get('source_ps',''))}\n"
            f"سرلیک: {escape_markdown(data.get('title_ps',''))}\n\n"
            f"🇮🇷 *فارسي*\nسرچینه: {escape_markdown(data.get('source_fa',''))}\n"
            f"سرلیک: {escape_markdown(data.get('title_fa',''))}\n\n"
            f"🇬🇧 *English*\nSource: {escape_markdown(data.get('source_en',''))}\n"
            f"Title: {escape_markdown(data.get('title_en',''))}"
        )
        await context.bot.send_message(chat_id, result_text, parse_mode="Markdown")

        # ---- پایله د فایل په بڼه ----
        with open(result_path, "rb") as fh:
            await context.bot.send_document(
                chat_id=chat_id, document=fh, filename="result.json",
                caption="📄 د تحلیل پایله",
            )
        logger.info("پایله ګروپ ته ولیږل شوه")
    finally:
        # د دورې وروسته بشپړ reset — حتی که خطا راشي.
        state.reset()
        await context.bot.send_message(chat_id, "🔄 چمتو — نوی پکیج ولیږئ.")
