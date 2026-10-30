"""Точка расширения: приём апдейтов Telegram через вебхук.

Сейчас бот по умолчанию работает через long polling (BOT_MODE=polling) — белый IP
не нужен. Когда появится публичный адрес с HTTPS (Telegram принимает порты 443, 80,
88 и 8443; удобнее всего — обратный прокси nginx/Caddy перед WEBHOOK_PORT):

  1. WEBHOOK_BASE_URL=https://bot.example.com — по этому адресу Telegram найдёт бота;
  2. WEBHOOK_SECRET=<случайная строка из A-Z, a-z, 0-9, _ и -> — Telegram присылает
     её в заголовке X-Telegram-Bot-Api-Secret-Token, чужие запросы получат 401;
  3. BOT_MODE=webhook — при старте адаптер сам вызовет setWebhook
     на {WEBHOOK_BASE_URL}/webhook/telegram.

Вернуться к polling можно в любой момент: адаптер удаляет вебхук перед getUpdates.
"""

from __future__ import annotations

from aiogram import Bot, Dispatcher
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

from src.adapters.web import new_web_app

WEBHOOK_PATH = "/webhook/telegram"


def webhook_handler(dispatcher: Dispatcher, bot: Bot, secret: str | None) -> SimpleRequestHandler:
    """Обработчик POST-запросов Telegram: проверяет секрет и отдаёт апдейт в Dispatcher.

    Апдейт обрабатывается в фоне (handle_in_background), Telegram сразу получает 200.
    """
    return SimpleRequestHandler(dispatcher=dispatcher, bot=bot, secret_token=secret)


def create_webhook_app(dispatcher: Dispatcher, bot: Bot, secret: str | None) -> web.Application:
    app = new_web_app()
    webhook_handler(dispatcher, bot, secret).register(app, path=WEBHOOK_PATH)
    setup_application(app, dispatcher, bot=bot)
    return app
