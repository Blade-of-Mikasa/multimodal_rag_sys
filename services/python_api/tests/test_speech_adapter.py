from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from rag_api.videos.domain import AudioChunk, SpeechToTextError
from rag_api.videos.speech import HttpSpeechToTextModel


class FakeResponse:
    def __init__(self, status: int, payload: object, text: str = "") -> None:
        self.status = status
        self.payload = payload
        self.body_text = text

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        pass

    async def json(self, *, content_type=None):
        return self.payload

    async def text(self) -> str:
        return self.body_text


class FakeSession:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.call = None

    def post(self, url: str, **kwargs) -> FakeResponse:
        self.call = (url, kwargs)
        return self.response


class SpeechAdapterTest(unittest.IsolatedAsyncioTestCase):
    def adapter(self, session: FakeSession) -> HttpSpeechToTextModel:
        return HttpSpeechToTextModel(
            endpoint_url="https://models.example/v1/audio/transcriptions",
            api_key="secret",
            model_id="speech-general",
            model_version="2026-08",
            language="zh",
            timeout_seconds=20,
            max_segments=10,
            session=session,
        )

    async def test_maps_segment_timestamps_to_the_original_video(self) -> None:
        session = FakeSession(
            FakeResponse(
                200,
                {
                    "segments": [
                        {"start": 0.5, "end": 2.25, "text": " 混合检索 "},
                        {"start": 2.25, "end": 4.0, "text": "RRF 融合"},
                    ]
                },
            )
        )
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "audio.wav"
            path.write_bytes(b"wav")
            segments = await self.adapter(session).transcribe(
                AudioChunk(path=path, start_ms=30_000, duration_ms=10_000)
            )

        self.assertEqual(
            ((30_500, 32_250, "混合检索"), (32_250, 34_000, "RRF 融合")),
            tuple((item.start_ms, item.end_ms, item.text) for item in segments),
        )
        self.assertEqual(
            "Bearer secret", session.call[1]["headers"]["Authorization"]
        )
        self.assertIsNotNone(session.call[1]["data"])

    async def test_http_and_missing_timestamps_keep_retry_semantics(self) -> None:
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "audio.wav"
            path.write_bytes(b"wav")
            chunk = AudioChunk(path=path, start_ms=0, duration_ms=10_000)
            with self.assertRaises(SpeechToTextError) as retryable:
                await self.adapter(
                    FakeSession(FakeResponse(429, {}, "busy"))
                ).transcribe(chunk)
            self.assertTrue(retryable.exception.retryable)

            with self.assertRaises(SpeechToTextError) as permanent:
                await self.adapter(
                    FakeSession(FakeResponse(200, {"text": "no timestamps"}))
                ).transcribe(chunk)
            self.assertFalse(permanent.exception.retryable)


if __name__ == "__main__":
    unittest.main()
