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
    ChatMemberUpdated,
)
from dotenv import load_dotenv

import db
from game import Game, GAMES, USER_GAME, MAX_PLAYERS, level

load_dotenv()
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
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


async def start_lobby(chat_id: int, title: str, host_id: int, fast: bool):
    if chat_id in GAMES:
        return None, "Bu guruhda o'yin allaqachon davom etmoqda."
    game = Game(bot, bot_username, chat_id, title, host_id, fast, MIN_PLAYERS)
    GAMES[chat_id] = game
    msg = await bot.send_message(
        chat_id, game.lobby_text(), reply_markup=game.lobby_kb()
    )
    game.lobby_msg_id = msg.message_id
    game.task = asyncio.create_task(game.run(), name=f"mafia-{chat_id}")
    return game, None


@dp.message(CommandStart())
async def cmd_start(message: Message):
    user = message.from_user
    db.upsert_user(user.id, user.full_name, user.username)
    args = (message.text or "").partition(" ")[2].strip()
    if args.startswith("join_"):
        try:
            chat_id = int(args[5:])
        except ValueError:
            await message.answer("❌ Taklif havolasi noto'g'ri.")
            return
        game = GAMES.get(chat_id)
        if not game:
            await message.answer("❌ Bu o'yin topilmadi yoki qabul yopilgan.")
            return
        if not await require_subs(message):
            return
        result = await game.add_player(user.id, user.full_name)
        answers = {
            "ok": "✅ O'yinga muvaffaqiyatli qo'shildingiz! O'yin boshlanishini kuting.",
            "already": "Siz allaqachon ro'yxatdasiz.",
            "full": "❌ O'yinchilar soni to'lgan.",
            "busy": "❌ Siz boshqa o'yinda qatnashyapsiz.",
            "closed": "❌ Ro'yxatdan o'tish yopilgan.",
        }
        await message.answer(answers.get(result, "❌ Qo'shilib bo'lmadi."))
        return
    await message.answer(
        "🎭 <b>Mafia botiga xush kelibsiz!</b>\n\n"
        "O'yinni guruhda boshlang: /game yoki /fast\n"
        "Shaxsiy buyruqlar: /profile, /top, /help\n\n"
        "Botni guruhga qo'shing va do'stlaringiz bilan o'ynang."
    )


@dp.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(
        "<b>🎭 Mafia bot — buyruqlar</b>\n\n"
        "<b>Guruhda:</b>\n"
        "/game — oddiy o'yin\n/fast — tezkor o'yin\n"
        "/cancelgame — kutish xonasini bekor qilish (boshlovchi/admin)\n\n"
        "<b>Shaxsiy:</b>\n/profile — profilingiz\n/top — XP reytingi\n"
        "/stats — bot statistikasi\n\n"
        "O'yinga qo'shilish uchun guruhdagi «Qo'shilish» tugmasini bosing. "
        "Bot sizga rol va tungi harakatlarni shaxsiy xabarda yuboradi."
    )


@dp.message(Command("game", "fast"))
async def cmd_game(message: Message):
    if not is_group(message):
        await message.answer("O'yinni boshlash uchun botni guruhga qo'shing.")
        return
    db.upsert_user(message.from_user.id, message.from_user.full_name,
                   message.from_user.username)
    db.upsert_group(message.chat.id, message.chat.title or "Guruh")
    if message.chat.id in GAMES:
        await message.reply("⚠️ Bu guruhda o'yin allaqachon bor.")
        return
    fast = (message.text or "").split()[0].lower().endswith("fast")
    game, error = await start_lobby(
        message.chat.id, message.chat.title or "Guruh",
        message.from_user.id, fast,
    )
    if error:
        await message.reply(error)


@dp.message(Command("cancelgame"))
async def cmd_cancel(message: Message):
    if not is_group(message):
        return
    game = GAMES.get(message.chat.id)
    if not game or game.phase != "lobby":
        await message.reply("Hozir bekor qilinadigan kutish xonasi yo'q.")
        return
    member = await bot.get_chat_member(message.chat.id, message.from_user.id)
    if message.from_user.id != game.host_id and member.status not in ("administrator", "creator"):
        await message.reply("Faqat o'yin boshlovchisi yoki guruh admini bekor qila oladi.")
        return
    game.phase = "cancelled"
    game.start_event.set()
    await message.reply("❌ O'yin bekor qilindi.")


@dp.message(Command("profile"))
async def cmd_profile(message: Message):
    db.upsert_user(message.from_user.id, message.from_user.full_name,
                   message.from_user.username)
    u = db.get_user(message.from_user.id)
    rate = (u["wins"] / u["games"] * 100) if u["games"] else 0
    await message.answer(
        f"👤 <b>{escape(u['name'] or message.from_user.full_name)}</b>\n"
        f"⭐ XP: <b>{u['xp']}</b>\n"
        f"🏅 Daraja: {level(u['xp'])}\n"
        f"🎮 O'yinlar: {u['games']}\n"
        f"🏆 G'alabalar: {u['wins']}\n"
        f"📈 G'alaba foizi: {rate:.1f}%"
    )


@dp.message(Command("top"))
async def cmd_top(message: Message):
    rows = db.top_users(10)
    if not rows:
        await message.answer("Hali reyting uchun ma'lumot yo'q.")
        return
    lines = ["🏆 <b>TOP 10 o'yinchi</b>"]
    for i, u in enumerate(rows, 1):
        lines.append(f"{i}. {escape(u['name'] or str(u['user_id']))} — "
                     f"<b>{u['xp']} XP</b> · {level(u['xp'])}")
    await message.answer("\n".join(lines))


@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    await message.answer(
        "📊 <b>Bot statistikasi</b>\n"
        f"👤 Foydalanuvchilar: {db.count_users()}\n"
        f"👥 Guruhlar: {db.count_groups()}\n"
        f"🎮 Guruhlardagi o'yinlar: {db.total_games()}"
    )


@dp.callback_query(F.data.startswith("ls:"))
async def cb_lobby_start(call: CallbackQuery):
    try:
        chat_id = int(call.data.split(":")[1])
    except (ValueError, IndexError):
        await call.answer("Noto'g'ri so'rov.", show_alert=True)
        return
    game = GAMES.get(chat_id)
    if not game or game.phase != "lobby":
        await call.answer("Bu kutish xonasi yopilgan.", show_alert=True)
        return
    if call.from_user.id != game.host_id:
        try:
            member = await bot.get_chat_member(chat_id, call.from_user.id)
        except Exception:
            await call.answer("Guruh a'zoligini tekshirib bo'lmadi.", show_alert=True)
            return
        if member.status not in ("administrator", "creator"):
            await call.answer("Faqat boshlovchi yoki guruh admini boshlashi mumkin.", show_alert=True)
            return
    if len(game.players) < game.min_players:
        await call.answer(f"Kamida {game.min_players} o'yinchi kerak.", show_alert=True)
        return
    game.start_event.set()
    await call.answer("O'yin boshlanmoqda!")


@dp.callback_query(F.data.startswith("sub:"))
async def cb_subscription(call: CallbackQuery):
    uid = call.from_user.id
    db.upsert_user(uid, call.from_user.full_name, call.from_user.username)
    if call.data == "sub:confirm":
        db.set_link_ok(uid)
    ok, missing = await subscription_status(uid)
    if ok:
        await call.message.edit_text("✅ Obuna tasdiqlandi! Endi o'yinga qo'shilishingiz mumkin.")
    else:
        await call.message.edit_reply_markup(reply_markup=subscription_keyboard(missing))
        await call.answer("Hali barcha obunalar tasdiqlanmadi.", show_alert=True)
        return
    await call.answer()


@dp.callback_query(F.data.startswith("n:"))
async def cb_night(call: CallbackQuery):
    try:
        _, chat, kind, target = call.data.split(":")
        chat_id, target_id = int(chat), int(target)
    except (ValueError, TypeError):
        await call.answer("Noto'g'ri tanlov.", show_alert=True)
        return
    game = GAMES.get(chat_id)
    if not game:
        await call.answer("O'yin tugagan.", show_alert=True)
        return
    # Check subscription on private action too; membership requirements apply before joining.
    result = await game.record_action(call.from_user.id, kind, target_id)
    if result:
        await call.answer("Tanlov qabul qilindi.")
        try:
            await call.message.edit_text(result)
        except Exception:
            pass
    else:
        await call.answer("Bu tanlov hozir mumkin emas.", show_alert=True)


@dp.callback_query(F.data.startswith("w:"))
async def cb_witch(call: CallbackQuery):
    try:
        _, chat, kind = call.data.split(":")
        chat_id = int(chat)
    except (ValueError, TypeError):
        await call.answer("Noto'g'ri tanlov.", show_alert=True)
        return
    game = GAMES.get(chat_id)
    menu = game.witch_menu(call.from_user.id, kind) if game else None
    if not menu:
        await call.answer("Bu harakat hozir mumkin emas.", show_alert=True)
        return
    text, markup = menu
    await call.message.edit_text(text, reply_markup=markup)
    await call.answer()


@dp.callback_query(F.data.startswith("v:"))
async def cb_vote(call: CallbackQuery):
    try:
        _, chat, target = call.data.split(":")
        chat_id, target_id = int(chat), int(target)
    except (ValueError, TypeError):
        await call.answer("Noto'g'ri ovoz.", show_alert=True)
        return
    game = GAMES.get(chat_id)
    if not game:
        await call.answer("O'yin tugagan.", show_alert=True)
        return
    result = await game.record_vote(call.from_user.id, target_id)
    messages = {
        "ok": "Ovozingiz qabul qilindi.",
        "late": "Ovoz berish tugagan yoki tanlangan o'yinchi tirik emas.",
        "out": "Faqat tirik o'yinchilar ovoz bera oladi.",
        "self": "O'zingizga ovoz bera olmaysiz.",
    }
    await call.answer(messages.get(result, "Ovoz qabul qilinmadi."),
                      show_alert=(result != "ok"))


@dp.callback_query(F.data.startswith("r:"))
async def cb_revenge(call: CallbackQuery):
    try:
        _, chat, target = call.data.split(":")
        chat_id, target_id = int(chat), int(target)
    except (ValueError, TypeError):
        await call.answer("Noto'g'ri tanlov.", show_alert=True)
        return
    game = GAMES.get(chat_id)
    if game and game.record_revenge(call.from_user.id, target_id):
        await call.answer("Qasos tanlandi!")
        try:
            await call.message.edit_text("⚔️ Qasos tanlandi.")
        except Exception:
            pass
    else:
        await call.answer("Bu tanlov endi mumkin emas.", show_alert=True)


# Bot guruhga qo'shilganini qayd etish.
@dp.my_chat_member()
async def on_bot_added(event: ChatMemberUpdated):
    if event.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
        if event.new_chat_member.status in ("member", "administrator"):
            db.upsert_group(event.chat.id, event.chat.title or "Guruh")


# ---------------- Admin panel buyruqlari ----------------
@dp.message(Command("admin"))
async def cmd_admin(message: Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer(
        "<b>🛠 Admin buyruqlari</b>\n"
        "/addtg CHAT_ID | NOMI | URL — Telegram kanal qo'shish\n"
        "/addlink NOMI | URL — tashqi obuna havolasi qo'shish\n"
        "/subs — obunalar ro'yxati\n/delsub ID — obunani o'chirish\n"
        "/broadcast MATN — foydalanuvchilarga xabar yuborish"
    )


@dp.message(Command("addtg"))
async def cmd_addtg(message: Message):
    if not is_admin(message.from_user.id):
        return
    raw = (message.text or "").partition(" ")[2]
    parts = [x.strip() for x in raw.split("|")]
    if len(parts) != 3 or not parts[0]:
        await message.reply("Format: /addtg CHAT_ID | NOMI | URL")
        return
    db.add_sub("tg", parts[1], parts[0], parts[2])
    await message.reply("✅ Telegram obunasi qo'shildi.")


@dp.message(Command("addlink"))
async def cmd_addlink(message: Message):
    if not is_admin(message.from_user.id):
        return
    parts = [x.strip() for x in (message.text or "").partition(" ")[2].split("|")]
    if len(parts) != 2:
        await message.reply("Format: /addlink NOMI | URL")
        return
    db.add_sub("link", parts[0], "", parts[1])
    db.reset_link_ok()
    await message.reply("✅ Tashqi havola qo'shildi. Foydalanuvchilar qayta tasdiqlashi kerak.")


@dp.message(Command("subs"))
async def cmd_subs(message: Message):
    if not is_admin(message.from_user.id):
        return
    subs = db.list_subs()
    if not subs:
        await message.answer("Majburiy obunalar yo'q.")
        return
    await message.answer("\n".join(
        f"#{s['id']} [{s['kind']}] {escape(s['title'] or '')} — {escape(s['url'] or '')}"
        for s in subs
    ))


@dp.message(Command("delsub"))
async def cmd_delsub(message: Message):
    if not is_admin(message.from_user.id):
        return
    try:
        sub_id = int((message.text or "").split(maxsplit=1)[1])
    except (ValueError, IndexError):
        await message.reply("Format: /delsub ID")
        return
    db.del_sub(sub_id)
    await message.reply("✅ Bajarildi.")


@dp.message(Command("broadcast"))
async def cmd_broadcast(message: Message):
    if not is_admin(message.from_user.id):
        return
    text = (message.text or "").partition(" ")[2].strip()
    if not text:
        await message.reply("Format: /broadcast Xabar matni")
        return
    sent = failed = 0
    for uid in db.user_ids():
        try:
            await bot.send_message(uid, text)
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.04)
    await message.reply(f"📣 Yuborildi: {sent}\nXatolik: {failed}")


async def main():
    global bot, bot_username
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN .env faylida ko'rsatilmagan.")
    db.init()
    bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    me = await bot.get_me()
    bot_username = me.username
    log.info("Bot ishga tushdi: @%s", bot_username)
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
