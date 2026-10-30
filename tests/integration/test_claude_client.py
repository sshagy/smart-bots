"""ClaudeUsageClient поверх настоящего AsyncAnthropic с подменённым HTTP-транспортом.

Сеть не используется: httpx2.MockTransport отвечает так, как отвечает Messages API,
поэтому проверяется и наш код, и реальный разбор ответа SDK.
"""

import json
from collections.abc import Callable
from datetime import UTC, datetime

import httpx2
import pytest
from anthropic import AsyncAnthropic, DefaultAsyncHttpxClient

from src.domain.entities import RateLimit, RateLimitKind, TokenUsage
from src.domain.gateways import UsageGatewayError
from src.infrastructure.claude_client import PING_PROMPT, ClaudeUsageClient, parse_rate_limits

pytestmark = pytest.mark.asyncio

MESSAGE = {
    "id": "msg_01",
    "type": "message",
    "role": "assistant",
    "model": "claude-opus-5-5",
    "content": [{"type": "text", "text": "OK"}],
    "stop_reason": "end_turn",
    "stop_sequence": None,
    "usage": {
        "input_tokens": 18,
        "output_tokens": 5,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
    },
}
RATE_LIMIT_HEADERS = {
    "request-id": "req_011CXYZ",
    "anthropic-ratelimit-requests-limit": "1000",
    "anthropic-ratelimit-requests-remaining": "999",
    "anthropic-ratelimit-requests-reset": "2026-10-30T12:00:01Z",
    "anthropic-ratelimit-tokens-limit": "2400000",
    "anthropic-ratelimit-tokens-remaining": "2399000",
    "anthropic-ratelimit-tokens-reset": "2026-10-30T12:00:30Z",
    "anthropic-ratelimit-input-tokens-limit": "2000000",
    "anthropic-ratelimit-input-tokens-remaining": "2000000",
    "anthropic-ratelimit-input-tokens-reset": "2026-10-30T12:00:00Z",
    "anthropic-ratelimit-output-tokens-limit": "400000",
    "anthropic-ratelimit-output-tokens-remaining": "399000",
    "anthropic-ratelimit-output-tokens-reset": "2026-10-30T12:00:05Z",
}

Handler = Callable[[httpx2.Request], httpx2.Response]


def make_gateway(handler: Handler, model: str = "claude-opus-5-5") -> ClaudeUsageClient:
    client = AsyncAnthropic(
        api_key="sk-ant-test",
        max_retries=0,
        http_client=DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler)),
    )
    return ClaudeUsageClient(client, model)


def error_response(
    status: int, error_type: str, *, headers: dict[str, str] | None = None, **extra: object
) -> httpx2.Response:
    body = {"type": "error", "error": {"type": error_type, "message": "nope", **extra}}
    return httpx2.Response(status, json=body, headers=headers)


async def test_report_contains_real_usage_and_limits() -> None:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, json=MESSAGE, headers=RATE_LIMIT_HEADERS)

    report = await make_gateway(handler).fetch_usage_report()

    assert report.model == "claude-opus-5-5"
    assert report.usage == TokenUsage(input_tokens=18, output_tokens=5)
    assert report.request_id == "req_011CXYZ"
    assert report.model_reply == "OK"
    assert report.rate_limits[0] == RateLimit(
        RateLimitKind.REQUESTS, 1000, 999, datetime(2026, 10, 30, 12, 0, 1, tzinfo=UTC)
    )
    assert [limit.kind for limit in report.rate_limits] == list(RateLimitKind)

    [request] = requests
    assert request.method == "POST"
    assert request.url.path == "/v1/messages"
    body = json.loads(request.content)
    assert body["model"] == "claude-opus-5-5"
    assert body["messages"] == [{"role": "user", "content": PING_PROMPT}]
    assert body["output_config"] == {"effort": "low"}


async def test_missing_rate_limit_headers_give_empty_limits() -> None:
    gateway = make_gateway(lambda request: httpx2.Response(200, json=MESSAGE))

    report = await gateway.fetch_usage_report()

    assert report.rate_limits == ()
    assert report.request_id is None


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (error_response(401, "authentication_error"), "ANTHROPIC_API_KEY"),
        (error_response(403, "permission_error"), "нет доступа к модели claude-opus-5-5"),
        (error_response(404, "not_found_error"), "модель claude-opus-5-5 не найдена"),
        (
            error_response(429, "rate_limit_error", headers={"retry-after": "7"}),
            "лимит запросов исчерпан, повторите через 7 с",
        ),
        (
            error_response(
                429, "rate_limit_error", details={"error_code": "enforced_spend_limit_reached"}
            ),
            "исчерпан месячный лимит расходов",
        ),
        (error_response(529, "overloaded_error"), "API вернул ошибку 529"),
    ],
)
async def test_api_errors_become_readable_gateway_errors(
    response: httpx2.Response, expected: str
) -> None:
    gateway = make_gateway(lambda request: response)

    with pytest.raises(UsageGatewayError, match=expected):
        await gateway.fetch_usage_report()


async def test_network_failure() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("connection refused", request=request)

    with pytest.raises(UsageGatewayError, match="нет соединения"):
        await make_gateway(handler).fetch_usage_report()


async def test_parse_rate_limits_skips_absent_and_invalid_values() -> None:
    headers = {
        "anthropic-ratelimit-requests-limit": "50",
        "anthropic-ratelimit-requests-remaining": "not-a-number",
        "anthropic-ratelimit-requests-reset": "вчера",
    }

    assert parse_rate_limits(headers) == (RateLimit(RateLimitKind.REQUESTS, 50, None, None),)
