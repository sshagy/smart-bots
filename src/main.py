"""Точка входа.

python -m src.main --messenger telegram            # режим из BOT_MODE (по умолчанию polling)
python -m src.main --messenger whatsapp --mode webhook
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import signal
import sys
from collections.abc import Sequence

from pydantic import ValidationError

from src.bootstrap import build_container, create_adapter, validate_settings
from src.config import BotMode, ConfigurationError, Settings
from src.domain.entities import MessengerType

logger = logging.getLogger("smart_bots")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="smart-bots",
        description="Бот-модератор с единым ядром логики для нескольких мессенджеров",
    )
    parser.add_argument(
        "--messenger", "-m", required=True, choices=[m.value for m in MessengerType]
    )
    parser.add_argument(
        "--mode", choices=[m.value for m in BotMode], help="переопределяет переменную BOT_MODE"
    )
    return parser.parse_args(argv)


def configure_logging(level: str) -> None:
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    # httpx пишет каждый запрос на INFO — для бота это шум.
    logging.getLogger("httpx").setLevel(logging.WARNING)


async def run(messenger: MessengerType, settings: Settings) -> None:
    # SIGTERM (docker stop, systemd) — так же мягко, как Ctrl+C: дождаться ответов и закрыть БД.
    task = asyncio.current_task()
    if task is not None:
        with contextlib.suppress(NotImplementedError, RuntimeError):  # на Windows сигналов нет
            asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, task.cancel)

    async with build_container(settings) as container:
        adapter = create_adapter(messenger, settings, container)
        try:
            await adapter.run(settings.bot_mode)
        except asyncio.CancelledError:
            logger.info("Получен сигнал остановки, завершаю работу")


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        settings = Settings()
    except ValidationError as exc:
        print(f"Ошибка в настройках:\n{exc}", file=sys.stderr)
        return 2
    if args.mode:
        settings = settings.model_copy(update={"bot_mode": BotMode(args.mode)})
    configure_logging(settings.log_level)

    messenger = MessengerType(args.messenger)
    try:
        validate_settings(messenger, settings)
        asyncio.run(run(messenger, settings))
    except ConfigurationError as exc:
        logger.error("%s", exc)
        return 2
    except KeyboardInterrupt:
        logger.info("Остановлено пользователем")
    except Exception:
        # Обычно это старт без сети или с неверным токеном — подробности в трейсбэке.
        logger.exception("Бот остановлен из-за ошибки")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
