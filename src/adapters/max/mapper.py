"""Апдейт MAX Bot API (модели maxapi-sdk) → доменный `IncomingMessage`.

В maxapi-sdk объявлены не все поля API: тип чата приходит как `recipient.chat_type`,
id сообщения — как `body.mid`. Модели SDK сохраняют такие поля (extra="allow"),
поэтому читаем их через getattr, а объявленные поля используем как запасной вариант.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from maxapi.types import Update

from src.domain.entities import ChatType, IncomingMessage, MessengerType, User

_CHAT_TYPES = {
    "dialog": ChatType.PRIVATE,
    "chat": ChatType.GROUP,
    # "channel" не модерируем: писать в канал могут только его админы.
}


def to_incoming_message(update: Update) -> IncomingMessage | None:
    update_type = getattr(update.update_type, "value", update.update_type)
    if update_type == "bot_started":
        return _from_bot_started(update)
    if update_type != "message_created" or update.message is None:
        return None

    message = update.message
    body, sender, recipient = message.body, message.sender, message.recipient
    if body is None or not body.text or sender is None or sender.is_bot or recipient is None:
        return None

    chat_type = _CHAT_TYPES.get(getattr(recipient, "chat_type", None) or recipient.type or "")
    chat_id = recipient.chat_id or message.chat_id
    message_id = getattr(body, "mid", None) or message.message_id
    if chat_type is None or chat_id is None or not message_id:
        return None

    return IncomingMessage(
        messenger=MessengerType.MAX,
        message_id=str(message_id),
        chat_id=str(chat_id),
        chat_type=chat_type,
        sender=_user(sender.model_dump()),
        text=body.text,
        timestamp=_from_millis(message.timestamp or update.timestamp),
    )


def _from_bot_started(update: Update) -> IncomingMessage | None:
    """Пользователь нажал «Начать» в диалоге с ботом — отвечаем как на /start."""
    user = getattr(update, "user", None)
    if update.chat_id is None or not isinstance(user, dict) or "user_id" not in user:
        return None
    return IncomingMessage(
        messenger=MessengerType.MAX,
        message_id=f"bot_started:{update.chat_id}:{update.timestamp}",
        chat_id=str(update.chat_id),
        chat_type=ChatType.PRIVATE,
        sender=_user(user),
        text="/start",
        timestamp=_from_millis(update.timestamp),
    )


def _user(data: dict[str, Any]) -> User:
    full_name = " ".join(part for part in (data.get("first_name"), data.get("last_name")) if part)
    return User(
        id=str(data["user_id"]),
        username=data.get("username"),
        display_name=data.get("name") or full_name or None,
    )


def _from_millis(value: int | None) -> datetime:
    # MAX передаёт время в миллисекундах Unix
    return datetime.fromtimestamp(value / 1000, UTC) if value else datetime.now(UTC)
