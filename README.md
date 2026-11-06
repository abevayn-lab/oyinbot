# Telegram Mafia Bot

## O'rnatish
1. Python 3.10+ o'rnating.
2. `pip install -r requirements.txt`
3. `.env.example` faylidan `.env` yarating va `BOT_TOKEN`, `ADMIN_IDS` ni to'ldiring.
4. Botni `@BotFather` orqali yarating. Botni guruhga qo'shing va kerakli ruxsatlarni bering.
5. `python main.py` bilan ishga tushiring.

## Buyruqlar
- Guruh: `/game`, `/fast`, `/cancelgame`
- Shaxsiy: `/start`, `/help`, `/profile`, `/top`, `/stats`
- Admin: `/admin`, `/addtg`, `/addlink`, `/subs`, `/delsub`, `/broadcast`

## Majburiy obuna
- Telegram kanal: `/addtg @kanal | Kanal nomi | https://t.me/kanal`
  Botni kanalga admin qilib qo'ying, aks holda a'zolikni tekshira olmaydi.
- Tashqi havola (Instagram va boshqalar): `/addlink Sahifa nomi | https://instagram.com/...`
  Telegram tashqi platformadagi obunani tekshira olmaydi; foydalanuvchi tugma orqali qo'lda tasdiqlaydi.

## Eslatma
- O'yin jarayoni xotirada saqlanadi; bot qayta ishga tushsa, davom etayotgan o'yinlar tiklanmaydi.
- SQLite bazasi `mafia.db` nomi bilan avtomatik yaratiladi.
- Botni 24/7 ishlatish uchun VPS yoki doimiy ishlaydigan hosting kerak.
