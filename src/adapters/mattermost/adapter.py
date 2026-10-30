from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlsplit

import httpx
from aiohttp import web
from mattermostautodriver import AsyncTypedDriver

from src.adapters.base import MessengerAdapter, WebhookConfig, image_as_link
from src.adapters.mattermost.mapper import (
    MattermostInbound,
    from_outgoing_webhook,
    from_websocket_event,
)
from src.adapters.mattermost.webhook import create_webhook_app
from src.adapters.web import serve
from src.config import ConfigurationError
from src.domain.entities import ImageReply, MessengerType, Reply, TextReply
from src.use_cases.router import MessageRouter

logger = logging.getLogger(__name__)

MAX_IMAGE_BYTES = 20 * 1024 * 1024


def driver_options(url: str, token: str, **extra: Any) -> dict[str, Any]:
    """MATTERMOST_URL=https://chat.example.com → опции mattermostautodriver."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ConfigurationError(
            f"MATTERMOST_URL должен быть вида https://chat.example.com: {url!r}"
        )
    if parts.path not in ("", "/"):
        raise ConfigurationError("Mattermost с подкаталогом в адресе драйвер не поддерживает")
    return {
        "url": parts.hostname,
        "scheme": parts.scheme,
        "port": parts.port or (443 if parts.scheme == "https" else 80),
        "token": token,
        "keepalive": True,  # переподключать WebSocket после обрыва
        "keepalive_delay": 5,
        **extra,
    }


class MattermostAdapter(MessengerAdapter):
    messenger = MessengerType.MATTERMOST

    def __init__(
        self,
        router: MessageRouter,
        webhook: WebhookConfig,
        *,
        driver: AsyncTypedDriver,
        http: httpx.AsyncClient,
        webhook_token: str | None = None,
    ) -> None:
        super().__init__(router, webhook)
        self.driver = driver
        self._http = http  # скачивание картинок для загрузки в Mattermost
        self._webhook_token = webhook_token
        self._bot_user_id: str | None = None

    async def login(self) -> None:
        me = await self.driver.login()
        self._bot_user_id = me["id"]
        logger.info("Mattermost: вошли как @%s", me.get("username"))

    async def on_websocket_event(self, raw: str) -> None:
        try:
            event = json.loads(raw)
        except ValueError:
            return
        inbound = from_websocket_event(event, bot_user_id=self._bot_user_id)
        if inbound is not None:
            # В фоне: WebSocket-цикл драйвера ждёт обработчик, а /check может идти секунды.
            self._tasks.spawn(self._process(inbound))

    def handle_outgoing_webhook(self, payload: dict[str, Any]) -> None:
        inbound = from_outgoing_webhook(payload, bot_user_id=self._bot_user_id)
        if inbound is not None:
            self._tasks.spawn(self._process(inbound))

    def create_app(self) -> web.Application:
        if not self._webhook_token:
            raise ConfigurationError("Для BOT_MODE=webhook задайте MATTERMOST_WEBHOOK_TOKEN")
        return create_webhook_app(self.handle_outgoing_webhook, self._webhook_token)

    async def _process(self, inbound: MattermostInbound) -> None:
        replies = await self._router.handle(inbound.message)
        await self.send_replies(inbound.message.chat_id, replies, root_id=inbound.thread_root_id)

    async def run_polling(self) -> None:
        # WebSocket — аналог long polling: соединение открывает бот, белый IP не нужен.
        await self.login()
        logger.info("Mattermost-бот слушает события через WebSocket")
        await self.driver.init_websocket(self.on_websocket_event)

    async def run_webhook(self) -> None:
        app = self.create_app()
        await self.login()  # отвечаем через REST API от имени бота
        await serve(app, self._webhook.host, self._webhook.port)

    async def close(self) -> None:
        if self.driver.websocket is not None:
            self.driver.websocket.disconnect()
        await self.driver.close()

    async def send_replies(
        self, channel_id: str, replies: Sequence[Reply], *, root_id: str | None = None
    ) -> None:
        for reply in replies:
            try:
                match reply:
                    case TextReply():
                        await self.driver.posts.create_post(
                            channel_id=channel_id, message=reply.text, root_id=root_id
                        )
                    case ImageReply():
                        await self._send_image(channel_id, reply, root_id)
            except httpx.HTTPError as exc:  # включает ошибки API Mattermost (MattermostError)
                logger.error(
                    "Не удалось отправить ответ в Mattermost-канал %s: %s", channel_id, exc
                )

    async def _send_image(self, channel_id: str, reply: ImageReply, root_id: str | None) -> None:
        # У Mattermost нет «картинки по ссылке» в API — скачиваем и загружаем файлом.
        try:
            response = await self._http.get(reply.url)
            response.raise_for_status()
            if len(response.content) > MAX_IMAGE_BYTES:
                raise ValueError("картинка слишком большая")
            filename = PurePosixPath(urlsplit(reply.url).path).name or "meme.jpg"
            uploaded = await self.driver.files.upload_file(
                files=(filename, response.content, response.headers.get("content-type")),
                channel_id=channel_id,
            )
            file_ids = [info["id"] for info in uploaded.get("file_infos", [])]
        except (httpx.HTTPError, ValueError, KeyError):
            logger.warning("Не удалось загрузить картинку %s, отправляю ссылку", reply.url)
            await self.driver.posts.create_post(
                channel_id=channel_id, message=image_as_link(reply), root_id=root_id
            )
            return
        await self.driver.posts.create_post(
            channel_id=channel_id, message=reply.caption or "", file_ids=file_ids, root_id=root_id
        )
