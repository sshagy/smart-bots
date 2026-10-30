import pytest

from src.domain.commands import Command, ParsedCommand, command_prefix_for, parse_command
from src.domain.entities import MessengerType

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/help", ParsedCommand("help")),
        ("  /START  ", ParsedCommand("start")),
        ("/help@SmartModeratorBot", ParsedCommand("help")),
        ("/check сейчас", ParsedCommand("check", "сейчас")),
        ("!mem", ParsedCommand("mem")),
        ("mem", ParsedCommand("mem")),
        ("Help", ParsedCommand("help")),
        ("/unknown", ParsedCommand("unknown")),
    ],
)
async def test_parse_command(text: str, expected: ParsedCommand) -> None:
    assert parse_command(text) == expected


@pytest.mark.parametrize("text", ["", "   ", "/", "!!!", "привет", "mem пожалуйста", "/ help"])
async def test_not_a_command(text: str) -> None:
    assert parse_command(text) is None


async def test_known_and_unknown_command_names() -> None:
    assert ParsedCommand("mem").command is Command.MEM
    assert ParsedCommand("unknown").command is None


async def test_command_prefix_depends_on_messenger() -> None:
    assert command_prefix_for(MessengerType.TELEGRAM) == "/"
    assert command_prefix_for(MessengerType.MAX) == "/"
    assert command_prefix_for(MessengerType.MATTERMOST) == "!"
