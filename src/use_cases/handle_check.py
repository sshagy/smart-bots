from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from src.domain.entities import ApiUsageReport, RateLimit, RateLimitKind, TextReply
from src.domain.gateways import IClaudeUsageGateway, UsageGatewayError

_LIMIT_LABELS = {
    RateLimitKind.REQUESTS: "запросы",
    RateLimitKind.TOKENS: "токены (самый строгий из лимитов)",
    RateLimitKind.INPUT_TOKENS: "входные токены",
    RateLimitKind.OUTPUT_TOKENS: "выходные токены",
}


class CheckUsageUseCase:
    """/check — реальный расход токенов и остаток лимитов из ответа Claude API."""

    def __init__(
        self,
        gateway: IClaudeUsageGateway | None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._gateway = gateway
        self._clock = clock

    async def execute(self) -> TextReply:
        if self._gateway is None:
            return TextReply(
                "Команда check не настроена: администратору нужно задать ANTHROPIC_API_KEY."
            )
        try:
            report = await self._gateway.fetch_usage_report()
        except UsageGatewayError as exc:
            return TextReply(f"⚠️ Не удалось получить данные Claude API: {exc}")
        return TextReply(format_usage_report(report, now=self._clock()))


def format_usage_report(report: ApiUsageReport, *, now: datetime) -> str:
    usage = report.usage
    lines = [
        f"📊 Claude API · модель {report.model}",
        "",
        "Потрачено на этот запрос:",
        f"• входные токены: {_number(usage.input_tokens)}",
        f"• выходные токены: {_number(usage.output_tokens)}",
    ]
    if usage.cache_creation_input_tokens:
        lines.append(f"• запись в кэш: {_number(usage.cache_creation_input_tokens)}")
    if usage.cache_read_input_tokens:
        lines.append(f"• чтение из кэша: {_number(usage.cache_read_input_tokens)}")
    lines.append(f"• всего: {_number(usage.total_tokens)}")

    lines += ["", "Остаток лимитов (осталось из лимита):"]
    if report.rate_limits:
        lines += [f"• {_format_limit(limit, now)}" for limit in report.rate_limits]
    else:
        lines.append("• API не вернул заголовки anthropic-ratelimit-*")

    if report.model_reply:
        lines += ["", f"Ответ модели: {report.model_reply}"]
    if report.request_id:
        lines.append(f"request-id: {report.request_id}")
    return "\n".join(lines)


def _format_limit(limit: RateLimit, now: datetime) -> str:
    text = f"{_LIMIT_LABELS[limit.kind]}: {_number(limit.remaining)} из {_number(limit.limit)}"
    if limit.reset_at is not None:
        seconds = int((limit.reset_at - now).total_seconds())
        if seconds > 0:
            text += f", полностью восстановится через {_duration(seconds)}"
    return text


def _number(value: int | None) -> str:
    # Неразрывный пробел между разрядами: «2 000 000» не переносится по строкам.
    return "—" if value is None else f"{value:,}".replace(",", " ")


def _duration(seconds: int) -> str:
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours} ч {minutes} мин"
    if minutes:
        return f"{minutes} мин {sec} с"
    return f"{sec} с"
