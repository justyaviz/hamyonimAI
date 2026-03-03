from fastapi import FastAPI, Request
# boshqa importlar...

app = FastAPI()

# keyin bot, dp, webhook va qolgan kodlar...
import aiosqlite
from datetime import datetime, date

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
        CREATE TABLE IF NOT EXISTS debts(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            dtype TEXT NOT NULL, -- i_owe / owed_to_me
            person TEXT NOT NULL,
            amount INTEGER NOT NULL,
            note TEXT,
            is_closed INTEGER DEFAULT 0,
            created_at TEXT NOT NULL
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

async def category_month_sum(user_id: int, category: str):
    pref = month_prefix()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT COALESCE(SUM(amount),0) FROM tx WHERE user_id=? AND ttype='expense' AND category=? AND substr(tx_date,1,7)=?",
            (user_id, category, pref)
        )
        v = (await cur.fetchone())[0]
    return int(v)

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
