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
    ReplyKeyboardMarkup, KeyboardButton, BotCommand, BotCommandScopeDefault,
    BotCommandScopeAllPrivateChats, BotCommandScopeAllGroupChats,
)
from aiogram.utils.deep_linking import decode_payload
from aiogram.filters.command import CommandObject
from dotenv import load_dotenv

import db
from game import Game, GAMES, USER_GAME, MAX_PLAYERS

load_dotenv()
raw_log_level = os.getenv("LOG_LEVEL", "INFO").strip().upper()
log_level = int(raw_log_level) if raw_log_level.isdigit() else getattr(logging, raw_log_level, logging.INFO)
logging.basicConfig(level=log_level, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
log = logging.getLogger("mafia-bot")

TOKEN = os.getenv("BOT_TOKEN", "").strip()
MIN_PLAYERS = max(3, min(20, int(os.getenv("MIN_PLAYERS", "5"))))
dp = Dispatcher()
bot: Bot | None = None
bot_username = ""

main_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🎭 Mafia o'ynash")],
        [KeyboardButton(text="👤 Profil"), KeyboardButton(text="🏆 Reyting")],
    ],
    resize_keyboard=True,
    is_persistent=True,
)

def is_group(message: Message) -> bool:
    return message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)

def game_add_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="➕ Botni guruhga qo'shish",
            url=f"https://t.me/{bot_username}?startgroup=true"
        )
    ]])

async def show_start(message: Message):
    db.upsert_user(message.from_user.id, message.from_user.full_name, message.from_user.username)
    await message.answer(
        "🎭 <b>MAFIA BOTIGA XUSH KELIBSIZ!</b>\n\n"
        "Do'stlaringiz bilan guruhda Mafia o'ynang. Botni guruhga qo'shing, "
        "guruhda /game buyrug'ini yuboring va o'yinchilarni yig'ing.",
        reply_markup=main_kb,
    )
    await message.answer("👇 O'yinni boshlash uchun tugmani bosing:", reply_markup=game_add_keyboard())

@dp.message(CommandStart())
async def start_handler(message: Message, command: CommandObject):
    db.upsert_user(message.from_user.id, message.from_user.full_name, message.from_user.username)
    if command.args and command.args.startswith("join_"):
        try:
            chat_id = int(command.args[5:])
        except ValueError:
            await message.answer("❌ O'yin havolasi noto'g'ri.")
            return
        game = GAMES.get(chat_id)
        if not game:
            await message.answer("❌ Bu o'yin topilmadi yoki qabul yopilgan. Guruhda yangi /game oching.")
            return
        result = await game.add_player(message.from_user.id, message.from_user.full_name)
        messages = {
            "ok": "✅ Siz o'yinga qo'shildingiz! O'yin boshlanganda rolingiz shu yerga yuboriladi.",
            "already": "ℹ️ Siz allaqachon shu o'yindasiz.",
            "full": "❌ O'yinchilar soni to'ldi.",
            "busy": "❌ Siz boshqa o'yinda qatnashyapsiz.",
            "closed": "❌ O'yinga qo'shilish yopilgan.",
        }
        await message.answer(messages.get(result, "O'yinga qo'shilishda xatolik."), reply_markup=main_kb)
        return
    await show_start(message)

@dp.message(F.text == "🎭 Mafia o'ynash")
async def play_button(message: Message):
    await message.answer(
        "🎭 <b>Mafia o'yini guruhda o'tkaziladi.</b>\n\n"
        "1) Botni guruhga qo'shing.\n2) Guruhda /game yuboring.\n"
        f"3) Do'stlaringiz botdagi qo'shilish tugmasini bosishadi. Kamida {MIN_PLAYERS} kishi kerak.",
        reply_markup=game_add_keyboard(),
    )

@dp.message(F.text == "👤 Profil")
@dp.message(Command("profile"))
async def profile_handler(message: Message):
    db.upsert_user(message.from_user.id, message.from_user.full_name, message.from_user.username)
    u = db.get_user(message.from_user.id)
    if not u:
        await message.answer("Profil topilmadi.")
        return
    await message.answer(
        f"👤 <b>{escape(message.from_user.full_name)}</b>\n"
        f"🎮 O'yinlar: {u['games']}\n🏆 G'alabalar: {u['wins']}\n⭐ XP: {u['xp']}"
    )

@dp.message(F.text == "🏆 Reyting")
@dp.message(Command("top"))
async def top_handler(message: Message):
    rows = db.top_users(10)
    if not rows:
        await message.answer("Hali reyting uchun natijalar yo'q.")
        return
    lines = ["🏆 <b>TOP 10 MAFIA O'YINCHILARI</b>"]
    for i, u in enumerate(rows, 1):
        lines.append(f"{i}. {escape(u['name'] or 'O'yinchi')} — ⭐ {u['xp']} XP")
    await message.answer("\n".join(lines))

async def create_game(message: Message, fast: bool):
    if not is_group(message):
        await message.answer("❗ O'yinni boshlash uchun botni guruhga qo'shing va guruhda /game yuboring.")
        return
    if message.chat.id in GAMES:
        await message.answer("⚠️ Bu guruhda o'yin allaqachon ochilgan.")
        return
    db.upsert_user(message.from_user.id, message.from_user.full_name, message.from_user.username)
    game = Game(bot, bot_username, message.chat.id, message.chat.title or "guruh",
                message.from_user.id, fast, MIN_PLAYERS)
    GAMES[message.chat.id] = game
    sent = await message.answer(game.lobby_text(), reply_markup=game.lobby_kb())
    game.lobby_msg_id = sent.message_id
    game.task = asyncio.create_task(game.run())

@dp.message(Command("game"))
async def game_handler(message: Message):
    await create_game(message, False)

@dp.message(Command("fast"))
async def fast_handler(message: Message):
    await create_game(message, True)

@dp.callback_query(F.data.startswith("ls:"))
async def lobby_start(call: CallbackQuery):
    chat_id = int(call.data.split(":")[1])
    game = GAMES.get(chat_id)
    if not game:
        await call.answer("O'yin topilmadi.", show_alert=True)
        return
    if call.from_user.id != game.host_id:
        await call.answer("Faqat xona egasi o'yinni boshlashi mumkin.", show_alert=True)
        return
    if len(game.players) < game.min_players:
        await call.answer(f"Kamida {game.min_players} o'yinchi kerak.", show_alert=True)
        return
    game.start_event.set()
    await call.answer("O'yin boshlanmoqda!")

@dp.callback_query(F.data.startswith("lc:"))
async def lobby_cancel(call: CallbackQuery):
    chat_id = int(call.data.split(":")[1])
    game = GAMES.get(chat_id)
    if not game:
        await call.answer("O'yin topilmadi.")
        return
    if call.from_user.id != game.host_id:
        await call.answer("Faqat xona egasi bekor qilishi mumkin.", show_alert=True)
        return
    game.phase = "cancelled"
    game.start_event.set()
    await call.answer("O'yin bekor qilindi.")

@dp.callback_query(F.data.startswith("n:"))
async def night_action(call: CallbackQuery):
    _, chat_id, kind, target = call.data.split(":")
    game = GAMES.get(int(chat_id))
    if not game:
        await call.answer("O'yin tugagan.", show_alert=True)
        return
    result = await game.record_action(call.from_user.id, kind, int(target))
    await call.answer(result or "Bu amal siz uchun mavjud emas.", show_alert=not bool(result))

@dp.callback_query(F.data.startswith("w:"))
async def witch_action(call: CallbackQuery):
    _, chat_id, kind = call.data.split(":")
    game = GAMES.get(int(chat_id))
    if not game:
        await call.answer("O'yin tugagan.", show_alert=True)
        return
    result = game.witch_menu(call.from_user.id, kind)
    if not result:
        await call.answer("Bu amal hozir mavjud emas.", show_alert=True)
        return
    text, markup = result
    await call.message.answer(text, reply_markup=markup)
    await call.answer()

@dp.callback_query(F.data.startswith("v:"))
async def vote_action(call: CallbackQuery):
    _, chat_id, target = call.data.split(":")
    game = GAMES.get(int(chat_id))
    if not game:
        await call.answer("O'yin tugagan.", show_alert=True)
        return
    result = await game.record_vote(call.from_user.id, int(target))
    messages = {"ok": "Ovozingiz qabul qilindi.", "late": "Ovoz berish yopilgan.",
                "out": "Siz o'yinda faol emassiz.", "self": "O'zingizga ovoz bera olmaysiz."}
    await call.answer(messages.get(result, "Ovoz qabul qilinmadi."), show_alert=result != "ok")

@dp.callback_query(F.data.startswith("r:"))
async def revenge_action(call: CallbackQuery):
    _, chat_id, target = call.data.split(":")
    game = GAMES.get(int(chat_id))
    if not game or not game.record_revenge(call.from_user.id, int(target)):
        await call.answer("Bu tanlov endi mavjud emas.", show_alert=True)
        return
    await call.answer("Qasos tanlovi qabul qilindi!")

async def main():
    global bot, bot_username
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable is missing")
    db.init()
    bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    me = await bot.get_me()
    bot_username = me.username or ""
    await bot.set_my_commands(
        [BotCommand(command="start", description="Botni ishga tushirish"),
         BotCommand(command="game", description="Guruhda yangi Mafia o'yini"),
         BotCommand(command="fast", description="Tezkor Mafia o'yini"),
         BotCommand(command="profile", description="Profilim"),
         BotCommand(command="top", description="Reyting")],
        scope=BotCommandScopeDefault(),
    )
    await bot.set_my_commands(
        [BotCommand(command="game", description="Yangi Mafia o'yini"),
         BotCommand(command="fast", description="Tezkor Mafia o'yini")],
        scope=BotCommandScopeAllGroupChats(),
    )
    log.info("Bot started: @%s", bot_username)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
