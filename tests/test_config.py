def test_settings():
    from app.config import Settings
    assert Settings(telegram_bot_token="test").telegram_mode == "polling"
