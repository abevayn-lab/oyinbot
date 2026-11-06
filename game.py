"""Mafia o'yin mexanikasi: rollar, tun/kun/ovoz berish, tasodifiy voqealar, XP."""
import asyncio
import logging
import random
from collections import Counter
from dataclasses import dataclass
from html import escape
from urllib.parse import quote

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import db

log = logging.getLogger("game")

MAX_PLAYERS = 20

# Soniyalarda. Tezkor rejim (/fast) ancha qisqa.
TIMERS = {
    False: {"lobby": 90, "night": 45, "day": 90, "vote": 40},
    True: {"lobby": 60, "night": 25, "day": 40, "vote": 20},
}

GAMES: dict = {}      # chat_id -> Game
USER_GAME: dict = {}  # user_id -> chat_id (bir vaqtda faqat bitta o'yin)

ROLE_INFO = {
    "mafia": ("🔪 Mafiya", "Har kecha bitta odamni yo'q qilasiz. Kunduzi begunoh qiyofasida yuring va gumonni boshqalarga qarating."),
    "doctor": ("💉 Shifokor", "Har kecha bitta odamni (o'zingizni ham) himoya qilasiz."),
    "commissar": ("🕵️ Komissar", "Har kecha bitta odamni tekshirasiz: u mafiyami yoki yo'q."),
    "civilian": ("👤 Tinch aholi", "Kunduzi muhokama qiling, mafiyani toping va ovoz bering."),
    "spy": ("🔍 Ayg'oqchi", "Kechasi bitta odamning aniq rolini bilib olasiz (jami 2 marta)."),
    "witch": ("🧙 Jodugar", "Bir martalik shifo va bir martalik zahar kuchingiz bor."),
    "avenger": ("⚔️ Qasoskor", "Agar sizni o'ldirishsa (yoki ovoz bilan chiqarishsa), o'zingiz bilan bitta odamni olib ketasiz."),
}

LEVELS = [
    (0, "🌱 Yangi boshlovchi"),
    (100, "🕶 Tajribali"),
    (300, "🔎 Detektiv"),
    (700, "🎩 Mafia Boss"),
    (1500, "👑 Afsona"),
]


def level(xp: int) -> str:
    name = LEVELS[0][1]
    for need, title in LEVELS:
        if xp >= need:
            name = title
    return name


def role_list(n: int):
    mafia = 1 if n < 8 else 2 if n < 12 else 3
    roles = ["mafia"] * mafia + ["doctor", "commissar"]
    if n >= 6:
        roles.append("spy")
    if n >= 7:
        roles.append("witch")
    if n >= 8:
        roles.append("avenger")
    roles += ["civilian"] * (n - len(roles))
    random.shuffle(roles)
    return roles[:n]


@dataclass
class Player:
    uid: int
    name: str
    role: str = "civilian"
    alive: bool = True
    spy_uses: int = 2
    heal_used: bool = False
    poison_used: bool = False


class Game:
    def __init__(self, bot: Bot, bot_username: str, chat_id: int, chat_title: str,
                 host_id: int, fast: bool, min_players: int):
        self.bot = bot
        self.bot_username = bot_username
        self.chat_id = chat_id
        self.chat_title = chat_title or "guruh"
        self.host_id = host_id
        self.fast = fast
        self.min_players = min_players
        self.t = TIMERS[fast]

        self.players: dict[int, Player] = {}
        self.phase = "lobby"   # lobby, starting, night, day, vote, tally, ended, cancelled
        self.round = 0
        self.task = None
        self.lobby_msg_id = None

        self.start_event = asyncio.Event()
        self.phase_event = asyncio.Event()

        self.expected: set = set()
        self.night_actions: dict = {}
        self.votes: dict = {}
        self.vote_msg_id = None
        self.no_vote = False

        self.revenge_for = None
        self.revenge_pick = None
        self.revenge_event = asyncio.Event()

    # ------------------------------------------------------------ yordamchilar
    def alive(self):
        return [p for p in self.players.values() if p.alive]

    def who(self, p: Player) -> str:
        return f'<a href="tg://user?id={p.uid}">{escape(p.name)}</a>'

    async def send(self, text, **kw):
        try:
            return await self.bot.send_message(self.chat_id, text, **kw)
        except Exception as e:  # noqa
            log.warning("send failed: %s", e)
            return None

    async def dm(self, uid, text, **kw):
        try:
            return await self.bot.send_message(uid, text, **kw)
        except Exception as e:  # noqa
            log.info("dm to %s failed: %s", uid, e)
            return None

    def cleanup(self):
        GAMES.pop(self.chat_id, None)
        for uid in list(self.players):
            if USER_GAME.get(uid) == self.chat_id:
                USER_GAME.pop(uid, None)

    # ------------------------------------------------------------ lobby
    def lobby_text(self) -> str:
        mode = "⚡️ Tezkor rejim" if self.fast else "🎭 Oddiy rejim"
        lst = "\n".join(f"{i}. {self.who(p)}" for i, p in enumerate(self.players.values(), 1))
        lst = lst or "— hali hech kim yo'q —"
        return (
            f"🎭 <b>MAFIA — yangi o'yin!</b>\n{mode}\n\n"
            f"👥 O'yinchilar ({len(self.players)}/{MAX_PLAYERS}):\n{lst}\n\n"
            f"Kamida <b>{self.min_players}</b> kishi kerak. Qo'shilish uchun tugmani bosing 👇"
        )

    def lobby_kb(self) -> InlineKeyboardMarkup:
        url = f"https://t.me/{self.bot_username}?start=join_{self.chat_id}"
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🎮 Qo'shilish", url=url)],
            [InlineKeyboardButton(text="▶️ Boshlash", callback_data=f"ls:{self.chat_id}")],
            [InlineKeyboardButton(text="❌ Bekor qilish", callback_data=f"lc:{self.chat_id}")],
        ])

    async def refresh_lobby(self):
        if not self.lobby_msg_id:
            return
        try:
            await self.bot.edit_message_text(
                self.lobby_text(), chat_id=self.chat_id, message_id=self.lobby_msg_id,
                reply_markup=self.lobby_kb())
        except Exception:
            pass

    async def add_player(self, uid: int, name: str) -> str:
        if self.phase != "lobby":
            return "closed"
        if uid in self.players:
            return "already"
        if len(self.players) >= MAX_PLAYERS:
            return "full"
        if USER_GAME.get(uid) not in (None, self.chat_id):
            return "busy"
        self.players[uid] = Player(uid, name)
        USER_GAME[uid] = self.chat_id
        await self.refresh_lobby()
        return "ok"

    async def lobby(self):
        try:
            await asyncio.wait_for(self.start_event.wait(), self.t["lobby"])
        except asyncio.TimeoutError:
            pass
        if self.phase == "cancelled":
            try:
                await self.bot.edit_message_reply_markup(
                    chat_id=self.chat_id, message_id=self.lobby_msg_id, reply_markup=None)
            except Exception:
                pass
            return
        if len(self.players) < self.min_players:
            self.phase = "cancelled"
            await self.send(f"❌ O'yin bekor qilindi — kamida {self.min_players} o'yinchi kerak edi.")
        else:
            self.phase = "starting"
        try:
            await self.bot.edit_message_reply_markup(
                chat_id=self.chat_id, message_id=self.lobby_msg_id, reply_markup=None)
        except Exception:
            pass

    # ------------------------------------------------------------ asosiy sikl
    async def run(self):
        try:
            await self.lobby()
            if self.phase == "cancelled":
                return
            db.group_game(self.chat_id)
            await self.assign_roles()
            while True:
                self.round += 1
                await self.night()
                if await self.check_end():
                    break
                await self.day()
                if await self.check_end():
                    break
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("game crashed")
            await self.send("⚠️ Xatolik yuz berdi, o'yin to'xtatildi.")
        finally:
            self.cleanup()

    async def assign_roles(self):
        roles = role_list(len(self.players))
        for p, r in zip(self.players.values(), roles):
            p.role = r
        mafia = [p for p in self.players.values() if p.role == "mafia"]
        comp = Counter(p.role for p in self.players.values())
        comp_txt = ", ".join(
            f"{ROLE_INFO[r][0]}" + (f" ×{c}" if c > 1 else "") for r, c in comp.items() if r != "civilian")
        await self.send(
            f"🎬 <b>O'yin boshlandi!</b> {len(self.players)} o'yinchi.\n"
            f"Tarkib: {comp_txt}, {ROLE_INFO['civilian'][0]} ×{comp.get('civilian', 0)}\n\n"
            f"📩 Rollar shaxsiy xabarda yuborildi. Tez orada birinchi tun tushadi..."
        )
        for p in self.players.values():
            title, desc = ROLE_INFO[p.role]
            text = f"🎭 Sizning rolingiz: <b>{title}</b>\n\n{desc}"
            if p.role == "mafia" and len(mafia) > 1:
                mates = ", ".join(escape(m.name) for m in mafia if m.uid != p.uid)
                text += f"\n\n🤝 Mafiya jamoangiz: {mates}"
            await self.dm(p.uid, text)
        await asyncio.sleep(5)

    # ------------------------------------------------------------ TUN
    def has_night_action(self, p: Player) -> bool:
        if p.role in ("mafia", "doctor", "commissar"):
            return True
        if p.role == "spy":
            return p.spy_uses > 0
        if p.role == "witch":
            return not (p.heal_used and p.poison_used)
        return False

    def target_kb(self, p: Player, kind: str, include_self: bool) -> InlineKeyboardMarkup:
        rows = []
        for t in self.alive():
            if t.uid == p.uid and not include_self:
                continue
            if kind == "k" and t.role == "mafia":
                continue
            rows.append([InlineKeyboardButton(
                text=t.name[:30], callback_data=f"n:{self.chat_id}:{kind}:{t.uid}")])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    async def send_night_menu(self, p: Player):
        r = p.role
        if r == "mafia":
            await self.dm(p.uid, "🔪 <b>Kimni yo'q qilamiz?</b>", reply_markup=self.target_kb(p, "k", False))
        elif r == "doctor":
            await self.dm(p.uid, "💉 <b>Kimni himoya qilasiz?</b>", reply_markup=self.target_kb(p, "h", True))
        elif r == "commissar":
            await self.dm(p.uid, "🕵️ <b>Kimni tekshirasiz?</b>", reply_markup=self.target_kb(p, "c", False))
        elif r == "spy":
            await self.dm(p.uid, f"🔍 <b>Kimning rolini bilmoqchisiz?</b> (qolgan: {p.spy_uses})",
                          reply_markup=self.target_kb(p, "s", False))
        elif r == "witch":
            row = []
            if not p.poison_used:
                row.append(InlineKeyboardButton(text="🧪 Zahar", callback_data=f"w:{self.chat_id}:p"))
            if not p.heal_used:
                row.append(InlineKeyboardButton(text="💊 Shifo", callback_data=f"w:{self.chat_id}:h"))
            kb = InlineKeyboardMarkup(inline_keyboard=[
                row, [InlineKeyboardButton(text="⏭ O'tkazib yuborish", callback_data=f"n:{self.chat_id}:x:0")]])
            await self.dm(p.uid, "🧙 <b>Bu kecha nima qilasiz?</b>", reply_markup=kb)

    def witch_menu(self, uid: int, kind: str):
        p = self.players.get(uid)
        if self.phase != "night" or not p or not p.alive or p.role != "witch" or uid not in self.expected:
            return None
        if uid in self.night_actions:
            return None
        if kind == "p" and not p.poison_used:
            return "🧪 <b>Kimga zahar beramiz?</b>", self.target_kb(p, "wp", False)
        if kind == "h" and not p.heal_used:
            return "💊 <b>Kimga shifo beramiz?</b>", self.target_kb(p, "wh", True)
        return None

    async def night(self):
        self.phase = "night"
        self.night_actions = {}
        self.expected = set()
        self.phase_event = asyncio.Event()
        await self.send(
            f"🌙 <b>{self.round}-tun.</b> Shahar uyquga ketdi...\n"
            f"Mafiya va maxsus rollar shaxsiy xabarda harakat qilsin! ⏳ {self.t['night']} soniya")
        for p in self.alive():
            if self.has_night_action(p):
                self.expected.add(p.uid)
                await self.send_night_menu(p)
        try:
            await asyncio.wait_for(self.phase_event.wait(), self.t["night"])
        except asyncio.TimeoutError:
            pass
        await self.resolve_night()

    async def record_action(self, uid: int, kind: str, target: int):
        p = self.players.get(uid)
        if self.phase != "night" or not p or not p.alive or uid not in self.expected:
            return None
        if uid in self.night_actions:
            return "⚠️ Siz allaqachon tanlov qilgansiz."
        allowed = {
            "k": p.role == "mafia",
            "h": p.role == "doctor",
            "c": p.role == "commissar",
            "s": p.role == "spy" and p.spy_uses > 0,
            "wp": p.role == "witch" and not p.poison_used,
            "wh": p.role == "witch" and not p.heal_used,
            "x": p.role == "witch",
        }
        if not allowed.get(kind):
            return None

        tp = None
        if kind != "x":
            tp = self.players.get(target)
            if not tp or not tp.alive:
                return None
            if kind == "k" and tp.role == "mafia":
                return None
            if kind in ("c", "s", "wp") and tp.uid == uid:
                return None

        self.night_actions[uid] = (kind, target)
        text = "✅ Tanlov qabul qilindi."
        if kind == "x":
            text = "⏭ Bu kecha o'tkazib yuborildi."
        elif kind == "c":
            verdict = "🔪 <b>MAFIYA!</b>" if tp.role == "mafia" else "✅ mafiya emas"
            text = f"🕵️ Tekshiruv natijasi: {escape(tp.name)} — {verdict}"
        elif kind == "s":
            p.spy_uses -= 1
            text = f"🔍 {escape(tp.name)} ning roli: <b>{ROLE_INFO[tp.role][0]}</b>"
        elif kind == "wp":
            p.poison_used = True
            text = f"🧪 {escape(tp.name)} ga zahar berildi."
        elif kind == "wh":
            p.heal_used = True
            text = f"💊 {escape(tp.name)} ga shifo berildi."
        elif kind == "k":
            text = f"🔪 Siz {escape(tp.name)} ni tanladingiz."
            for m in self.alive():
                if m.role == "mafia" and m.uid != uid:
                    await self.dm(m.uid, f"🔪 {escape(p.name)} → {escape(tp.name)}")

        if self.expected <= set(self.night_actions):
            self.phase_event.set()
        return text

    async def resolve_night(self):
        acts = self.night_actions
        kills = Counter(t for k, t in acts.values() if k == "k")
        victim = None
        if kills:
            top = max(kills.values())
            victim = random.choice([t for t, c in kills.items() if c == top])
        healed = {t for k, t in acts.values() if k in ("h", "wh")}
        poisoned = [t for k, t in acts.values() if k == "wp"]

        dead, saved = [], False
        if victim is not None:
            if victim in healed:
                saved = True
            else:
                dead.append(victim)
        for t in poisoned:
            if t not in dead:
                dead.append(t)

        msg = ["🌅 <b>Tong otdi!</b>"]
        if not dead:
            msg.append("Bu kecha hech kim o'lmadi." + (" 💉 Kimdir o'z vaqtida qutqarildi!" if saved else ""))
        else:
            if saved:
                msg.append("💉 Shifokor kimnidir qutqardi, lekin...")
            for uid in dead:
                p = self.players[uid]
                p.alive = False
                msg.append(f"💀 {self.who(p)} — {ROLE_INFO[p.role][0]} halok bo'ldi.")
        await self.send("\n".join(msg))
        for uid in dead:
            if self.players[uid].role == "avenger":
                await self.revenge(self.players[uid])

    # ------------------------------------------------------------ QASOSKOR
    async def revenge(self, p: Player):
        targets = [t for t in self.alive() if t.uid != p.uid]
        if not targets:
            return
        self.revenge_for = p.uid
        self.revenge_pick = None
        self.revenge_event = asyncio.Event()
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=t.name[:30], callback_data=f"r:{self.chat_id}:{t.uid}")]
            for t in targets])
        await self.dm(p.uid, "⚔️ <b>Siz halok bo'ldingiz!</b> Qasos vaqti — o'zingiz bilan kimni olib ketasiz? (20 soniya)",
                      reply_markup=kb)
        await self.send("⚔️ Qasoskor oxirgi nafasida kimnidir tanlamoqda...")
        try:
            await asyncio.wait_for(self.revenge_event.wait(), 20)
        except asyncio.TimeoutError:
            pass
        uid = self.revenge_pick or random.choice(targets).uid
        self.revenge_for = None
        v = self.players[uid]
        v.alive = False
        await self.send(f"⚔️ Qasoskor {self.who(v)} ni o'zi bilan olib ketdi! U — {ROLE_INFO[v.role][0]} edi.")

    def record_revenge(self, uid: int, target: int) -> bool:
        if self.revenge_for != uid or self.revenge_pick:
            return False
        t = self.players.get(target)
        if not t or not t.alive or target == uid:
            return False
        self.revenge_pick = target
        self.revenge_event.set()
        return True

    # ------------------------------------------------------------ KUN
    async def day(self):
        self.phase = "day"
        self.no_vote = False
        alive = self.alive()
        lst = "\n".join(f"• {self.who(p)}" for p in alive)
        await self.send(f"☀️ <b>{self.round}-kun.</b> Tirik o'yinchilar ({len(alive)}):\n{lst}\n\n"
                        f"💬 Muhokama boshlandi! ⏳ {self.t['day']} soniya")
        await self.random_event()
        await asyncio.sleep(self.t["day"])
        if self.no_vote:
            await self.send("🌫 Bugun ovoz berish yo'q. Tun yaqinlashmoqda...")
            return
        await self.vote()

    async def random_event(self):
        if random.random() > 0.4:
            return
        alive = self.alive()
        mafia = [p for p in alive if p.role == "mafia"]
        others = [p for p in alive if p.role != "mafia"]
        ev = random.choice(["fog", "fire", "truth"])
        if ev == "truth" and not (mafia and others):
            ev = "fog"
        if ev == "fog":
            self.no_vote = True
            await self.send("🌫 <b>Voqea: Qalin tuman!</b> Hech narsa ko'rinmaydi — bugun ovoz berish bo'lmaydi, faqat muhokama.")
        elif ev == "fire":
            p = random.choice(alive)
            await self.send(f"🔥 <b>Voqea: Yong'in!</b> Olov orasidan {self.who(p)} ning hujjatlari topildi — "
                            f"u <b>{ROLE_INFO[p.role][0]}</b>!")
        else:
            pair = [random.choice(mafia), random.choice(others)]
            random.shuffle(pair)
            await self.send(f"🔮 <b>Voqea: Haqiqat soati!</b> {self.who(pair[0])} va {self.who(pair[1])} — "
                            f"ularning orasida <b>bittasi mafiya</b>!")

    # ------------------------------------------------------------ OVOZ BERISH
    def vote_kb(self) -> InlineKeyboardMarkup:
        rows = [[InlineKeyboardButton(text=p.name[:30], callback_data=f"v:{self.chat_id}:{p.uid}")]
                for p in self.alive()]
        rows.append([InlineKeyboardButton(text="🙅 Hech kimni", callback_data=f"v:{self.chat_id}:0")])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    def vote_text(self) -> str:
        counts = Counter(t for t in self.votes.values() if t != 0)
        skip = sum(1 for t in self.votes.values() if t == 0)
        lines = [f"🗳 <b>Ovoz berish vaqti!</b> Kimni chiqaramiz? ⏳ {self.t['vote']} soniya",
                 f"Ovoz berganlar: {len(self.votes)}/{len(self.alive())}"]
        for p in self.alive():
            if counts.get(p.uid):
                lines.append(f"• {self.who(p)} — {counts[p.uid]} ovoz")
        if skip:
            lines.append(f"• 🙅 Hech kimni — {skip}")
        return "\n".join(lines)

    async def update_vote_msg(self, final=False):
        if not self.vote_msg_id:
            return
        try:
            await self.bot.edit_message_text(
                self.vote_text(), chat_id=self.chat_id, message_id=self.vote_msg_id,
                reply_markup=None if final else self.vote_kb())
        except Exception:
            pass

    async def record_vote(self, uid: int, target: int) -> str:
        if self.phase != "vote":
            return "late"
        p = self.players.get(uid)
        if not p or not p.alive:
            return "out"
        if target == uid:
            return "self"
        if target != 0:
            t = self.players.get(target)
            if not t or not t.alive:
                return "late"
        self.votes[uid] = target
        if all(a.uid in self.votes for a in self.alive()):
            self.phase_event.set()
        await self.update_vote_msg()
        return "ok"

    async def vote(self):
        self.phase = "vote"
        self.votes = {}
        self.phase_event = asyncio.Event()
        m = await self.send(self.vote_text(), reply_markup=self.vote_kb())
        self.vote_msg_id = m.message_id if m else None
        try:
            await asyncio.wait_for(self.phase_event.wait(), self.t["vote"])
        except asyncio.TimeoutError:
            pass
        self.phase = "tally"
        await self.update_vote_msg(final=True)

        counts = Counter(t for t in self.votes.values() if t != 0)
        if not counts:
            await self.send("🤷 Hech kim chiqarilmadi. Shahar tinchimadi...")
            return
        top = max(counts.values())
        leaders = [t for t, c in counts.items() if c == top]
        if len(leaders) > 1:
            await self.send("⚖️ Ovozlar teng bo'ldi — hech kim chiqarilmadi.")
            return
        v = self.players[leaders[0]]
        v.alive = False
        await self.send(f"🗳 {self.who(v)} <b>{top}</b> ovoz bilan chiqarildi. U — <b>{ROLE_INFO[v.role][0]}</b> edi!")
        if v.role == "avenger":
            await self.revenge(v)

    # ------------------------------------------------------------ YAKUN
    async def check_end(self) -> bool:
        alive = self.alive()
        m = sum(1 for p in alive if p.role == "mafia")
        t = len(alive) - m
        if m == 0:
            await self.finish("town")
            return True
        if m >= t:
            await self.finish("mafia")
            return True
        return False

    async def finish(self, winner: str):
        self.phase = "ended"
        head = "🎉 <b>Tinch aholi g'alaba qildi!</b>" if winner == "town" else "🔪 <b>Mafiya g'alaba qildi!</b>"
        lines, winners = [], []
        for p in self.players.values():
            team = "mafia" if p.role == "mafia" else "town"
            won = team == winner
            gained = (30 if won else 10) + (10 if p.alive else 0)
            db.add_result(p.uid, gained, won)
            if won:
                winners.append(p)
            lines.append(f"{'🏆' if won else '💔'} {self.who(p)} — {ROLE_INFO[p.role][0]} (+{gained} XP)")

        awards = []
        if winners:
            awards.append(f"🌟 Eng yaxshi o'yinchi: {self.who(random.choice(winners))}")
        if winner == "mafia":
            liars = [p for p in self.players.values() if p.role == "mafia"]
            if liars:
                awards.append(f"🎭 Eng katta yolg'onchi: {self.who(random.choice(liars))}")
        else:
            coms = [p for p in self.players.values() if p.role == "commissar"]
            if coms:
                awards.append(f"🔎 Eng o'tkir ko'z: {self.who(coms[0])}")

        text = f"{head}\n\n" + "\n".join(lines)
        if awards:
            text += "\n\n" + "\n".join(awards)
        text += "\n\nYangi o'yin uchun /game yoki tezkor rejim uchun /fast"

        share = ("https://t.me/share/url?url=" + quote(f"https://t.me/{self.bot_username}") +
                 "&text=" + quote("Do'stlar bilan Mafia o'ynaymiz! 🎭"))
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Botni o'z guruhingga qo'sh",
                                  url=f"https://t.me/{self.bot_username}?startgroup=true")],
            [InlineKeyboardButton(text="📣 Do'stlarga ulashish", url=share)],
        ])
        await self.send(text, reply_markup=kb)
