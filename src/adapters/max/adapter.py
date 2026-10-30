from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from typing import Any

from maxapi import Bot, image_attachment
from maxapi.exceptions import MaxError
from maxapi.types import Update, UpdateType

from src.adapters.base import MessengerAdapter, WebhookConfig, image_as_link
from src.adapters.max.mapper import to_incoming_message
from src.adapters.max.webhook import WEBHOOK_PATH, create_webhook_app
from src.adapters.web import serve
from src.domain.entities import ImageReply, IncomingMessage, MessengerType, Reply, TextReply
from src.use_cases.router import MessageRouter

logger = logging.getLogger(__name__)

UPDATE_TYPES = (UpdateType.MESSAGE_CREATED, UpdateType.BOT_STARTED)


class MaxAdapter(MessengerAdapter):
    """maxapi-sdk используется как типизированный клиент Bot API.

    Цикл long polling свой, а не `Dispatcher.start_polling`: в maxapi-sdk 0.13 диспетчер
    падает на апдейтах без message (bot_started), а его PollingRunner после ошибки
    не сдвигает marker и бесконечно перезапрашивает ту же пачку апдейтов.
    """

    messenger = MessengerType.MAX

    def __init__(
        self,
        router: MessageRouter,
        webhook: WebhookConfig,
        *,
        bot: Bot,
        poll_timeout: int = 30,
        retry_delay: float = 5.0,
    ) -> None:
        super().__init__(router, webhook)
        self.bot = bot
        self._poll_timeout = poll_timeout
        self._retry_delay = retry_delay

    @classmethod
    def from_token(cls, router: MessageRouter, webhook: WebhookConfig, token: str) -> MaxAdapter:
        return cls(router, webhook, bot=Bot(token=token))

    def handle_update(self, update: Update) -> None:
        # Ответ готовим в фоне: получение следующих апдейтов не ждёт, пока /check сходит в Claude.
        incoming = to_incoming_message(update)
        if incoming is not None:
            self._tasks.spawn(self._process(incoming))

    async def process_raw_update(self, payload: dict[str, Any]) -> None:
        self.handle_update(Update.model_validate(payload))

    async def _process(self, incoming: IncomingMessage) -> None:
        replies = await self._router.handle(incoming)
        await self.send_replies(int(incoming.chat_id), replies)

    async def run_polling(self) -> None:
        # Пока есть webhook-подписка, MAX не отдаёт события через GET /updates.
        for subscription in (await self.bot.get_subscriptions()).subscriptions:
            logger.warning("Удаляю webhook-подписку %s ради long polling", subscription.url)
            await self.bot.delete_webhook(subscription.url)
        me = await self.bot.get_me()
        logger.info("MAX-бот %s получает сообщения через long polling", me.username or me.user_id)

        marker: int | None = None
        while True:
            try:
                page = await self.bot.get_updates(
                    marker=marker, timeout=self._poll_timeout, types=UPDATE_TYPES
                )
            except Exception:
                # Сеть/сервер MAX недоступны — ждём и пробуем снова, бот не должен падать.
                logger.exception("Ошибка long polling MAX, повтор через %s с", self._retry_delay)
                await asyncio.sleep(self._retry_delay)
                continue
            for update in page.updates:
                self.handle_update(update)
            if page.marker is not None:
                marker = page.marker

    async def run_webhook(self) -> None:
        await self.bot.set_webhook(
            self._webhook.public_url(WEBHOOK_PATH),
            update_types=UPDATE_TYPES,
            secret=self._webhook.secret,
        )
        app = create_webhook_app(self.process_raw_update, self._webhook.secret)
        await serve(app, self._webhook.host, self._webhook.port)

    async def close(self) -> None:
        await self.bot.close_session()

    async def send_replies(self, chat_id: int, replies: Sequence[Reply]) -> None:
        for reply in replies:
            try:
                match reply:
                    case TextReply():
                        link = None
                        if reply.reply_to_message_id is not None:
                            link = {"type": "reply", "mid": reply.reply_to_message_id}
                        await self.bot.send_message(chat_id=chat_id, text=reply.text, link=link)
                    case ImageReply():
                        await self._send_image(chat_id, reply)
            except MaxError:
                logger.exception("Не удалось отправить ответ в MAX-чат %s", chat_id)

    async def _send_image(self, chat_id: int, reply: ImageReply) -> None:
        try:
            # MAX умеет прикреплять картинку по внешнему URL — загружать файл не нужно.
            await self.bot.send_message(
                chat_id=chat_id,
                text=reply.caption,
                attachments=[image_attachment(url=reply.url)],
            )
        except MaxError:
            logger.warning("MAX не принял картинку %s, отправляю ссылку", reply.url)
            await self.bot.send_message(chat_id=chat_id, text=image_as_link(reply))
