"""Mattermost-адаптер: настоящий AsyncTypedDriver, REST API подменён httpx.MockTransport."""

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer
from mattermostautodriver import AsyncTypedDriver
from pytest_mock import MockerFixture

from src.adapters.base import WebhookConfig
from src.adapters.mattermost import MattermostAdapter
from src.adapters.mattermost.adapter import driver_options
from src.adapters.mattermost.mapper import from_websocket_event
from src.adapters.mattermost.webhook import WEBHOOK_PATH
from src.config import ConfigurationError
from src.domain.entities import ChatType, MessengerType
from tests.helpers import FIXED_NOW, FIXED_NOW_TS, InMemoryModerationRepository, make_router

pytestmark = pytest.mark.asyncio

BOT_ID = "bot-user-id"


def posted_event(
    message: str,
    *,
    channel_type: str = "O",
    user_id: str = "user-1",
    root_id: str = "",
    post_type: str = "",
    props: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Событие WebSocket API «posted»: сам пост лежит внутри JSON-строкой."""
    post = {
        "id": "post-1",
        "create_at": FIXED_NOW_TS * 1000,
        "user_id": user_id,
        "channel_id": "channel-1",
        "root_id": root_id,
        "message": message,
        "type": post_type,
        "props": props or {},
    }
    return {
        "event": "posted",
        "data": {
            "channel_type": channel_type,
            "channel_name": "town-square",
            "post": json.dumps(post),
            "sender_name": "@vasya",
            "team_id": "team-1",
        },
        "broadcast": {"channel_id": "channel-1"},
        "seq": 3,
    }


class FakeMattermostServer:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.upload_status = 201

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        route = (request.method, request.url.path)
        if route == ("GET", "/api/v4/users/me"):
            return httpx.Response(200, json={"id": BOT_ID, "username": "smart-bot"})
        if route == ("POST", "/api/v4/posts"):
            return httpx.Response(201, json={"id": "new-post", **json.loads(request.content)})
        if route == ("POST", "/api/v4/files"):
            if self.upload_status != 201:
                return httpx.Response(self.upload_status, json=_error(self.upload_status))
            return httpx.Response(201, json={"file_infos": [{"id": "file-1"}], "client_ids": []})
        return httpx.Response(404, json=_error(404))

    def posts(self) -> list[dict[str, Any]]:
        return [json.loads(r.content) for r in self.requests if r.url.path == "/api/v4/posts"]

    def uploads(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path == "/api/v4/files"]


def _error(status: int) -> dict[str, Any]:
    return {"id": "api.error", "message": "error", "status_code": status, "request_id": "r1",
            "is_oauth_error": False}  # fmt: skip


def image_server(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("missing.jpg"):
        return httpx.Response(404)
    return httpx.Response(200, content=b"\xff\xd8jpeg", headers={"content-type": "image/jpeg"})


@pytest.fixture
def server() -> FakeMattermostServer:
    return FakeMattermostServer()


@pytest.fixture
def repository() -> InMemoryModerationRepository:
    return InMemoryModerationRepository()


@pytest_asyncio.fixture
async def adapter(
    server: FakeMattermostServer, repository: InMemoryModerationRepository
) -> AsyncIterator[MattermostAdapter]:
    options = driver_options(
        "https://chat.example.com", "bot-token", transport=httpx.MockTransport(server)
    )
    http = httpx.AsyncClient(transport=httpx.MockTransport(image_server))
    adapter = MattermostAdapter(
        make_router(repository=repository),
        WebhookConfig(port=0),
        driver=AsyncTypedDriver(options),
        http=http,
        webhook_token="hook-token",
    )
    await adapter.login()
    yield adapter
    await adapter.close()
    await http.aclose()


async def feed(adapter: MattermostAdapter, event: dict[str, Any]) -> None:
    await adapter.on_websocket_event(json.dumps(event))
    await adapter.wait_for_background_tasks()


# --- настройки и маппер ---


async def test_driver_options_from_url() -> None:
    assert driver_options("https://chat.example.com", "t")["port"] == 443
    options = driver_options("http://localhost:8065/", "t")
    assert (options["scheme"], options["url"], options["port"]) == ("http", "localhost", 8065)
    with pytest.raises(ConfigurationError):
        driver_options("chat.example.com", "t")
    with pytest.raises(ConfigurationError):
        driver_options("https://example.com/mattermost", "t")


async def test_mapper_channel_and_direct_messages() -> None:
    group = from_websocket_event(posted_event("бля", root_id="root-9"), bot_user_id=BOT_ID)
    direct = from_websocket_event(posted_event("!help", channel_type="D"), bot_user_id=BOT_ID)

    assert group is not None and direct is not None
    assert group.message.messenger is MessengerType.MATTERMOST
    assert (group.message.chat_type, group.message.chat_id) == (ChatType.GROUP, "channel-1")
    assert (group.message.sender.id, group.message.sender.username) == ("user-1", "vasya")
    assert group.message.timestamp == FIXED_NOW
    assert group.thread_root_id == "root-9"
    assert direct.message.chat_type is ChatType.PRIVATE


@pytest.mark.parametrize(
    "event",
    [
        posted_event("бля", user_id=BOT_ID),  # собственное сообщение бота
        posted_event("бля", post_type="system_join_channel"),
        posted_event("бля", props={"from_bot": "true"}),
        posted_event(""),
        {"event": "typing", "data": {"user_id": "user-1"}},
        {"event": "posted", "data": {"post": "{битый json"}},
    ],
)
async def test_mapper_ignores_service_events(event: dict[str, Any]) -> None:
    assert from_websocket_event(event, bot_user_id=BOT_ID) is None


# --- полный путь через WebSocket ---


async def test_login_remembers_bot_user(
    adapter: MattermostAdapter, server: FakeMattermostServer
) -> None:
    [me] = server.requests
    assert me.headers["Authorization"] == "Bearer bot-token"


async def test_profanity_in_channel_gets_warning(
    adapter: MattermostAdapter,
    server: FakeMattermostServer,
    repository: InMemoryModerationRepository,
) -> None:
    await feed(adapter, posted_event("ну бля"))

    [post] = server.posts()
    assert post == {
        "channel_id": "channel-1",
        "message": "@vasya, пожалуйста, не материтесь в чате, иначе будет бан.",
    }
    assert repository.events[0].message_id == "post-1"


async def test_reply_stays_in_thread(
    adapter: MattermostAdapter, server: FakeMattermostServer
) -> None:
    await feed(adapter, posted_event("бля", root_id="root-9"))

    assert server.posts()[0]["root_id"] == "root-9"


async def test_direct_help_suggests_exclamation_commands(
    adapter: MattermostAdapter, server: FakeMattermostServer
) -> None:
    await feed(adapter, posted_event("help", channel_type="D"))

    [post] = server.posts()
    assert "!mem" in post["message"]


async def test_own_messages_are_ignored(
    adapter: MattermostAdapter, server: FakeMattermostServer
) -> None:
    await feed(adapter, posted_event("бля", user_id=BOT_ID))

    assert server.posts() == []


async def test_mem_is_uploaded_as_file(
    adapter: MattermostAdapter, server: FakeMattermostServer
) -> None:
    await feed(adapter, posted_event("!mem", channel_type="D"))

    [upload] = server.uploads()
    assert upload.url.params["channel_id"] == "channel-1"
    assert b'filename="abc.jpg"' in upload.content
    [post] = server.posts()
    assert (post["message"], post["file_ids"]) == ("Clean code", ["file-1"])


async def test_mem_falls_back_to_link_when_upload_fails(
    adapter: MattermostAdapter, server: FakeMattermostServer
) -> None:
    server.upload_status = 413

    await feed(adapter, posted_event("!mem", channel_type="D"))

    [post] = server.posts()
    assert post["message"] == "Clean code\nhttps://i.redd.it/abc.jpg"


# --- режимы запуска и вебхук ---


async def test_run_polling_connects_websocket(
    adapter: MattermostAdapter, mocker: MockerFixture
) -> None:
    init_websocket = mocker.patch.object(adapter.driver, "init_websocket", return_value=_done())

    await adapter.run_polling()

    init_websocket.assert_called_once_with(adapter.on_websocket_event)


async def test_outgoing_webhook_checks_token(
    adapter: MattermostAdapter, server: FakeMattermostServer
) -> None:
    payload = {
        "token": "hook-token",
        "channel_id": "channel-1",
        "post_id": "post-7",
        "user_id": "user-1",
        "user_name": "vasya",
        "text": "бля",
        "timestamp": 1,
    }

    async with TestClient(TestServer(adapter.create_app())) as client:
        wrong = await client.post(WEBHOOK_PATH, json={**payload, "token": "nope"})
        ok_json = await client.post(WEBHOOK_PATH, json=payload)
        ok_form = await client.post(WEBHOOK_PATH, data={**payload, "post_id": "post-8"})
        await adapter.wait_for_background_tasks()

    assert (wrong.status, ok_json.status, ok_form.status) == (401, 200, 200)
    assert len(server.posts()) == 2


async def test_webhook_mode_requires_token(adapter: MattermostAdapter) -> None:
    adapter._webhook_token = None

    with pytest.raises(ConfigurationError, match="MATTERMOST_WEBHOOK_TOKEN"):
        adapter.create_app()


async def _done() -> None:
    return None
