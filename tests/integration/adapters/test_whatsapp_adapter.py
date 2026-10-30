"""WhatsApp-адаптер: настоящий aiohttp-вебхук и kapso-SDK, HTTP к Graph API подменён."""

import asyncio
import hashlib
import hmac
import json
import logging
from typing import Any

import pytest
from aiohttp.test_utils import TestClient, TestServer
from kapso_whatsapp import WhatsAppClient
from kapso_whatsapp.webhooks import normalize_webhook
from pytest_mock import MockerFixture

from src.adapters.base import WebhookConfig
from src.adapters.whatsapp import WhatsAppAdapter
from src.adapters.whatsapp.mapper import to_incoming_messages
from src.adapters.whatsapp.webhook import SIGNATURE_HEADER, WEBHOOK_PATH
from src.domain.entities import ChatType, MessengerType
from tests.helpers import (
    FIXED_NOW,
    FIXED_NOW_TS,
    InMemoryModerationRepository,
    make_router,
    wait_until,
)

pytestmark = pytest.mark.asyncio

APP_SECRET = "meta-app-secret"
VERIFY_TOKEN = "verify-me"
IVAN = "79990001122"


def text_message(
    body: str, *, message_id: str = "wamid.IN1", group_id: str | None = None, **extra: Any
) -> dict[str, Any]:
    message = {
        "from": IVAN,
        "id": message_id,
        "timestamp": str(FIXED_NOW_TS),
        "type": "text",
        "text": {"body": body},
        **extra,
    }
    if group_id:
        message["group_id"] = group_id
    return message


def webhook_payload(
    *messages: dict[str, Any],
    statuses: list[dict[str, Any]] | None = None,
    echoes: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Тело вебхука в формате WhatsApp Cloud API (поле messages)."""
    value: dict[str, Any] = {
        "messaging_product": "whatsapp",
        "metadata": {"display_phone_number": "15550001111", "phone_number_id": "PHONE_ID"},
    }
    if messages:
        value["contacts"] = [{"profile": {"name": "Иван"}, "wa_id": IVAN}]
        value["messages"] = list(messages)
    if statuses:
        value["statuses"] = statuses
    if echoes:
        value["message_echoes"] = echoes
    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": "WABA_ID", "changes": [{"field": "messages", "value": value}]}],
    }


def signed(payload: dict[str, Any], secret: str = APP_SECRET) -> tuple[bytes, dict[str, str]]:
    body = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return body, {SIGNATURE_HEADER: signature, "Content-Type": "application/json"}


class FakeGraphApi:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append({"method": method, "path": path, **kwargs})
        return {"messaging_product": "whatsapp", "messages": [{"id": "wamid.OUT"}]}


@pytest.fixture
def graph() -> FakeGraphApi:
    return FakeGraphApi()


@pytest.fixture
def repository() -> InMemoryModerationRepository:
    return InMemoryModerationRepository()


@pytest.fixture
def adapter(
    graph: FakeGraphApi, repository: InMemoryModerationRepository, mocker: MockerFixture
) -> WhatsAppAdapter:
    client = WhatsAppClient(access_token="test-token")
    mocker.patch.object(client, "request", side_effect=graph.request)
    return WhatsAppAdapter(
        make_router(repository=repository),
        WebhookConfig(port=0),
        client=client,
        phone_number_id="DEFAULT_PHONE_ID",
        verify_token=VERIFY_TOKEN,
        app_secret=APP_SECRET,
    )


async def post_signed(adapter: WhatsAppAdapter, payload: dict[str, Any]) -> int:
    body, headers = signed(payload)
    async with TestClient(TestServer(adapter.create_app())) as client:
        response = await client.post(WEBHOOK_PATH, data=body, headers=headers)
    await adapter.wait_for_background_tasks()
    return response.status


# --- маппер ---


async def test_mapper_private_and_group_messages() -> None:
    result = normalize_webhook(
        webhook_payload(
            text_message("/help"), text_message("бля", message_id="wamid.G", group_id="G1")
        )
    )

    private, group = to_incoming_messages(result)

    assert private.phone_number_id == "PHONE_ID"
    assert private.message.messenger is MessengerType.WHATSAPP
    assert (private.message.chat_type, private.message.chat_id) == (ChatType.PRIVATE, IVAN)
    assert (private.message.sender.id, private.message.sender.display_name) == (IVAN, "Иван")
    assert private.message.timestamp == FIXED_NOW
    assert (group.message.chat_type, group.message.chat_id) == (ChatType.GROUP, "G1")
    assert group.message.sender.id == IVAN  # автор — участник группы


async def test_mapper_reads_media_captions_and_skips_other_types() -> None:
    image = {
        "from": IVAN,
        "id": "wamid.I",
        "timestamp": "1",
        "type": "image",
        "image": {"caption": "/mem"},
    }
    sticker = {"from": IVAN, "id": "wamid.S", "timestamp": "1", "type": "sticker", "sticker": {}}

    [inbound] = to_incoming_messages(normalize_webhook(webhook_payload(image, sticker)))

    assert inbound.message.text == "/mem"


async def test_mapper_ignores_outbound_echoes() -> None:
    echo = {"from": "15550001111", "to": IVAN, "id": "wamid.E", "timestamp": "1", "type": "text",
            "text": {"body": "бля"}}  # fmt: skip

    assert to_incoming_messages(normalize_webhook(webhook_payload(echoes=[echo]))) == []


# --- вебхук ---


async def test_subscription_verification_handshake(adapter: WhatsAppAdapter) -> None:
    async with TestClient(TestServer(adapter.create_app())) as client:
        ok = await client.get(
            WEBHOOK_PATH,
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": VERIFY_TOKEN,
                "hub.challenge": "42",
            },
        )
        wrong_token = await client.get(
            WEBHOOK_PATH,
            params={"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "42"},
        )
        challenge = await ok.text()

    assert (ok.status, challenge) == (200, "42")
    assert wrong_token.status == 403


async def test_requests_without_valid_signature_are_rejected(
    adapter: WhatsAppAdapter, graph: FakeGraphApi
) -> None:
    body, headers = signed(webhook_payload(text_message("/help")), secret="attacker")
    async with TestClient(TestServer(adapter.create_app())) as client:
        unsigned = await client.post(WEBHOOK_PATH, data=body)
        forged = await client.post(WEBHOOK_PATH, data=body, headers=headers)

    assert (unsigned.status, forged.status) == (401, 401)
    assert graph.calls == []


async def test_signed_invalid_json_is_bad_request(adapter: WhatsAppAdapter) -> None:
    body = b"{not json"
    signature = "sha256=" + hmac.new(APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
    async with TestClient(TestServer(adapter.create_app())) as client:
        response = await client.post(WEBHOOK_PATH, data=body, headers={SIGNATURE_HEADER: signature})

    assert response.status == 400


async def test_unparsable_payload_is_acknowledged_to_avoid_retries(
    adapter: WhatsAppAdapter, graph: FakeGraphApi, caplog: pytest.LogCaptureFixture
) -> None:
    broken = text_message("бля")
    broken["text"] = "строка вместо объекта"  # структура, которую Meta не присылает

    with caplog.at_level(logging.ERROR):
        assert await post_signed(adapter, webhook_payload(broken)) == 200

    assert graph.calls == []
    assert "Не удалось разобрать вебхук WhatsApp" in caplog.text


# --- ответы ---


async def test_help_in_private_chat_uses_typed_sdk_call(
    adapter: WhatsAppAdapter, graph: FakeGraphApi
) -> None:
    assert await post_signed(adapter, webhook_payload(text_message("/help"))) == 200

    [call] = graph.calls
    assert (call["method"], call["path"]) == ("POST", "PHONE_ID/messages")
    assert call["json"]["recipient_type"] == "individual"
    assert call["json"]["to"] == IVAN
    assert "/check" in call["json"]["text"]["body"]


async def test_profanity_in_group_is_answered_in_group(
    adapter: WhatsAppAdapter, graph: FakeGraphApi, repository: InMemoryModerationRepository
) -> None:
    await post_signed(adapter, webhook_payload(text_message("ну бля", group_id="GROUP123")))

    [call] = graph.calls
    assert call["json"] == {
        "messaging_product": "whatsapp",
        "recipient_type": "group",
        "to": "GROUP123",
        "type": "text",
        "text": {"body": "Иван, пожалуйста, не материтесь в чате, иначе будет бан."},
        "context": {"message_id": "wamid.IN1"},
    }
    [event] = repository.events
    assert (event.chat_id, event.user_id, event.username) == ("GROUP123", IVAN, "Иван")


async def test_mem_is_sent_as_image_link(adapter: WhatsAppAdapter, graph: FakeGraphApi) -> None:
    await post_signed(adapter, webhook_payload(text_message("/mem")))

    [call] = graph.calls
    assert call["json"]["type"] == "image"
    assert call["json"]["image"] == {"link": "https://i.redd.it/abc.jpg", "caption": "Clean code"}


async def test_redelivered_webhook_does_not_warn_twice(
    adapter: WhatsAppAdapter, graph: FakeGraphApi
) -> None:
    payload = webhook_payload(text_message("бля", group_id="GROUP123"))

    await post_signed(adapter, payload)
    await post_signed(adapter, payload)  # Meta повторила доставку

    assert len(graph.calls) == 1


async def test_failed_delivery_statuses_are_logged(
    adapter: WhatsAppAdapter, graph: FakeGraphApi, caplog: pytest.LogCaptureFixture
) -> None:
    failed = {"id": "wamid.OUT", "status": "failed", "recipient_id": IVAN,
              "errors": [{"code": 131053, "title": "Media upload error"}]}  # fmt: skip

    with caplog.at_level(logging.WARNING):
        assert await post_signed(adapter, webhook_payload(statuses=[failed])) == 200

    assert graph.calls == []
    assert "Media upload error" in caplog.text


async def test_polling_mode_starts_webhook_server_with_warning(
    adapter: WhatsAppAdapter, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING):
        task = asyncio.create_task(adapter.run_polling())
        try:
            await wait_until(lambda: "не поддерживает long polling" in caplog.text)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
