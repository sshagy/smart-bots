"""Интерфейсы внешних сервисов. Реализации — в `src/infrastructure`."""

from __future__ import annotations

from abc import ABC, abstractmethod

from src.domain.entities import ApiUsageReport, Meme


class GatewayError(Exception):
    """Ошибка внешнего сервиса. Текст безопасно показывать пользователю."""


class UsageGatewayError(GatewayError):
    pass


class MemeGatewayError(GatewayError):
    pass


class IClaudeUsageGateway(ABC):
    @abstractmethod
    async def fetch_usage_report(self) -> ApiUsageReport:
        """Делает запрос к Claude API и возвращает расход токенов и остаток лимитов."""


class IMemeGateway(ABC):
    @abstractmethod
    async def get_random_meme(self) -> Meme:
        """Случайный мем с прямой ссылкой на картинку."""
