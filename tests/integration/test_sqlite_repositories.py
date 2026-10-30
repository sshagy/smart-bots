from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio

from src.domain.entities import KeywordStats, MessengerType, ModerationEvent
from src.infrastructure.db.connection import SCHEMA_VERSION, Database
from src.infrastructure.db.repositories import SqliteModerationRepository, SqliteStatsRepository

pytestmark = pytest.mark.asyncio

T0 = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)


def event(
    message_id: str,
    *,
    user_id: str = "u1",
    username: str | None = "vasya",
    keyword: str = "бля",
    chat_id: str = "chat-1",
    messenger: MessengerType = MessengerType.TELEGRAM,
    at: datetime = T0,
) -> ModerationEvent:
    return ModerationEvent(
        message_id=message_id,
        chat_id=chat_id,
        user_id=user_id,
        username=username,
        matched_keyword=keyword,
        timestamp=at,
        messenger_type=messenger,
    )


@pytest_asyncio.fixture
async def database() -> AsyncIterator[Database]:
    db = await Database.connect(":memory:")
    yield db
    await db.close()


@pytest.fixture
def events(database: Database) -> SqliteModerationRepository:
    return SqliteModerationRepository(database)


@pytest.fixture
def stats(database: Database) -> SqliteStatsRepository:
    return SqliteStatsRepository(database)


async def test_save_and_read_event(events: SqliteModerationRepository) -> None:
    original = event("m1", messenger=MessengerType.MAX, chat_id="-777")

    assert await events.save(original) is True

    [stored] = await events.list_events()
    assert stored.id is not None
    assert stored == replace(original, id=stored.id)
    assert stored.messenger_type is MessengerType.MAX
    assert stored.timestamp.tzinfo is not None


async def test_duplicate_message_is_ignored(events: SqliteModerationRepository) -> None:
    assert await events.save(event("m1")) is True
    assert await events.save(event("m1", keyword="сука")) is False

    [stored] = await events.list_events()
    assert stored.matched_keyword == "бля"


async def test_same_message_id_in_other_chat_or_messenger_is_a_new_event(
    events: SqliteModerationRepository,
) -> None:
    assert await events.save(event("m1"))
    assert await events.save(event("m1", chat_id="chat-2"))
    assert await events.save(event("m1", messenger=MessengerType.WHATSAPP))

    assert len(await events.list_events()) == 3


async def test_list_events_newest_first_with_filters(events: SqliteModerationRepository) -> None:
    for minute, chat in enumerate(["a", "b", "a", "a"]):
        await events.save(event(f"m{minute}", chat_id=chat, at=T0 + timedelta(minutes=minute)))

    newest_in_a = await events.list_events(chat_id="a", limit=2)

    assert [e.message_id for e in newest_in_a] == ["m3", "m2"]
    assert await events.list_events(messenger=MessengerType.MATTERMOST) == []


async def test_naive_timestamp_is_stored_as_utc(events: SqliteModerationRepository) -> None:
    await events.save(event("m1", at=datetime(2026, 10, 30, 12, 0)))

    [stored] = await events.list_events()
    assert stored.timestamp == T0


async def test_stats_by_user(
    events: SqliteModerationRepository, stats: SqliteStatsRepository
) -> None:
    await events.save(event("1", user_id="u1", username="old_name", at=T0))
    await events.save(event("2", user_id="u1", username="new_name", at=T0 + timedelta(hours=1)))
    await events.save(event("3", user_id="u2", username="petya", at=T0 + timedelta(hours=2)))
    await events.save(event("4", user_id="u1", messenger=MessengerType.MAX, username=None))

    result = await stats.get_stats_by_user()

    assert [(s.messenger_type, s.user_id, s.violations) for s in result] == [
        (MessengerType.TELEGRAM, "u1", 2),
        (MessengerType.TELEGRAM, "u2", 1),
        (MessengerType.MAX, "u1", 1),
    ]
    top = result[0]
    assert top.username == "new_name"  # имя из самого свежего события
    assert top.last_violation_at == T0 + timedelta(hours=1)


async def test_stats_by_user_filters(
    events: SqliteModerationRepository, stats: SqliteStatsRepository
) -> None:
    await events.save(event("1", user_id="u1", chat_id="a", at=T0))
    await events.save(event("2", user_id="u2", chat_id="b", at=T0 + timedelta(days=1)))
    await events.save(event("3", user_id="u3", chat_id="b", messenger=MessengerType.MATTERMOST))

    by_chat = await stats.get_stats_by_user(chat_id="b")
    by_messenger = await stats.get_stats_by_user(messenger=MessengerType.TELEGRAM, limit=1)
    recent = await stats.get_stats_by_user(since=T0 + timedelta(hours=1))

    assert {s.user_id for s in by_chat} == {"u2", "u3"}
    assert [s.user_id for s in by_messenger] == ["u2"]  # при равенстве — свежий нарушитель выше
    assert [s.user_id for s in recent] == ["u2"]


async def test_top_keywords(
    events: SqliteModerationRepository, stats: SqliteStatsRepository
) -> None:
    for index, keyword in enumerate(["бля", "сука", "бля", "*хуй*", "сука", "бля"]):
        await events.save(event(str(index), keyword=keyword))
    await events.save(event("other", keyword="*хуй*", messenger=MessengerType.WHATSAPP))

    assert await stats.get_top_keywords(limit=2) == [
        KeywordStats("бля", 3),
        KeywordStats("*хуй*", 2),  # при равенстве — по алфавиту
    ]
    assert await stats.get_top_keywords(messenger=MessengerType.WHATSAPP) == [
        KeywordStats("*хуй*", 1)
    ]
    assert await stats.get_top_keywords(chat_id="nowhere") == []


async def test_file_database_persists_between_connections(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "stats.sqlite3"

    first = await Database.connect(path)
    await SqliteModerationRepository(first).save(event("m1"))
    await first.close()

    second = await Database.connect(path)
    try:
        assert await second.schema_version() == SCHEMA_VERSION
        assert len(await SqliteModerationRepository(second).list_events()) == 1
        async with second.connection.execute("PRAGMA journal_mode") as cursor:
            assert (await cursor.fetchone())[0] == "wal"
    finally:
        await second.close()
