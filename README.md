# smart-bots

Бот-модератор для **Telegram, MAX, WhatsApp и Mattermost** с единым ядром бизнес-логики.
Python 3.11+, полностью асинхронный, чистая архитектура, статистика в SQLite, тесты на pytest.

- **Групповые чаты** — бот ищет в сообщениях ключевые слова (по умолчанию — мат), отвечает
  на нарушение предупреждением «@vasya, пожалуйста, не материтесь в чате, иначе будет бан.» и
  записывает событие в SQLite.
- **Личные сообщения** — команды:
  - `/help` — справка;
  - `/check` — реальный расход токенов и остаток лимитов Claude API (из ответа API);
  - `/mem` — случайный мем картинкой с <https://meme-api.com/gimme>.
- **Режимы** — `BOT_MODE=polling` (по умолчанию, белый IP не нужен) и `BOT_MODE=webhook`.

Логика одна для всех мессенджеров: адаптер переводит событие мессенджера в доменный
`IncomingMessage`, ядро (`MessageRouter`) решает, что ответить, и возвращает список `Reply`,
а адаптер отрисовывает ответ средствами своего SDK.

---

## Содержание

1. [Архитектура](#архитектура)
2. [Дерево проекта](#дерево-проекта)
3. [Выбор библиотек](#выбор-библиотек)
4. [Установка](#установка)
5. [Получение токенов](#получение-токенов)
6. [Настройка (.env)](#настройка-env)
7. [Запуск](#запуск)
8. [Переход на вебхуки](#переход-на-вебхуки)
9. [Модерация и статистика](#модерация-и-статистика)
10. [Команда /check](#команда-check)
11. [Тесты](#тесты)
12. [Ограничения](#ограничения)

---

## Архитектура

```
             Telegram      MAX       WhatsApp    Mattermost
                │           │            │            │
   adapters/    ▼           ▼            ▼            ▼        ← контроллеры: SDK мессенджеров живут
             mapper → IncomingMessage          Reply → send       только здесь
                              │                  ▲
   use_cases/                 ▼                  │
             MessageRouter ── группа ──► ModerateMessageUseCase
                          └─ личка ───► HelpUseCase / CheckUsageUseCase / GetMemeUseCase
                              │
   domain/   сущности, ModerationService, парсер команд,
             интерфейсы IModerationRepository / IStatsRepository / IClaudeUsageGateway / IMemeGateway
                              ▲
   infrastructure/            │ реализуют интерфейсы домена
             SQLite (aiosqlite) · ClaudeUsageClient (anthropic) · MemeApiClient (httpx)
```

Правило зависимостей: `domain` ни от чего не зависит; `use_cases` знают только `domain`;
`adapters` и `infrastructure` зависят от внутренних слоёв, но не друг от друга.
Всё соединяется в одном месте — `src/bootstrap.py` (composition root).
SDK мессенджеров импортируются лениво, поэтому для запуска Telegram-бота не нужен SDK WhatsApp.

Ключевые решения:

- **Повторная доставка не дублирует предупреждения.** Таблица событий имеет
  `UNIQUE (messenger_type, chat_id, message_id)`. Если Meta повторила вебхук или апдейт пришёл
  дважды, `save()` вернёт `False`, и бот промолчит.
- **Ответы готовятся в фоне.** Вебхук сразу получает `200`, а `/check`, который идёт в Claude API,
  не задерживает обработку следующих сообщений. При остановке (Ctrl+C, SIGTERM) бот дожидается
  начатых ответов и закрывает БД.
- **Ошибки изолированы.** Падение отправки ответа или внешнего API логируется и не роняет
  polling. В группу бот ничего не пишет, в личку отвечает «Что-то пошло не так».

## Дерево проекта

```
smart-bots/
├── config/
│   └── moderation_keywords.txt     # список ключевых слов (шаблоны, исключения)
├── src/
│   ├── domain/                     # чистая бизнес-логика, без внешних библиотек
│   │   ├── entities.py             # IncomingMessage, ModerationEvent, Reply, ApiUsageReport…
│   │   ├── repositories.py         # IModerationRepository, IStatsRepository (ABC)
│   │   ├── gateways.py             # IClaudeUsageGateway, IMemeGateway (ABC)
│   │   ├── services.py             # ModerationService — поиск ключевых слов
│   │   └── commands.py             # распознавание /help, /help@bot, !mem, mem
│   ├── use_cases/
│   │   ├── moderate_message.py     # проверка + предупреждение + запись события
│   │   ├── handle_help.py
│   │   ├── handle_check.py         # форматирование расхода токенов и лимитов
│   │   ├── handle_mem.py
│   │   └── router.py               # MessageRouter: группа → модерация, личка → команды
│   ├── adapters/                   # контроллеры мессенджеров
│   │   ├── base.py                 # MessengerAdapter, режимы, фоновые задачи
│   │   ├── web.py                  # aiohttp-сервер вебхуков (+ /healthz)
│   │   ├── telegram/               # aiogram 3
│   │   ├── max/                    # maxapi-sdk
│   │   ├── whatsapp/               # kapso-whatsapp-cloud-api
│   │   └── mattermost/             # mattermostautodriver
│   │       ├── mapper.py           #   событие SDK → IncomingMessage
│   │       ├── adapter.py          #   polling/WebSocket, отправка Reply
│   │       └── webhook.py          #   webhook_handler — точка расширения
│   ├── infrastructure/
│   │   ├── db/connection.py        # aiosqlite, миграции через PRAGMA user_version
│   │   ├── db/repositories.py      # SqliteModerationRepository, SqliteStatsRepository
│   │   ├── claude_client.py        # AsyncAnthropic + with_raw_response
│   │   └── meme_client.py          # httpx-клиент meme-api
│   ├── bootstrap.py                # composition root
│   ├── config.py                   # настройки из окружения (pydantic-settings)
│   └── main.py                     # python -m src.main --messenger …
├── tests/
│   ├── unit/                       # домен и use cases с моками
│   ├── integration/                # SQLite, клиенты API, адаптеры, сборка приложения
│   └── helpers.py
├── .env.example
└── pyproject.toml
```

## Выбор библиотек

Все библиотеки актуальны на октябрь 2026 года и поддерживают Python 3.11+.

| Назначение | Библиотека | Версия | Комментарий |
|---|---|---|---|
| Telegram | `aiogram` | 3.31 | long polling и вебхуки (`SimpleRequestHandler`) |
| MAX | `maxapi-sdk` | 0.13 | используется как **типизированный клиент** Bot API, см. ниже |
| WhatsApp | `kapso-whatsapp-cloud-api` | 0.3 | async, Pydantic, проверка подписи и нормализация вебхуков |
| Mattermost | `mattermostautodriver` | 11.11 | **вместо `mattermostdriver`**, см. ниже |
| SQLite | `aiosqlite` | 0.22 | |
| Claude | `anthropic` | 2.3 | `AsyncAnthropic`, API-версия `2026-10-21` |
| HTTP | `httpx`, `aiohttp` | 0.28 / 3.14 | httpx — meme-api и Mattermost; aiohttp — сервер вебхуков |
| Конфигурация | `pydantic-settings` | 2.16 | типизированные настройки из окружения и `.env` |
| Тесты | `pytest`, `pytest-asyncio`, `pytest-mock` | 9.2 / 1.4 / 3.17 | |

Отступления от исходного списка и их причины:

- **`mattermostautodriver` вместо `mattermostdriver`.** Последний релиз `mattermostdriver` (7.3.2)
  вышел в январе 2022 года, и проект не поддерживается. В его WebSocket-клиенте TLS-контекст
  создаётся с `Purpose.CLIENT_AUTH`, то есть без проверки сертификата сервера
  (`verify_mode=CERT_NONE`), а это открывает дорогу MITM. `mattermostautodriver` — поддерживаемый
  форк того же драйвера с тем же API (`login`, `posts.create_post`, `init_websocket`), генерируется из
  OpenAPI-спецификации Mattermost, есть `AsyncTypedDriver`. Версия 11.11.1 вышла 15.10.2026,
  требует Python 3.10+.
- **`maxapi-sdk` — только как клиент.** Его `Dispatcher` в версии 0.13 падает на апдейтах без
  `message` (например, `bot_started`). А `PollingRunner` после любой ошибки не сдвигает marker
  и бесконечно перезапрашивает ту же пачку апдейтов. Поэтому цикл `GET /updates` с маркером
  написан в адаптере, а SDK используется для типизированных запросов.
  Ещё одна деталь: `maxapi-sdk` и `maxapi` (love-apples) ставятся в один и тот же пакет `maxapi`,
  поэтому одновременно их ставить нельзя.
- **`pydantic-settings`** в списке не было. Он даёт валидацию типов, `SecretStr` (токены не
  попадают в логи) и чтение `.env` без своего парсера. pydantic и так приходит транзитивно
  от aiogram и kapso.

## Установка

Нужен Python 3.11–3.14.

```bash
git clone https://github.com/sshagy/smart-bots.git
cd smart-bots
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate

pip install -e ".[all,dev]"          # все мессенджеры + инструменты разработки
# или только нужный мессенджер:
pip install -e ".[telegram]"         # варианты: telegram, max, whatsapp, mattermost
```

С `uv`: `uv venv && uv pip install -e ".[all,dev]"`.

## Получение токенов

### Telegram

1. Напишите [@BotFather](https://t.me/BotFather) → `/newbot` → придумайте имя и username →
   получите токен → `TELEGRAM_BOT_TOKEN`.
2. **Обязательно для модерации:** `/setprivacy` → выберите бота → `Disable`. Иначе в группах бот
   получает только команды и упоминания. Другой вариант — сделать бота администратором группы.
   После смены режима удалите бота из группы и добавьте снова.

### MAX

1. Токен присваивается при создании бота на платформе для разработчиков
   [dev.max.ru](https://dev.max.ru): раздел «Чат-боты» → ваш бот → «Настройки». Получить его
   можно и через Master Bot в самом мессенджере. → `MAX_BOT_TOKEN`.
   По сторонним источникам, ботов сейчас регистрируют организации, ИП и самозанятые через
   кабинет MAX для бизнеса (business.max.ru → «Чат-боты» → «Интеграция»), а модерация бота занимает
   до 48 часов. Сверьте актуальный порядок с документацией dev.max.ru.
2. **Для модерации:** добавьте бота в групповой чат администратором с правом
   «Читать все сообщения», иначе он не увидит сообщения участников.

Токен передаётся в заголовке `Authorization`, это делает SDK.

### WhatsApp (Cloud API)

1. [Meta for Developers](https://developers.facebook.com) → My Apps → Create App (тип Business) →
   добавьте продукт **WhatsApp**.
2. **WhatsApp → API Setup:**
   - сгенерируйте **временный токен** (живёт около 24 часов, подходит для тестов) →
     `WHATSAPP_ACCESS_TOKEN`;
   - скопируйте **Phone number ID** → `WHATSAPP_PHONE_NUMBER_ID`;
   - с тестового номера можно писать только на номера, добавленные в список получателей.
3. **Постоянный токен для продакшена:** Meta Business Suite → Business Settings → Users →
   **System Users** → Add. Назначьте пользователю приложение и WhatsApp-аккаунт и сгенерируйте
   токен с правами `whatsapp_business_messaging` и `whatsapp_business_management`.
4. **App Secret:** App Dashboard → App settings → Basic → App Secret → `WHATSAPP_APP_SECRET`
   (по нему проверяется подпись `X-Hub-Signature-256` каждого вебхука).
5. **Вебхук:** WhatsApp → Configuration → Webhook → Edit:
   - Callback URL: `https://<ваш-адрес>/webhook/whatsapp`;
   - Verify token: любая строка, та же, что в `WHATSAPP_VERIFY_TOKEN`;
   - Webhook fields: подпишитесь на `messages`.

### Mattermost

1. Войдите в свой аккаунт → **Profile → Security → Personal Access Tokens → Create Token** →
   `MATTERMOST_TOKEN`. Если пункта нет, администратор должен включить токены:
   System Console → Integrations → Integration Management → *Enable Personal Access Tokens*.
2. Рекомендуемая альтернатива — отдельный **бот-аккаунт**: System Console → Integrations →
   Bot Accounts → *Enable Bot Account Creation*, затем Integrations → Bot Accounts →
   Add Bot Account. Его токен тоже кладётся в `MATTERMOST_TOKEN`.
3. Добавьте бота в команду и в каналы, которые нужно модерировать.
4. `MATTERMOST_URL` — адрес сервера, например `https://chat.example.com`.

### Claude API (для /check)

Claude Console ([platform.claude.com](https://platform.claude.com)) → API Keys → Create Key →
`ANTHROPIC_API_KEY`. Без ключа бот работает, а `/check` честно отвечает, что команда не настроена.

## Настройка (.env)

```bash
cp .env.example .env
```

Заполните секцию нужного мессенджера. Полный список переменных — в [`.env.example`](.env.example):

| Переменная | Назначение |
|---|---|
| `BOT_MODE` | `polling` (по умолчанию) или `webhook` |
| `LOG_LEVEL`, `DATABASE_PATH` | уровень логов, путь к SQLite (`data/smart_bots.sqlite3`) |
| `MODERATION_KEYWORDS_FILE`, `MODERATION_KEYWORDS` | файл со словами и дополнительные слова через запятую |
| `MODERATION_WARNING_TEXT` | текст предупреждения, `{user}` → @username или имя |
| `ANTHROPIC_API_KEY`, `CLAUDE_MODEL` | ключ и модель для `/check` (по умолчанию `claude-opus-5-5`) |
| `MEME_API_URL`, `HTTP_TIMEOUT_SECONDS` | источник мемов и таймаут HTTP |
| `WEBHOOK_HOST`, `WEBHOOK_PORT`, `WEBHOOK_BASE_URL`, `WEBHOOK_SECRET` | сервер вебхуков |
| `TELEGRAM_BOT_TOKEN` | Telegram |
| `MAX_BOT_TOKEN` | MAX |
| `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_VERIFY_TOKEN`, `WHATSAPP_APP_SECRET` | WhatsApp |
| `MATTERMOST_URL`, `MATTERMOST_TOKEN`, `MATTERMOST_WEBHOOK_TOKEN` | Mattermost |

Токены читаются только из окружения или `.env`, в коде их нет. В логах они маскируются
(`SecretStr`). Если какой-то переменной не хватает, бот сразу сообщает, какой именно, и
завершается с кодом 2.

## Запуск

Из корня репозитория, с активированным окружением:

```bash
python -m src.main --messenger telegram
python -m src.main --messenger max
python -m src.main --messenger whatsapp
python -m src.main --messenger mattermost

python -m src.main -m telegram --mode webhook   # --mode переопределяет BOT_MODE
smart-bots -m telegram                          # то же самое через entry point после pip install
```

Каждый мессенджер — отдельный процесс, все они могут писать в одну базу `DATABASE_PATH`.

Как каждый адаптер получает события без белого IP:

| Мессенджер | `BOT_MODE=polling` | Особенности |
|---|---|---|
| Telegram | `getUpdates` (long polling); вебхук, если был, удаляется | отключите privacy mode |
| MAX | `GET /updates` с маркером; старые webhook-подписки удаляются | бот — админ с правом «Читать все сообщения» |
| WhatsApp | **long polling у Cloud API нет**, поднимается webhook-сервер | локально нужен туннель, см. ниже |
| Mattermost | WebSocket API (соединение открывает бот) | команды пишите как `!help` или `help` |

**WhatsApp локально.** Meta доставляет сообщения только вебхуком. Без белого IP пробросьте порт
туннелем и укажите выданный https-адрес в Meta App Dashboard:

```bash
python -m src.main -m whatsapp                    # слушает WEBHOOK_HOST:WEBHOOK_PORT (0.0.0.0:8080)
cloudflared tunnel --url http://localhost:8080    # или: ngrok http 8080
# Callback URL: https://<адрес-туннеля>/webhook/whatsapp
```

**Mattermost и «/».** Клиент Mattermost перехватывает сообщения, начинающиеся с `/`: `/help` —
встроенная команда, а `/mem` и `/check` он отклонит как неизвестные. Поэтому бот понимает и
`!mem`, и просто `mem`, а справка в Mattermost сама подсказывает префикс `!`.

## Переход на вебхуки

Когда появится белый IP или домен с HTTPS (обычно за обратным прокси nginx/Caddy, который
проксирует на `WEBHOOK_PORT`):

1. `WEBHOOK_BASE_URL=https://bot.example.com` — публичный адрес;
2. `WEBHOOK_SECRET=<случайная строка из A-Z, a-z, 0-9, _ и ->` — для Telegram и MAX;
3. `BOT_MODE=webhook`.

| Мессенджер | URL | Что делает адаптер |
|---|---|---|
| Telegram | `/webhook/telegram` | сам вызывает `setWebhook` с `secret_token`; чужие запросы получают 401 |
| MAX | `/webhook/max` | сам подписывается (`POST /subscriptions`) с `secret`; проверяет `X-Max-Bot-Api-Secret` |
| WhatsApp | `/webhook/whatsapp` | URL указывается в Meta App Dashboard; проверяет подпись `X-Hub-Signature-256` |
| Mattermost | `/webhook/mattermost` | Outgoing Webhook (Integrations → Outgoing Webhooks), токен → `MATTERMOST_WEBHOOK_TOKEN` |

Точки расширения находятся в `src/adapters/<мессенджер>/webhook.py` (функция `webhook_handler`),
там же в docstring — пошаговая инструкция для каждого мессенджера. `GET /healthz` отвечает `ok`
и подходит для балансировщика.

Для Mattermost WebSocket остаётся лучшим вариантом и при белом IP: исходящие вебхуки
Mattermost работают только в публичных каналах, без личных сообщений.

## Модерация и статистика

### Ключевые слова

Файл `config/moderation_keywords.txt` (путь задаёт `MODERATION_KEYWORDS_FILE`), по одному
шаблону на строку:

| Шаблон | Что ловит |
|---|---|
| `бля` | только целое слово: «бля», но не «рубля» |
| `бляд*` | слово, начинающееся с «бляд» |
| `*пизд*` | слово, содержащее «пизд» |
| `иди на` | фразу, слова разделены любыми пробелами |
| `!*страху*` | исключение: «страхуй», «застрахуйте» никогда не нарушение |
| `# …` | комментарий |

Перед сравнением текст нормализуется: регистр, `ё → е`, невидимые символы, латинские двойники
и цифры внутри кириллических слов (`xуй → хуй`, `6ля → бля`), растянутые буквы
(`бляяять → блять`). В тестах есть корпус обычных слов («оскорблять», «употреблять»,
«хлебать», «Ебург», «психуешь»…), на которых встроенный список не должен срабатывать.

### Хранилище

```sql
CREATE TABLE moderation_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    messenger_type TEXT NOT NULL,   -- telegram | max | whatsapp | mattermost
    chat_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    username TEXT,                  -- логин, а если его нет (WhatsApp) — отображаемое имя
    matched_keyword TEXT NOT NULL,  -- сработавший шаблон, например «бля*»
    timestamp TEXT NOT NULL,        -- ISO-8601 UTC
    UNIQUE (messenger_type, chat_id, message_id)
);
```

Агрегация — `SqliteStatsRepository` (`src/infrastructure/db/repositories.py`):

- `get_stats_by_user(messenger=None, chat_id=None, since=None, limit=10)` — нарушители по числу
  нарушений, с последним известным именем и временем последнего нарушения;
- `get_top_keywords(messenger=None, chat_id=None, since=None, limit=10)` — самые частые шаблоны.

Быстрый взгляд на статистику без кода:

```bash
sqlite3 data/smart_bots.sqlite3 \
  "SELECT messenger_type, username, COUNT(*) FROM moderation_events GROUP BY 1, user_id ORDER BY 3 DESC LIMIT 10"
```

## Команда /check

Бот делает минимальный запрос к Messages API (`max_tokens=256`, `effort: low`) через
`AsyncAnthropic().messages.with_raw_response.create(...)` и показывает реальные данные:

- **потрачено** — `usage.input_tokens` и `usage.output_tokens` этого запроса (плюс кэш, если был);
- **остаток лимитов** — заголовки ответа `anthropic-ratelimit-{requests,tokens,input-tokens,
  output-tokens}-{limit,remaining,reset}`.

```
📊 Claude API · модель claude-opus-5-5

Потрачено на этот запрос:
• входные токены: 18
• выходные токены: 5
• всего: 23

Остаток лимитов (осталось из лимита):
• запросы: 999 из 1 000, полностью восстановится через 1 с
• токены (самый строгий из лимитов): 2 399 000 из 2 400 000, полностью восстановится через 2 с
• входные токены: 2 000 000 из 2 000 000
• выходные токены: 399 000 из 400 000, полностью восстановится через 2 с

Ответ модели: OK
request-id: req_011CXYZ
```

Лимиты Claude API считаются **отдельно для каждой модели**, поэтому `CLAUDE_MODEL` стоит
выставить на ту модель, которой вы реально пользуетесь. Ошибки переводятся в понятные
сообщения: неверный ключ, нет доступа к модели, исчерпан лимит запросов («повторите через N с»),
исчерпан месячный лимит расходов. Один `/check` стоит пару десятков токенов.

## Тесты

```bash
pytest -v          # 228 тестов, сеть не нужна
ruff check . && ruff format --check .
```

Все тесты асинхронные (`pytestmark = pytest.mark.asyncio`).

- **Юнит** (`tests/unit`): `ModerationService` (шаблоны, исключения, нормализация, корпус
  мата и корпус обычных слов без ложных срабатываний), парсер команд, use cases с моками
  репозитория и шлюзов (вызываются ли нужные методы, как отформатирован ответ), роутер,
  конфигурация.
- **Интеграционные** (`tests/integration`):
  - SQLite in-memory и файловая: сохранение и чтение `ModerationEvent`, дубли, фильтры, агрегаты, WAL;
  - `ClaudeUsageClient` поверх настоящего `AsyncAnthropic` с mock-транспортом `httpx2`: разбор
    `usage` и заголовков лимитов, ошибки 401/403/404/429/529;
  - `MemeApiClient` на `httpx.MockTransport`;
  - контроллеры всех четырёх мессенджеров на **настоящих объектах SDK**: aiogram `Update` проходит
    через `Dispatcher.feed_update`, модели maxapi-sdk, payload WhatsApp Cloud API с подписью,
    события WebSocket Mattermost. Проверяется, что входящее сообщение правильно разобрано, ядро
    вызвано, а в API мессенджера ушёл нужный запрос (reply, тред, группа, картинка с запасными
    вариантами). Вебхуки проверяются через aiohttp `TestClient`: секреты, подписи, рукопожатие Meta;
  - сборка приложения (`bootstrap`) и коды выхода CLI.

## Ограничения

- **WhatsApp Cloud API** получает сообщения только вебхуком. Групповые сообщения приходят через
  Groups API (поле `group_id`), то есть только из групп, созданных самим бизнесом. Доступность
  Groups API для вашего номера уточните в документации Meta. Если WhatsApp не смог скачать
  картинку мема, он сообщает об этом асинхронно статусом `failed`, бот пишет такие статусы в лог.
- **Mattermost в webhook-режиме** модерирует только публичные каналы — это ограничение
  исходящих вебхуков Mattermost.
- **`/check`** доступна любому пользователю, который пишет боту в личку, и тратит немного токенов
  с вашего ключа.
