from datetime import timedelta

import pytest
from pytest_mock import MockerFixture

from src.domain.entities import ApiUsageReport, RateLimit, RateLimitKind, TokenUsage
from src.domain.gateways import IClaudeUsageGateway, UsageGatewayError
from src.use_cases.handle_check import CheckUsageUseCase
from tests.helpers import FIXED_NOW

pytestmark = pytest.mark.asyncio

NBSP = " "


def make_report(**overrides: object) -> ApiUsageReport:
    fields: dict[str, object] = {
        "model": "claude-opus-5-5",
        "usage": TokenUsage(input_tokens=18, output_tokens=5),
        "rate_limits": (
            RateLimit(RateLimitKind.REQUESTS, 1000, 999, FIXED_NOW + timedelta(seconds=1)),
            RateLimit(
                RateLimitKind.TOKENS, 2_400_000, 2_399_000, FIXED_NOW + timedelta(seconds=30)
            ),
            RateLimit(RateLimitKind.INPUT_TOKENS, 2_000_000, 2_000_000, FIXED_NOW),
            RateLimit(RateLimitKind.OUTPUT_TOKENS, 400_000, None, None),
        ),
        "request_id": "req_123",
        "model_reply": "OK",
    }
    fields.update(overrides)
    return ApiUsageReport(**fields)  # type: ignore[arg-type]


async def test_check_formats_real_usage_and_limits(mocker: MockerFixture) -> None:
    gateway = mocker.AsyncMock(spec=IClaudeUsageGateway)
    gateway.fetch_usage_report.return_value = make_report()

    reply = await CheckUsageUseCase(gateway, clock=lambda: FIXED_NOW).execute()

    gateway.fetch_usage_report.assert_awaited_once_with()
    text = reply.text
    assert "модель claude-opus-5-5" in text
    assert "входные токены: 18" in text
    assert "выходные токены: 5" in text
    assert "всего: 23" in text
    assert f"запросы: 999 из 1{NBSP}000, полностью восстановится через 1 с" in text
    assert f"2{NBSP}399{NBSP}000 из 2{NBSP}400{NBSP}000, полностью восстановится через 30 с" in text
    # уже восстановленный лимит — без «через …»
    assert f"входные токены: 2{NBSP}000{NBSP}000 из 2{NBSP}000{NBSP}000\n" in text
    assert f"выходные токены: — из 400{NBSP}000" in text
    assert "Ответ модели: OK" in text
    assert "request-id: req_123" in text
    assert "кэш" not in text


async def test_check_shows_cache_tokens_when_present(mocker: MockerFixture) -> None:
    usage = TokenUsage(10, 2, cache_creation_input_tokens=100, cache_read_input_tokens=50)
    gateway = mocker.AsyncMock(spec=IClaudeUsageGateway)
    gateway.fetch_usage_report.return_value = make_report(usage=usage)

    text = (await CheckUsageUseCase(gateway, clock=lambda: FIXED_NOW).execute()).text

    assert "запись в кэш: 100" in text
    assert "чтение из кэша: 50" in text
    assert "всего: 162" in text


async def test_check_without_rate_limit_headers(mocker: MockerFixture) -> None:
    gateway = mocker.AsyncMock(spec=IClaudeUsageGateway)
    gateway.fetch_usage_report.return_value = make_report(rate_limits=(), request_id=None)

    text = (await CheckUsageUseCase(gateway).execute()).text

    assert "не вернул заголовки anthropic-ratelimit-*" in text
    assert "request-id" not in text


async def test_check_reports_gateway_errors(mocker: MockerFixture) -> None:
    gateway = mocker.AsyncMock(spec=IClaudeUsageGateway)
    gateway.fetch_usage_report.side_effect = UsageGatewayError("неверный ANTHROPIC_API_KEY")

    reply = await CheckUsageUseCase(gateway).execute()

    assert reply.text == "⚠️ Не удалось получить данные Claude API: неверный ANTHROPIC_API_KEY"


async def test_check_without_configured_gateway() -> None:
    reply = await CheckUsageUseCase(gateway=None).execute()

    assert "ANTHROPIC_API_KEY" in reply.text
