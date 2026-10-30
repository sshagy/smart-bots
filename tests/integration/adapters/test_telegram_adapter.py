"""Telegram-адаптер: настоящие объекты aiogram, сеть подменена фейковой сессией."""

import asyncio
from collections.abc import AsyncGenerator
from typing import Any

import pytest
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendAnimation, SendMessage, SendPhoto, SetWebhook, TelegramMethod
from aiogram.types import Message, Update, URLInputFile
from aiohttp.test_utils import TestClient, TestServer

from src.adapters.base import WebhookConfig
from src.adapters.telegram import TelegramAdapter
from src.adapters.telegram.mapper import to_incoming_message
from src.adapters.telegram.webhook import WEBHOOK_PATH, create_webhook_app
from src.domain.entities import ChatType, Meme, MessengerType
from tests.helpers import (
    FIXED_NOW,
    FIXED_NOW_TS,
    InMemoryModerationRepository,
    StaticMemeGateway,
    make_router,
    wait_until,
)

pytestmark = pytest.mark.asyncio

GROUP = {"id": -1001234567890, "type": "supergroup", "title": "Наш чат"}
PRIVATE = {"id": 42, "type": "private", "first_name": "Вася"}
VASYA = {
    "id": 42,
    "is_bot": False,
    "first_name": "Вася",
    "last_name": "Пупкин",
    "username": "vasya",
}


class FakeTelegramSession(BaseSession):
    """Вместо HTTP-запросов к Bot API запоминает вызванные методы."""

    def __init__(self) -> None:
        super().__init__()
        self.requests: list[TelegramMethod[Any]] = []
        self.failing: set[type[TelegramMethod[Any]]] = set()

    async def make_request(self, bot: Bot, method: TelegramMethod[Any], timeout: int | None = None):
        self.requests.append(method)
        if type(method) in self.failing:
            raise TelegramBadRequest(
                method, "Bad Request: wrong file identifier/HTTP URL specified"
            )
        return True

    async def stream_content(
        self, url: str, *args: Any, **kwargs: Any
    ) -> AsyncGenerator[bytes, None]:
        yield b"image-bytes"

    async def close(self) -> None:
        pass

    def sent(self, method_type: type) -> list[Any]:
        return [request for request in self.requests if isinstance(request, method_type)]


def message_payload(text: str | None = "привет", chat: dict = GROUP, **extra: Any) -> dict:
    payload = {"message_id": 555, "date": FIXED_NOW_TS, "chat": chat, "from": VASYA, **extra}
    if text is not None:
        payload["text"] = text
    return payload


@pytest.fixture
def session() -> FakeTelegramSession:
    return FakeTelegramSession()


@pytest.fixture
def repository() -> InMemoryModerationRepository:
    return InMemoryModerationRepository()


@pytest.fixture
def adapter(
    session: FakeTelegramSession, repository: InMemoryModerationRepository
) -> TelegramAdapter:
    bot = Bot(token="42:TEST-TOKEN", session=session)
    return TelegramAdapter(
        make_router(repository=repository),
        WebhookConfig(base_url="https://bot.example.com", secret="s3cret", port=0),
        bot=bot,
    )


async def feed(adapter: TelegramAdapter, message: dict) -> None:
    update = Update.model_validate(
        {"update_id": 1, "message": message}, context={"bot": adapter.bot}
    )
    await adapter.dispatcher.feed_update(adapter.bot, update)


# --- маппер ---


async def test_mapper_converts_group_message() -> None:
    message = Message.model_validate(message_payload("ну бля"))

    incoming = to_incoming_message(message)

    assert incoming is not None
    assert incoming.messenger is MessengerType.TELEGRAM
    assert incoming.chat_type is ChatType.GROUP
    assert (incoming.message_id, incoming.chat_id) == ("555", "-1001234567890")
    assert (incoming.sender.id, incoming.sender.username) == ("42", "vasya")
    assert incoming.sender.display_name == "Вася Пупкин"
    assert incoming.text == "ну бля"
    assert incoming.timestamp == FIXED_NOW


async def test_mapper_private_chat_and_caption() -> None:
    message = Message.model_validate(
        message_payload(None, chat=PRIVATE, caption="/help", photo=[_photo_size()])
    )

    incoming = to_incoming_message(message)

    assert incoming is not None
    assert incoming.chat_type is ChatType.PRIVATE
    assert incoming.text == "/help"


async def test_mapper_anonymous_admin_uses_sender_chat() -> None:
    payload = message_payload(
        "бля", sender_chat={"id": -100999, "type": "supergroup", "title": "Админ"}
    )
    del payload["from"]

    incoming = to_incoming_message(Message.model_validate(payload))

    assert incoming is not None
    assert (incoming.sender.id, incoming.sender.display_name) == ("-100999", "Админ")


@pytest.mark.parametrize(
    "payload",
    [
        message_payload(None, sticker=None),  # нет текста
        message_payload("бля", chat={"id": -100777, "type": "channel", "title": "Канал"}),
        message_payload("бля", **{"from": {**VASYA, "is_bot": True}}),  # другой бот
    ],
)
async def test_mapper_ignores_unsupported_messages(payload: dict) -> None:
    assert to_incoming_message(Message.model_validate(payload)) is None


# --- полный путь: апдейт → ядро → вызов Bot API ---


async def test_profanity_in_group_gets_warning_reply(
    adapter: TelegramAdapter, session: FakeTelegramSession, repository: InMemoryModerationRepository
) -> None:
    await feed(adapter, message_payload("ну бля, опять"))

    [sent] = session.sent(SendMessage)
    assert sent.chat_id == GROUP["id"]
    assert sent.text == "@vasya, пожалуйста, не материтесь в чате, иначе будет бан."
    assert sent.reply_parameters.message_id == 555
    assert [event.matched_keyword for event in repository.events] == ["бля*"]


async def test_clean_group_message_gets_no_reply(
    adapter: TelegramAdapter, session: FakeTelegramSession
) -> None:
    await feed(adapter, message_payload("всем привет"))

    assert session.requests == []


async def test_help_in_private_chat(adapter: TelegramAdapter, session: FakeTelegramSession) -> None:
    await feed(adapter, message_payload("/help", chat=PRIVATE))

    [sent] = session.sent(SendMessage)
    assert sent.chat_id == PRIVATE["id"]
    assert "/check" in sent.text
    assert sent.reply_parameters is None


async def test_mem_sends_photo_by_url(
    adapter: TelegramAdapter, session: FakeTelegramSession
) -> None:
    await feed(adapter, message_payload("/mem", chat=PRIVATE))

    [photo] = session.sent(SendPhoto)
    assert (photo.photo, photo.caption) == ("https://i.redd.it/abc.jpg", "Clean code")


async def test_mem_falls_back_to_upload_and_then_to_link(
    adapter: TelegramAdapter, session: FakeTelegramSession
) -> None:
    session.failing.add(SendPhoto)

    await feed(adapter, message_payload("/mem", chat=PRIVATE))

    by_url, uploaded = session.sent(SendPhoto)
    assert by_url.photo == "https://i.redd.it/abc.jpg"
    assert isinstance(uploaded.photo, URLInputFile)
    [link] = session.sent(SendMessage)
    assert link.text == "Clean code\nhttps://i.redd.it/abc.jpg"


async def test_gif_meme_is_sent_as_animation(
    session: FakeTelegramSession, repository: InMemoryModerationRepository
) -> None:
    gif = StaticMemeGateway(Meme(title="гифка", image_url="https://i.redd.it/fun.GIF"))
    adapter = TelegramAdapter(
        make_router(meme_gateway=gif),
        WebhookConfig(),
        bot=Bot(token="42:TEST-TOKEN", session=session),
    )

    await feed(adapter, message_payload("/mem", chat=PRIVATE))

    [animation] = session.sent(SendAnimation)
    assert animation.animation == "https://i.redd.it/fun.GIF"


# --- вебхук ---


async def test_webhook_rejects_wrong_secret_and_accepts_right_one(
    adapter: TelegramAdapter, session: FakeTelegramSession
) -> None:
    app = create_webhook_app(adapter.dispatcher, adapter.bot, secret="s3cret")
    update = {"update_id": 7, "message": message_payload("бля")}

    async with TestClient(TestServer(app)) as client:
        rejected = await client.post(
            WEBHOOK_PATH, json=update, headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"}
        )
        accepted = await client.post(
            WEBHOOK_PATH, json=update, headers={"X-Telegram-Bot-Api-Secret-Token": "s3cret"}
        )
        await wait_until(lambda: bool(session.sent(SendMessage)))
        health = await client.get("/healthz")

    assert rejected.status == 401
    assert accepted.status == 200
    assert health.status == 200
    assert "не материтесь" in session.sent(SendMessage)[0].text


async def test_run_webhook_registers_url_with_secret(
    adapter: TelegramAdapter, session: FakeTelegramSession
) -> None:
    task = asyncio.create_task(adapter.run_webhook())
    try:
        await wait_until(lambda: bool(session.sent(SetWebhook)))
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    [registration] = session.sent(SetWebhook)
    assert registration.url == "https://bot.example.com/webhook/telegram"
    assert registration.secret_token == "s3cret"
    assert "message" in registration.allowed_updates


def _photo_size() -> dict:
    return {"file_id": "f", "file_unique_id": "u", "width": 10, "height": 10}
