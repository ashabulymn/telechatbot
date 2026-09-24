import asyncio

from cryptography.fernet import Fernet

from app.config import get_settings
from app.db import Database


def test_custom_api_key_is_encrypted_at_rest(tmp_path, monkeypatch):
    key = Fernet.generate_key().decode()
    db_path = tmp_path / "telechatbot.db"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test")
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("CUSTOM_SETTINGS_ENCRYPTION_KEY", key)
    get_settings.cache_clear()

    async def scenario():
        db = Database()
        await db.init()
        await db.set_custom_api_key(123, "super-secret-api-key")
        custom = await db.get_custom_settings(123)
        assert custom["api_key"] == "super-secret-api-key"

        import aiosqlite
        async with aiosqlite.connect(db_path) as conn:
            row = await (await conn.execute(
                "SELECT custom_api_key FROM user_settings WHERE telegram_user_id=123"
            )).fetchone()
        assert row[0].startswith("enc:v1:")
        assert "super-secret-api-key" not in row[0]

    asyncio.run(scenario())
