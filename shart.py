"""Telegram group Shart Chambalagi game."""
import asyncio
import logging
import random
from html import escape

from aiogram import F, Dispatcher, Bot
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
)

log = logging.getLogger("shart-game")

SESSIONS = {}       # chat_id -> current game session
PENDING = {}        # user_id -> (chat_id, selected_user_id)
MAX_PLAYERS = 50
MAX_CONDITION = 250


def mention(uid: int, name: str) -> str:
    return f'<a href="tg://user?id={uid}">{escape(name)}</a>'


async def is_admin(bot: Bot, chat_id: int, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in ("creator", "administrator")
    except Exception:
        return False


def lobby_markup(chat_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ O‘yinga qo‘shilish", callback_data=f"shj:{chat_id}")],
        [InlineKeyboardButton(text="🎡 Odamni tanlash", callback_data=f"shpick:{chat_id}")],
    ])


def lobby_text(s):
    names = [f"• {mention(uid, p['name'])}" for uid, p in s["players"].items()]
    return (
        "🎡 <b>SHART CHAMBARAgi boshlandi!</b>\n\n"
        "O‘yinga qo‘shilish uchun tugmani bosing.\n\n"
        f"👥 Ishtirokchilar: <b>{len(names)}</b>\n"
        + ("\n".join(names) if names else "Hali hech kim qo‘shilmadi.")
        + "\n\nAdmin odam tanlash tugmasini bosadi."
    )


async def handle_start(message: Message, args: str):
    """Handle /start shart_<chat_id>_<target_id> deep links."""
    try:
        _, chat_raw, target_raw = args.split("_", 2)
        chat_id, target_id = int(chat_raw), int(target_raw)
    except (ValueError, AttributeError):
        await message.answer("❌ Shart havolasi noto‘g‘ri yoki eskirgan.")
        return
    s = SESSIONS.get(chat_id)
    if not s or s["phase"] != "collecting" or s.get("selected") != target_id:
        await message.answer("❌ Hozir shart yuborish vaqti emas. Guruhdagi yangi havolani bosing.")
        return
    if message.from_user.id == target_id:
        await message.answer("😄 Bu safar shartni boshqalar yozadi. Siz shartni bajarasiz!")
        return
    if message.from_user.id in s["submitted"]:
        await message.answer("✅ Siz bu tur uchun shart yuborib bo‘lgansiz.")
        return
    PENDING[message.from_user.id] = (chat_id, target_id)
    await message.answer(
        f"✍️ <b>{escape(s['players'][target_id]['name'])}</b> uchun shartingizni yozing.\n\n"
        "Bitta kulgili va xavfsiz topshiriq yuboring. "
        f"Matn {MAX_CONDITION} belgidan oshmasin."
    )


async def create_handler(message: Message, bot: Bot, bot_username: str):
    if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        await message.answer("❗ /shart buyrug‘ini Telegram guruhida yuboring.")
        return
    if not await is_admin(bot, message.chat.id, message.from_user.id):
        await message.answer("🔒 Bu buyruqni faqat guruh adminlari ishlata oladi.")
        return
    if message.chat.id in SESSIONS:
        await message.answer("⚠️ Bu guruhda Shart chambaragi allaqachon ochilgan.")
        return
    SESSIONS[message.chat.id] = {
        "phase": "lobby",
        "players": {},
        "remaining": [],
        "selected": None,
        "conditions": [],
        "submitted": set(),
        "bot_username": bot_username,
        "result_message_id": None,
    }
    sent = await message.answer(lobby_text(SESSIONS[message.chat.id]),
                                reply_markup=lobby_markup(message.chat.id))
    SESSIONS[message.chat.id]["lobby_message_id"] = sent.message_id


async def join_callback(call: CallbackQuery, bot: Bot):
    chat_id = int(call.data.split(":")[1])
    s = SESSIONS.get(chat_id)
    if not s or s["phase"] != "lobby":
        await call.answer("Qo‘shilish yopilgan.", show_alert=True)
        return
    if call.message.chat.id != chat_id:
        await call.answer("Bu o‘yin boshqa guruhda.", show_alert=True)
        return
    uid = call.from_user.id
    if uid in s["players"]:
        await call.answer("Siz allaqachon qo‘shilgansiz.")
        return
    if len(s["players"]) >= MAX_PLAYERS:
        await call.answer("Ishtirokchilar soni to‘ldi.", show_alert=True)
        return
    s["players"][uid] = {"name": call.from_user.full_name}
    s["remaining"].append(uid)
    try:
        await call.message.edit_text(lobby_text(s), reply_markup=lobby_markup(chat_id))
    except Exception:
        pass
    await call.answer("✅ O‘yinga qo‘shildingiz!")


async def pick_callback(call: CallbackQuery, bot: Bot):
    chat_id = int(call.data.split(":")[1])
    s = SESSIONS.get(chat_id)
    if not s:
        await call.answer("O‘yin topilmadi.", show_alert=True)
        return
    if not await is_admin(bot, chat_id, call.from_user.id):
        await call.answer("Faqat admin odam tanlay oladi.", show_alert=True)
        return
    if s["phase"] not in ("lobby", "done") or not s["remaining"]:
        await call.answer("Tanlash uchun qolgan ishtirokchi yo‘q yoki hozir tanlab bo‘lmaydi.", show_alert=True)
        return
    if s["phase"] == "lobby" and not s["players"]:
        await call.answer("Avval odamlar o‘yinga qo‘shilsin.", show_alert=True)
        return
    s["phase"] = "collecting"
    s["conditions"] = []
    s["submitted"] = set()
    selected = random.choice(s["remaining"])
    s["remaining"].remove(selected)
    s["selected"] = selected
    name = s["players"][selected]["name"]
    link = f"https://t.me/{s['bot_username']}?start=shart_{chat_id}_{selected}"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✍️ Shart yozish", url=link)],
        [InlineKeyboardButton(text="🎡 Shartlarni aylantirish", callback_data=f"shspin:{chat_id}")],
    ])
    await call.message.answer(
        f"🎉 <b>G‘ILDIRAK TANLADI!</b>\n\n"
        f"👤 Tanlangan odam: {mention(selected, name)}\n\n"
        "Endi guruhdagilar “Shart yozish” tugmasini bosib, shu odam uchun botga shart yuborishsin.\n"
        "Shartlar yig‘ilgach, admin “Shartlarni aylantirish” tugmasini bosadi.",
        reply_markup=kb,
    )
    await call.answer("Odam tanlandi!")


async def submit_condition(message: Message):
    pending = PENDING.get(message.from_user.id)
    if not pending:
        return
    chat_id, target_id = pending
    s = SESSIONS.get(chat_id)
    if not s or s["phase"] != "collecting" or s.get("selected") != target_id:
        PENDING.pop(message.from_user.id, None)
        await message.answer("❌ Bu tur yakunlangan. Guruhdagi yangi shart havolasidan foydalaning.")
        return
    text = (message.text or "").strip()
    if not text:
        await message.answer("Shart matnini yuboring.")
        return
    if len(text) > MAX_CONDITION:
        await message.answer(f"❌ Shart {MAX_CONDITION} belgidan oshmasin.")
        return
    if message.from_user.id == target_id:
        await message.answer("Siz tanlangan odamsiz, bu turda shart yubora olmaysiz.")
        return
    if message.from_user.id in s["submitted"]:
        await message.answer("Siz bu tur uchun shart yuborib bo‘lgansiz.")
        return
    s["conditions"].append({"text": text, "by": message.from_user.id,
                            "name": message.from_user.full_name})
    s["submitted"].add(message.from_user.id)
    PENDING.pop(message.from_user.id, None)
    await message.answer("✅ Shartingiz qabul qilindi! Guruhda g‘ildirak aylanishini kuting.")
    try:
        await message.bot.send_message(
            chat_id,
            f"📝 Yangi shart qo‘shildi! Jami shartlar: <b>{len(s['conditions'])}</b>"
        )
    except Exception:
        log.exception("Could not notify group about a new condition")


async def spin_callback(call: CallbackQuery, bot: Bot):
    chat_id = int(call.data.split(":")[1])
    s = SESSIONS.get(chat_id)
    if not s or s["phase"] != "collecting":
        await call.answer("Hozir shart g‘ildiragini aylantirib bo‘lmaydi.", show_alert=True)
        return
    if not await is_admin(bot, chat_id, call.from_user.id):
        await call.answer("Shart g‘ildiragini faqat admin aylantiradi.", show_alert=True)
        return
    if not s["conditions"]:
        await call.answer("Hali hech kim shart yubormadi.", show_alert=True)
        return
    s["phase"] = "spinning"
    conditions = list(s["conditions"])
    wheel_lines = "\n".join(
        f"{i}. {escape(item['text'])}" for i, item in enumerate(conditions, 1)
    )
    wheel = await call.message.answer(
        "🎡 <b>SHARTLAR G‘ILDIRAGI</b>\n\n" + wheel_lines + "\n\n🔄 Aylanmoqda..."
    )
    frames = ["🎡 🔴 ⚪ 🔵 ⚪", "🔄 ⚪ 🔴 ⚪ 🔵", "🎡 🔵 ⚪ 🔴 ⚪",
              "🔄 ⚪ 🔵 ⚪ 🔴", "🎡 🔴 ⚪ 🔵 ⚪"]
    for frame in frames:
        await asyncio.sleep(0.55)
        try:
            await wheel.edit_text(
                "🎡 <b>SHARTLAR G‘ILDIRAGI</b>\n\n" + wheel_lines + f"\n\n{frame}"
            )
        except Exception:
            pass
    chosen = random.choice(conditions)
    s["chosen_condition"] = chosen
    s["phase"] = "done"
    selected = s["selected"]
    player_name = s["players"][selected]["name"]
    result_text = (
        "🎊 <b>G‘ILDIRAK TO‘XTADI!</b>\n\n"
        f"👤 Tanlangan odam: {mention(selected, player_name)}\n\n"
        "🎯 <b>Unga tushgan shart:</b>\n"
        f"🔥 <b>{escape(chosen['text'])}</b>\n\n"
        "Endi tanlangan odam shartni bajaradi!"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Shart bajarildi", callback_data=f"shdone:{chat_id}:{selected}")],
        [InlineKeyboardButton(text="🎡 Keyingi odamni tanlash", callback_data=f"shnext:{chat_id}")],
    ])
    await wheel.edit_text(result_text, reply_markup=kb)
    await call.answer("G‘ildirak to‘xtadi!")


async def done_callback(call: CallbackQuery, bot: Bot):
    _, chat_raw, target_raw = call.data.split(":")
    chat_id, target_id = int(chat_raw), int(target_raw)
    s = SESSIONS.get(chat_id)
    if not s or s["phase"] != "done" or s.get("selected") != target_id:
        await call.answer("Bu tur topilmadi.", show_alert=True)
        return
    if call.from_user.id != target_id and not await is_admin(bot, chat_id, call.from_user.id):
        await call.answer("Bu tugmani faqat tanlangan odam yoki admin bosishi mumkin.", show_alert=True)
        return
    s["phase"] = "await_next"
    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.answer(f"✅ {mention(target_id, s['players'][target_id]['name'])} shartni bajarganini tasdiqladi!")
    await call.answer("Bajarildi!")


async def next_callback(call: CallbackQuery, bot: Bot):
    chat_id = int(call.data.split(":")[1])
    s = SESSIONS.get(chat_id)
    if not s:
        await call.answer("O‘yin topilmadi.", show_alert=True)
        return
    if not await is_admin(bot, chat_id, call.from_user.id):
        await call.answer("Keyingi odamni faqat admin tanlaydi.", show_alert=True)
        return
    if s["phase"] not in ("done", "await_next"):
        await call.answer("Avval joriy tur yakunlansin.", show_alert=True)
        return
    if not s["remaining"]:
        await call.message.answer("🏁 Barcha ishtirokchilar navbatdan o‘tdi! Yangi o‘yin uchun /shart yuboring.")
        SESSIONS.pop(chat_id, None)
        await call.answer("O‘yin yakunlandi!")
        return
    selected = random.choice(s["remaining"])
    s["remaining"].remove(selected)
    s["selected"] = selected
    s["conditions"] = []
    s["submitted"] = set()
    s["phase"] = "collecting"
    name = s["players"][selected]["name"]
    link = f"https://t.me/{s['bot_username']}?start=shart_{chat_id}_{selected}"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✍️ Shart yozish", url=link)],
        [InlineKeyboardButton(text="🎡 Shartlarni aylantirish", callback_data=f"shspin:{chat_id}")],
    ])
    await call.message.answer(
        f"🎉 <b>KEYINGI ODAM TANLANDI!</b>\\n\\n"
        f"👤 Tanlangan odam: {mention(selected, name)}\\n\\n"
        "Endi guruhdagilar “Shart yozish” tugmasini bosib, botga shart yuborishsin.",
        reply_markup=kb,
    )
    await call.answer("Keyingi odam tanlandi!")


async def stop_handler(message: Message, bot: Bot):
    if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return
    if not await is_admin(bot, message.chat.id, message.from_user.id):
        await message.answer("🔒 Bu buyruq faqat adminlar uchun.")
        return
    s = SESSIONS.pop(message.chat.id, None)
    if not s:
        await message.answer("Hozir faol Shart chambaragi yo‘q.")
        return
    for uid, (pending_chat, _) in list(PENDING.items()):
        if pending_chat == message.chat.id:
            PENDING.pop(uid, None)
    await message.answer("🛑 Shart chambaragi admin tomonidan to‘xtatildi.")


def register_shart(dp: Dispatcher, bot: Bot, bot_username: str):
    async def create(message: Message):
        await create_handler(message, bot, bot_username)

    async def join(call: CallbackQuery):
        await join_callback(call, bot)

    async def pick(call: CallbackQuery):
        await pick_callback(call, bot)

    async def spin(call: CallbackQuery):
        await spin_callback(call, bot)

    async def done(call: CallbackQuery):
        await done_callback(call, bot)

    async def next_round(call: CallbackQuery):
        await next_callback(call, bot)

    async def stop(message: Message):
        await stop_handler(message, bot)

    dp.message.register(create, Command("shart"))
    dp.message.register(stop, Command("shartstop"))
    dp.message.register(submit_condition, F.chat.type == ChatType.PRIVATE, F.text)
    dp.callback_query.register(join, F.data.startswith("shj:"))
    dp.callback_query.register(pick, F.data.startswith("shpick:"))
    dp.callback_query.register(spin, F.data.startswith("shspin:"))
    dp.callback_query.register(done, F.data.startswith("shdone:"))
    dp.callback_query.register(next_round, F.data.startswith("shnext:"))
