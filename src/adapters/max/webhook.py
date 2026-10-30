"""Точка расширения: приём апдейтов MAX через вебхук.

По умолчанию бот работает через long polling (GET /updates, BOT_MODE=polling) —
белый IP не нужен. Когда появится публичный адрес с HTTPS:

  1. WEBHOOK_BASE_URL=https://bot.example.com — сюда MAX будет присылать события;
  2. WEBHOOK_SECRET=<случайная строка> — MAX передаёт её в заголовке
     X-Max-Bot-Api-Secret, запросы без него получат 403;
  3. BOT_MODE=webhook — при старте адаптер подпишется (POST /subscriptions)
     на {WEBHOOK_BASE_URL}/webhook/max.

Пока у бота есть подписка, long polling не работает, поэтому в режиме polling
адаптер сам удаляет старые подписки.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiohttp import web
from pydantic import ValidationError

from src.adapters.web import new_web_app, secrets_equal

logger = logging.getLogger(__name__)

WEBHOOK_PATH = "/webhook/max"
SECRET_HEADER = "X-Max-Bot-Api-Secret"

UpdateConsumer = Callable[[dict[str, Any]], Awaitable[None]]


def webhook_handler(
    consume: UpdateConsumer, secret: str | None
) -> Callable[[web.Request], Awaitable[web.Response]]:
    """Обработчик POST-запросов MAX: проверяет секрет и передаёт апдейт адаптеру."""

    async def handle(request: web.Request) -> web.Response:
        if secret is not None and not secrets_equal(request.headers.get(SECRET_HEADER), secret):
            return web.Response(status=403, text="Forbidden")
        try:
            payload = await request.json()
            await consume(payload)
        except (ValueError, ValidationError):
            logger.warning("MAX прислал некорректный апдейт")
            return web.Response(status=400, text="Bad Request")
        return web.json_response({"ok": True})

    return handle


def create_webhook_app(consume: UpdateConsumer, secret: str | None) -> web.Application:
    app = new_web_app()
    app.router.add_post(WEBHOOK_PATH, webhook_handler(consume, secret))
    return app
