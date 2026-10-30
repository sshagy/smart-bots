from pathlib import Path

import pytest
from pydantic import ValidationError

from src.config import BotMode, ConfigurationError, Settings

pytestmark = pytest.mark.asyncio


def make_settings(**kwargs: object) -> Settings:
    # _env_file=None: тесты не должны зависеть от .env разработчика
    return Settings(_env_file=None, **kwargs)  # type: ignore[call-arg]


async def test_defaults() -> None:
    settings = make_settings(moderation_keywords_file=None)

    assert settings.bot_mode is BotMode.POLLING
    assert settings.claude_model == "claude-opus-5-5"
    assert settings.load_moderation_keywords() == []


async def test_values_come_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_MODE", "webhook")
    monkeypatch.setenv("LOG_LEVEL", "debug")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:TOP-SECRET-VALUE")
    monkeypatch.setenv("WEBHOOK_BASE_URL", "https://bot.example.com/")
    monkeypatch.setenv("MODERATION_KEYWORDS", "бля*, сука ,,!страху*")

    settings = make_settings(moderation_keywords_file=None)

    assert settings.bot_mode is BotMode.WEBHOOK
    assert settings.log_level == "DEBUG"
    assert settings.telegram_bot_token.get_secret_value() == "123:TOP-SECRET-VALUE"
    assert "TOP-SECRET-VALUE" not in repr(settings)  # секреты не утекают в логи
    assert settings.webhook_base_url == "https://bot.example.com"
    assert settings.moderation_keywords == ["бля*", "сука", "!страху*"]


async def test_empty_values_in_env_file_mean_not_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Ровно так выглядит .env, скопированный из .env.example без заполнения секретов.
    env_file = tmp_path / ".env"
    env_file.write_text(
        "WEBHOOK_SECRET=\nANTHROPIC_API_KEY=\nMODERATION_KEYWORDS=\nWEBHOOK_PORT=9000\n",
        encoding="utf-8",
    )
    for name in ("WEBHOOK_SECRET", "ANTHROPIC_API_KEY", "MODERATION_KEYWORDS", "WEBHOOK_PORT"):
        monkeypatch.delenv(name, raising=False)

    settings = Settings(_env_file=env_file)  # type: ignore[call-arg]

    assert settings.webhook_secret is None
    assert settings.anthropic_api_key is None
    assert settings.moderation_keywords == []
    assert settings.webhook_port == 9000


async def test_invalid_bot_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_MODE", "push")

    with pytest.raises(ValidationError):
        make_settings()


async def test_keywords_file_and_env_are_merged(tmp_path: Path) -> None:
    keywords_file = tmp_path / "words.txt"
    keywords_file.write_text("# комментарий\nбля*\n\n  сука\n", encoding="utf-8")

    settings = make_settings(
        moderation_keywords_file=keywords_file, moderation_keywords=["сука", "хер"]
    )

    assert settings.load_moderation_keywords() == ["бля*", "сука", "хер"]


async def test_missing_keywords_file_is_not_an_error(tmp_path: Path) -> None:
    settings = make_settings(moderation_keywords_file=tmp_path / "missing.txt")

    assert settings.load_moderation_keywords() == []


async def test_require_names_missing_variables() -> None:
    settings = make_settings(telegram_bot_token="123:abc")

    settings.require("telegram_bot_token")
    with pytest.raises(ConfigurationError, match="MATTERMOST_URL, MATTERMOST_TOKEN"):
        settings.require("telegram_bot_token", "mattermost_url", "mattermost_token")
