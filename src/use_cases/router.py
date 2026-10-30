"""Единая точка входа ядра: одно и то же поведение для всех мессенджеров."""

from __future__ import annotations

import logging

from src.domain.commands import Command, command_prefix_for, parse_command
from src.domain.entities import ChatType, IncomingMessage, Reply, TextReply
from src.use_cases.handle_check import CheckUsageUseCase
from src.use_cases.handle_help import HelpUseCase
from src.use_cases.handle_mem import GetMemeUseCase
from src.use_cases.moderate_message import ModerateMessageUseCase

logger = logging.getLogger(__name__)


class MessageRouter:
    """Групповые чаты → модерация; личные сообщения → команды."""

    def __init__(
        self,
        *,
        moderate: ModerateMessageUseCase,
        help_: HelpUseCase,
        check: CheckUsageUseCase,
        meme: GetMemeUseCase,
    ) -> None:
        self._moderate = moderate
        self._help = help_
        self._check = check
        self._meme = meme

    async def handle(self, message: IncomingMessage) -> list[Reply]:
        if not message.text.strip():
            return []
        if message.chat_type is ChatType.GROUP:
            try:
                return await self._moderate.execute(message)
            except Exception:
                # В группу ничего не пишем, чтобы не шуметь, — только в лог.
                logger.exception("Ошибка модерации сообщения %s", message.message_id)
                return []
        try:
            return await self._handle_private(message)
        except Exception:
            logger.exception("Ошибка обработки команды %r", message.text)
            return [TextReply("Что-то пошло не так. Попробуйте ещё раз чуть позже.")]

    async def _handle_private(self, message: IncomingMessage) -> list[Reply]:
        prefix = command_prefix_for(message.messenger)
        parsed = parse_command(message.text)
        if parsed is None:
            return [TextReply(f"Я понимаю только команды. Список команд: {prefix}help")]

        match parsed.command:
            case Command.START | Command.HELP:
                return [await self._help.execute(message.messenger)]
            case Command.CHECK:
                return [await self._check.execute()]
            case Command.MEM:
                return await self._meme.execute()
            case None:
                return [
                    TextReply(f"Неизвестная команда «{parsed.name}». Список команд: {prefix}help")
                ]
