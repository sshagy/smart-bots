"""Приём событий WhatsApp Cloud API через вебхук.

У WhatsApp Cloud API нет long polling: Meta доставляет сообщения только вебхуком,
поэтому для WhatsApp этот модуль — не заглушка, а единственный способ получать
сообщения (BOT_MODE=polling тоже поднимает этот сервер).

Пока нет белого IP, для локальной отладки пробросьте WEBHOOK_PORT туннелем
(`cloudflared tunnel --url http://localhost:8080` или `ngrok http 8080`) и укажите
полученный https-адрес + /webhook/whatsapp в Meta App Dashboard → WhatsApp →
Configuration → Callback URL. Когда появится белый IP — просто замените адрес там же.

  • GET  — проверка подписки: Meta присылает hub.verify_token, сверяем с WHATSAPP_VERIFY_TOKEN;
  • POST — события: подпись X-Hub-Signature-256 сверяем с WHATSAPP_APP_SECRET.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiohttp import web
from kapso_whatsapp.webhooks import verify_signature

from src.adapters.web import new_web_app, secrets_equal

logger = logging.getLogger(__name__)

WEBHOOK_PATH = "/webhook/whatsapp"
SIGNATURE_HEADER = "X-Hub-Signature-256"

PayloadConsumer = Callable[[dict[str, Any]], None]


def webhook_handler(
    consume: PayloadConsumer, *, verify_token: str, app_secret: str
) -> tuple[
    Callable[[web.Request], Awaitable[web.Response]],
    Callable[[web.Request], Awaitable[web.Response]],
]:
    """Пара aiohttp-обработчиков: подтверждение подписки (GET) и приём событий (POST)."""

    async def verify(request: web.Request) -> web.Response:
        query = request.query
        if query.get("hub.mode") == "subscribe" and secrets_equal(
            query.get("hub.verify_token"), verify_token
        ):
            return web.Response(text=query.get("hub.challenge", ""))
        return web.Response(status=403, text="Forbidden")

    async def receive(request: web.Request) -> web.Response:
        body = await request.read()
        if not verify_signature(
            app_secret=app_secret,
            raw_body=body,
            signature_header=request.headers.get(SIGNATURE_HEADER),
        ):
            logger.warning("Отклонён вебхук WhatsApp с неверной подписью")
            return web.Response(status=401, text="Invalid signature")
        try:
            payload = json.loads(body)
        except ValueError:
            return web.Response(status=400, text="Bad Request")
        # Ответ 200 — сразу: обработка идёт в фоне, иначе Meta сочтёт доставку неудачной
        # и пришлёт событие повторно.
        consume(payload)
        return web.Response(text="ok")

    return verify, receive


def create_webhook_app(
    consume: PayloadConsumer, *, verify_token: str, app_secret: str
) -> web.Application:
    verify, receive = webhook_handler(consume, verify_token=verify_token, app_secret=app_secret)
    app = new_web_app()
    app.router.add_get(WEBHOOK_PATH, verify)
    app.router.add_post(WEBHOOK_PATH, receive)
    return app
