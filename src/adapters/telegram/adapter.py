from __future__ import annotations

import logging
from collections.abc import Sequence

import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramAPIError
from aiogram.types import Message, ReplyParameters, URLInputFile

from src.adapters.base import MessengerAdapter, WebhookConfig, image_as_link, truncate
from src.adapters.telegram.mapper import to_incoming_message
from src.adapters.telegram.webhook import WEBHOOK_PATH, create_webhook_app
from src.adapters.web import serve
from src.domain.entities import ImageReply, MessengerType, Reply, TextReply
from src.use_cases.router import MessageRouter

logger = logging.getLogger(__name__)


class TelegramAdapter(MessengerAdapter):
    messenger = MessengerType.TELEGRAM

    def __init__(self, router: MessageRouter, webhook: WebhookConfig, *, bot: Bot) -> None:
        super().__init__(router, webhook)
        self.bot = bot
        self.dispatcher = Dispatcher()
        # Один обработчик на все сообщения: что делать с текстом, решает ядро (MessageRouter).
        self.dispatcher.message.register(self.on_message)

    @classmethod
    def from_token(
        cls, router: MessageRouter, webhook: WebhookConfig, token: str
    ) -> TelegramAdapter:
        return cls(router, webhook, bot=Bot(token=token))

    async def on_message(self, message: Message) -> None:
        incoming = to_incoming_message(message)
        if incoming is None:
            return
        replies = await self._router.handle(incoming)
        await self.send_replies(message.chat.id, replies)

    async def run_polling(self) -> None:
        # Пока установлен вебхук, Telegram не отдаёт апдейты через getUpdates.
        await self.bot.delete_webhook(drop_pending_updates=False)
        me = await self.bot.get_me()
        logger.info("Telegram-бот @%s получает сообщения через long polling", me.username)
        await self.dispatcher.start_polling(self.bot, handle_signals=False, close_bot_session=False)

    async def run_webhook(self) -> None:
        await self.bot.set_webhook(
            self._webhook.public_url(WEBHOOK_PATH),
            secret_token=self._webhook.secret,
            allowed_updates=self.dispatcher.resolve_used_update_types(),
        )
        app = create_webhook_app(self.dispatcher, self.bot, self._webhook.secret)
        await serve(app, self._webhook.host, self._webhook.port)

    async def close(self) -> None:
        await self.bot.session.close()

    async def send_replies(self, chat_id: int, replies: Sequence[Reply]) -> None:
        for reply in replies:
            try:
                match reply:
                    case TextReply():
                        await self._send_text(chat_id, reply)
                    case ImageReply():
                        await self._send_image(chat_id, reply)
            except TelegramAPIError:
                logger.exception("Не удалось отправить ответ в Telegram-чат %s", chat_id)

    async def _send_text(self, chat_id: int, reply: TextReply) -> None:
        reply_parameters = None
        if reply.reply_to_message_id is not None:
            reply_parameters = ReplyParameters(
                message_id=int(reply.reply_to_message_id),
                allow_sending_without_reply=True,  # сообщение могли успеть удалить
            )
        await self.bot.send_message(chat_id, reply.text, reply_parameters=reply_parameters)

    async def _send_image(self, chat_id: int, reply: ImageReply) -> None:
        caption = truncate(reply.caption)
        animated = reply.url.lower().endswith(".gif")
        send = self.bot.send_animation if animated else self.bot.send_photo
        try:
            # Быстрый путь: Telegram сам скачивает картинку по ссылке.
            await send(chat_id, reply.url, caption=caption)
            return
        except TelegramAPIError as exc:
            logger.info("Telegram не скачал %s (%s), загружаю файл сам", reply.url, exc)
        try:
            await send(chat_id, URLInputFile(reply.url), caption=caption)
        except (TelegramAPIError, aiohttp.ClientError):
            logger.warning("Не удалось отправить картинку %s, отправляю ссылку", reply.url)
            await self.bot.send_message(chat_id, image_as_link(reply))
