"""События Mattermost (WebSocket и исходящий вебхук) → доменные `IncomingMessage`."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from src.domain.entities import ChatType, IncomingMessage, MessengerType, User

_CHANNEL_TYPES = {
    "D": ChatType.PRIVATE,  # личный диалог с ботом
    "O": ChatType.GROUP,  # открытый канал
    "P": ChatType.GROUP,  # приватный канал
    "G": ChatType.GROUP,  # групповой диалог
}


@dataclass(frozen=True, slots=True)
class MattermostInbound:
    message: IncomingMessage
    thread_root_id: str | None  # если сообщение в треде — отвечаем в тот же тред


def from_websocket_event(
    event: dict[str, Any], *, bot_user_id: str | None
) -> MattermostInbound | None:
    if event.get("event") != "posted":
        return None
    data = event.get("data") or {}
    try:
        post = json.loads(data.get("post") or "")  # Mattermost кладёт пост JSON-строкой
    except ValueError:
        return None
    if not isinstance(post, dict) or post.get("user_id") == bot_user_id or _is_automated(post):
        return None

    chat_type = _CHANNEL_TYPES.get(data.get("channel_type", ""))
    text = post.get("message")
    if chat_type is None or not text or not post.get("id") or not post.get("channel_id"):
        return None

    username = (data.get("sender_name") or "").lstrip("@") or None
    return MattermostInbound(
        message=IncomingMessage(
            messenger=MessengerType.MATTERMOST,
            message_id=post["id"],
            chat_id=post["channel_id"],
            chat_type=chat_type,
            sender=User(id=post["user_id"], username=username),
            text=text,
            timestamp=_from_millis(post.get("create_at")),
        ),
        thread_root_id=post.get("root_id") or None,
    )


def from_outgoing_webhook(
    payload: dict[str, Any], *, bot_user_id: str | None
) -> MattermostInbound | None:
    """Исходящие вебхуки Mattermost срабатывают только в публичных каналах."""
    post_id, channel_id = payload.get("post_id"), payload.get("channel_id")
    user_id, text = payload.get("user_id"), payload.get("text")
    if not (post_id and channel_id and user_id and text) or user_id == bot_user_id:
        return None
    return MattermostInbound(
        message=IncomingMessage(
            messenger=MessengerType.MATTERMOST,
            message_id=str(post_id),
            chat_id=str(channel_id),
            chat_type=ChatType.GROUP,
            sender=User(id=str(user_id), username=payload.get("user_name") or None),
            text=str(text),
            timestamp=_from_millis(_to_int(payload.get("timestamp"))),
        ),
        thread_root_id=None,
    )


def _is_automated(post: dict[str, Any]) -> bool:
    if post.get("type"):  # системные сообщения: вход в канал, смена заголовка и т.п.
        return True
    props = post.get("props") or {}
    return props.get("from_bot") == "true" or props.get("from_webhook") == "true"


def _to_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _from_millis(value: int | None) -> datetime:
    return datetime.fromtimestamp(value / 1000, UTC) if value else datetime.now(UTC)
