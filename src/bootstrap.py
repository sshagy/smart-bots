"""Composition root: здесь и только здесь слои соединяются в работающее приложение."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx
from anthropic import AsyncAnthropic
from pydantic import SecretStr

from src.adapters.base import MessengerAdapter, WebhookConfig
from src.config import BotMode, ConfigurationError, Settings, secret_value
from src.domain.entities import MessengerType
from src.domain.services import ModerationService
from src.infrastructure.claude_client import ClaudeUsageClient
from src.infrastructure.db.connection import Database
from src.infrastructure.db.repositories import SqliteModerationRepository
from src.infrastructure.meme_client import MemeApiClient
from src.use_cases.handle_check import CheckUsageUseCase
from src.use_cases.handle_help import HelpUseCase
from src.use_cases.handle_mem import GetMemeUseCase
from src.use_cases.moderate_message import ModerateMessageUseCase
from src.use_cases.router import MessageRouter

logger = logging.getLogger(__name__)

USER_AGENT = "smart-bots/0.1 (+https://github.com/sshagy/smart-bots)"

_REQUIRED = {
    MessengerType.TELEGRAM: ("telegram_bot_token",),
    MessengerType.MAX: ("max_bot_token",),
    MessengerType.WHATSAPP: (
        "whatsapp_access_token",
        "whatsapp_phone_number_id",
        "whatsapp_verify_token",
        "whatsapp_app_secret",
    ),
    MessengerType.MATTERMOST: ("mattermost_url", "mattermost_token"),
}
_REQUIRED_FOR_WEBHOOK = {
    MessengerType.TELEGRAM: ("webhook_base_url",),
    MessengerType.MAX: ("webhook_base_url",),
    MessengerType.WHATSAPP: (),  # адрес вебхука указывается в Meta App Dashboard
    MessengerType.MATTERMOST: ("mattermost_webhook_token",),
}


def validate_settings(messenger: MessengerType, settings: Settings) -> None:
    """Проверяет настройки до старта, чтобы ошибка была понятной, а не из глубин SDK."""
    required = list(_REQUIRED[messenger])
    if settings.bot_mode is BotMode.WEBHOOK:
        required += _REQUIRED_FOR_WEBHOOK[messenger]
        if messenger in (MessengerType.TELEGRAM, MessengerType.MAX) and not settings.webhook_secret:
            logger.warning("WEBHOOK_SECRET не задан — вебхук примет запрос от кого угодно")
    settings.require(*required)


@dataclass(frozen=True, slots=True)
class Container:
    router: MessageRouter
    http: httpx.AsyncClient


@asynccontextmanager
async def build_container(settings: Settings) -> AsyncIterator[Container]:
    database = await Database.connect(settings.database_path)
    http = httpx.AsyncClient(
        timeout=settings.http_timeout_seconds,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    )
    api_key = secret_value(settings.anthropic_api_key)
    anthropic = AsyncAnthropic(api_key=api_key, timeout=30.0, max_retries=1) if api_key else None
    if anthropic is None:
        logger.warning(
            "ANTHROPIC_API_KEY не задан — команда /check будет отвечать, что не настроена"
        )

    keywords = settings.load_moderation_keywords()
    if not keywords:
        logger.warning("Список ключевых слов пуст — модерация ничего не найдёт")
    try:
        router = MessageRouter(
            moderate=ModerateMessageUseCase(
                ModerationService(keywords),
                SqliteModerationRepository(database),
                settings.moderation_warning_text,
            ),
            help_=HelpUseCase(),
            check=CheckUsageUseCase(
                ClaudeUsageClient(anthropic, settings.claude_model) if anthropic else None
            ),
            meme=GetMemeUseCase(MemeApiClient(http, settings.meme_api_url)),
        )
        yield Container(router=router, http=http)
    finally:
        await http.aclose()
        if anthropic is not None:
            await anthropic.close()
        await database.close()


def create_adapter(
    messenger: MessengerType, settings: Settings, container: Container
) -> MessengerAdapter:
    """SDK мессенджеров импортируются лениво: для Telegram не нужен SDK WhatsApp и т.д."""
    webhook = WebhookConfig(
        host=settings.webhook_host,
        port=settings.webhook_port,
        base_url=settings.webhook_base_url,
        secret=secret_value(settings.webhook_secret),
    )
    router = container.router

    match messenger:
        case MessengerType.TELEGRAM:
            from src.adapters.telegram import TelegramAdapter

            return TelegramAdapter.from_token(router, webhook, _secret(settings.telegram_bot_token))
        case MessengerType.MAX:
            from src.adapters.max import MaxAdapter

            return MaxAdapter.from_token(router, webhook, _secret(settings.max_bot_token))
        case MessengerType.WHATSAPP:
            from kapso_whatsapp import WhatsAppClient

            from src.adapters.whatsapp import WhatsAppAdapter

            return WhatsAppAdapter(
                router,
                webhook,
                client=WhatsAppClient(access_token=_secret(settings.whatsapp_access_token)),
                phone_number_id=str(settings.whatsapp_phone_number_id),
                verify_token=_secret(settings.whatsapp_verify_token),
                app_secret=_secret(settings.whatsapp_app_secret),
            )
        case MessengerType.MATTERMOST:
            from mattermostautodriver import AsyncTypedDriver

            from src.adapters.mattermost import MattermostAdapter
            from src.adapters.mattermost.adapter import driver_options

            options = driver_options(
                str(settings.mattermost_url), _secret(settings.mattermost_token)
            )
            return MattermostAdapter(
                router,
                webhook,
                driver=AsyncTypedDriver(options),
                http=container.http,
                webhook_token=secret_value(settings.mattermost_webhook_token),
            )


def _secret(value: SecretStr | None) -> str:
    if value is None:  # validate_settings проверяет это заранее; здесь — страховка
        raise ConfigurationError("Не задан обязательный секрет, см. .env.example")
    return value.get_secret_value()
