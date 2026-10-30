"""Конфигурация приложения.

Все секреты и настройки читаются только из переменных окружения (и файла `.env`
в рабочей директории) — в коде нет ни одного токена.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_KEYWORDS_FILE = PROJECT_ROOT / "config" / "moderation_keywords.txt"


class BotMode(StrEnum):
    POLLING = "polling"
    WEBHOOK = "webhook"


class ConfigurationError(RuntimeError):
    """Не хватает обязательных настроек для выбранного мессенджера или режима."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Общие ---
    bot_mode: BotMode = BotMode.POLLING
    log_level: str = "INFO"
    database_path: str = "data/smart_bots.sqlite3"

    # --- Модерация ---
    # Через запятую: MODERATION_KEYWORDS="слово1,слово2*,!исключение"
    moderation_keywords: Annotated[list[str], NoDecode] = Field(default_factory=list)
    moderation_keywords_file: Path | None = DEFAULT_KEYWORDS_FILE
    # {user} подставляется как @username или имя автора сообщения.
    moderation_warning_text: str = "{user}, пожалуйста, не материтесь в чате, иначе будет бан."

    # --- Claude API (/check) ---
    anthropic_api_key: SecretStr | None = None
    claude_model: str = "claude-opus-5-5"

    # --- Meme API (/mem) ---
    meme_api_url: str = "https://meme-api.com/gimme"
    http_timeout_seconds: float = 15.0

    # --- Вебхуки (BOT_MODE=webhook) ---
    webhook_host: str = "0.0.0.0"
    webhook_port: int = 8080
    webhook_base_url: str | None = None  # публичный HTTPS-адрес, например https://bot.example.com
    webhook_secret: SecretStr | None = None

    # --- Telegram ---
    telegram_bot_token: SecretStr | None = None

    # --- MAX ---
    max_bot_token: SecretStr | None = None

    # --- WhatsApp Cloud API ---
    whatsapp_access_token: SecretStr | None = None
    whatsapp_phone_number_id: str | None = None
    whatsapp_verify_token: SecretStr | None = None
    whatsapp_app_secret: SecretStr | None = None

    # --- Mattermost ---
    mattermost_url: str | None = None  # например https://chat.example.com
    mattermost_token: SecretStr | None = None
    mattermost_webhook_token: SecretStr | None = None

    @field_validator("moderation_keywords", mode="before")
    @classmethod
    def _split_keywords(cls, value: Any) -> Any:
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

    @field_validator("log_level")
    @classmethod
    def _upper_log_level(cls, value: str) -> str:
        return value.upper()

    @field_validator("webhook_base_url", "mattermost_url")
    @classmethod
    def _strip_trailing_slash(cls, value: str | None) -> str | None:
        return value.rstrip("/") if value else value

    def load_moderation_keywords(self) -> list[str]:
        """Слова из файла плюс слова из MODERATION_KEYWORDS, без дублей и комментариев."""
        keywords: list[str] = []
        if self.moderation_keywords_file is not None and self.moderation_keywords_file.exists():
            keywords.extend(read_keywords_file(self.moderation_keywords_file))
        keywords.extend(self.moderation_keywords)
        return list(dict.fromkeys(keywords))

    def require(self, *field_names: str) -> None:
        """Проверяет, что обязательные настройки заданы, иначе — понятная ошибка."""
        missing = [name.upper() for name in field_names if not getattr(self, name)]
        if missing:
            raise ConfigurationError(
                "Не заданы переменные окружения: " + ", ".join(missing) + ". См. .env.example."
            )


def read_keywords_file(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")]


def secret_value(secret: SecretStr | None) -> str | None:
    return secret.get_secret_value() if secret is not None else None
