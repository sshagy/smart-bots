from collections.abc import AsyncIterator, Callable

import httpx
import pytest
import pytest_asyncio

from src.domain.entities import Meme
from src.domain.gateways import MemeGatewayError
from src.infrastructure.meme_client import MemeApiClient

pytestmark = pytest.mark.asyncio

API_URL = "https://meme-api.com/gimme"


def meme(url: str = "https://i.redd.it/f7ibqp1dmiu51.jpg", **overrides: object) -> dict:
    """Ответ meme-api.com/gimme."""
    return {
        "postLink": "https://redd.it/jiovfz",
        "subreddit": "ProgrammerHumor",
        "title": "Clean code",
        "url": url,
        "nsfw": False,
        "spoiler": False,
        "author": "someone",
        "ups": 66603,
        "preview": [],
        **overrides,
    }


class FakeMemeApi:
    """Отдаёт заранее заданные ответы по очереди и запоминает запросы."""

    def __init__(self, *responses: httpx.Response | Callable[[httpx.Request], httpx.Response]):
        self._responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        response = self._responses.pop(0) if len(self._responses) > 1 else self._responses[0]
        return response(request) if callable(response) else response


@pytest_asyncio.fixture
async def make_client() -> AsyncIterator[Callable[[FakeMemeApi], MemeApiClient]]:
    clients: list[httpx.AsyncClient] = []

    def factory(api: FakeMemeApi) -> MemeApiClient:
        http = httpx.AsyncClient(transport=httpx.MockTransport(api))
        clients.append(http)
        return MemeApiClient(http, API_URL, max_attempts=3)

    yield factory
    for http in clients:
        await http.aclose()


async def test_returns_meme(make_client) -> None:
    api = FakeMemeApi(httpx.Response(200, json=meme()))

    result = await make_client(api).get_random_meme()

    assert result == Meme(
        title="Clean code",
        image_url="https://i.redd.it/f7ibqp1dmiu51.jpg",
        post_link="https://redd.it/jiovfz",
        subreddit="ProgrammerHumor",
    )
    assert str(api.requests[0].url) == API_URL


@pytest.mark.parametrize(
    "unsuitable",
    [
        meme(nsfw=True),
        meme(spoiler=True),
        meme(url="https://v.redd.it/video123"),  # видео, не картинка
        meme(url="http://i.redd.it/insecure.jpg"),  # не https
        {"code": 503, "message": "Reddit is down"},  # ответ-ошибка без url
    ],
)
async def test_skips_unsuitable_memes(make_client, unsuitable: dict) -> None:
    api = FakeMemeApi(
        httpx.Response(200, json=unsuitable),
        httpx.Response(200, json=meme(url="https://i.redd.it/good.png")),
    )

    result = await make_client(api).get_random_meme()

    assert result.image_url == "https://i.redd.it/good.png"
    assert len(api.requests) == 2


async def test_batch_endpoint_format(make_client) -> None:
    batch = {"count": 2, "memes": [meme(nsfw=True), meme(url="https://i.redd.it/2.gif")]}
    api = FakeMemeApi(httpx.Response(200, json=batch))

    result = await make_client(api).get_random_meme()

    assert result.image_url == "https://i.redd.it/2.gif"
    assert len(api.requests) == 1


async def test_gives_up_after_max_attempts(make_client) -> None:
    api = FakeMemeApi(httpx.Response(200, json=meme(nsfw=True)))

    with pytest.raises(MemeGatewayError, match="не нашлось подходящей картинки"):
        await make_client(api).get_random_meme()
    assert len(api.requests) == 3


def raise_timeout(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("timeout", request=request)


def raise_connect_error(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("dns failure", request=request)


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (httpx.Response(500, text="Internal Server Error"), "вернул ошибку 500"),
        (httpx.Response(200, text="<html>не json</html>"), "некорректный ответ"),
        (httpx.Response(200, json=["не", "объект"]), "некорректный ответ"),
        (raise_timeout, "не ответил вовремя"),
        (raise_connect_error, "недоступен"),
    ],
)
async def test_api_failures(make_client, response, expected: str) -> None:
    with pytest.raises(MemeGatewayError, match=expected):
        await make_client(FakeMemeApi(response)).get_random_meme()
