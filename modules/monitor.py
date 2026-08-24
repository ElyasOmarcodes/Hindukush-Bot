"""د ګروپ څارنه — پیغامونه نیول، ری‌اکشن ورکول، د البوم اداره.

Group monitoring: capture text / photo / image-document messages into the
buffer, give each an ordered reaction, and correctly assemble albums that
Telegram delivers as separate updates sharing one ``media_group_id``.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from telegram import ReactionTypeEmoji, Update
from telegram.ext import ContextTypes

from .package import BotState, BufferItem, can_add
from .utils import ensure_dirs, split_whatsapp

logger = logging.getLogger("monitor")

# څومره ثانیې د البوم د نورو برخو راتلو ته انتظار وکړو.
ALBUM_SETTLE_SECONDS = 1.2


def _state(context: ContextTypes.DEFAULT_TYPE) -> BotState:
    return context.application.bot_data["state"]


# ټلګرام یوازې ځینې ایموجي د ری‌اکشن په توګه مني (لکه 1️⃣2️⃣3️⃣ نه مني). کوم چې
# یو ځل رد شي، بیا یې نه ازمویو — نه بې‌ګټې API کال، نه د لاګ شور.
_REJECTED_EMOJI: set = set()


async def react(context, chat_id: int, message_id: int, emoji: str) -> bool:
    """یو ری‌اکشن ورکړه؛ که ونه منل شي، False راوګرځوه (loop نه ماتوي).

    Set a single reaction. Telegram only allows certain emojis; a rejected one
    is remembered so we never waste another call on it.
    """
    if emoji in _REJECTED_EMOJI:
        return False
    try:
        await context.bot.set_message_reaction(
            chat_id=chat_id,
            message_id=message_id,
            reaction=[ReactionTypeEmoji(emoji=emoji)],
        )
        return True
    except Exception as err:  # noqa: BLE001
        if "REACTION_INVALID" in str(err).upper().replace(" ", "_"):
            _REJECTED_EMOJI.add(emoji)
            logger.info("ټلګرام «%s» د ری‌اکشن په توګه نه مني — بیا یې نه ازمویم", emoji)
        else:
            logger.warning("ری‌اکشن ونه منل شو (%s): %s", emoji, err)
        return False


async def react_ordered(context, state: BotState, chat_id: int, message_id: int, index: int) -> None:
    """د توکي د ترتیب پر بنسټ شمېره-ری‌اکشن ورکړه، بدیل ته fallback.

    React with the position number (1️⃣…); if Telegram rejects it, fall back to
    the safe reaction set from config.
    """
    cfg = state.config
    seq = cfg.reaction_sequence
    fallback = cfg.reaction_fallback
    if index < len(seq) and await react(context, chat_id, message_id, seq[index]):
        return
    if index < len(fallback):
        await react(context, chat_id, message_id, fallback[index])


async def _add_text(context, state: BotState, msg) -> None:
    """متن بفر ته ور اضافه کړه — یو پیغام کیدای شي **څو راپورونه** ولري.

    ⚠️ اداره کې څو اډمینان په واټساپ کې راپورونه لیکي، نو کارونکی دری واړه
    یوځای کاپي کوي او په یوه ټلګرام پیغام کې یې لیږي. که د واټساپ سرلیکونه
    («[۸/۲۴, ۲:۰۳ ب.ظ] نوم:») ولري، دلته یې جلا راپورونو ته بېلوو.
    """
    raw = msg.text or msg.caption or ""
    parts = split_whatsapp(raw)
    if len(parts) > 1:
        logger.info("پیغام #%d کې %d راپورونه وموندل شول (واټساپ)", msg.message_id, len(parts))

    last_index: int | None = None
    for part in parts:
        ok, reason = can_add(state, "text")
        if not ok:
            logger.info("متن رد شو: %s", reason)
            break
        last_index = len(state.buffer)
        state.buffer.append(
            BufferItem(type="text", message_id=msg.message_id, content=part)
        )
        logger.info("پیغام #%d → بفر (متن) — توکی %d", msg.message_id, last_index + 1)

    if last_index is None:                 # هیڅ برخه ونه منل شوه
        await react(context, msg.chat_id, msg.message_id, state.config.reaction_reject)
        return
    # یو ری‌اکشن بس دی — ټلګرام یوازې وروستی ساتي، نو د **اخري** توکي شمېره ښیو.
    await react_ordered(context, state, msg.chat_id, msg.message_id, last_index)


async def _add_image(context, state: BotState, msg) -> None:
    ok, reason = can_add(state, "image")
    if not ok:
        logger.info("انځور رد شو: %s", reason)
        await react(context, msg.chat_id, msg.message_id, state.config.reaction_reject)
        return

    # تر ټولو لوړ کیفیت نسخه واخله (photo[-1]) یا د document اصلي فایل.
    ensure_dirs(state.config.pending_dir)
    pending_path = Path(state.config.pending_dir) / f"{msg.message_id}.jpg"
    try:
        if msg.photo:
            tg_file = await msg.photo[-1].get_file()
        else:  # د image document — هیڅ فشار نه لري (بشپړ کیفیت)
            tg_file = await msg.document.get_file()
        await tg_file.download_to_drive(str(pending_path))
    except Exception as err:  # noqa: BLE001
        logger.error("د انځور ښکته کول ناکام: %s", err)
        await react(context, msg.chat_id, msg.message_id, state.config.reaction_reject)
        return

    index = len(state.buffer)
    state.buffer.append(
        BufferItem(type="image", message_id=msg.message_id, path=str(pending_path))
    )
    logger.info("پیغام #%d → بفر (انځور) — توکی %d", msg.message_id, index + 1)
    await react_ordered(context, state, msg.chat_id, msg.message_id, index)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """د ګروپ د راغلي پیغام اصلي دروازه.  Main entry for a group message."""
    msg = update.effective_message
    if msg is None:
        return
    state = _state(context)

    # که رباټ غیر فعال وي، پیغامونه هیڅ مه نیسه (د /on تر مخه).
    if not state.listening:
        return

    # د پروسس پر مهال نوي توکي مه نیسه.
    if state.processing:
        logger.info("پروسس روان دی — پیغام #%d له پامه غورځول شو", msg.message_id)
        return

    # ---- متن ----
    if msg.text and not msg.photo and not msg.document:
        await _add_text(context, state, msg)
        return

    # ---- انځور (photo یا د image document) ----
    is_image_doc = bool(msg.document and (msg.document.mime_type or "").startswith("image/"))
    if msg.photo or is_image_doc:
        # عاجل حالت یوازې متن مني — انځور رد کړه.
        if state.mode == "urgent":
            logger.info("عاجل حالت — انځور #%d رد شو", msg.message_id)
            await react(context, msg.chat_id, msg.message_id, state.config.reaction_reject)
            return
        mgid = msg.media_group_id
        if mgid is None:
            await _add_image(context, state, msg)
            return
        # د البوم برخه — راټول یې کړه او یو تایمر ورته کېږده.
        state.media_buffer[mgid].append(msg)
        if mgid in state.media_tasks:
            state.media_tasks[mgid].cancel()

        async def flush(group_id=mgid):
            try:
                await asyncio.sleep(ALBUM_SETTLE_SECONDS)
                msgs = state.media_buffer.pop(group_id, [])
                state.media_tasks.pop(group_id, None)
                logger.info("البوم %s: %d برخې راټولې شوې", group_id, len(msgs))
                for m in msgs:
                    await _add_image(context, state, m)
            except asyncio.CancelledError:  # نوې برخه راغله — تایمر بیا پیل شو
                pass

        state.media_tasks[mgid] = asyncio.create_task(flush())
