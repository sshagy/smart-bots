"""Подключение к SQLite и миграции схемы."""

from __future__ import annotations

from pathlib import Path

import aiosqlite

# Каждая миграция выполняется в своей транзакции вместе с повышением PRAGMA user_version,
# поэтому прерванный запуск не оставит схему «наполовину».
_MIGRATIONS: dict[int, str] = {
    1: """
        CREATE TABLE moderation_events (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            messenger_type  TEXT    NOT NULL,
            chat_id         TEXT    NOT NULL,
            message_id      TEXT    NOT NULL,
            user_id         TEXT    NOT NULL,
            username        TEXT,
            matched_keyword TEXT    NOT NULL,
            timestamp       TEXT    NOT NULL,  -- ISO-8601 в UTC: сортируется как строка
            -- одно сообщение = одно событие: повторная доставка апдейта не создаёт дубль
            UNIQUE (messenger_type, chat_id, message_id)
        );
        CREATE INDEX ix_moderation_events_user ON moderation_events (messenger_type, user_id);
        CREATE INDEX ix_moderation_events_keyword ON moderation_events (matched_keyword);
        CREATE INDEX ix_moderation_events_timestamp ON moderation_events (timestamp);
    """,
}
SCHEMA_VERSION = max(_MIGRATIONS)


class Database:
    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._connection = connection

    @classmethod
    async def connect(cls, path: str | Path) -> Database:
        """Открывает базу (":memory:" — для тестов) и применяет недостающие миграции."""
        in_memory = str(path) == ":memory:"
        if not in_memory:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        connection = await aiosqlite.connect(path)
        connection.row_factory = aiosqlite.Row
        if not in_memory:
            # WAL: чтение статистики не блокирует запись новых событий.
            await connection.execute("PRAGMA journal_mode = WAL")
        database = cls(connection)
        await database.migrate()
        return database

    @property
    def connection(self) -> aiosqlite.Connection:
        return self._connection

    async def schema_version(self) -> int:
        async with self._connection.execute("PRAGMA user_version") as cursor:
            row = await cursor.fetchone()
        return int(row[0]) if row else 0

    async def migrate(self) -> None:
        current = await self.schema_version()
        for version in sorted(v for v in _MIGRATIONS if v > current):
            await self._connection.executescript(
                f"BEGIN;\n{_MIGRATIONS[version]}\nPRAGMA user_version = {version};\nCOMMIT;"
            )

    async def close(self) -> None:
        await self._connection.close()
