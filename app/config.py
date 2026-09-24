from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    telegram_bot_token: str
    telegram_mode: str = "polling"
    telegram_webhook_url: str | None = None
    telegram_webhook_secret: str | None = None
    telegram_webhook_host: str = "0.0.0.0"
    telegram_webhook_port: int = 8080
    admin_telegram_user_id: int | None = None

    ai_base_url: str = "https://api.openai.com/v1"
    ai_api_key: str = ""
    ai_model: str = ""
    ai_default_provider: str = "default"
    ai_providers_json: str = ""
    ai_extra_body_json: str = ""
    ai_timeout_seconds: float = 120
    ai_max_history_messages: int = 20
    ai_system_prompt: str = ""
    ai_transcription_model: str = "gpt-4o-mini-transcribe"

    database_path: str = "/app/data/telechatbot.db"
    custom_settings_encryption_key: str = ""
    attachment_max_mb: int = 20
    attachment_max_image_mb: int = 5
    attachment_dir: str = "/app/data/attachments"
    attachment_max_prompt_chars: int = 30000
    log_level: str = "INFO"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

@lru_cache
def get_settings() -> Settings:
    return Settings()
