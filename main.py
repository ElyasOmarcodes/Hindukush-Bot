"""د NewsBot د پیل نقطه — handlers ثبتول او polling چلول.

Entry point for the Telegram NewsBot (Phase 1). Loads config, wires up the
group monitor, the slash commands and the callback handler, registers a global
error handler so a single failure never stops the loop, and starts polling.
"""

from __future__ import annotations

import logging

from telegram import BotCommand, Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from modules import commands, monitor
from modules.config import Config, load_config
from modules.package import BotState
from modules.utils import ensure_dirs, setup_logging

logger = logging.getLogger("main")

BOT_COMMANDS = [
    BotCommand("start", "منو پرانیستل 🛠"),
    BotCommand("menu", "منو پرانیستل 🛠"),
    BotCommand("status", "اوسنی حالت"),
    BotCommand("help", "مرسته"),
]


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """هره تېروتنه ونیسه — loop دوام کوي، رباټ نه ودریږي.

    Global error handler: log the error and, when possible, tell the chat that
    the bot is still alive. Never re-raise, so one failure can't kill the loop.
    """
    logger.error("خطا: %s", context.error, exc_info=context.error)
    try:
        if isinstance(update, Update) and update.effective_chat:
            await context.bot.send_message(
                update.effective_chat.id,
                "⚠️ یوه تېروتنه وشوه، خو رباټ روان دی.",
            )
    except Exception:  # noqa: BLE001 - د خطا-اداره کوونکي دننه خطا مه پریږده
        pass


async def _post_init(app: Application) -> None:
    """د پیل وروسته — د دستورونو مینو په Telegram کې ثبت کړه."""
    try:
        await app.bot.set_my_commands(BOT_COMMANDS)
        logger.info("د دستورونو مینو ثبت شوه")
    except Exception as err:  # noqa: BLE001
        logger.warning("د دستورونو ثبتول ناکام: %s", err)


def build_application(config: Config) -> Application:
    """د config پر بنسټ Application جوړ او handlers ورته ثبت کړه."""
    app = ApplicationBuilder().token(config.bot_token).post_init(_post_init).build()

    # روان حالت په bot_data کې وساته چې ټول handlers ورته لاس رسی ولري.
    app.bot_data["state"] = BotState(config=config)

    # که د ګروپ ID ټاکل شوی وي، یوازې هماغه ګروپ وڅاره؛ نه نو هرچیرته (dev).
    if config.group_id:
        chat_filter = filters.Chat(chat_id=config.group_id)
        logger.info("د ګروپ څارنه: %s", config.group_id)
    else:
        chat_filter = filters.ALL
        logger.warning("TELEGRAM_GROUP_ID نه دی ټاکل شوی — ټول چټونه څارل کیږي (dev حالت)")

    # ---- دستورونه (د پاور-کاروونکو لپاره لا هم کار کوي) ----
    app.add_handler(CommandHandler("start", commands.cmd_start, filters=chat_filter))
    app.add_handler(CommandHandler("menu", commands.cmd_start, filters=chat_filter))
    app.add_handler(CommandHandler("on", commands.cmd_on, filters=chat_filter))
    app.add_handler(CommandHandler("off", commands.cmd_off, filters=chat_filter))
    app.add_handler(CommandHandler("cmp", commands.cmd_cmp, filters=chat_filter))
    app.add_handler(CommandHandler("cnl", commands.cmd_cnl, filters=chat_filter))
    app.add_handler(CommandHandler("clean", commands.cmd_clean, filters=chat_filter))
    app.add_handler(CommandHandler("status", commands.cmd_status, filters=chat_filter))
    app.add_handler(CommandHandler("help", commands.cmd_help, filters=chat_filter))
    app.add_handler(CallbackQueryHandler(commands.on_callback))

    # ---- د بوټ کیبورډ بټنې — باید له monitor مخکې ثبت شي چې د بټنو متن
    #      د پکیج محتوا ونه ګڼل شي (یو ګروپ کې لومړی سمون کوونکی چلیږي).
    app.add_handler(MessageHandler(
        chat_filter & filters.Text(commands.BUTTON_LABELS),
        commands.on_button,
    ))

    # ---- د پیغامونو څارنه (متن + انځور + د image document) ----
    app.add_handler(MessageHandler(
        chat_filter
        & (filters.TEXT | filters.PHOTO | filters.Document.IMAGE)
        & ~filters.COMMAND,
        monitor.handle_message,
    ))

    app.add_error_handler(error_handler)
    return app


def main() -> None:
    setup_logging()
    config = load_config()

    if not config.bot_token:
        raise SystemExit(
            "TELEGRAM_BOT_TOKEN نه دی ټاکل شوی. په .env کې یې ورکړه یا د GitHub "
            "Actions secret په توګه یې اضافه کړه."
        )

    ensure_dirs(config.packages_dir, config.pending_dir, "logs")

    app = build_application(config)
    logger.info("NewsBot پیلیږي…")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
