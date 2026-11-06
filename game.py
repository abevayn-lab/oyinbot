
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
