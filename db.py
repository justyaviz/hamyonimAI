import aiosqlite
from datetime import datetime

DB_PATH = "hamyonim.db"

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
            created_at TEXT NOT NULL
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
                (user_id, datetime.now().isoformat(timespec="seconds"))
            )
            await db.commit()

async def get_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id, plan, sub_expires_at, is_banned FROM users WHERE user_id=?", (user_id,))
        return await cur.fetchone()

async def set_subscription(user_id: int, plan: str, expires_at_iso: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET plan=?, sub_expires_at=? WHERE user_id=?",
            (plan, expires_at_iso, user_id)
        )
        await db.commit()

async def add_payment(user_id: int, plan: str, amount: int, status: str, payload: str | None = None):
    from datetime import datetime
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO payments(user_id, plan, amount, status, payload, created_at) VALUES(?,?,?,?,?,?)",
            (user_id, plan, amount, status, payload, datetime.now().isoformat(timespec="seconds"))
        )
        await db.commit()
