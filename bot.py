#!/usr/bin/env python3
"""Hindukush Telegram Bot.

A simple bot that replies to the /start command and to normal text messages.
The bot token is read from the TELEGRAM_BOT_TOKEN environment variable so
that the secret is never committed to the repository.
"""

import logging
import os

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the /start command."""
    user = update.effective_user
    name = user.first_name if user else "there"
    logger.info("Received /start from %s", user.id if user else "unknown")
    await update.message.reply_text(
        f"👋 سلام {name}! زه د هندوکش بوټ یم.\n"
        "ما ته هر پیغام ولیکه او زه به یې ځواب درکړم."
    )


async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reply to normal (non-command) text messages."""
    text = update.message.text
    logger.info("Received message: %s", text)
    await update.message.reply_text(f"تاسو ولیکل: {text}")


def build_application(token: str) -> Application:
    """Create and configure the Telegram application."""
    application = Application.builder().token(token).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, echo)
    )
    return application


def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit(
            "TELEGRAM_BOT_TOKEN environment variable is not set. "
            "Add it as a GitHub Actions secret or export it locally."
        )

    application = build_application(token)
    logger.info("Starting Hindukush-Bot ...")
    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
