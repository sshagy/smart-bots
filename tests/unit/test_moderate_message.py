import pytest

from src.domain.entities import MessengerType, ModerationEvent, TextReply, User
from src.domain.services import ModerationService
from src.use_cases.moderate_message import ModerateMessageUseCase
from tests.helpers import FIXED_NOW, InMemoryModerationRepository, make_message

pytestmark = pytest.mark.asyncio

WARNING = "{user}, пожалуйста, не материтесь в чате, иначе будет бан."


@pytest.fixture
def repository() -> InMemoryModerationRepository:
    return InMemoryModerationRepository()


@pytest.fixture
def use_case(repository: InMemoryModerationRepository) -> ModerateMessageUseCase:
    return ModerateMessageUseCase(ModerationService(["бля*"]), repository, WARNING)


async def test_violation_sends_warning_and_saves_event(
    use_case: ModerateMessageUseCase, repository: InMemoryModerationRepository
) -> None:
    message = make_message("Ну блин... нет, бля!", message_id="555", chat_id="-100777")

    replies = await use_case.execute(message)

    assert replies == [
        TextReply("@vasya, пожалуйста, не материтесь в чате, иначе будет бан.", "555")
    ]
    assert repository.events == [
        ModerationEvent(
            message_id="555",
            chat_id="-100777",
            user_id="42",
            username="vasya",
            matched_keyword="бля*",
            timestamp=FIXED_NOW,
            messenger_type=MessengerType.TELEGRAM,
            id=1,
        )
    ]


async def test_clean_message_is_ignored(
    use_case: ModerateMessageUseCase, repository: InMemoryModerationRepository
) -> None:
    assert await use_case.execute(make_message("Всем доброе утро!")) == []
    assert repository.events == []


async def test_redelivered_message_is_not_warned_twice(
    use_case: ModerateMessageUseCase, repository: InMemoryModerationRepository
) -> None:
    message = make_message("бля")

    first = await use_case.execute(message)
    second = await use_case.execute(message)

    assert len(first) == 1
    assert second == []
    assert len(repository.events) == 1


async def test_display_name_is_used_when_there_is_no_username(
    use_case: ModerateMessageUseCase, repository: InMemoryModerationRepository
) -> None:
    sender = User(id="79990001122", display_name="Иван")
    message = make_message("бля", messenger=MessengerType.WHATSAPP, user=sender)

    [reply] = await use_case.execute(message)

    assert reply.text.startswith("Иван, ")
    assert repository.events[0].username == "Иван"


async def test_warning_template_with_foreign_braces_does_not_crash(
    repository: InMemoryModerationRepository,
) -> None:
    use_case = ModerateMessageUseCase(ModerationService(["бля"]), repository, "{user}: {ой} мат")

    [reply] = await use_case.execute(make_message("бля"))

    assert reply.text == "@vasya: {ой} мат"
