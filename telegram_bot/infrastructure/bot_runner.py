from __future__ import annotations

from telegram.ext import Application, CommandHandler

from infrastructure.bot_handlers import cmd_start


def build_bot_application(token: str) -> Application:
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", cmd_start))
    return app
