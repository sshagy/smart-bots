"""Общая часть адаптеров: режимы запуска, фоновые задачи, настройки вебхуков.

Адаптер — это «контроллер» чистой архитектуры: переводит событие мессенджера в
`IncomingMessage`, вызывает `MessageRouter` и отрисовывает `Reply` средствами SDK.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from collections.abc import Coroutine
from dataclasses import dataclass
from typing import Any, ClassVar

from src.config import BotMode, ConfigurationError
from src.domain.entities import ImageReply, MessengerType
from src.use_cases.router import MessageRouter

logger = logging.getLogger(__name__)

# Подписи к картинкам в Telegram и WhatsApp ограничены 1024 символами.
CAPTION_LIMIT = 1000


def truncate(text: str | None, limit: int = CAPTION_LIMIT) -> str | None:
    if text is None or len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def image_as_link(reply: ImageReply) -> str:
    """Запасной вариант, когда мессенджер не принял картинку: ссылка, превью построит клиент."""
    return f"{reply.caption}\n{reply.url}" if reply.caption else reply.url


@dataclass(frozen=True, slots=True)
class WebhookConfig:
    host: str = "0.0.0.0"
    port: int = 8080
    base_url: str | None = None  # публичный HTTPS-адрес бота
    secret: str | None = None

    def public_url(self, path: str) -> str:
        if not self.base_url:
            raise ConfigurationError(
                "Для BOT_MODE=webhook задайте WEBHOOK_BASE_URL — публичный HTTPS-адрес бота."
            )
        return self.base_url + path


class BackgroundTasks:
    """Фоновая обработка событий.

    Мессенджеры ждут быстрого ответа на вебхук (Meta повторяет доставку, если нет 200),
    а обработка может идти секунды (/check ходит в Claude API) — поэтому событие
    обрабатывается в фоне. При остановке бот дожидается уже начатых ответов.
    """

    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[Any]] = set()

    def spawn(self, coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._on_done)
        return task

    def _on_done(self, task: asyncio.Task[Any]) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and (error := task.exception()) is not None:
            logger.error("Ошибка фоновой обработки события", exc_info=error)

    async def drain(self, grace_period: float | None = None) -> None:
        """Ждёт завершения фоновых задач; через grace_period секунд отменяет оставшиеся."""
        while self._tasks:
            _, pending = await asyncio.wait(set(self._tasks), timeout=grace_period)
            if pending:
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                return


class MessengerAdapter(ABC):
    messenger: ClassVar[MessengerType]

    def __init__(self, router: MessageRouter, webhook: WebhookConfig) -> None:
        self._router = router
        self._webhook = webhook
        self._tasks = BackgroundTasks()

    async def run(self, mode: BotMode) -> None:
        logger.info("Запуск адаптера %s в режиме %s", self.messenger, mode)
        try:
            if mode is BotMode.WEBHOOK:
                await self.run_webhook()
            else:
                await self.run_polling()
        finally:
            await self._tasks.drain(grace_period=10)
            await self.close()

    @abstractmethod
    async def run_polling(self) -> None:
        """События без белого IP: бот сам ходит к серверу (long polling / WebSocket)."""

    @abstractmethod
    async def run_webhook(self) -> None:
        """События через HTTP-вебхук: мессенджер сам приходит к боту на публичный URL."""

    async def close(self) -> None:  # noqa: B027 — необязательный хук, не abstract
        """Закрывает сетевые сессии SDK."""

    async def wait_for_background_tasks(self) -> None:
        await self._tasks.drain()
