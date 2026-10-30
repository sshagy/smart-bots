import pytest

from src.domain.entities import MessengerType
from src.use_cases.handle_help import HelpUseCase

pytestmark = pytest.mark.asyncio


async def test_help_lists_all_commands() -> None:
    reply = await HelpUseCase().execute(MessengerType.TELEGRAM)

    for command in ("/help", "/check", "/mem"):
        assert command in reply.text
    assert "перехватывает" not in reply.text


async def test_help_for_mattermost_suggests_exclamation_prefix() -> None:
    reply = await HelpUseCase().execute(MessengerType.MATTERMOST)

    for command in ("!help", "!check", "!mem"):
        assert command in reply.text
    assert "перехватывает клиент" in reply.text
