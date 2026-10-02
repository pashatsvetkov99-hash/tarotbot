import aiosqlite
from config import DB_PATH, FREE_USES, ADMIN_IDS


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                free_uses_left INTEGER NOT NULL DEFAULT 1,
                paid_credits INTEGER NOT NULL DEFAULT 0,
                telegram_payment_charge_id TEXT
            )"""
        )
        await db.execute(
            """CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                situation_text TEXT NOT NULL,
                sphere TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )"""
        )
        await db.commit()


async def ensure_user(user_id: int) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            await db.execute(
                "INSERT INTO users (user_id, free_uses_left) VALUES (?, ?)",
                (user_id, FREE_USES),
            )
            await db.commit()
            return {"user_id": user_id, "free_uses_left": FREE_USES, "paid_credits": 0}
        return dict(row)


async def can_use_reading(user_id: int) -> bool:
    if user_id in ADMIN_IDS:
        return True
    user = await ensure_user(user_id)
    return user["free_uses_left"] > 0 or user["paid_credits"] > 0


async def consume_reading(user_id: int) -> None:
    if user_id in ADMIN_IDS:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT free_uses_left, paid_credits FROM users WHERE user_id = ?",
            (user_id,),
        )
        row = await cursor.fetchone()
        if row[0] > 0:
            await db.execute(
                "UPDATE users SET free_uses_left = free_uses_left - 1 WHERE user_id = ?",
                (user_id,),
            )
        else:
            await db.execute(
                "UPDATE users SET paid_credits = paid_credits - 1 WHERE user_id = ?",
                (user_id,),
            )
        await db.commit()


async def save_session(user_id: int, situation_text: str, sphere: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO sessions (user_id, situation_text, sphere) VALUES (?, ?, ?)",
            (user_id, situation_text, sphere),
        )
        await db.commit()


async def add_payment(user_id: int, charge_id: str, credits: int) -> bool:
    """Returns True if payment was credited, False if duplicate."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT telegram_payment_charge_id FROM users WHERE user_id = ?",
            (user_id,),
        )
        row = await cursor.fetchone()
        if row and row[0] == charge_id:
            return False  # duplicate
        await db.execute(
            """UPDATE users
               SET paid_credits = paid_credits + ?,
                   telegram_payment_charge_id = ?
               WHERE user_id = ?""",
            (credits, charge_id, user_id),
        )
        await db.commit()
        return True


async def cleanup_old_sessions(days: int = 30) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "DELETE FROM sessions WHERE created_at < datetime('now', ?)",
            (f"-{days} days",),
        )
        await db.commit()
        return cursor.rowcount