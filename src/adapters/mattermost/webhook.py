"""Точка расширения: приём сообщений Mattermost через исходящий вебхук.

По умолчанию (BOT_MODE=polling) бот подключается к WebSocket API Mattermost —
соединение открывает сам бот, поэтому белый IP не нужен, а события приходят из
всех каналов и личных сообщений. Это рекомендуемый режим и при наличии белого IP.

Режим BOT_MODE=webhook — для серверов, где WebSocket бота недоступен:
  1. Главное меню → Integrations → Outgoing Webhooks → Add: канал, который
     модерировать, Callback URL = {WEBHOOK_BASE_URL}/webhook/mattermost;
  2. сгенерированный токен — в MATTERMOST_WEBHOOK_TOKEN (запросы без него получат 401);
  3. BOT_MODE=webhook.
Ограничение Mattermost: исходящие вебхуки работают только в публичных каналах,
поэтому в этом режиме доступна модерация, но не команды в личке.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiohttp import web

from src.adapters.web import new_web_app, secrets_equal

logger = logging.getLogger(__name__)

WEBHOOK_PATH = "/webhook/mattermost"

PayloadConsumer = Callable[[dict[str, Any]], None]


def webhook_handler(
    consume: PayloadConsumer, token: str
) -> Callable[[web.Request], Awaitable[web.Response]]:
    """Обработчик исходящего вебхука: проверяет токен и ставит сообщение в обработку."""

    async def handle(request: web.Request) -> web.Response:
        try:
            if request.content_type == "application/json":
                payload = await request.json()
            else:  # Mattermost умеет слать и application/x-www-form-urlencoded
                payload = dict(await request.post())
        except ValueError:
            return web.Response(status=400, text="Bad Request")
        if not isinstance(payload, dict) or not secrets_equal(str(payload.get("token", "")), token):
            return web.Response(status=401, text="Unauthorized")
        consume(payload)
        # Пустой JSON — Mattermost ничего не публикует от имени вебхука, ответ отправит бот.
        return web.json_response({})

    return handle


def create_webhook_app(consume: PayloadConsumer, token: str) -> web.Application:
    app = new_web_app()
    app.router.add_post(WEBHOOK_PATH, webhook_handler(consume, token))
    return app
