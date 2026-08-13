from __future__ import annotations

import unittest

from rag_api.images.domain import NormalizedImage, VisionError
from rag_api.images.vision import HttpVisionModel


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


IMAGE = NormalizedImage(
    payload=b"png",
    media_type="image/png",
    width=800,
    height=600,
    model_width=800,
    model_height=600,
)


class VisionAdapterTest(unittest.IsolatedAsyncioTestCase):
    def adapter(self, session: FakeSession) -> HttpVisionModel:
        return HttpVisionModel(
            endpoint_url="https://models.example/v1/responses",
            api_key="secret",
            model_id="vision-general",
            model_version="2026-08",
            timeout_seconds=5,
            caption_max_bytes=8,
            ocr_max_bytes=8,
            session=session,
        )

    async def test_uses_structured_image_input_and_bounds_utf8_output(self) -> None:
        session = FakeSession(
            FakeResponse(
                200,
                {
                    "output": [
                        {
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": (
                                        '{"caption":"自行车商店",'
                                        '"ocr_text":"营业时间很长"}'
                                    ),
                                }
                            ]
                        }
                    ]
                },
            )
        )

        analysis = await self.adapter(session).analyze(IMAGE)

        self.assertLessEqual(len(analysis.caption.encode("utf-8")), 8)
        self.assertLessEqual(len(analysis.ocr_text.encode("utf-8")), 8)
        request = session.call[1]["json"]
        image_input = request["input"][0]["content"][1]
        self.assertTrue(image_input["image_url"].startswith("data:image/png;base64,"))
        self.assertTrue(request["text"]["format"]["strict"])
        self.assertEqual(
            "Bearer secret", session.call[1]["headers"]["Authorization"]
        )

    async def test_http_and_contract_errors_preserve_retry_semantics(self) -> None:
        with self.assertRaises(VisionError) as retryable:
            await self.adapter(FakeSession(FakeResponse(429, {}, "busy"))).analyze(
                IMAGE
            )
        self.assertTrue(retryable.exception.retryable)

        with self.assertRaises(VisionError) as permanent:
            await self.adapter(
                FakeSession(FakeResponse(200, {"output_text": "not-json"}))
            ).analyze(IMAGE)
        self.assertFalse(permanent.exception.retryable)


if __name__ == "__main__":
    unittest.main()
