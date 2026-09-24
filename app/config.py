from functools import lru_cache
from pathlib import Path

from cryptography.fernet import Fernet
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
    ai_video_transcription_enabled: bool = True
    ai_transcription_max_seconds: int = 300

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
    settings = Settings()
    key = settings.custom_settings_encryption_key.strip()

    # Keep the encryption key persistent without requiring manual .env setup.
    # If the operator explicitly configured a key, always respect it.
    if key:
        settings.custom_settings_encryption_key = key
        return settings

    key_path = Path(settings.database_path).parent / ".custom_settings_encryption_key"
    key_path.parent.mkdir(parents=True, exist_ok=True)

    if key_path.exists():
        key = key_path.read_text(encoding="utf-8").strip()
        try:
            Fernet(key.encode())
        except Exception as exc:
            raise RuntimeError(
                "File .custom_settings_encryption_key tidak valid. "
                "Hapus file tersebut hanya jika tidak ada API key terenkripsi yang perlu dipertahankan."
            ) from exc
    else:
        key = Fernet.generate_key().decode()
        key_path.write_text(key + "\n", encoding="utf-8")
        try:
            key_path.chmod(0o600)
        except OSError:
            pass

    settings.custom_settings_encryption_key = key
    return settings
