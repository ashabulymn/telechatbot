import aiosqlite
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from .config import get_settings


class Database:
    def __init__(self):
        settings = get_settings()
        self.path = settings.database_path
        self._encryption_key = settings.custom_settings_encryption_key.strip()

    def _encrypt_api_key(self, value):
        if not value:
            return None
        if not self._encryption_key:
            raise RuntimeError("CUSTOM_SETTINGS_ENCRYPTION_KEY belum dikonfigurasi.")
        try:
            return "enc:v1:" + Fernet(self._encryption_key.encode()).encrypt(value.encode()).decode()
        except Exception as exc:
            raise RuntimeError("CUSTOM_SETTINGS_ENCRYPTION_KEY tidak valid.") from exc

    def _decrypt_api_key(self, value):
        if not value:
            return None
        if not value.startswith("enc:v1:"):
            return value
        if not self._encryption_key:
            raise RuntimeError("CUSTOM_SETTINGS_ENCRYPTION_KEY diperlukan untuk membaca API key terenkripsi.")
        try:
            return Fernet(self._encryption_key.encode()).decrypt(value[7:].encode()).decode()
        except (InvalidToken, ValueError, TypeError) as exc:
            raise RuntimeError("CUSTOM_SETTINGS_ENCRYPTION_KEY tidak cocok dengan data terenkripsi.") from exc

    async def init(self):
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.path) as db:
            await db.execute("""CREATE TABLE IF NOT EXISTS conversations (id INTEGER PRIMARY KEY AUTOINCREMENT, telegram_user_id INTEGER NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
            await db.execute("""CREATE TABLE IF NOT EXISTS users (user_number INTEGER PRIMARY KEY AUTOINCREMENT, telegram_user_id INTEGER UNIQUE NOT NULL, username TEXT, first_name TEXT, last_name TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
            await db.execute("""CREATE TABLE IF NOT EXISTS setting_changes (setting_number INTEGER PRIMARY KEY AUTOINCREMENT, telegram_user_id INTEGER NOT NULL, action TEXT NOT NULL, value TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
            await db.execute("""CREATE TABLE IF NOT EXISTS user_settings (telegram_user_id INTEGER PRIMARY KEY, model TEXT, provider TEXT, custom_base_url TEXT, custom_api_key TEXT, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
            cols = await (await db.execute("PRAGMA table_info(user_settings)")).fetchall()
            names = {c[1] for c in cols}
            if "provider" not in names:
                await db.execute("ALTER TABLE user_settings ADD COLUMN provider TEXT")
            if "custom_base_url" not in names:
                await db.execute("ALTER TABLE user_settings ADD COLUMN custom_base_url TEXT")
            if "custom_api_key" not in names:
                await db.execute("ALTER TABLE user_settings ADD COLUMN custom_api_key TEXT")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_conversations_user ON conversations(telegram_user_id, id)")
            await db.commit()

    async def ensure_user(self, user_id, username=None, first_name=None, last_name=None):
        async with aiosqlite.connect(self.path) as db:
            await db.execute("BEGIN IMMEDIATE")
            cur = await db.execute("SELECT user_number FROM users WHERE telegram_user_id=?", (user_id,))
            row = await cur.fetchone()
            if row:
                user_number = row[0]
                await db.execute(
                    """UPDATE users SET username=?, first_name=?, last_name=?,
                       updated_at=CURRENT_TIMESTAMP WHERE telegram_user_id=?""",
                    (username, first_name, last_name, user_id),
                )
            else:
                cur = await db.execute(
                    """INSERT INTO users(telegram_user_id,username,first_name,last_name)
                       VALUES (?,?,?,?)""",
                    (user_id, username, first_name, last_name),
                )
                user_number = cur.lastrowid
            await db.commit()
        return user_number

    async def record_setting_change(self, user_id, action, value):
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "INSERT INTO setting_changes(telegram_user_id,action,value) VALUES (?,?,?)",
                (user_id, action, value),
            )
            setting_number = cur.lastrowid
            await db.commit()
        return setting_number

    async def add_message(self, user_id, role, content):
        async with aiosqlite.connect(self.path) as db:
            await db.execute("INSERT INTO conversations(telegram_user_id, role, content) VALUES (?, ?, ?)", (user_id, role, content))
            await db.commit()

    async def history(self, user_id, limit):
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT role, content FROM conversations WHERE telegram_user_id=? ORDER BY id DESC LIMIT ?", (user_id, limit))
            rows = await cur.fetchall()
        return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]

    async def clear(self, user_id):
        async with aiosqlite.connect(self.path) as db:
            await db.execute("DELETE FROM conversations WHERE telegram_user_id=?", (user_id,))
            await db.commit()

    async def get_model(self, user_id):
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute("SELECT model FROM user_settings WHERE telegram_user_id=?", (user_id,))
            row = await cur.fetchone()
        return row[0] if row and row[0] else None

    async def set_model(self, user_id, model):
        async with aiosqlite.connect(self.path) as db:
            await db.execute("INSERT INTO user_settings(telegram_user_id,model) VALUES (?,?) ON CONFLICT(telegram_user_id) DO UPDATE SET model=excluded.model,updated_at=CURRENT_TIMESTAMP", (user_id, model))
            await db.commit()

    async def get_provider(self, user_id):
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute("SELECT provider FROM user_settings WHERE telegram_user_id=?", (user_id,))
            row = await cur.fetchone()
        return row[0] if row and row[0] else None

    async def set_provider(self, user_id, provider):
        async with aiosqlite.connect(self.path) as db:
            await db.execute("INSERT INTO user_settings(telegram_user_id,provider,model) VALUES (?,?,NULL) ON CONFLICT(telegram_user_id) DO UPDATE SET provider=excluded.provider,model=NULL,updated_at=CURRENT_TIMESTAMP", (user_id, provider))
            await db.commit()

    async def get_custom_settings(self, user_id):
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "SELECT custom_base_url, custom_api_key FROM user_settings WHERE telegram_user_id=?",
                (user_id,),
            )
            row = await cur.fetchone()
        encrypted = row[1] if row and row[1] else None
        api_key = self._decrypt_api_key(encrypted)
        if encrypted and api_key and not encrypted.startswith("enc:v1:") and self._encryption_key:
            await self.set_custom_api_key(user_id, api_key)
        return {
            "base_url": row[0] if row and row[0] else None,
            "api_key": api_key,
        }

    async def set_custom_base_url(self, user_id, base_url):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO user_settings(telegram_user_id,custom_base_url) VALUES (?,?) "
                "ON CONFLICT(telegram_user_id) DO UPDATE SET custom_base_url=excluded.custom_base_url,updated_at=CURRENT_TIMESTAMP",
                (user_id, base_url),
            )
            await db.commit()

    async def set_custom_api_key(self, user_id, api_key):
        encrypted = self._encrypt_api_key(api_key)
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO user_settings(telegram_user_id,custom_api_key) VALUES (?,?) "
                "ON CONFLICT(telegram_user_id) DO UPDATE SET custom_api_key=excluded.custom_api_key,updated_at=CURRENT_TIMESTAMP",
                (user_id, encrypted),
            )
            await db.commit()

    async def clear_custom_settings(self, user_id):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE user_settings SET custom_base_url=NULL, custom_api_key=NULL, updated_at=CURRENT_TIMESTAMP WHERE telegram_user_id=?",
                (user_id,),
            )
            await db.commit()
