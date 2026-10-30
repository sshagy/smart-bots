from types import SimpleNamespace

import pytest
from pytest_mock import MockerFixture

from src.domain.entities import ChatType, ImageReply, MessengerType, TextReply
from src.use_cases.handle_check import CheckUsageUseCase
from src.use_cases.handle_help import HelpUseCase
from src.use_cases.handle_mem import GetMemeUseCase
from src.use_cases.moderate_message import ModerateMessageUseCase
from src.use_cases.router import MessageRouter
from tests.helpers import make_message

pytestmark = pytest.mark.asyncio

HELP = TextReply("справка")
CHECK = TextReply("лимиты")
MEME = [ImageReply("https://i.redd.it/x.png", "мем")]
WARNING = [TextReply("не материтесь", "100")]


@pytest.fixture
def deps(mocker: MockerFixture) -> SimpleNamespace:
    moderate = mocker.AsyncMock(spec=ModerateMessageUseCase)
    moderate.execute.return_value = WARNING
    help_ = mocker.AsyncMock(spec=HelpUseCase)
    help_.execute.return_value = HELP
    check = mocker.AsyncMock(spec=CheckUsageUseCase)
    check.execute.return_value = CHECK
    meme = mocker.AsyncMock(spec=GetMemeUseCase)
    meme.execute.return_value = MEME
    return SimpleNamespace(moderate=moderate, help_=help_, check=check, meme=meme)


@pytest.fixture
def router(deps: SimpleNamespace) -> MessageRouter:
    return MessageRouter(moderate=deps.moderate, help_=deps.help_, check=deps.check, meme=deps.meme)


def private(text: str, messenger: MessengerType = MessengerType.TELEGRAM):
    return make_message(text, chat_type=ChatType.PRIVATE, messenger=messenger)


async def test_group_messages_go_to_moderation(
    router: MessageRouter, deps: SimpleNamespace
) -> None:
    message = make_message("/help", chat_type=ChatType.GROUP)

    assert await router.handle(message) == WARNING

    deps.moderate.execute.assert_awaited_once_with(message)
    deps.help_.execute.assert_not_awaited()


@pytest.mark.parametrize("text", ["/help", "/start", "/help@SmartModeratorBot", "help"])
async def test_help_command(router: MessageRouter, deps: SimpleNamespace, text: str) -> None:
    assert await router.handle(private(text)) == [HELP]
    deps.help_.execute.assert_awaited_once_with(MessengerType.TELEGRAM)
    deps.moderate.execute.assert_not_awaited()


@pytest.mark.parametrize("text", ["/check", "!check", "check"])
async def test_check_command(router: MessageRouter, deps: SimpleNamespace, text: str) -> None:
    assert await router.handle(private(text)) == [CHECK]
    deps.check.execute.assert_awaited_once_with()


@pytest.mark.parametrize("text", ["/mem", "!mem", "MEM"])
async def test_mem_command(router: MessageRouter, deps: SimpleNamespace, text: str) -> None:
    assert await router.handle(private(text)) == MEME
    deps.meme.execute.assert_awaited_once_with()


async def test_unknown_command(router: MessageRouter) -> None:
    replies = await router.handle(private("/ban"))

    assert replies == [TextReply("Неизвестная команда «ban». Список команд: /help")]


async def test_plain_text_in_private_chat(router: MessageRouter, deps: SimpleNamespace) -> None:
    replies = await router.handle(private("привет, бот"))

    assert replies == [TextReply("Я понимаю только команды. Список команд: /help")]
    deps.moderate.execute.assert_not_awaited()


async def test_hints_use_messenger_specific_prefix(router: MessageRouter) -> None:
    replies = await router.handle(private("привет", messenger=MessengerType.MATTERMOST))

    assert replies == [TextReply("Я понимаю только команды. Список команд: !help")]


@pytest.mark.parametrize("chat_type", list(ChatType))
async def test_empty_text_is_ignored(
    router: MessageRouter, deps: SimpleNamespace, chat_type: ChatType
) -> None:
    assert await router.handle(make_message("   ", chat_type=chat_type)) == []
    deps.moderate.execute.assert_not_awaited()


async def test_command_failure_returns_apology(
    router: MessageRouter, deps: SimpleNamespace
) -> None:
    deps.check.execute.side_effect = RuntimeError("boom")

    replies = await router.handle(private("/check"))

    assert replies == [TextReply("Что-то пошло не так. Попробуйте ещё раз чуть позже.")]


async def test_moderation_failure_is_silent_in_group(
    router: MessageRouter, deps: SimpleNamespace
) -> None:
    deps.moderate.execute.side_effect = RuntimeError("database is locked")

    assert await router.handle(make_message("бля")) == []
