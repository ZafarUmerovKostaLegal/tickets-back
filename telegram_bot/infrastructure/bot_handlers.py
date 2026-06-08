from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

WELCOME_TEXT = (
    "Здравствуйте!\n\n"
    "Вы написали бот Kosta Legal.\n"
    "Скоро здесь появятся уведомления и сервисные функции.\n\n"
    "Команды:\n"
    "/start — это сообщение"
)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    await update.message.reply_text(WELCOME_TEXT)
