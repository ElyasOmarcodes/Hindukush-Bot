"""Basic tests for Hindukush-Bot.

These tests do not contact Telegram. They only verify that the application
is constructed correctly and that the handlers are wired up.
"""

from telegram.ext import CommandHandler, MessageHandler

import bot


def test_build_application_registers_handlers():
    application = bot.build_application("123456:TEST-TOKEN")

    handlers = application.handlers[0]
    assert any(isinstance(h, CommandHandler) for h in handlers), (
        "Expected a CommandHandler for /start"
    )
    assert any(isinstance(h, MessageHandler) for h in handlers), (
        "Expected a MessageHandler for text messages"
    )


def test_start_handler_exists():
    assert callable(bot.start)


def test_echo_handler_exists():
    assert callable(bot.echo)
