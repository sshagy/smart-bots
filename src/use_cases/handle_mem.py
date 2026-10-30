from __future__ import annotations

from src.domain.entities import ImageReply, Reply, TextReply
from src.domain.gateways import IMemeGateway, MemeGatewayError


class GetMemeUseCase:
    """/mem — случайная картинка из открытого meme-api."""

    def __init__(self, gateway: IMemeGateway) -> None:
        self._gateway = gateway

    async def execute(self) -> list[Reply]:
        try:
            meme = await self._gateway.get_random_meme()
        except MemeGatewayError as exc:
            return [TextReply(f"😔 Не удалось получить мем: {exc}")]
        return [ImageReply(url=meme.image_url, caption=meme.title)]
