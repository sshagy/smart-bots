from __future__ import annotations

from src.domain.commands import command_prefix_for
from src.domain.entities import MessengerType, TextReply


class HelpUseCase:
    async def execute(self, messenger: MessengerType) -> TextReply:
        p = command_prefix_for(messenger)
        lines = [
            "🤖 Я бот-модератор.",
            "",
            "В групповых чатах слежу за нецензурной лексикой: предупреждаю нарушителя "
            "и записываю случай в статистику.",
            "",
            "Команды в личных сообщениях:",
            f"{p}help — эта справка",
            f"{p}check — расход токенов и остаток лимитов Claude API",
            f"{p}mem — случайный мем",
        ]
        if p != "/":
            lines += [
                "",
                "Команды со «/» здесь перехватывает клиент мессенджера, поэтому используйте "
                f"«{p}» или просто слово: {p}mem или mem.",
            ]
        return TextReply("\n".join(lines))
