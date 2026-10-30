"""Вебхук WhatsApp Cloud API → доменные `IncomingMessage`.

Payload сначала нормализуется kapso-SDK (`normalize_webhook`: ключи в camelCase,
направление сообщения, плоские списки сообщений/контактов/статусов).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from kapso_whatsapp.webhooks import NormalizedWebhookResult

from src.domain.entities import ChatType, IncomingMessage, MessengerType, User

logger = logging.getLogger(__name__)

# Типы сообщений, в которых бывает текст: у медиа — подпись (caption).
_CAPTIONED_TYPES = ("image", "video", "document")


@dataclass(frozen=True, slots=True)
class WhatsAppInbound:
    message: IncomingMessage
    phone_number_id: str | None  # номер бизнеса, на который пришло сообщение — с него и отвечаем


def to_incoming_messages(result: NormalizedWebhookResult) -> list[WhatsAppInbound]:
    names = {
        contact.get("waId"): (contact.get("profile") or {}).get("name")
        for contact in result.contacts
    }
    inbound: list[WhatsAppInbound] = []
    for raw in result.messages:
        if (raw.get("kapso") or {}).get("direction") == "outbound":
            continue  # эхо сообщений, отправленных самим бизнесом
        sender_id = raw.get("from") or raw.get("businessScopedUserId")
        text = _text_of(raw)
        if not raw.get("id") or not sender_id or not text:
            continue
        # Groups API: у группового сообщения есть group_id, from — участник группы.
        group_id = raw.get("groupId")
        inbound.append(
            WhatsAppInbound(
                message=IncomingMessage(
                    messenger=MessengerType.WHATSAPP,
                    message_id=raw["id"],
                    chat_id=group_id or sender_id,
                    chat_type=ChatType.GROUP if group_id else ChatType.PRIVATE,
                    sender=User(id=sender_id, display_name=names.get(sender_id)),
                    text=text,
                    timestamp=_from_unix(raw.get("timestamp")),
                ),
                phone_number_id=result.phone_number_id,
            )
        )
    return inbound


def log_failed_deliveries(result: NormalizedWebhookResult) -> None:
    """Ошибки доставки WhatsApp сообщает асинхронно — статусом failed в следующем вебхуке."""
    for status in result.statuses:
        if status.status == "failed":
            logger.warning(
                "WhatsApp не доставил сообщение %s получателю %s: %s",
                status.id,
                status.recipient_id,
                status.errors,
            )


def _text_of(raw: dict[str, Any]) -> str | None:
    message_type = raw.get("type")
    if message_type == "text":
        return (raw.get("text") or {}).get("body")
    if message_type in _CAPTIONED_TYPES:
        return (raw.get(message_type) or {}).get("caption")
    return None


def _from_unix(value: Any) -> datetime:
    try:
        return datetime.fromtimestamp(int(value), UTC)
    except (TypeError, ValueError):
        return datetime.now(UTC)
