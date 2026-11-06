"""Telegram Mafia bot entrypoint (aiogram 3.x)."""
import asyncio
import logging
import os
from html import escape

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode, ChatType
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
    ChatMemberUpdated, BotCommand, BotCommandScopeDefault,
    BotCommandScopeAllPrivateChats, BotCommandScopeAllGroupChats,
)
from dotenv import load_dotenv

import db
from game import Game, GAMES, USER_GAME, MAX_PLAYERS, level

load_dotenv()
raw_log_level = os.getenv("LOG_LEVEL", "INFO").strip().upper()
log_level = int(raw_log_level) if raw_log_level.isdigit() else getattr(logging, raw_log_level, logging.INFO)
logging.basicConfig(
    level=log_level,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("mafia-bot")

TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS = {
    int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
}
MIN_PLAYERS = max(3, min(20, int(os.getenv("MIN_PLAYERS", "5"))))

dp = Dispatcher()
bot: Bot | None = None
bot_username = ""


def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


def is_group(message: Message) -> bool:
    return message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)


async def subscription_status(uid: int):
    """Returns (ok, missing buttons). Telegram memberships are verified;
    external links are user-confirmed because Telegram cannot verify them.
    """
    missing = []
    for sub in db.list_subs():
        if sub["kind"] == "tg" and sub["chat_id"]:
            try:
                member = await bot.get_chat_member(sub["chat_id"], uid)
                if member.status in ("left", "kicked"):
                    missing.append(sub)
            except Exception:
                log.exception("Could not verify subscription %s", sub["chat_id"])
                missing.append(sub)
        elif sub["kind"] == "link" and not db.link_ok(uid):
            missing.append(sub)
    return not missing, missing


def subscription_keyboard(missing):
    rows = []
    for sub in missing:
        if sub["url"]:
            rows.append([InlineKeyboardButton(
                text=f"➕ {sub['title'][:50]}", url=sub["url"]
            )])
    if any(s["kind"] == "link" for s in missing):
        rows.append([InlineKeyboardButton(
            text="✅ Havolalarga obuna bo'ldim", callback_data="sub:confirm"
        )])
    rows.append([InlineKeyboardButton(
        text="🔄 Tekshirish", callback_data="sub:check"
    )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def require_subs(message: Message) -> bool:
    db.upsert_user(message.from_user.id, message.from_user.full_name,
                   message.from_user.username)
    ok, missing = await subscription_status(message.from_user.id)
    if ok:
        return True
    await message.answer(
        "🔒 Botdan foydalanish uchun quyidagi kanal/sahifalarga obuna bo'ling:",
        reply_markup=subscription_keyboard(missing),
    )
    return False
