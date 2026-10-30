"""aiogram `Message` → доменный `IncomingMessage`."""

from __future__ import annotations

from aiogram.types import Message

from src.domain.entities import ChatType, IncomingMessage, MessengerType, User

_CHAT_TYPES = {
    "private": ChatType.PRIVATE,
    "group": ChatType.GROUP,
    "supergroup": ChatType.GROUP,
    # "channel" не модерируем: писать в канал могут только его админы.
}


def to_incoming_message(message: Message) -> IncomingMessage | None:
    chat_type = _CHAT_TYPES.get(message.chat.type)
    text = message.text or message.caption  # подпись к фото тоже может содержать мат
    if chat_type is None or not text:
        return None

    if message.from_user is not None:
        if message.from_user.is_bot:
            return None
        sender = User(
            id=str(message.from_user.id),
            username=message.from_user.username,
            display_name=message.from_user.full_name,
        )
    elif message.sender_chat is not None:  # анонимный админ или пост от имени канала
        sender = User(
            id=str(message.sender_chat.id),
            username=message.sender_chat.username,
            display_name=message.sender_chat.title,
        )
    else:
        return None

    return IncomingMessage(
        messenger=MessengerType.TELEGRAM,
        message_id=str(message.message_id),
        chat_id=str(message.chat.id),
        chat_type=chat_type,
        sender=sender,
        text=text,
        timestamp=message.date,
    )
