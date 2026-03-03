import os
import re
import json
import hmac
import hashlib
from datetime import datetime, timedelta, date
from urllib.parse import parse_qsl

import aiosqlite
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import (
    Update, Message,
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
    WebAppInfo,
    LabeledPrice
)

# -------------------- ENV / CONFIG --------------------
def norm_https(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    if not url:
        return ""
    if not url.startswith("http"):
        url = "https://" + url
    if not url.startswith("https://"):
        url = url.replace("http://", "https://", 1)
    return url.rstrip("/")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
WEBHOOK_URL = norm_https(os.getenv("WEBHOOK_URL", ""))  # e.g. https://hamyonimai-production.up.railway.app
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "hisobchiAI_2026_new").strip()
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "justyaviz7").strip().lstrip("@")
PAYMENT_PROVIDER_TOKEN = os.getenv("PAYMENT_PROVIDER_TOKEN", "").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()

CURRENCY = "UZS"
APP_TITLE = "HAMYONIM AI"

if not BOT_TOKEN:
    # Railway logsda ko‘rinsin
    print("ERROR: BOT_TOKEN is missing!")

# -------------------- APP / BOT --------------------
app = FastAPI()
bot = Bot(BOT_TOKEN)
dp = Dispatcher()

# -------------------- DB --------------------
DB_PATH = "hamyonim.db"

def now_iso():
    return datetime.now().isoformat(timespec="seconds")

def month_prefix():
    return date.today().strftime("%Y-%m")

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS users(
            user_id INTEGER PRIMARY KEY,
            created_at TEXT NOT NULL,
            plan TEXT DEFAULT 'FREE',
            sub_expires_at TEXT,
            is_banned INTEGER DEFAULT 0
        )
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS tx(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            ttype TEXT NOT NULL, -- income/expense
            amount INTEGER NOT NULL,
            category TEXT NOT NULL,
            note TEXT,
            tx_date TEXT NOT NULL, -- YYYY-MM-DD
            created_at TEXT NOT NULL
        )
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS limits(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            category TEXT NOT NULL,
            monthly_limit INTEGER NOT NULL
        )
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS payments(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            plan TEXT NOT NULL,
            amount INTEGER NOT NULL,
            status TEXT NOT NULL, -- pending/paid/failed/manual
            payload TEXT,
            created_at TEXT NOT NULL
        )
        """)
        await db.commit()

async def ensure_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id FROM users WHERE user_id=?", (user_id,))
        row = await cur.fetchone()
        if not row:
            await db.execute(
                "INSERT INTO users(user_id, created_at) VALUES(?, ?)",
                (user_id, now_iso())
            )
            await db.commit()

async def get_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT user_id, plan, sub_expires_at, is_banned FROM users WHERE user_id=?",
            (user_id,)
        )
        return await cur.fetchone()

async def set_subscription(user_id: int, plan: str, expires_at_iso: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET plan=?, sub_expires_at=? WHERE user_id=?",
            (plan, expires_at_iso, user_id)
        )
        await db.commit()

async def add_payment(user_id: int, plan: str, amount: int, status: str, payload: str | None = None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO payments(user_id, plan, amount, status, payload, created_at) VALUES(?,?,?,?,?,?)",
            (user_id, plan, amount, status, payload, now_iso())
        )
        await db.commit()

async def add_tx(user_id: int, ttype: str, amount: int, category: str, note: str | None, tx_date: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO tx(user_id, ttype, amount, category, note, tx_date, created_at) VALUES(?,?,?,?,?,?,?)",
            (user_id, ttype, amount, category, note, tx_date, now_iso())
        )
        await db.commit()

async def month_sums(user_id: int):
    pref = month_prefix()
    async with aiosqlite.connect(DB_PATH) as db:
        cur1 = await db.execute(
            "SELECT COALESCE(SUM(amount),0) FROM tx WHERE user_id=? AND ttype='income' AND substr(tx_date,1,7)=?",
            (user_id, pref)
        )
        inc = (await cur1.fetchone())[0]
        cur2 = await db.execute(
            "SELECT COALESCE(SUM(amount),0) FROM tx WHERE user_id=? AND ttype='expense' AND substr(tx_date,1,7)=?",
            (user_id, pref)
        )
        exp = (await cur2.fetchone())[0]
    return int(inc), int(exp)

async def get_limit(user_id: int, category: str):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT monthly_limit FROM limits WHERE user_id=? AND category=?",
            (user_id, category)
        )
        row = await cur.fetchone()
        return int(row[0]) if row else None

async def set_limit(user_id: int, category: str, monthly_limit: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT id FROM limits WHERE user_id=? AND category=?",
            (user_id, category)
        )
        row = await cur.fetchone()
        if row:
            await db.execute(
                "UPDATE limits SET monthly_limit=? WHERE user_id=? AND category=?",
                (monthly_limit, user_id, category)
            )
        else:
            await db.execute(
                "INSERT INTO limits(user_id, category, monthly_limit) VALUES(?,?,?)",
                (user_id, category, monthly_limit)
            )
        await db.commit()

async def category_month_sum(user_id: int, category: str):
    pref = month_prefix()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT COALESCE(SUM(amount),0) FROM tx WHERE user_id=? AND ttype='expense' AND category=? AND substr(tx_date,1,7)=?",
            (user_id, category, pref)
        )
        v = (await cur.fetchone())[0]
    return int(v)

async def top_expense_categories(user_id: int, limit: int = 5):
    pref = month_prefix()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            SELECT category, COALESCE(SUM(amount),0) s
            FROM tx
            WHERE user_id=? AND ttype='expense' AND substr(tx_date,1,7)=?
            GROUP BY category
            ORDER BY s DESC
            LIMIT ?
            """,
            (user_id, pref, limit)
        )
        return await cur.fetchall()

async def users_count():
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM users")
        return int((await cur.fetchone())[0])


# -------------------- PLANS --------------------
PLANS = {
    "TRIAL7": {"title": "7 kun sinov", "days": 7, "amount": 7000},
    "M1": {"title": "Oylik", "days": 30, "amount": 37000},
    "M3": {"title": "3 oylik", "days": 90, "amount": 111000},
    "M6": {"title": "6 oylik", "days": 180, "amount": 197000},
    "Y1": {"title": "Yillik", "days": 365, "amount": 297000},
}

WELCOME = (
    f"💙 *{APP_TITLE}*\n\n"
    "✅ Kirim-chiqimni tugmalar bilan boshqarasiz.\n"
    "Yozib ham kiritish mumkin:\n"
    "`25000 taksi`\n"
    "`+500000 oylik`\n"
    "`kecha 120000 ovqat`\n\n"
    "👇 Pastdagi tugmalarni ishlating."
)

# -------------------- CATEGORIES --------------------
CAT = {
    "taksi": ("🚕", ["taksi", "taxi", "yandex", "uber", "transport"]),
    "oziq-ovqat": ("🍏", ["ovqat", "oziq", "market", "non", "suv", "choy", "kofe"]),
    "restoran": ("🍽️", ["restoran", "kafe", "lavash", "fast", "burger", "pizza", "pepsi"]),
    "uy": ("🏠", ["ijara", "kvartira", "kommunal", "gaz", "svet", "internet"]),
    "aloqa": ("📶", ["telefon", "aloqa", "tarif", "mobil"]),
    "sog‘liq": ("💊", ["dor", "apteka", "shifokor"]),
    "kiyim": ("👕", ["kiyim", "krossovka", "oyoq"]),
    "biznes": ("💼", ["biznes", "tovar", "ish", "xizmat"]),
    "o‘yin-kulgi": ("🎉", ["kino", "dam", "sayr", "muzika"]),
    "boshqa": ("🧾", ["boshqa", "turli"]),
}
SYN2CAT = {}
for k, (_, syns) in CAT.items():
    for s in syns:
        SYN2CAT[s.lower()] = k

def canonical_category(raw: str) -> str:
    r = (raw or "").strip().lower()
    if r in CAT:
        return r
    if r in SYN2CAT:
        return SYN2CAT[r]
    for syn, ck in SYN2CAT.items():
        if syn in r:
            return ck
    return "boshqa"

def cat_label(cat_key: str) -> str:
    e = CAT.get(cat_key, ("🧾", []))[0]
    pretty = cat_key.capitalize()
    if cat_key == "oziq-ovqat":
        pretty = "Oziq-ovqat"
    if cat_key == "o‘yin-kulgi":
        pretty = "O‘yin-kulgi"
    if cat_key == "sog‘liq":
        pretty = "Sog‘liq"
    return f"{e} {pretty}"

def money(n: int) -> str:
    return f"{n:,}".replace(",", " ")


# -------------------- UI KEYBOARDS --------------------
def main_kb():
    web_url = f"{WEBHOOK_URL}/app" if WEBHOOK_URL else ""
    web_btn = KeyboardButton(text="📊 Dashboard", web_app=WebAppInfo(url=web_url)) if web_url else KeyboardButton(text="📊 Dashboard")
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="➕ Kirim"), KeyboardButton(text="➖ Chiqim")],
            [KeyboardButton(text="🎤 Ovoz"), web_btn],
            [KeyboardButton(text="📈 Hisobot"), KeyboardButton(text="👤 Kabinet")],
            [KeyboardButton(text="🎯 Limit"), KeyboardButton(text="💎 Obuna")],
            [KeyboardButton(text="🆘 Support")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Masalan: 25000 taksi | +500000 oylik | kecha 120000 ovqat"
    )

def support_inline():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"👤 Admin: @{ADMIN_USERNAME}", url=f"https://t.me/{ADMIN_USERNAME}")]
    ])

def plans_inline():
    rows = []
    for k, v in PLANS.items():
        rows.append([InlineKeyboardButton(text=f"💳 {v['title']} — {money(v['amount'])} so‘m", callback_data=f"buy:{k}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# -------------------- PARSER --------------------
NUM_RE = re.compile(r"(\+)?\s*([\d\s\.,_]+)\s*(k|m)?", re.I)
DATE_WORDS = {"bugun": 0, "kecha": 1, "kechagi": 1}

INCOME_WORDS = {"kirim", "tushdi", "oldim", "oylik", "maosh", "daromad"}
EXPENSE_WORDS = {"chiqim", "ketdi", "xarajat", "sarfladim", "to'ladim", "toladim"}

def parse_date_and_strip(text: str):
    t = (text or "").strip()
    parts = t.split()
    tx_date = date.today()
    if parts:
        w = parts[0].lower()
        if w in DATE_WORDS:
            tx_date = date.today() - timedelta(days=DATE_WORDS[w])
            t = " ".join(parts[1:]).strip()
    return tx_date.strftime("%Y-%m-%d"), t

def parse_amount_text(text: str):
    if not text:
        return None
    tx_date, rest = parse_date_and_strip(text)

    m = NUM_RE.search(rest)
    if not m:
        return None

    plus = m.group(1)
    num = m.group(2)
    mult = (m.group(3) or "").lower()

    num = num.replace(" ", "").replace("_", "")
    digits = re.sub(r"[^\d]", "", num)
    if not digits:
        return None

    amount = int(digits)
    if mult == "k":
        amount *= 1000
    elif mult == "m":
        amount *= 1_000_000

    after = rest[m.end():].strip()
    before = rest[:m.start()].strip()

    ttype = "income" if plus else "expense"
    lowered = rest.lower()
    if any(w in lowered for w in INCOME_WORDS):
        ttype = "income"
    if any(w in lowered for w in EXPENSE_WORDS):
        ttype = "expense"

    cat_raw = ""
    note = None

    if after:
        a = after.split()
        cat_raw = a[0]
        note = " ".join(a[1:]).strip() or None
    elif before:
        b = before.split()
        cat_raw = b[-1]
        note = " ".join(b[:-1]).strip() or None

    cat_key = canonical_category(cat_raw)
    return {"ttype": ttype, "amount": amount, "category": cat_key, "note": note, "tx_date": tx_date}


async def build_saved_message(user_id: int, parsed: dict):
    inc, exp = await month_sums(user_id)
    bal = inc - exp
    cat_m = await category_month_sum(user_id, parsed["category"])
    lim = await get_limit(user_id, parsed["category"])

    kind = "Kirim" if parsed["ttype"] == "income" else "Chiqim"
    sign = "+" if parsed["ttype"] == "income" else ""

    msg = (
        "Hisobotga qo‘shildi ✅\n\n"
        f"*{kind}:*\n"
        f"Sana: *{parsed['tx_date']}*\n\n"
        f"Summa: *{sign}{money(parsed['amount'])}* UZS\n"
        f"Kategoriya: *{cat_label(parsed['category'])}*\n"
    )
    if parsed.get("note"):
        msg += f"Izoh: {parsed['note']}\n"

    if parsed["ttype"] == "expense":
        msg += f"\n💡 {cat_label(parsed['category'])} bo‘yicha bu oy: *{money(cat_m)}* UZS\n"
        if lim:
            pct = int((cat_m / lim) * 100) if lim > 0 else 0
            msg += f"🎯 Limit: *{money(lim)}* UZS  |  Ishlatildi: *{pct}%*\n"
            if pct >= 80:
                msg += "⚠️ Limit 80% dan oshdi!\n"

    msg += (
        f"\nBu oydagi kirim: *{money(inc)}* UZS\n"
        f"Bu oydagi chiqim: *{money(exp)}* UZS\n"
        f"Balans holati: *{money(bal)}* UZS\n"
    )
    return msg


# -------------------- BOT HANDLERS --------------------
@dp.message(CommandStart())
async def cmd_start(m: Message):
    await ensure_user(m.from_user.id)
    await m.answer(WELCOME, parse_mode="Markdown", reply_markup=main_kb())

@dp.message(F.text == "🆘 Support")
async def support(m: Message):
    await ensure_user(m.from_user.id)
    await m.answer("🆘 Yordam uchun adminga yozing:", reply_markup=main_kb())
    await m.answer("👇", reply_markup=support_inline())

@dp.message(F.text == "➕ Kirim")
async def income_hint(m: Message):
    await m.answer("➕ Kirim yozing:\n`+500000 oylik`", parse_mode="Markdown", reply_markup=main_kb())

@dp.message(F.text == "➖ Chiqim")
async def expense_hint(m: Message):
    await m.answer("➖ Chiqim yozing:\n`25000 taksi`\n`kecha 120000 ovqat`", parse_mode="Markdown", reply_markup=main_kb())

@dp.message(F.text == "📈 Hisobot")
async def report(m: Message):
    await ensure_user(m.from_user.id)
    inc, exp = await month_sums(m.from_user.id)
    bal = inc - exp
    top = await top_expense_categories(m.from_user.id, 5)

    lines = [
        "📈 *Bu oy hisobot*\n",
        f"➕ Kirim: *{money(inc)}* UZS",
        f"➖ Chiqim: *{money(exp)}* UZS",
        f"🟦 Balans: *{money(bal)}* UZS\n",
    ]
    if top:
        lines.append("🔥 *Top xarajatlar:*")
        for c, s in top:
            lines.append(f"• {cat_label(c)} — *{money(int(s))}* UZS")

    await m.answer("\n".join(lines), parse_mode="Markdown", reply_markup=main_kb())

@dp.message(F.text == "👤 Kabinet")
async def cabinet(m: Message):
    await ensure_user(m.from_user.id)
    u = await get_user(m.from_user.id)
    plan = u[1] if u else "FREE"
    exp = u[2] if u else None
    badge = f"{plan} (gacha: {exp})" if exp else plan
    await m.answer(
        f"👤 *Kabinet*\n\nID: `{m.from_user.id}`\nObuna: *{badge}*\n\nSupport: @{ADMIN_USERNAME}",
        parse_mode="Markdown",
        reply_markup=main_kb()
    )

@dp.message(F.text == "🎯 Limit")
async def limit_help(m: Message):
    await m.answer(
        "🎯 Limit o‘rnatish:\n`limit taksi 300000`\n`limit oziq-ovqat 1000000`",
        parse_mode="Markdown",
        reply_markup=main_kb()
    )

@dp.message(F.text == "💎 Obuna")
async def sub_menu(m: Message):
    await ensure_user(m.from_user.id)
    await m.answer("💎 Tariflardan birini tanlang:", reply_markup=main_kb())
    await m.answer("Tariflar:", reply_markup=plans_inline())

@dp.callback_query(F.data.startswith("buy:"))
async def buy_cb(cb):
    await ensure_user(cb.from_user.id)
    plan = cb.data.split(":", 1)[1].upper()
    if plan not in PLANS:
        await cb.answer("Noto‘g‘ri tarif", show_alert=True)
        return

    amount = PLANS[plan]["amount"]
    await add_payment(cb.from_user.id, plan, amount, status="pending", payload=f"sub:{plan}:{cb.from_user.id}")

    if PAYMENT_PROVIDER_TOKEN:
        prices = [LabeledPrice(label=PLANS[plan]["title"], amount=amount * 100)]
        await bot.send_invoice(
            chat_id=cb.message.chat.id,
            title=f"{APP_TITLE} — {PLANS[plan]['title']}",
            description="Obuna to‘lovi",
            provider_token=PAYMENT_PROVIDER_TOKEN,
            currency=CURRENCY,
            prices=prices,
            payload=f"sub:{plan}:{cb.from_user.id}",
        )
        await cb.answer("Invoice yuborildi ✅")
    else:
        await bot.send_message(
            cb.message.chat.id,
            f"✅ Buyurtma: *{PLANS[plan]['title']}* — *{money(amount)}* so‘m.\n\n"
            f"Manual to‘lov: chek yuboring.\nSupport: @{ADMIN_USERNAME}",
            parse_mode="Markdown",
            reply_markup=main_kb()
        )
        await cb.answer("Manual ✅")

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
        await set_subscription(uid, plan, expires)
        await add_payment(uid, plan, PLANS[plan]["amount"], status="paid", payload=payload)
        await m.answer(
            f"🎉 Obuna aktiv!\n*{PLANS[plan]['title']}*\nYakun: `{expires}`",
            parse_mode="Markdown",
            reply_markup=main_kb()
        )
    except Exception:
        await m.answer("To‘lov qaydida xatolik. Admin tekshiradi.", reply_markup=main_kb())

@dp.message()
async def all_text(m: Message):
    await ensure_user(m.from_user.id)
    t = (m.text or "").strip()

    if t.lower().startswith("limit "):
        parts = t.split()
        if len(parts) >= 3:
            cat = canonical_category(parts[1])
            amt_digits = re.sub(r"[^\d]", "", parts[2])
            if amt_digits:
                await set_limit(m.from_user.id, cat, int(amt_digits))
                await m.answer(
                    f"✅ Limit saqlandi: *{cat_label(cat)}* — *{money(int(amt_digits))}* UZS",
                    parse_mode="Markdown",
                    reply_markup=main_kb()
                )
                return

    parsed = parse_amount_text(t)
    if not parsed:
        await m.answer("👇 Pastdagi tugmalardan foydalaning.", reply_markup=main_kb())
        return

    await add_tx(m.from_user.id, parsed["ttype"], parsed["amount"], parsed["category"], parsed["note"], parsed["tx_date"])
    msg = await build_saved_message(m.from_user.id, parsed)
    await m.answer(msg, parse_mode="Markdown", reply_markup=main_kb())


# -------------------- WEBHOOK ENDPOINT (THIS FIXES 404) --------------------
@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    update = Update.model_validate(data)
    await dp.feed_update(bot, update)
    return {"ok": True}


# -------------------- STARTUP: set webhook --------------------
@app.on_event("startup")
async def on_startup():
    await init_db()
    if WEBHOOK_URL:
        await bot.set_webhook(f"{WEBHOOK_URL}/webhook")


# -------------------- SIMPLE ROOT (optional) --------------------
@app.get("/")
async def root():
    return {"ok": True, "app": APP_TITLE}


# -------------------- DASHBOARD WEBAPP (simple) --------------------
def verify_telegram_init_data(init_data: str, bot_token: str) -> dict | None:
    if not init_data:
        return None
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True))
        hash_recv = pairs.pop("hash", None)
        if not hash_recv:
            return None
        data_check_arr = [f"{k}={pairs[k]}" for k in sorted(pairs.keys())]
        data_check_string = "\n".join(data_check_arr)
        secret_key = hashlib.sha256(bot_token.encode()).digest()
        h = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(h, hash_recv):
            return None
        return pairs
    except Exception:
        return None

@app.get("/app", response_class=HTMLResponse)
async def web_app():
    return HTMLResponse(f"""
<!doctype html>
<html><head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>{APP_TITLE} Dashboard</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
body{{margin:0;font-family:system-ui;background:#0b1220;color:#eaf0ff}}
.wrap{{padding:16px;max-width:820px;margin:0 auto}}
.card{{background:#121a2b;border:1px solid rgba(255,255,255,.08);border-radius:18px;padding:14px;margin-top:12px}}
.big{{font-size:28px;font-weight:900}}
.item{{display:flex;justify-content:space-between;padding:10px 12px;border-radius:14px;background:rgba(255,255,255,.04);margin:8px 0}}
</style></head>
<body>
<div class="wrap">
  <h2>📊 {APP_TITLE} — Dashboard</h2>
  <div class="card"><div class="big" id="bal">Yuklanmoqda...</div><div id="mini"></div></div>
  <div class="card"><b>Top xarajatlar</b><div id="top"></div></div>
</div>
<script>
const tg = Telegram.WebApp; tg.ready();
const initData = tg.initData || "";
fetch("/api/stats", {{
  method:"POST",
  headers:{{"Content-Type":"application/json"}},
  body: JSON.stringify({{initData}})
}}).then(r=>r.json()).then(d=>{{
  if(!d.ok){{document.getElementById("bal").innerText="Kirish tasdiqlanmadi";return;}}
  document.getElementById("bal").innerText = "Balans: " + d.balance;
  document.getElementById("mini").innerText = "Kirim: " + d.income + " | Chiqim: " + d.expense;
  const box=document.getElementById("top"); box.innerHTML="";
  (d.top_list||[]).forEach(x=>{{
    const div=document.createElement("div");
    div.className="item";
    div.innerHTML = `<div>${{x.category}}</div><div><b>${{x.sum}}</b></div>`;
    box.appendChild(div);
  }});
}});
</script>
</body></html>
""")

@app.post("/api/stats")
async def api_stats(request: Request):
    body = await request.json()
    init_data = (body.get("initData") or "").strip()
    parsed = verify_telegram_init_data(init_data, BOT_TOKEN)
    if not parsed:
        return JSONResponse({"ok": False})

    user_json = parsed.get("user")
    if not user_json:
        return JSONResponse({"ok": False})

    try:
        user = json.loads(user_json)
        user_id = int(user["id"])
    except Exception:
        return JSONResponse({"ok": False})

    await ensure_user(user_id)
    inc, exp = await month_sums(user_id)
    bal = inc - exp
    top = await top_expense_categories(user_id, 5)
    top_list = [{"category": cat_label(c), "sum": f"{money(int(s))} UZS"} for c, s in top]

    return JSONResponse({
        "ok": True,
        "month": date.today().strftime("%Y-%m"),
        "income": f"{money(inc)} UZS",
        "expense": f"{money(exp)} UZS",
        "balance": f"{money(bal)} UZS",
        "top_list": top_list,
    })


# -------------------- ADMIN PANEL (simple) --------------------
def check_admin(req: Request):
    token = req.headers.get("x-admin-token") or req.query_params.get("token")
    if token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")

@app.get("/admin", response_class=HTMLResponse)
async def admin_home(request: Request):
    check_admin(request)
    total = await users_count()
    return HTMLResponse(f"""
    <html><head><meta charset="utf-8"><title>{APP_TITLE} Admin</title>
    <style>
      body{{font-family:system-ui;background:#0b1220;color:#eaf0ff;padding:18px}}
      .card{{background:#121a2b;border:1px solid rgba(255,255,255,.08);border-radius:16px;padding:14px;margin:12px 0}}
      input,button{{padding:10px;border-radius:10px;border:1px solid rgba(255,255,255,.15);background:#0b1220;color:#fff}}
      button{{background:#1690f5;border:none;font-weight:800;cursor:pointer}}
      a{{color:#7cc0ff}}
      .row{{display:flex;gap:10px;flex-wrap:wrap}}
    </style></head>
    <body>
      <h2>🛠 {APP_TITLE} — Admin</h2>
      <div class="card">Users: <b>{total}</b> | Support: <b>@{ADMIN_USERNAME}</b></div>

      <div class="card">
        <h3>✅ Obuna berish (manual)</h3>
        <form action="/admin/subset" method="get">
          <input type="hidden" name="token" value="{ADMIN_TOKEN}">
          <input name="user_id" placeholder="user_id" required>
          <input name="plan" placeholder="plan (M1/M3/M6/Y1/TRIAL7)" required>
          <input name="days" placeholder="days (30/90/...)" required>
          <button type="submit">Berish</button>
        </form>
      </div>

      <div class="card">
        <a href="/admin/users?token={ADMIN_TOKEN}">/admin/users (JSON)</a>
      </div>
    </body></html>
    """)

@app.get("/admin/users")
async def admin_users(request: Request):
    check_admin(request)
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute(
            "SELECT user_id, plan, sub_expires_at, is_banned, created_at FROM users ORDER BY created_at DESC LIMIT 500"
        )
        rows = await cur.fetchall()
    return {"users": rows}

@app.get("/admin/subset")
async def admin_set_sub(request: Request, user_id: int, plan: str, days: int):
    check_admin(request)
    expires = (datetime.now() + timedelta(days=int(days))).isoformat(timespec="seconds")
    await set_subscription(int(user_id), plan.upper(), expires)
    return {"ok": True, "user_id": int(user_id), "plan": plan.upper(), "expires": expires}
