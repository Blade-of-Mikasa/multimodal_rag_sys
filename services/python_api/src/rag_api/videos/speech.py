"""OpenAI-compatible transcription adapter behind SpeechToTextModel."""

from __future__ import annotations

from contextlib import asynccontextmanager
from math import isfinite
from typing import Any, AsyncIterator

import aiohttp

from rag_api.ingestion.domain import truncate_utf8
from rag_api.videos.domain import AudioChunk, SpeechToTextError, TranscriptSegment


class HttpSpeechToTextModel:
    def __init__(
        self,
        *,
        endpoint_url: str,
        model_id: str,
        model_version: str,
        timeout_seconds: float,
        max_segments: int,
        language: str | None = None,
        api_key: str | None = None,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self._endpoint_url = endpoint_url
        self._model_id = model_id
        self._model_version = model_version
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._max_segments = max_segments
        self._language = language
        self._api_key = api_key
        self._session = session

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def model_version(self) -> str:
        return self._model_version

    async def transcribe(self, audio: AudioChunk) -> tuple[TranscriptSegment, ...]:
        headers: dict[str, str] = {}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        try:
            with audio.path.open("rb") as source:
                form = aiohttp.FormData()
                form.add_field("model", self._model_id)
                form.add_field("response_format", "verbose_json")
                form.add_field("timestamp_granularities[]", "segment")
                if self._language:
                    form.add_field("language", self._language)
                form.add_field(
                    "file",
                    source,
                    filename=audio.path.name,
                    content_type="audio/wav",
                )
                async with self._session_scope() as session:
                    async with session.post(
                        self._endpoint_url,
                        data=form,
                        headers=headers,
                        timeout=self._timeout,
                    ) as response:
                        if response.status < 200 or response.status >= 300:
                            detail = (await response.text())[:500]
                            retryable = response.status in {408, 409, 425, 429} or (
                                response.status >= 500
                            )
                            raise SpeechToTextError(
                                f"speech endpoint returned {response.status}: {detail}",
                                retryable=retryable,
                            )
                        payload = await response.json(content_type=None)
        except SpeechToTextError:
            raise
        except OSError as error:
            raise SpeechToTextError(
                "local audio chunk is unavailable", retryable=False
            ) from error
        except (aiohttp.ClientError, TimeoutError) as error:
            raise SpeechToTextError(
                f"speech endpoint unavailable: {type(error).__name__}",
                retryable=True,
            ) from error
        except ValueError as error:
            raise SpeechToTextError(
                "speech endpoint returned invalid JSON", retryable=False
            ) from error
        return self._parse_segments(payload, audio)

    def _parse_segments(
        self, payload: Any, audio: AudioChunk
    ) -> tuple[TranscriptSegment, ...]:
        if not isinstance(payload, dict):
            raise SpeechToTextError(
                "speech response must be an object", retryable=False
            )
        raw_segments = payload.get("segments")
        if not isinstance(raw_segments, list):
            raise SpeechToTextError(
                "speech response contains no segment timestamps", retryable=False
            )
        if len(raw_segments) > self._max_segments:
            raise SpeechToTextError(
                "speech response exceeded the segment limit", retryable=False
            )
        parsed: list[TranscriptSegment] = []
        previous_start = -1.0
        for item in raw_segments:
            try:
                start_seconds = float(item["start"])
                end_seconds = float(item["end"])
                text = item["text"]
            except (KeyError, TypeError, ValueError) as error:
                raise SpeechToTextError(
                    "speech response contains an invalid segment", retryable=False
                ) from error
            if (
                not isfinite(start_seconds)
                or not isfinite(end_seconds)
                or start_seconds < 0
                or end_seconds <= start_seconds
                or start_seconds < previous_start
                or end_seconds * 1000 > audio.duration_ms + 1_000
                or not isinstance(text, str)
            ):
                raise SpeechToTextError(
                    "speech response contains an invalid timestamp", retryable=False
                )
            previous_start = start_seconds
            text = truncate_utf8(text.strip(), 8_192)
            if not text:
                continue
            parsed.append(
                TranscriptSegment(
                    start_ms=audio.start_ms + round(start_seconds * 1000),
                    end_ms=audio.start_ms + round(end_seconds * 1000),
                    text=text,
                )
            )
        return tuple(parsed)

    @asynccontextmanager
    async def _session_scope(self) -> AsyncIterator[aiohttp.ClientSession]:
        if self._session is not None:
            yield self._session
            return
        async with aiohttp.ClientSession() as session:
            yield session
