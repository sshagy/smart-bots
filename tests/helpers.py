"""Фабрики и фейки, общие для тестов."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from src.domain.entities import ChatType, IncomingMessage, MessengerType, ModerationEvent, User
from src.domain.repositories import IModerationRepository

FIXED_NOW = datetime(2026, 10, 30, 12, 0, 0, tzinfo=UTC)


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
