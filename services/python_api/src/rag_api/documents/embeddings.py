"""OpenAI-compatible HTTP adapter behind the generic embedding port."""

from __future__ import annotations

from contextlib import asynccontextmanager
from math import isfinite
from typing import AsyncIterator, Sequence

import aiohttp

from rag_api.documents.domain import EmbeddingError


class HttpEmbeddingModel:
    def __init__(
        self,
        *,
        endpoint_url: str,
        model_id: str,
        model_version: str,
        dimension: int,
        timeout_seconds: float,
        api_key: str | None = None,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self._endpoint_url = endpoint_url
        self._model_id = model_id
        self._model_version = model_version
        self._dimension = dimension
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._api_key = api_key
        self._session = session

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def model_version(self) -> str:
        return self._model_version

    @property
    def dimension(self) -> int:
        return self._dimension

    async def embed(
        self, texts: Sequence[str]
    ) -> tuple[tuple[float, ...], ...]:
        if not texts or any(not text for text in texts):
            raise ValueError("embedding input must contain non-empty text")
        headers = {}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        try:
            async with self._session_scope() as session:
                async with session.post(
                    self._endpoint_url,
                    json={"model": self._model_id, "input": list(texts)},
                    headers=headers,
                    timeout=self._timeout,
                ) as response:
                    if response.status < 200 or response.status >= 300:
                        detail = (await response.text())[:500]
                        retryable = response.status in {408, 409, 425, 429} or (
                            response.status >= 500
                        )
                        raise EmbeddingError(
                            f"embedding endpoint returned {response.status}: {detail}",
                            retryable=retryable,
                        )
                    payload = await response.json(content_type=None)
        except EmbeddingError:
            raise
        except (aiohttp.ClientError, TimeoutError) as error:
            raise EmbeddingError(
                f"embedding endpoint unavailable: {type(error).__name__}",
                retryable=True,
            ) from error

        try:
            data = payload["data"]
            indexed: dict[int, tuple[float, ...]] = {}
            for item in data:
                index = item["index"]
                if isinstance(index, bool) or not isinstance(index, int):
                    raise TypeError("embedding index must be an integer")
                vector = tuple(float(value) for value in item["embedding"])
                if index in indexed:
                    raise ValueError("embedding response contains duplicate index")
                if len(vector) != self._dimension or any(
                    not isfinite(value) for value in vector
                ):
                    raise ValueError("embedding dimension or values are invalid")
                indexed[index] = vector
            if set(indexed) != set(range(len(texts))):
                raise ValueError("embedding response indices do not match inputs")
            return tuple(indexed[index] for index in range(len(texts)))
        except (KeyError, TypeError, ValueError) as error:
            raise EmbeddingError(
                f"invalid embedding response: {error}", retryable=False
            ) from error

    @asynccontextmanager
    async def _session_scope(self) -> AsyncIterator[aiohttp.ClientSession]:
        if self._session is not None:
            yield self._session
            return
        async with aiohttp.ClientSession() as session:
            yield session
