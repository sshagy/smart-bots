"""Шлюз к Claude API для команды /check.

Делает минимальный запрос к Messages API и берёт реальные данные из ответа:
`usage` (сколько токенов потрачено) и заголовки `anthropic-ratelimit-*`
(сколько осталось до лимитов организации для этой модели).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

import anthropic
from anthropic import AsyncAnthropic

from src.domain.entities import ApiUsageReport, RateLimit, RateLimitKind, TokenUsage
from src.domain.gateways import IClaudeUsageGateway, UsageGatewayError

PING_PROMPT = "Ответь одним словом: OK"

_HEADER_NAMES = {
    RateLimitKind.REQUESTS: "requests",
    RateLimitKind.TOKENS: "tokens",
    RateLimitKind.INPUT_TOKENS: "input-tokens",
    RateLimitKind.OUTPUT_TOKENS: "output-tokens",
}


class ClaudeUsageClient(IClaudeUsageGateway):
    def __init__(self, client: AsyncAnthropic, model: str) -> None:
        self._client = client
        self._model = model

    async def fetch_usage_report(self) -> ApiUsageReport:
        try:
            raw = await self._client.messages.with_raw_response.create(
                model=self._model,
                # Ответ модели не важен — нужны usage и заголовки лимитов, поэтому просим
                # минимум рассуждений: такой «пинг» стоит пару десятков токенов.
                max_tokens=256,
                output_config={"effort": "low"},
                messages=[{"role": "user", "content": PING_PROMPT}],
            )
            message = await raw.parse()
        except anthropic.AuthenticationError as exc:
            raise UsageGatewayError("API отклонил ключ — проверьте ANTHROPIC_API_KEY") from exc
        except anthropic.PermissionDeniedError as exc:
            raise UsageGatewayError(f"у ключа нет доступа к модели {self._model}") from exc
        except anthropic.NotFoundError as exc:
            raise UsageGatewayError(f"модель {self._model} не найдена (CLAUDE_MODEL)") from exc
        except anthropic.RateLimitError as exc:
            raise UsageGatewayError(_rate_limit_message(exc)) from exc
        except anthropic.APIStatusError as exc:
            raise UsageGatewayError(f"API вернул ошибку {exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:  # включает таймауты
            raise UsageGatewayError("нет соединения с API, попробуйте позже") from exc

        usage = message.usage
        return ApiUsageReport(
            model=message.model,
            usage=TokenUsage(
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_creation_input_tokens=usage.cache_creation_input_tokens or 0,
                cache_read_input_tokens=usage.cache_read_input_tokens or 0,
            ),
            rate_limits=parse_rate_limits(raw.headers),
            request_id=raw.request_id,
            model_reply="".join(b.text for b in message.content if b.type == "text").strip()
            or None,
        )


def parse_rate_limits(headers: Mapping[str, str]) -> tuple[RateLimit, ...]:
    """Разбирает заголовки anthropic-ratelimit-{requests,tokens,input-tokens,output-tokens}-*."""
    limits: list[RateLimit] = []
    for kind, name in _HEADER_NAMES.items():
        prefix = f"anthropic-ratelimit-{name}-"
        limit = _to_int(headers.get(prefix + "limit"))
        remaining = _to_int(headers.get(prefix + "remaining"))
        reset_at = _to_datetime(headers.get(prefix + "reset"))
        if limit is not None or remaining is not None or reset_at is not None:
            limits.append(RateLimit(kind, limit, remaining, reset_at))
    return tuple(limits)


def _rate_limit_message(exc: anthropic.RateLimitError) -> str:
    body: Any = exc.body
    error = body.get("error", {}) if isinstance(body, dict) else {}
    details = error.get("details") if isinstance(error, dict) else None
    if isinstance(details, dict) and details.get("error_code") == "enforced_spend_limit_reached":
        return "исчерпан месячный лимит расходов организации"
    retry_after = exc.response.headers.get("retry-after")
    suffix = f", повторите через {retry_after} с" if retry_after else ""
    return f"лимит запросов исчерпан{suffix}"


def _to_int(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def _to_datetime(value: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(value) if value else None  # RFC 3339
    except ValueError:
        return None
