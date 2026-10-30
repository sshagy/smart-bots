"""Composition root и точка входа: сборка приложения без сети."""

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from src.adapters.mattermost import MattermostAdapter
from src.adapters.max import MaxAdapter
from src.adapters.telegram import TelegramAdapter
from src.adapters.whatsapp import WhatsAppAdapter
from src.bootstrap import build_container, create_adapter, validate_settings
from src.config import BotMode, ConfigurationError, Settings
from src.domain.entities import ChatType, MessengerType, TextReply
from src.main import main, parse_args
from tests.helpers import make_message

pytestmark = pytest.mark.asyncio

ALL_TOKENS = {
    "telegram_bot_token": "123456:telegram-token",
    "max_bot_token": "max-token",
    "whatsapp_access_token": "wa-token",
    "whatsapp_phone_number_id": "PHONE_ID",
    "whatsapp_verify_token": "verify",
    "whatsapp_app_secret": "app-secret",
    "mattermost_url": "https://chat.example.com",
    "mattermost_token": "mm-token",
}


def make_settings(tmp_path: Path, **overrides: object) -> Settings:
    values = {"database_path": str(tmp_path / "bot.sqlite3"), **overrides}
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Изолирует тест от переменных окружения и .env разработчика."""
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.mark.parametrize(
    ("messenger", "missing"),
    [
        (MessengerType.TELEGRAM, "TELEGRAM_BOT_TOKEN"),
        (MessengerType.MAX, "MAX_BOT_TOKEN"),
        (MessengerType.WHATSAPP, "WHATSAPP_ACCESS_TOKEN, WHATSAPP_PHONE_NUMBER_ID"),
        (MessengerType.MATTERMOST, "MATTERMOST_URL, MATTERMOST_TOKEN"),
    ],
)
async def test_validate_settings_names_missing_variables(
    tmp_path: Path, messenger: MessengerType, missing: str
) -> None:
    with pytest.raises(ConfigurationError, match=missing):
        validate_settings(messenger, make_settings(tmp_path))


async def test_webhook_mode_needs_extra_settings(tmp_path: Path) -> None:
    webhook = make_settings(tmp_path, bot_mode=BotMode.WEBHOOK, **ALL_TOKENS)

    with pytest.raises(ConfigurationError, match="WEBHOOK_BASE_URL"):
        validate_settings(MessengerType.TELEGRAM, webhook)
    with pytest.raises(ConfigurationError, match="MATTERMOST_WEBHOOK_TOKEN"):
        validate_settings(MessengerType.MATTERMOST, webhook)
    validate_settings(MessengerType.WHATSAPP, webhook)  # URL задаётся в Meta App Dashboard
    validate_settings(MessengerType.MAX, make_settings(tmp_path, max_bot_token="t"))


async def test_container_wires_real_core_with_sqlite(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)

    async with build_container(settings) as container:
        warning = await container.router.handle(make_message("ну бля, опять"))
        check = await container.router.handle(make_message("/check", chat_type=ChatType.PRIVATE))

    assert warning == [
        TextReply("@vasya, пожалуйста, не материтесь в чате, иначе будет бан.", "100")
    ]
    assert "ANTHROPIC_API_KEY" in check[0].text  # ключа нет — /check честно об этом говорит
    assert container.http.is_closed
    # closing(): `with sqlite3.connect()` сам по себе соединение не закрывает
    with closing(sqlite3.connect(settings.database_path)) as db:
        assert db.execute("SELECT matched_keyword FROM moderation_events").fetchall() == [("бля",)]


@pytest.mark.parametrize(
    ("messenger", "adapter_type"),
    [
        (MessengerType.TELEGRAM, TelegramAdapter),
        (MessengerType.MAX, MaxAdapter),
        (MessengerType.WHATSAPP, WhatsAppAdapter),
        (MessengerType.MATTERMOST, MattermostAdapter),
    ],
)
async def test_create_adapter_for_each_messenger(
    tmp_path: Path, messenger: MessengerType, adapter_type: type
) -> None:
    settings = make_settings(tmp_path, **ALL_TOKENS)

    async with build_container(settings) as container:
        adapter = create_adapter(messenger, settings, container)
        try:
            assert isinstance(adapter, adapter_type)
            assert adapter.messenger is messenger
        finally:
            await adapter.close()


async def test_parse_args() -> None:
    args = parse_args(["--messenger", "mattermost", "--mode", "webhook"])

    assert (args.messenger, args.mode) == ("mattermost", "webhook")
    with pytest.raises(SystemExit):
        parse_args(["--messenger", "icq"])


async def test_main_exits_with_code_2_when_tokens_are_missing(clean_env: Path) -> None:
    assert main(["--messenger", "max"]) == 2
    assert not (clean_env / "data").exists()  # до старта ничего не создано


async def test_main_exits_with_code_2_on_invalid_settings(
    clean_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BOT_MODE", "push")

    assert main(["--messenger", "telegram"]) == 2
