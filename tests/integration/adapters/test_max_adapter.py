"""MAX-адаптер: настоящие модели и клиент maxapi-sdk, HTTP подменён на уровне Bot.request."""

import asyncio
from typing import Any

import pytest
from aiohttp.test_utils import TestClient, TestServer
from maxapi import Bot
from maxapi.exceptions import MaxApiError, MaxConnection
from maxapi.types import SubscriptionsPage, Update, UpdatesPage, User
from pytest_mock import MockerFixture

from src.adapters.base import WebhookConfig
from src.adapters.max import MaxAdapter
from src.adapters.max.mapper import to_incoming_message
from src.adapters.max.webhook import SECRET_HEADER, WEBHOOK_PATH, create_webhook_app
from src.domain.entities import ChatType, MessengerType
from tests.helpers import (
    FIXED_NOW,
    FIXED_NOW_TS,
    InMemoryModerationRepository,
    make_router,
    wait_until,
)

pytestmark = pytest.mark.asyncio

NOW_MS = FIXED_NOW_TS * 1000


def message_update(
    text: str | None = "привет",
    *,
    chat_type: str = "chat",
    chat_id: int = -70000000001,
    is_bot: bool = False,
) -> dict[str, Any]:
    """Апдейт message_created в том виде, в каком его присылает MAX Bot API."""
    return {
        "update_type": "message_created",
        "timestamp": NOW_MS,
        "message": {
            "sender": {
                "user_id": 1001,
                "first_name": "Вася",
                "last_name": "Пупкин",
                "username": "vasya",
                "is_bot": is_bot,
                "name": "Вася Пупкин",
            },
            "recipient": {"chat_id": chat_id, "chat_type": chat_type, "user_id": None},
            "timestamp": NOW_MS,
            "body": {"mid": "mid.0000abc", "seq": 1, "text": text, "attachments": None},
        },
        "user_locale": "ru",
    }


class FakeMaxApi:
    """Запоминает HTTP-вызовы, которые сделал бы maxapi-sdk."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.fail_next: list[Exception] = []
        self.subscriptions: list[dict[str, Any]] = []
        # Ответы на GET /updates; когда закончатся — long poll «висит», как настоящий.
        self.update_pages: list[dict[str, Any] | Exception] = []

    async def __call__(self, method: str, path: str, model: Any = None, **kwargs: Any) -> Any:
        self.calls.append({"method": method, "path": path, **kwargs})
        if (method, path) == ("GET", "/updates"):
            if not self.update_pages:
                await asyncio.Event().wait()
            page = self.update_pages.pop(0)
            if isinstance(page, Exception):
                raise page
            return UpdatesPage.model_validate(page)
        if self.fail_next:
            raise self.fail_next.pop(0)
        if (method, path) == ("GET", "/subscriptions"):
            return SubscriptionsPage(subscriptions=self.subscriptions)
        if (method, path) == ("GET", "/me"):
            return User(user_id=1, username="smart_bot", is_bot=True)
        return None

    def calls_to(self, method: str, path: str) -> list[dict[str, Any]]:
        return [c for c in self.calls if (c["method"], c["path"]) == (method, path)]

    def messages(self) -> list[dict[str, Any]]:
        return self.calls_to("POST", "/messages")


@pytest.fixture
def api() -> FakeMaxApi:
    return FakeMaxApi()


@pytest.fixture
def repository() -> InMemoryModerationRepository:
    return InMemoryModerationRepository()


@pytest.fixture
def adapter(
    api: FakeMaxApi, repository: InMemoryModerationRepository, mocker: MockerFixture
) -> MaxAdapter:
    bot = Bot(token="max-test-token")
    # Передаём связанный async-метод: экземпляр с async __call__ AsyncMock не распознаёт.
    mocker.patch.object(bot, "request", side_effect=api.__call__)
    return MaxAdapter(
        make_router(repository=repository),
        WebhookConfig(base_url="https://bot.example.com", secret="s3cret", port=0),
        bot=bot,
    )


async def feed(adapter: MaxAdapter, payload: dict[str, Any]) -> None:
    await adapter.process_raw_update(payload)
    await adapter.wait_for_background_tasks()


# --- маппер ---


async def test_mapper_converts_group_message() -> None:
    incoming = to_incoming_message(Update.model_validate(message_update("ну бля")))

    assert incoming is not None
    assert incoming.messenger is MessengerType.MAX
    assert incoming.chat_type is ChatType.GROUP
    assert (incoming.message_id, incoming.chat_id) == ("mid.0000abc", "-70000000001")
    assert (incoming.sender.id, incoming.sender.username) == ("1001", "vasya")
    assert incoming.sender.display_name == "Вася Пупкин"
    assert incoming.timestamp == FIXED_NOW


async def test_mapper_dialog_is_private_chat() -> None:
    incoming = to_incoming_message(
        Update.model_validate(message_update("/help", chat_type="dialog"))
    )

    assert incoming is not None
    assert incoming.chat_type is ChatType.PRIVATE


async def test_mapper_bot_started_becomes_start_command() -> None:
    update = Update.model_validate(
        {
            "update_type": "bot_started",
            "timestamp": NOW_MS,
            "chat_id": 777,
            "user": {"user_id": 1001, "first_name": "Вася", "name": "Вася"},
        }
    )

    incoming = to_incoming_message(update)

    assert incoming is not None
    assert (incoming.text, incoming.chat_type, incoming.chat_id) == (
        "/start",
        ChatType.PRIVATE,
        "777",
    )


@pytest.mark.parametrize(
    "payload",
    [
        message_update(None),
        message_update("бля", chat_type="channel"),
        message_update("бля", is_bot=True),
        {"update_type": "user_added", "timestamp": NOW_MS, "chat_id": 1},
    ],
)
async def test_mapper_ignores_unsupported_updates(payload: dict[str, Any]) -> None:
    assert to_incoming_message(Update.model_validate(payload)) is None


# --- полный путь ---


async def test_profanity_in_group_gets_reply_warning(
    adapter: MaxAdapter, api: FakeMaxApi, repository: InMemoryModerationRepository
) -> None:
    await feed(adapter, message_update("ну бля"))

    [sent] = api.messages()
    assert sent["params"] == {"chat_id": -70000000001}
    assert sent["json"]["text"] == "@vasya, пожалуйста, не материтесь в чате, иначе будет бан."
    assert sent["json"]["link"] == {"type": "reply", "mid": "mid.0000abc"}
    assert repository.events[0].message_id == "mid.0000abc"


async def test_bot_started_gets_help(adapter: MaxAdapter, api: FakeMaxApi) -> None:
    await feed(
        adapter,
        {"update_type": "bot_started", "timestamp": NOW_MS, "chat_id": 777, "user": {"user_id": 1}},
    )

    [sent] = api.messages()
    assert sent["params"] == {"chat_id": 777}
    assert "/mem" in sent["json"]["text"]


async def test_mem_attaches_image_by_url(adapter: MaxAdapter, api: FakeMaxApi) -> None:
    await feed(adapter, message_update("/mem", chat_type="dialog", chat_id=777))

    [sent] = api.messages()
    assert sent["json"]["text"] == "Clean code"
    assert sent["json"]["attachments"] == [
        {"type": "image", "payload": {"url": "https://i.redd.it/abc.jpg"}}
    ]


async def test_mem_falls_back_to_link_when_image_is_rejected(
    adapter: MaxAdapter, api: FakeMaxApi
) -> None:
    api.fail_next.append(MaxApiError(code=400, raw={"code": "attachment.invalid"}))

    await feed(adapter, message_update("/mem", chat_type="dialog", chat_id=777))

    _, fallback = api.messages()
    assert fallback["json"]["text"] == "Clean code\nhttps://i.redd.it/abc.jpg"


# --- вебхук и режимы запуска ---


async def test_webhook_checks_secret_and_processes_update(
    adapter: MaxAdapter, api: FakeMaxApi
) -> None:
    app = create_webhook_app(adapter.process_raw_update, secret="s3cret")

    async with TestClient(TestServer(app)) as client:
        forbidden = await client.post(WEBHOOK_PATH, json=message_update("бля"))
        broken = await client.post(WEBHOOK_PATH, data="{", headers={SECRET_HEADER: "s3cret"})
        invalid = await client.post(
            WEBHOOK_PATH, json={"oops": 1}, headers={SECRET_HEADER: "s3cret"}
        )
        accepted = await client.post(
            WEBHOOK_PATH, json=message_update("бля"), headers={SECRET_HEADER: "s3cret"}
        )
        await adapter.wait_for_background_tasks()

    assert (forbidden.status, broken.status, invalid.status, accepted.status) == (
        403,
        400,
        400,
        200,
    )
    assert len(api.messages()) == 1


async def test_run_webhook_subscribes_with_secret(adapter: MaxAdapter, api: FakeMaxApi) -> None:
    task = asyncio.create_task(adapter.run_webhook())
    try:
        await wait_until(lambda: any(c["path"] == "/subscriptions" for c in api.calls))
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    [subscribe] = [c for c in api.calls if (c["method"], c["path"]) == ("POST", "/subscriptions")]
    assert subscribe["json"] == {
        "url": "https://bot.example.com/webhook/max",
        "update_types": ["message_created", "bot_started"],
        "secret": "s3cret",
    }


async def run_polling_until(adapter: MaxAdapter, condition) -> None:
    task = asyncio.create_task(adapter.run_polling())
    try:
        await wait_until(condition)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    await adapter.wait_for_background_tasks()


async def test_polling_handles_updates_and_advances_marker(
    adapter: MaxAdapter, api: FakeMaxApi
) -> None:
    api.update_pages = [{"updates": [message_update("ну бля")], "marker": 5}]

    await run_polling_until(adapter, lambda: len(api.calls_to("GET", "/updates")) == 2)

    first, second = api.calls_to("GET", "/updates")
    assert "marker" not in first["params"]
    assert second["params"]["marker"] == 5  # следующий запрос — после обработанных апдейтов
    assert second["params"]["types"] == "message_created,bot_started"
    assert len(api.messages()) == 1


async def test_polling_survives_network_errors(adapter: MaxAdapter, api: FakeMaxApi) -> None:
    adapter._retry_delay = 0
    api.update_pages = [
        MaxConnection("сеть недоступна"),
        {"updates": [message_update("/help", chat_type="dialog", chat_id=777)], "marker": 1},
    ]

    await run_polling_until(adapter, lambda: bool(api.messages()))

    assert "/check" in api.messages()[0]["json"]["text"]


async def test_polling_removes_old_webhook_subscriptions(
    adapter: MaxAdapter, api: FakeMaxApi
) -> None:
    api.subscriptions = [{"url": "https://old.example.com/hook"}]

    await run_polling_until(adapter, lambda: bool(api.calls_to("GET", "/updates")))

    [delete] = api.calls_to("DELETE", "/subscriptions")
    assert delete["params"] == {"url": "https://old.example.com/hook"}
