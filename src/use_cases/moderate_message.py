from __future__ import annotations

import logging

from src.domain.entities import IncomingMessage, ModerationEvent, Reply, TextReply, User
from src.domain.repositories import IModerationRepository
from src.domain.services import ModerationService

logger = logging.getLogger(__name__)


class ModerateMessageUseCase:
    """Проверяет сообщение группового чата и при нарушении предупреждает автора."""

    def __init__(
        self,
        moderation: ModerationService,
        repository: IModerationRepository,
        warning_template: str,
    ) -> None:
        self._moderation = moderation
        self._repository = repository
        self._warning_template = warning_template

    async def execute(self, message: IncomingMessage) -> list[Reply]:
        violation = self._moderation.find_violation(message.text)
        if violation is None:
            return []

        event = ModerationEvent(
            message_id=message.message_id,
            chat_id=message.chat_id,
            user_id=message.sender.id,
            # В WhatsApp и MAX логина может не быть — тогда сохраняем отображаемое имя.
            username=message.sender.username or message.sender.display_name,
            matched_keyword=violation.keyword,
            timestamp=message.timestamp,
            messenger_type=message.messenger,
        )
        if not await self._repository.save(event):
            logger.info(
                "Сообщение %s/%s уже обработано — повторное предупреждение не отправляем",
                message.messenger,
                message.message_id,
            )
            return []

        logger.info(
            "Нарушение в %s чате %s: пользователь %s, шаблон %r",
            message.messenger,
            message.chat_id,
            message.sender.id,
            violation.keyword,
        )
        return [TextReply(self._render_warning(message.sender), message.message_id)]

    def _render_warning(self, user: User) -> str:
        # replace, а не format: шаблон пишет администратор, и случайные фигурные скобки
        # в нём не должны ронять бота.
        return self._warning_template.replace("{user}", user.mention)
