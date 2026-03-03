import re
from datetime import datetime, timedelta

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import (
    Message,
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
    WebAppInfo,
    LabeledPrice,
    Update
)

import db
from config import (
    BOT_TOKEN, WEBHOOK_URL, ADMIN_TOKEN, PAYMENT_PROVIDER_TOKEN, ADMIN_USERNAME
)

app = FastAPI()
bot = Bot(BOT_TOKEN)
dp = Dispatcher()


# ------------------ PLANS ------------------
PLANS = {
    "TRIAL7": {"title": "7 kun sinov", "days": 7, "amount": 7000},
    "M1": {"title": "Oylik", "days": 30, "amount": 37000},
    "M3": {"title": "3 oylik", "days": 90, "amount": 111000},
    "M6": {"title": "6 oylik", "days": 180, "amount": 197000},
    "Y1": {"title": "Yillik", "days": 365, "amount": 297000},
}

WELCOME_TEXT = (
    "💙 *HAMYONIM AI*\n\n"
    "Pastdagi tugmalar orqali ishlang.\n"
    "Yozish ham mumkin: `25000 taksi` yoki `+500000 oylik`"
)

UPSELL_TEXT = (
    "✨ Ko'rganingizdek, kirim-chiqim va qarzlarni nazorat qilish juda ham oson.\n\n"
    "💎 Endi siz uchun ochiladigan imkoniyatlar:\n"
    "• Dashboard\n• Kirim-chiqimlar hisoboti\n• Qarzlar ro'yxati\n"
    "• Xarajatlarga Limit o'rnatish\n• Cheksiz kirim-chiqim\n• Balans nazorati\n"
    "va boshqalar\n\n"
    "✅ To'liq foydalanish uchun tarif tanlang 👇"
)


# ------------------ UI ------------------
def main_kb():
    # Telegram WebApp tugmasi
    web_btn = KeyboardButton(
        text="📊 Dashboard",
        web_app=WebAppInfo(url=f"{WEBHOOK_URL}/app") if WEBHOOK_URL else None
    )

    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="➕ Kirim"), KeyboardButton(text="➖ Chiqim")],
            [KeyboardButton(text="🎤 Ovoz orqali qo‘shish"), web_btn],
            [KeyboardButton(text="📈 Hisobot"), KeyboardButton(text="🤝 Qarzlar")],
            [KeyboardButton(text="🎯 Limitlar"), KeyboardButton(text="👤 Kabinet")],
            [KeyboardButton(text="💎 Tariflar"), KeyboardButton(text="🆘 Support")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Masalan: 25000 taksi | +500000 oylik"
    )


def support_inline():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"👤 Admin: @{ADMIN_USERNAME}", url=f"https://t.me/{ADMIN_USERNAME}")]
    ])


def plans_inline():
    rows = []
    for k, v in PLANS.items():
        rows.append([InlineKeyboardButton(text=f"💳 {v['title']} — {v['amount']:,} so‘m", callback_data=f"buy:{k}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ------------------ PARSER ------------------
AMOUNT_RE = re.compile(r"^\s*(\+)?\s*([\d_]+)\s*(k|m)?\s+(.+?)\s*$", re.I)

def parse_amount(text: str):
    """
    Examples:
      25000 taksi
      25k oziq-ovqat non
      +500000 oylik
      +2m bonus
    """
    m = AMOUNT_RE.match(text or "")
    if not m:
        return None

    plus, num, mult, rest = m.groups()
    num = int(num.replace("_", ""))
    mult = (mult or "").lower()
    if mult == "k":
        num *= 1000
    elif mult == "m":
        num *= 1_000_000

    parts = rest.strip().split()
    category = parts[0].lower()
    note = " ".join(parts[1:]).strip() if len(parts) > 1 else None
    ttype = "income" if plus else "expense"
    return ttype, num, category, note


# ------------------ BOT CORE ------------------
@dp.message(CommandStart())
async def start(m: Message):
    await db.ensure_user(m.from_user.id)
    await m.answer(WELCOME_TEXT, parse_mode="Markdown", reply_markup=main_kb())


@dp.message(F.text == "🆘 Support")
async def support(m: Message):
    await db.ensure_user(m.from_user.id)
    await m.answer("🆘 Yordam uchun adminga yozing:", reply_markup=main_kb())
    await m.answer("👇", reply_markup=support_inline())


@dp.message(F.text == "💎 Tariflar")
async def tariffs(m: Message):
    await db.ensure_user(m.from_user.id)
    await m.answer(UPSELL_TEXT, reply_markup=main_kb())
    await m.answer("Tarif tanlang:", reply_markup=plans_inline())


@dp.callback_query(F.data.startswith("buy:"))
async def buy_cb(cb):
    await db.ensure_user(cb.from_user.id)
    plan = cb.data.split(":", 1)[1].upper()
    if plan not in PLANS:
        await cb.answer("Noto‘g‘ri tarif", show_alert=True)
        return

    amount = PLANS[plan]["amount"]
    await db.add_payment(cb.from_user.id, plan, amount, status="pending")

    # Telegram Payments bo‘lsa invoice yuboramiz
    if PAYMENT_PROVIDER_TOKEN:
        prices = [LabeledPrice(label=PLANS[plan]["title"], amount=amount * 100)]
        await bot.send_invoice(
            chat_id=cb.message.chat.id,
            title=f"HAMYONIM AI — {PLANS[plan]['title']}",
            description="Obuna to‘lovi",
            provider_token=PAYMENT_PROVIDER_TOKEN,
            currency="UZS",
            prices=prices,
            payload=f"sub:{plan}:{cb.from_user.id}",
        )
        await cb.answer("Invoice yuborildi ✅")
    else:
        await bot.send_message(
            cb.message.chat.id,
            f"✅ Buyurtma qabul qilindi: *{PLANS[plan]['title']}* — *{amount:,}* so‘m.\n\n"
            "Hozircha to‘lov *manual*.\n"
            "To‘lov qilib chekni rasm qilib yuboring — admin tasdiqlaydi.",
            parse_mode="Markdown",
            reply_markup=main_kb()
        )
        await cb.answer("Manual to‘lov ✅")


@dp.pre_checkout_query()
async def pre_checkout(pre_checkout_query):
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)


@dp.message(F.successful_payment)
async def successful_payment(m: Message):
    payload = m.successful_payment.invoice_payload  # sub:PLAN:USERID
    try:
        _, plan, uid = payload.split(":")
        uid = int(uid)
        if uid != m.from_user.id:
            await m.answer("Xatolik: user mos emas.", reply_markup=main_kb())
            return

        days = PLANS[plan]["days"]
        expires = (datetime.now() + timedelta(days=days)).isoformat(timespec="seconds")
        await db.set_subscription(uid, plan, expires)
        await db.add_payment(uid, plan, PLANS[plan]["amount"], status="paid", payload=payload)

        await m.answer(
            f"🎉 Obuna aktiv!\n*{PLANS[plan]['title']}*\nYakun: `{expires}`",
            parse_mode="Markdown",
            reply_markup=main_kb()
        )
    except Exception:
        await m.answer("To‘lov qaydida xatolik. Admin tekshiradi.", reply_markup=main_kb())


@dp.message(F.text == "➕ Kirim")
async def income_hint(m: Message):
    await m.answer("➕ Kirim yozing:\n`+500000 oylik`", parse_mode="Markdown", reply_markup=main_kb())


@dp.message(F.text == "➖ Chiqim")
async def expense_hint(m: Message):
    await m.answer("➖ Chiqim yozing:\n`25000 taksi`", parse_mode="Markdown", reply_markup=main_kb())


@dp.message(F.text == "🎤 Ovoz orqali qo‘shish")
async def voice_hint(m: Message):
    await m.answer(
        "🎤 Ovoz yuboring.\nMasalan: “taksi uchun 5000 so‘m” yoki “kirim 500 ming oylik”.",
        reply_markup=main_kb()
    )


@dp.message(F.voice)
async def voice_received(m: Message):
    # Hozircha professional javob: keyin STT ulaymiz
    await m.answer(
        "🎤 Ovoz qabul qilindi ✅\n"
        "Hozir STT (ovozni matnga aylantirish) modulini ulayapmiz.\n"
        "Tez orada ovozdan avtomatik hisobga qo‘shadi.",
        reply_markup=main_kb()
    )
    # Keyingi bosqichda: voice file download + STT + parse + db.add_tx()


@dp.message(F.text == "📈 Hisobot")
async def report(m: Message):
    await db.ensure_user(m.from_user.id)
    inc, exp = await db.get_month_stats(m.from_user.id)
    bal = inc - exp
    await m.answer(
        f"📈 *Bu oy*\n\n"
        f"➕ Kirim: *{inc:,}* so‘m\n"
        f"➖ Chiqim: *{exp:,}* so‘m\n"
        f"🟦 Balans: *{bal:,}* so‘m",
        parse_mode="Markdown",
        reply_markup=main_kb()
    )


@dp.message(F.text == "🤝 Qarzlar")
async def debts(m: Message):
    await m.answer(
        "🤝 Qarzlar bo‘limi (keyingi bosqichda):\n"
        "• Qarz oldim / berdim\n• Ro‘yxat\n• Yopish\n",
        reply_markup=main_kb()
    )


@dp.message(F.text == "🎯 Limitlar")
async def limits(m: Message):
    await m.answer(
        "🎯 Limitlar bo‘limi (keyingi bosqichda):\n"
        "• Kategoriya bo‘yicha oy limit\n• Limit oshsa ogohlantirish\n",
        reply_markup=main_kb()
    )


@dp.message(F.text == "👤 Kabinet")
async def cabinet(m: Message):
    await m.answer(
        "👤 Kabinet:\n"
        "• Profil\n• Obuna holati\n• Sozlamalar\n",
        reply_markup=main_kb()
    )


@dp.message()
async def handle_text(m: Message):
    # Tugmalardan tashqari oddiy yozuvlarni ham qabul qilamiz (professional botlarda shunday)
    await db.ensure_user(m.from_user.id)

    parsed = parse_amount(m.text or "")
    if not parsed:
        # “/bos” demaymiz — faqat yumshoq ko‘rsatma
        await m.answer("Tushunmadim 🙂 Pastdagi tugmalardan foydalaning yoki: `25000 taksi`", parse_mode="Markdown",
                       reply_markup=main_kb())
        return

    ttype, amount, category, note = parsed
    await db.add_tx(m.from_user.id, ttype, amount, category, note)

    sign = "+" if ttype == "income" else "-"
    await m.answer(
        f"✅ Hisobga qo‘shildi!\n"
        f"{'➕ Kirim' if ttype=='income' else '➖ Chiqim'}: *{sign}{amount:,}* so‘m\n"
        f"🏷️ Kategoriya: *{category}*\n"
        f"{('📝 Izoh: ' + note) if note else ''}",
        parse_mode="Markdown",
        reply_markup=main_kb()
    )


# ------------------ FASTAPI: WEBHOOK + WEBAPP + ADMIN ------------------
@app.on_event("startup")
async def on_startup():
    await db.init_db()
    if WEBHOOK_URL:
        await bot.set_webhook(f"{WEBHOOK_URL}/webhook")


@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    update = Update.model_validate(data)
    await dp.feed_update(bot, update)
    return {"ok": True}


@app.get("/app", response_class=HTMLResponse)
async def web_app():
    # Minimal professional dark UI (WebApp)
    return HTMLResponse("""
<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>HAMYONIM AI — Dashboard</title>
  <script src="https://telegram.org/js/telegram-web-app.js"></script>
  <style>
    body{margin:0;font-family:system-ui;background:#0b1220;color:#eaf0ff}
    .wrap{padding:16px;max-width:720px;margin:0 auto}
    .h{font-size:20px;font-weight:800;margin:6px 0 14px}
    .grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}
    .card{background:#121a2b;border:1px solid rgba(255,255,255,.06);border-radius:16px;padding:14px}
    .big{font-size:28px;font-weight:900}
    .muted{opacity:.7;font-size:12px}
    .btn{margin-top:12px;width:100%;padding:12px;border-radius:14px;border:none;background:#1690f5;color:#fff;font-weight:800}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="h">📊 HAMYONIM AI — Dashboard</div>
    <div class="grid">
      <div class="card">
        <div class="muted">Balans (demo)</div>
        <div class="big">—</div>
      </div>
      <div class="card">
        <div class="muted">Bu oy chiqim (demo)</div>
        <div class="big">—</div>
      </div>
      <div class="card">
        <div class="muted">Bu oy kirim (demo)</div>
        <div class="big">—</div>
      </div>
      <div class="card">
        <div class="muted">Qarzlar (demo)</div>
        <div class="big">—</div>
      </div>
    </div>
    <button class="btn" onclick="Telegram.WebApp.close()">✅ Yopish</button>
  </div>
</body>
</html>
""")


def check_admin(req: Request):
    token = req.headers.get("x-admin-token") or req.query_params.get("token")
    if token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")


@app.get("/admin", response_class=HTMLResponse)
async def admin_home(request: Request):
    check_admin(request)
    return HTMLResponse(f"""
    <h2>HAMYONIM AI — Admin</h2>
    <p>Admin: <b>@{ADMIN_USERNAME}</b></p>
    <ul>
      <li><a href="/admin/ping?token={ADMIN_TOKEN}">Ping</a></li>
      <li><a href="/admin/users?token={ADMIN_TOKEN}">Users (JSON)</a></li>
      <li>/admin/subset?token=...&user_id=123&plan=M1&days=30</li>
      <li>/admin/broadcast (POST) token=... message=...</li>
    </ul>
    """)


@app.get("/admin/ping")
async def admin_ping(request: Request):
    check_admin(request)
    return {"ok": True}


@app.get("/admin/users")
async def admin_users(request: Request):
    check_admin(request)
    import aiosqlite
    async with aiosqlite.connect(db.DB_PATH) as conn:
        cur = await conn.execute(
            "SELECT user_id, plan, sub_expires_at, is_banned, created_at FROM users ORDER BY created_at DESC LIMIT 300"
        )
        rows = await cur.fetchall()
    return {"users": rows}


@app.get("/admin/subset")
async def admin_set_sub(request: Request, user_id: int, plan: str, days: int):
    check_admin(request)
    expires = (datetime.now() + timedelta(days=days)).isoformat(timespec="seconds")
    await db.set_subscription(user_id, plan.upper(), expires)
    return {"ok": True, "user_id": user_id, "plan": plan.upper(), "expires": expires}


@app.post("/admin/broadcast")
async def admin_broadcast(request: Request):
    check_admin(request)
    form = await request.form()
    msg = (form.get("message") or "").strip()
    if not msg:
        return PlainTextResponse("message required", status_code=400)

    import aiosqlite
    async with aiosqlite.connect(db.DB_PATH) as conn:
        cur = await conn.execute("SELECT user_id FROM users WHERE is_banned=0")
        users = [r[0] for r in await cur.fetchall()]

    sent = 0
    for uid in users[:2000]:
        try:
            await bot.send_message(uid, msg, reply_markup=main_kb())
            sent += 1
        except:
            pass
    return {"sent": sent, "total": len(users)}
