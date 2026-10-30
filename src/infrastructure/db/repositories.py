"""SQLite-реализации доменных репозиториев."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import aiosqlite

from src.domain.entities import KeywordStats, MessengerType, ModerationEvent, UserViolationStats
from src.domain.repositories import IModerationRepository, IStatsRepository
from src.infrastructure.db.connection import Database


def _to_db_time(value: datetime) -> str:
    if value.tzinfo is None:  # контракт домена — aware-время, но наивное считаем UTC
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat(timespec="milliseconds")


def _from_db_time(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _filters(
    messenger: MessengerType | None, chat_id: str | None, since: datetime | None
) -> tuple[str, list[Any]]:
    conditions: list[str] = []
    params: list[Any] = []
    if messenger is not None:
        conditions.append("messenger_type = ?")
        params.append(messenger.value)
    if chat_id is not None:
        conditions.append("chat_id = ?")
        params.append(chat_id)
    if since is not None:
        conditions.append("timestamp >= ?")
        params.append(_to_db_time(since))
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    return where, params


class SqliteModerationRepository(IModerationRepository):
    def __init__(self, database: Database) -> None:
        self._db = database

    async def save(self, event: ModerationEvent) -> bool:
        cursor = await self._db.connection.execute(
            """
            INSERT OR IGNORE INTO moderation_events
                (messenger_type, chat_id, message_id, user_id, username, matched_keyword, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.messenger_type.value,
                event.chat_id,
                event.message_id,
                event.user_id,
                event.username,
                event.matched_keyword,
                _to_db_time(event.timestamp),
            ),
        )
        await self._db.connection.commit()
        return cursor.rowcount == 1

    async def list_events(
        self,
        *,
        messenger: MessengerType | None = None,
        chat_id: str | None = None,
        limit: int = 100,
    ) -> list[ModerationEvent]:
        where, params = _filters(messenger, chat_id, None)
        rows = await self._fetch(
            f"SELECT * FROM moderation_events {where} ORDER BY timestamp DESC, id DESC LIMIT ?",
            [*params, limit],
        )
        return [
            ModerationEvent(
                id=row["id"],
                message_id=row["message_id"],
                chat_id=row["chat_id"],
                user_id=row["user_id"],
                username=row["username"],
                matched_keyword=row["matched_keyword"],
                timestamp=_from_db_time(row["timestamp"]),
                messenger_type=MessengerType(row["messenger_type"]),
            )
            for row in rows
        ]

    async def _fetch(self, sql: str, params: Sequence[Any]) -> list[aiosqlite.Row]:
        async with self._db.connection.execute(sql, params) as cursor:
            return list(await cursor.fetchall())


class SqliteStatsRepository(IStatsRepository):
    def __init__(self, database: Database) -> None:
        self._db = database

    async def get_stats_by_user(
        self,
        *,
        messenger: MessengerType | None = None,
        chat_id: str | None = None,
        since: datetime | None = None,
        limit: int = 10,
    ) -> list[UserViolationStats]:
        where, params = _filters(messenger, chat_id, since)
        # username — «голая» колонка: когда в запросе ровно один MAX(), SQLite берёт её
        # из строки с максимальным timestamp, то есть это последнее известное имя.
        sql = f"""
            SELECT messenger_type, user_id, username,
                   COUNT(*) AS violations, MAX(timestamp) AS last_violation_at
            FROM moderation_events {where}
            GROUP BY messenger_type, user_id
            ORDER BY violations DESC, last_violation_at DESC
            LIMIT ?
        """
        async with self._db.connection.execute(sql, [*params, limit]) as cursor:
            rows = await cursor.fetchall()
        return [
            UserViolationStats(
                messenger_type=MessengerType(row["messenger_type"]),
                user_id=row["user_id"],
                username=row["username"],
                violations=row["violations"],
                last_violation_at=_from_db_time(row["last_violation_at"]),
            )
            for row in rows
        ]

    async def get_top_keywords(
        self,
        *,
        messenger: MessengerType | None = None,
        chat_id: str | None = None,
        since: datetime | None = None,
        limit: int = 10,
    ) -> list[KeywordStats]:
        where, params = _filters(messenger, chat_id, since)
        sql = f"""
            SELECT matched_keyword, COUNT(*) AS hits
            FROM moderation_events {where}
            GROUP BY matched_keyword
            ORDER BY hits DESC, matched_keyword ASC
            LIMIT ?
        """
        async with self._db.connection.execute(sql, [*params, limit]) as cursor:
            rows = await cursor.fetchall()
        return [KeywordStats(keyword=row["matched_keyword"], count=row["hits"]) for row in rows]
