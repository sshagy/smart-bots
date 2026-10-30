import pytest
from pytest_mock import MockerFixture

from src.domain.entities import ImageReply, Meme, TextReply
from src.domain.gateways import IMemeGateway, MemeGatewayError
from src.use_cases.handle_mem import GetMemeUseCase

pytestmark = pytest.mark.asyncio


async def test_meme_is_sent_as_image(mocker: MockerFixture) -> None:
    gateway = mocker.AsyncMock(spec=IMemeGateway)
    gateway.get_random_meme.return_value = Meme(
        title="Clean code", image_url="https://i.redd.it/abc.jpg", subreddit="ProgrammerHumor"
    )

    replies = await GetMemeUseCase(gateway).execute()

    gateway.get_random_meme.assert_awaited_once_with()
    assert replies == [ImageReply(url="https://i.redd.it/abc.jpg", caption="Clean code")]


async def test_meme_api_failure_is_reported_as_text(mocker: MockerFixture) -> None:
    gateway = mocker.AsyncMock(spec=IMemeGateway)
    gateway.get_random_meme.side_effect = MemeGatewayError("сервис недоступен")

    replies = await GetMemeUseCase(gateway).execute()

    assert replies == [TextReply("😔 Не удалось получить мем: сервис недоступен")]
