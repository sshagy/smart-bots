"""Доменные сущности и value-объекты.

Модуль не зависит ни от одной библиотеки мессенджеров: адаптеры переводят
свои события в эти объекты, а ядро возвращает им ответы в виде `Reply`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class MessengerType(StrEnum):
    TELEGRAM = "telegram"
    MAX = "max"
    WHATSAPP = "whatsapp"
    MATTERMOST = "mattermost"


class ChatType(StrEnum):
    PRIVATE = "private"  # личный диалог с ботом — здесь работают команды
    GROUP = "group"  # групповой чат/канал — здесь работает модерация


@dataclass(frozen=True, slots=True)
class User:
    # Идентификаторы во всех мессенджерах храним строкой: у Telegram это int,
    # у WhatsApp — номер телефона, у Mattermost и MAX (mid) — строки.
    id: str
    username: str | None = None
    display_name: str | None = None

    @property
    def mention(self) -> str:
        """Как обратиться к пользователю в тексте ответа."""
        if self.username:
            return f"@{self.username}"
        return self.display_name or self.id


@dataclass(frozen=True, slots=True)
class IncomingMessage:
    messenger: MessengerType
    message_id: str
    chat_id: str
    chat_type: ChatType
    sender: User
    text: str
    timestamp: datetime  # всегда timezone-aware (UTC)


@dataclass(frozen=True, slots=True)
class ModerationEvent:
    message_id: str
    chat_id: str
    user_id: str
    username: str | None
    matched_keyword: str
    timestamp: datetime
    messenger_type: MessengerType
    id: int | None = None  # присваивается репозиторием


# --- Ответы ядра. Адаптер сам решает, как отрисовать их в своём мессенджере. ---


@dataclass(frozen=True, slots=True)
class TextReply:
    text: str
    reply_to_message_id: str | None = None


@dataclass(frozen=True, slots=True)
class ImageReply:
    url: str
    caption: str | None = None


Reply = TextReply | ImageReply


# --- Данные внешних сервисов ---


@dataclass(frozen=True, slots=True)
class Meme:
    title: str
    image_url: str
    post_link: str | None = None
    subreddit: str | None = None


class RateLimitKind(StrEnum):
    REQUESTS = "requests"
    TOKENS = "tokens"
    INPUT_TOKENS = "input_tokens"
    OUTPUT_TOKENS = "output_tokens"


@dataclass(frozen=True, slots=True)
class RateLimit:
    kind: RateLimitKind
    limit: int | None
    remaining: int | None
    reset_at: datetime | None  # когда лимит полностью восстановится


@dataclass(frozen=True, slots=True)
class TokenUsage:
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_creation_input_tokens
            + self.cache_read_input_tokens
        )


@dataclass(frozen=True, slots=True)
class ApiUsageReport:
    """Реальные данные о токенах и лимитах из ответа Claude API."""

    model: str
    usage: TokenUsage
    rate_limits: tuple[RateLimit, ...] = field(default_factory=tuple)
    request_id: str | None = None
    model_reply: str | None = None


# --- Статистика модерации ---


@dataclass(frozen=True, slots=True)
class UserViolationStats:
    messenger_type: MessengerType
    user_id: str
    username: str | None
    violations: int
    last_violation_at: datetime


@dataclass(frozen=True, slots=True)
class KeywordStats:
    keyword: str
    count: int
