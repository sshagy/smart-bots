"""Фабрики и фейки, общие для тестов."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
from dataclasses import replace
from datetime import UTC, datetime

from src.domain.entities import (
    ChatType,
    IncomingMessage,
    Meme,
    MessengerType,
    ModerationEvent,
    User,
)
from src.domain.gateways import IClaudeUsageGateway, IMemeGateway
from src.domain.repositories import IModerationRepository
from src.domain.services import ModerationService
from src.use_cases.handle_check import CheckUsageUseCase
from src.use_cases.handle_help import HelpUseCase
from src.use_cases.handle_mem import GetMemeUseCase
from src.use_cases.moderate_message import ModerateMessageUseCase
from src.use_cases.router import MessageRouter

FIXED_NOW = datetime(2026, 10, 30, 12, 0, 0, tzinfo=UTC)
FIXED_NOW_TS = int(FIXED_NOW.timestamp())
MEME = Meme(title="Clean code", image_url="https://i.redd.it/abc.jpg")
WARNING_TEMPLATE = "{user}, пожалуйста, не материтесь в чате, иначе будет бан."


def make_message(
    text: str = "привет",
    *,
    chat_type: ChatType = ChatType.GROUP,
    messenger: MessengerType = MessengerType.TELEGRAM,
    message_id: str = "100",
    chat_id: str = "-1001",
    user: User | None = None,
    timestamp: datetime = FIXED_NOW,
) -> IncomingMessage:
    return IncomingMessage(
        messenger=messenger,
        message_id=message_id,
        chat_id=chat_id,
        chat_type=chat_type,
        sender=user or User(id="42", username="vasya", display_name="Вася Пупкин"),
        text=text,
        timestamp=timestamp,
    )


class InMemoryModerationRepository(IModerationRepository):
    """Фейк репозитория с той же семантикой дублей, что и SQLite-реализация."""

    def __init__(self) -> None:
        self.events: list[ModerationEvent] = []

    async def save(self, event: ModerationEvent) -> bool:
        key = (event.messenger_type, event.chat_id, event.message_id)
        if any((e.messenger_type, e.chat_id, e.message_id) == key for e in self.events):
            return False
        self.events.append(replace(event, id=len(self.events) + 1))
        return True

    async def list_events(
        self,
        *,
        messenger: MessengerType | None = None,
        chat_id: str | None = None,
        limit: int = 100,
    ) -> list[ModerationEvent]:
        events = [
            e
            for e in reversed(self.events)
            if (messenger is None or e.messenger_type == messenger)
            and (chat_id is None or e.chat_id == chat_id)
        ]
        return events[:limit]


class StaticMemeGateway(IMemeGateway):
    def __init__(self, meme: Meme = MEME) -> None:
        self.meme = meme

    async def get_random_meme(self) -> Meme:
        return self.meme


def make_router(
    *,
    repository: IModerationRepository | None = None,
    usage_gateway: IClaudeUsageGateway | None = None,
    meme_gateway: IMemeGateway | None = None,
    keywords: Iterable[str] = ("бля*",),
) -> MessageRouter:
    """Настоящее ядро с фейковыми хранилищем и внешними сервисами — для тестов адаптеров."""
    return MessageRouter(
        moderate=ModerateMessageUseCase(
            ModerationService(keywords),
            repository or InMemoryModerationRepository(),
            WARNING_TEMPLATE,
        ),
        help_=HelpUseCase(),
        check=CheckUsageUseCase(usage_gateway),
        meme=GetMemeUseCase(meme_gateway or StaticMemeGateway()),
    )


async def wait_until(condition: Callable[[], bool], within: float = 2.0) -> None:
    """Ждёт фоновую обработку (вебхуки отвечают 200 раньше, чем бот ответит в чат)."""
    try:
        async with asyncio.timeout(within):
            while not condition():
                await asyncio.sleep(0.01)
    except TimeoutError:
        raise AssertionError(f"условие не выполнилось за {within} с") from None
