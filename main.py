import re
from datetime import datetime, timedelta, date
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, LabeledPrice
from aiogram.filters import CommandStart, Command

import db
from config import BOT_TOKEN, WEBHOOK_URL, ADMIN_TOKEN, PAYMENT_PROVIDER_TOKEN

app = FastAPI()
templates = Jinja2Templates(directory=".")

bot = Bot(BOT_TOKEN)
dp = Dispatcher()

# ---------- Pricing / Plans ----------
PLANS = {
    "TRIAL7": {"title": "7 kun sinov", "days": 7, "amount": 7000},
    "M1": {"title": "Oylik", "days": 30, "amount": 37000},
    "M3": {"title": "3 oylik", "days": 90, "amount": 111000},
    "M6": {"title": "6 oylik", "days": 180, "amount": 197000},
    "Y1": {"title": "Yillik", "days": 365, "amount": 297000},
}

WELCOME_UPSELL = (
    "✨ Ko'rganingizdek, kirim-chiqim va qarzlarni nazorat qilish juda ham oson!\n\n"
    "💎 Endi siz uchun ochiladigan imkoniyatlar:\n"
    "- Dashboard\n- Kirim-chiqimlar hisoboti\n- Qarzlar ro'yxati\n"
    "- Xarajatlarga Limit o'rnatish\n- Cheksiz kirim-chiqimlarni yozish\n"
    "- Balanslar nazorati\nva boshqalar\n\n"
    "✅ *HAMYONIM AI*dan to'liq foydalanish uchun tarif tanlang:"
)

AMOUNT_RE = re.compile(r"^\s*(\+)?\s*([\d_]+)\s*(k|m)?\s+(.+?)\s*$", re.I)

def parse_amount(text: str):
    m = AMOUNT_RE.match(text or "")
    if not m:
        return None
    plus, num, mult, rest = m.groups()
    num = int(num.replace("_", ""))
    mult = (mult or "").lower()
    if mult == "k": num *= 1000
    if mult == "m": num *= 1_000_000
    parts = rest.strip().split()
    category = parts[0].lower()
    note = " ".join(parts[1:]).strip() if len(parts) > 1 else None
    ttype = "income" if plus else "expense"
    return ttype, num, category, note

def is_sub_active(user_row):
    # row: (user_id, plan, sub_expires_at, is_banned)
    if not user_row: return False
    _, plan, exp, banned = user_row
    if banned: return False
    if not exp: return False
    try:
        return datetime.fromisoformat(exp) > datetime.now()
    except:
        return False

async def require_sub(user_id: int) -> bool:
    row = await db.get_user(user_id)
    return is_sub_active(row)

# ---------- BOT ----------
@dp.message(CommandStart())
async def start(m: Message):
    await db.ensure_user(m.from_user.id)
    await m.answer(
        "Salom! Men *HAMYONIM AI* 💙\n"
        "Kirim/chiqim, qarz, limit va hisobotlarni yuritaman.\n\n"
        "Yozish misoli:\n"
        "`25000 taksi`  yoki  `+500000 oylik`\n\n"
        "Tariflarni ko‘rish: /tarif",
        parse_mode="Markdown"
    )

@dp.message(Command("tarif"))
async def tarif(m: Message):
    text = WELCOME_UPSELL + "\n\n"
    for k, v in PLANS.items():
        text += f"• *{v['title']}* — `{v['amount']:,}` so‘m\n"
    text += "\nTo‘lov: /sotibol TRIAL7 | M1 | M3 | M6 | Y1"
    await m.answer(text, parse_mode="Markdown")

@dp.message(Command("sotibol"))
async def buy(m: Message):
    await db.ensure_user(m.from_user.id)
    parts = (m.text or "").split()
    if len(parts) < 2:
        await m.answer("Plan yozing: /sotibol M1 (yoki TRIAL7/M3/M6/Y1)")
        return
    plan = parts[1].upper()
    if plan not in PLANS:
        await m.answer("Noto‘g‘ri plan. /tarif dan tanlang.")
        return

    amount = PLANS[plan]["amount"]
    await db.add_payment(m.from_user.id, plan, amount, status="pending", payload=None)

    # 1) Agar Telegram Payments provider token bo‘lsa → invoice yuboramiz
    if PAYMENT_PROVIDER_TOKEN:
        prices = [LabeledPrice(label=PLANS[plan]["title"], amount=amount * 100)]  # tiyin
        await bot.send_invoice(
            chat_id=m.chat.id,
            title=f"HAMYONIM AI — {PLANS[plan]['title']}",
            description="Obuna to‘lovi",
            provider_token=PAYMENT_PROVIDER_TOKEN,
            currency="UZS",
            prices=prices,
            payload=f"sub:{plan}:{m.from_user.id}"
        )
    else:
        # 2) Aks holda manual: user chek yuboradi, admin tasdiqlaydi
        await m.answer(
            f"✅ Buyurtma qabul qilindi: *{PLANS[plan]['title']}* — *{amount:,}* so‘m.\n\n"
            "Hozircha to‘lov *manual* rejimda.\n"
            "To‘lov qiling va chekni shu botga rasm qilib yuboring — admin tasdiqlaydi.",
            parse_mode="Markdown"
        )

# Telegram Payments: pre_checkout + successful_payment
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
            await m.answer("Xatolik: user mos emas.")
            return
        days = PLANS[plan]["days"]
        expires = (datetime.now() + timedelta(days=days)).isoformat(timespec="seconds")
        await db.set_subscription(uid, plan, expires)
        await db.add_payment(uid, plan, PLANS[plan]["amount"], status="paid", payload=payload)
        await m.answer(f"🎉 Obuna aktiv! *{PLANS[plan]['title']}* yakun: `{expires}`", parse_mode="Markdown")
    except Exception:
        await m.answer("To‘lov qaydida xatolik. Admin tekshiradi.")

@dp.message()
async def handle_text(m: Message):
    await db.ensure_user(m.from_user.id)

    # Sub bo‘lmasa — faqat demo ruxsat, keyin upsell
    # (xohlasang demo limit: kuniga 3 ta yozuv)
    # Hozir: sub bo‘lmasa ham yozishga ruxsat beramiz, lekin premium funksiyalar bloklanadi.
    parsed = parse_amount(m.text or "")
    if not parsed:
        await m.answer("Yozish misoli: `25000 taksi` yoki `+500000 oylik`.\nTarif: /tarif", parse_mode="Markdown")
        return

    ttype, amount, category, note = parsed

    # Premium misol: LIMIT/DEBT/HISOBOT faqat obunada bo‘lsin (keyin qo‘shamiz)
    # Hozir faqat tx yozamiz:
    from aiosqlite import connect
    async with connect(db.DB_PATH) as conn:
        await conn.execute(
            "INSERT INTO tx(user_id, ttype, amount, category, note, created_at) VALUES(?,?,?,?,?,?)",
            (m.from_user.id, ttype, amount, category, note, datetime.now().isoformat(timespec="seconds"))
        )
        await conn.commit()

    sign = "+" if ttype == "income" else "-"
    await m.answer(f"✅ Saqlandi: *{sign}{amount:,}* so‘m | *{category}*", parse_mode="Markdown")


# ---------- WEBHOOK wiring for Railway ----------
@app.on_event("startup")
async def on_startup():
    await db.init_db()
    if WEBHOOK_URL:
        await bot.set_webhook(f"{WEBHOOK_URL}/webhook")

from aiogram.types import Update

@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    update = Update.model_validate(data)
    await dp.feed_update(bot, update)
    return {"ok": True}

# ---------- ADMIN PANEL ----------
def check_admin(req: Request):
    token = req.headers.get("x-admin-token") or req.query_params.get("token")
    if token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")

@app.get("/admin", response_class=HTMLResponse)
async def admin_home(request: Request):
    check_admin(request)
    # minimal dashboard
    import aiosqlite
    async with aiosqlite.connect(db.DB_PATH) as conn:
        u = (await (await conn.execute("SELECT COUNT(*) FROM users")).fetchone())[0]
        p = (await (await conn.execute("SELECT COUNT(*) FROM payments WHERE status='paid'")).fetchone())[0]
    html = f"""
    <h2>HAMYONIM AI — Admin</h2>
    <p>Users: <b>{u}</b></p>
    <p>Paid payments: <b>{p}</b></p>
    <p>Endpoints:</p>
    <ul>
      <li>/admin/users?token=...</li>
      <li>/admin/subset?token=...&user_id=123&plan=M1&days=30</li>
      <li>/admin/broadcast?token=... (POST: message=...)</li>
    </ul>
    """
    return HTMLResponse(html)

@app.get("/admin/users")
async def admin_users(request: Request):
    check_admin(request)
    import aiosqlite
    async with aiosqlite.connect(db.DB_PATH) as conn:
        cur = await conn.execute("SELECT user_id, plan, sub_expires_at, is_banned FROM users ORDER BY created_at DESC LIMIT 200")
        rows = await cur.fetchall()
    return {"users": rows}

@app.get("/admin/subset")
async def admin_set_sub(request: Request, user_id: int, plan: str, days: int):
    check_admin(request)
    expires = (datetime.now() + timedelta(days=days)).isoformat(timespec="seconds")
    await db.set_subscription(user_id, plan.upper(), expires)
    return {"ok": True, "user_id": user_id, "plan": plan, "expires": expires}

@app.post("/admin/broadcast")
async def admin_broadcast(request: Request):
    check_admin(request)
    form = await request.form()
    msg = form.get("message")
    if not msg:
        return PlainTextResponse("message required", status_code=400)

    import aiosqlite
    async with aiosqlite.connect(db.DB_PATH) as conn:
        cur = await conn.execute("SELECT user_id FROM users WHERE is_banned=0")
        users = [r[0] for r in await cur.fetchall()]

    sent = 0
    for uid in users[:2000]:  # xavfsiz limit
        try:
            await bot.send_message(uid, msg)
            sent += 1
        except:
            pass
    return {"sent": sent, "total": len(users)}
