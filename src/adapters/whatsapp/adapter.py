from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from aiohttp import web
from kapso_whatsapp import WhatsAppAPIError, WhatsAppClient
from kapso_whatsapp.webhooks import normalize_webhook

from src.adapters.base import MessengerAdapter, WebhookConfig, truncate
from src.adapters.web import serve
from src.adapters.whatsapp.mapper import (
    WhatsAppInbound,
    log_failed_deliveries,
    to_incoming_messages,
)
from src.adapters.whatsapp.webhook import WEBHOOK_PATH, create_webhook_app
from src.domain.entities import ChatType, ImageReply, MessengerType, Reply, TextReply
from src.use_cases.router import MessageRouter

logger = logging.getLogger(__name__)


class WhatsAppAdapter(MessengerAdapter):
    messenger = MessengerType.WHATSAPP

    def __init__(
        self,
        router: MessageRouter,
        webhook: WebhookConfig,
        *,
        client: WhatsAppClient,
        phone_number_id: str,
        verify_token: str,
        app_secret: str,
    ) -> None:
        super().__init__(router, webhook)
        self.client = client
        self._phone_number_id = phone_number_id
        self._verify_token = verify_token
        self._app_secret = app_secret

    def create_app(self) -> web.Application:
        return create_webhook_app(
            self.handle_payload, verify_token=self._verify_token, app_secret=self._app_secret
        )

    def handle_payload(self, payload: dict[str, Any]) -> None:
        """Разбирает вебхук и ставит ответы в фоновую обработку."""
        result = normalize_webhook(payload)
        log_failed_deliveries(result)
        for inbound in to_incoming_messages(result):
            self._tasks.spawn(self._process(inbound))

    async def _process(self, inbound: WhatsAppInbound) -> None:
        replies = await self._router.handle(inbound.message)
        await self.send_replies(inbound, replies)

    async def run_polling(self) -> None:
        logger.warning(
            "WhatsApp Cloud API не поддерживает long polling — Meta доставляет сообщения "
            "только вебхуком. Запускаю webhook-сервер на %s:%s; для локальной отладки "
            "пробросьте порт туннелем (cloudflared/ngrok) и укажите URL …%s в Meta App Dashboard.",
            self._webhook.host,
            self._webhook.port,
            WEBHOOK_PATH,
        )
        await self.run_webhook()

    async def run_webhook(self) -> None:
        # Callback URL и verify token указываются в Meta App Dashboard → WhatsApp → Configuration.
        await serve(self.create_app(), self._webhook.host, self._webhook.port)

    async def close(self) -> None:
        await self.client.close()

    async def send_replies(self, inbound: WhatsAppInbound, replies: Sequence[Reply]) -> None:
        message = inbound.message
        phone_number_id = inbound.phone_number_id or self._phone_number_id
        for reply in replies:
            try:
                if message.chat_type is ChatType.GROUP:
                    await self._send_to_group(phone_number_id, message.chat_id, reply)
                else:
                    await self._send_to_user(phone_number_id, message.chat_id, reply)
            except (WhatsAppAPIError, ValueError):  # ValueError — валидация моделей SDK
                logger.exception("Не удалось отправить ответ в WhatsApp-чат %s", message.chat_id)

    async def _send_to_user(self, phone_number_id: str, to: str, reply: Reply) -> None:
        match reply:
            case TextReply():
                context = (
                    {"message_id": reply.reply_to_message_id} if reply.reply_to_message_id else None
                )
                await self.client.messages.send_text(
                    phone_number_id=phone_number_id, to=to, body=reply.text, context=context
                )
            case ImageReply():
                # WhatsApp сам скачает картинку по ссылке; если не сможет — пришлёт статус failed.
                await self.client.messages.send_image(
                    phone_number_id=phone_number_id,
                    to=to,
                    image={"link": reply.url, "caption": truncate(reply.caption)},
                )

    async def _send_to_group(self, phone_number_id: str, group_id: str, reply: Reply) -> None:
        # Типизированные методы kapso-SDK шлют только recipient_type=individual, а для Groups API
        # нужен recipient_type=group, поэтому запрос собираем сами и отправляем через клиент SDK.
        payload: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "recipient_type": "group",
            "to": group_id,
        }
        match reply:
            case TextReply():
                payload |= {"type": "text", "text": {"body": reply.text}}
                if reply.reply_to_message_id:
                    payload["context"] = {"message_id": reply.reply_to_message_id}
            case ImageReply():
                payload |= {
                    "type": "image",
                    "image": {"link": reply.url, "caption": truncate(reply.caption)},
                }
        await self.client.request("POST", f"{phone_number_id}/messages", json=payload)
