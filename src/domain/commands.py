"""Распознавание команд в личных сообщениях."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from src.domain.entities import MessengerType


class Command(StrEnum):
    START = "start"
    HELP = "help"
    CHECK = "check"
    MEM = "mem"


# «/» — стандарт для Telegram и MAX. «!» и команда без префикса нужны там, где клиент
# сам перехватывает «/…» (Mattermost открывает свои slash-команды, а /help встроена).
COMMAND_PREFIXES = ("/", "!")
_PREFERRED_PREFIX = {MessengerType.MATTERMOST: "!"}
_COMMAND_NAME = re.compile(r"\w+")


def command_prefix_for(messenger: MessengerType) -> str:
    """Каким префиксом подсказывать команды пользователю этого мессенджера."""
    return _PREFERRED_PREFIX.get(messenger, "/")


@dataclass(frozen=True, slots=True)
class ParsedCommand:
    name: str
    args: str = ""

    @property
    def command(self) -> Command | None:
        try:
            return Command(self.name)
        except ValueError:
            return None


def parse_command(text: str) -> ParsedCommand | None:
    """«/help», «/help@MyBot», «!mem», «mem» → команда; обычный текст → None."""
    parts = text.strip().split(maxsplit=1)
    if not parts:
        return None
    head = parts[0]
    args = parts[1].strip() if len(parts) > 1 else ""

    if head.startswith(COMMAND_PREFIXES):
        # В группах Telegram добавляет имя бота: /help@SmartModeratorBot
        name = head[1:].split("@", 1)[0].casefold()
        return ParsedCommand(name, args) if _COMMAND_NAME.fullmatch(name) else None

    name = head.casefold()
    if not args and name in Command.__members__.values():
        return ParsedCommand(name)
    return None
