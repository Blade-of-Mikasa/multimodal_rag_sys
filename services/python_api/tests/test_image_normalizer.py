from __future__ import annotations

from io import BytesIO
import unittest

from PIL import Image

from rag_api.images.domain import ImageNormalizationError
from rag_api.images.normalizer import ImageNormalizer


def encoded_image(format_name: str, *, size: tuple[int, int] = (20, 10)) -> bytes:
    output = BytesIO()
    Image.new("RGBA", size, (255, 0, 0, 128)).save(output, format=format_name)
    return output.getvalue()


class ImageNormalizerTest(unittest.TestCase):
    def normalizer(self, **overrides: int) -> ImageNormalizer:
        return ImageNormalizer(
            max_pixels=overrides.get("max_pixels", 10_000),
            max_dimension=overrides.get("max_dimension", 12),
            max_output_bytes=overrides.get("max_output_bytes", 100_000),
        )

    def test_decodes_signature_downscales_and_strips_to_safe_payload(self) -> None:
        normalized = self.normalizer().normalize(
            encoded_image("PNG"), "image/png; charset=binary"
        )

        self.assertEqual((20, 10), (normalized.width, normalized.height))
        self.assertEqual((12, 6), (normalized.model_width, normalized.model_height))
        self.assertEqual("image/png", normalized.media_type)
        with Image.open(BytesIO(normalized.payload)) as decoded:
            self.assertEqual((12, 6), decoded.size)
            self.assertNotIn("exif", decoded.info)

    def test_rejects_mime_mismatch_invalid_bytes_and_pixel_bombs(self) -> None:
        with self.assertRaises(ImageNormalizationError):
            self.normalizer().normalize(encoded_image("PNG"), "image/jpeg")
        with self.assertRaises(ImageNormalizationError):
            self.normalizer().normalize(b"not-an-image", "image/png")
        with self.assertRaises(ImageNormalizationError):
            self.normalizer(max_pixels=100).normalize(
                encoded_image("PNG", size=(20, 20)), "image/png"
            )

    def test_rejects_formats_outside_the_exact_allowlist(self) -> None:
        with self.assertRaises(ImageNormalizationError):
            self.normalizer().normalize(encoded_image("GIF"), "image/gif")


if __name__ == "__main__":
    unittest.main()
