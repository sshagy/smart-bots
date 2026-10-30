"""Шлюз к открытому API мемов (https://github.com/D3vd/Meme_Api) для команды /mem."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

import httpx

from src.domain.entities import Meme
from src.domain.gateways import IMemeGateway, MemeGatewayError

DEFAULT_MEME_API_URL = "https://meme-api.com/gimme"
_IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".gif", ".webp")


class MemeApiClient(IMemeGateway):
    def __init__(
        self,
        http: httpx.AsyncClient,
        url: str = DEFAULT_MEME_API_URL,
        *,
        max_attempts: int = 3,
    ) -> None:
        self._http = http
        self._url = url
        self._max_attempts = max_attempts

    async def get_random_meme(self) -> Meme:
        # Случайный мем бывает NSFW, спойлером или видео — тогда просто берём следующий.
        for _ in range(self._max_attempts):
            for item in await self._fetch():
                if _is_suitable(item):
                    return Meme(
                        title=str(item.get("title") or "Мем"),
                        image_url=item["url"],
                        post_link=item.get("postLink"),
                        subreddit=item.get("subreddit"),
                    )
        raise MemeGatewayError("не нашлось подходящей картинки, попробуйте ещё раз")

    async def _fetch(self) -> list[dict[str, Any]]:
        try:
            response = await self._http.get(self._url)
            response.raise_for_status()
            payload = response.json()
        except httpx.TimeoutException as exc:
            raise MemeGatewayError("сервис мемов не ответил вовремя") from exc
        except httpx.HTTPStatusError as exc:
            raise MemeGatewayError(
                f"сервис мемов вернул ошибку {exc.response.status_code}"
            ) from exc
        except httpx.HTTPError as exc:
            raise MemeGatewayError("сервис мемов недоступен") from exc
        except ValueError as exc:  # тело — не JSON
            raise MemeGatewayError("сервис мемов вернул некорректный ответ") from exc

        if not isinstance(payload, dict):
            raise MemeGatewayError("сервис мемов вернул некорректный ответ")
        # /gimme отдаёт один мем, /gimme/{count} — {"count": N, "memes": [...]}
        memes = payload.get("memes")
        if isinstance(memes, list):
            return [item for item in memes if isinstance(item, dict)]
        return [payload]


def _is_suitable(item: dict[str, Any]) -> bool:
    url = item.get("url")
    if not isinstance(url, str) or not url.startswith("https://"):
        return False
    if item.get("nsfw") or item.get("spoiler"):
        return False
    return urlsplit(url).path.lower().endswith(_IMAGE_EXTENSIONS)
