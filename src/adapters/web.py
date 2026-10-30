"""Минимальный aiohttp-сервер для вебхуков всех мессенджеров."""

from __future__ import annotations

import asyncio
import hmac
import logging

from aiohttp import web

logger = logging.getLogger(__name__)


def new_web_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/healthz", _healthz)  # для балансировщика / мониторинга
    return app


async def serve(app: web.Application, host: str, port: int) -> None:
    """Работает, пока задачу не отменят (Ctrl+C / SIGTERM)."""
    # access_log=None: в строке запроса бывают секреты (hub.verify_token у WhatsApp).
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.info("Webhook-сервер слушает http://%s:%s", host, port)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


def secrets_equal(received: str | None, expected: str) -> bool:
    """Сравнение секретов за постоянное время (защита от timing-атак)."""
    return received is not None and hmac.compare_digest(received.encode(), expected.encode())


async def _healthz(request: web.Request) -> web.Response:
    return web.Response(text="ok")
