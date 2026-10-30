"""Интерфейсы хранилищ. Реализации — в `src/infrastructure/db`."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from src.domain.entities import KeywordStats, MessengerType, ModerationEvent, UserViolationStats


class IModerationRepository(ABC):
    @abstractmethod
    async def save(self, event: ModerationEvent) -> bool:
        """Сохраняет событие.

        Возвращает False, если событие для этого сообщения уже записано
        (повторная доставка вебхука/апдейта) — тогда предупреждение не дублируется.
        """

    @abstractmethod
    async def list_events(
        self,
        *,
        messenger: MessengerType | None = None,
        chat_id: str | None = None,
        limit: int = 100,
    ) -> list[ModerationEvent]:
        """Последние события, новые первыми."""


class IStatsRepository(ABC):
    @abstractmethod
    async def get_stats_by_user(
        self,
        *,
        messenger: MessengerType | None = None,
        chat_id: str | None = None,
        since: datetime | None = None,
        limit: int = 10,
    ) -> list[UserViolationStats]:
        """Нарушители, отсортированные по числу нарушений."""

    @abstractmethod
    async def get_top_keywords(
        self,
        *,
        messenger: MessengerType | None = None,
        chat_id: str | None = None,
        since: datetime | None = None,
        limit: int = 10,
    ) -> list[KeywordStats]:
        """Самые частые сработавшие ключевые слова."""
