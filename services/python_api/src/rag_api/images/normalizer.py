"""Strict still-image decoding and metadata-free model payload generation."""

from __future__ import annotations

from io import BytesIO
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError

from rag_api.images.domain import ImageNormalizationError, NormalizedImage


FORMAT_MEDIA_TYPES = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
}
ALLOWED_PIL_FORMATS = tuple(FORMAT_MEDIA_TYPES)
SUPPORTED_IMAGE_MEDIA_TYPES = frozenset(FORMAT_MEDIA_TYPES.values())


class ImageNormalizer:
    def __init__(
        self,
        *,
        max_pixels: int,
        max_dimension: int,
        max_output_bytes: int,
    ) -> None:
        if max_pixels <= 0 or max_dimension <= 0 or max_output_bytes <= 0:
            raise ValueError("image normalization limits must be positive")
        self._max_pixels = max_pixels
        self._max_dimension = max_dimension
        self._max_output_bytes = max_output_bytes

    def normalize(self, payload: bytes, declared_media_type: str) -> NormalizedImage:
        media_type = declared_media_type.partition(";")[0].strip().lower()
        if media_type not in SUPPORTED_IMAGE_MEDIA_TYPES:
            raise ImageNormalizationError(
                f"unsupported image content type: {media_type}"
            )
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(BytesIO(payload), formats=ALLOWED_PIL_FORMATS) as probe:
                    detected_media_type = FORMAT_MEDIA_TYPES.get(probe.format or "")
                    if detected_media_type != media_type:
                        raise ImageNormalizationError(
                            "declared image content type does not match file signature"
                        )
                    if getattr(probe, "n_frames", 1) != 1:
                        raise ImageNormalizationError(
                            "animated or multi-frame images are not supported"
                        )
                    width, height = probe.size
                    self._validate_dimensions(width, height)
                    probe.verify()

                with Image.open(
                    BytesIO(payload), formats=ALLOWED_PIL_FORMATS
                ) as decoded:
                    decoded.load()
                    normalized = ImageOps.exif_transpose(decoded)
                    width, height = normalized.size
                    self._validate_dimensions(width, height)
                    normalized.thumbnail(
                        (self._max_dimension, self._max_dimension),
                        Image.Resampling.LANCZOS,
                    )
                    model_width, model_height = normalized.size
                    encoded_payload, encoded_media_type = self._encode(
                        normalized, detected_media_type
                    )
        except ImageNormalizationError:
            raise
        except (
            Image.DecompressionBombError,
            Image.DecompressionBombWarning,
            UnidentifiedImageError,
            OSError,
            SyntaxError,
            ValueError,
        ) as error:
            raise ImageNormalizationError("invalid or unsafe image") from error

        return NormalizedImage(
            payload=encoded_payload,
            media_type=encoded_media_type,
            width=width,
            height=height,
            model_width=model_width,
            model_height=model_height,
        )

    def _validate_dimensions(self, width: int, height: int) -> None:
        if width <= 0 or height <= 0 or width * height > self._max_pixels:
            raise ImageNormalizationError("image dimensions exceed the pixel budget")

    def _encode(self, image: Image.Image, source_media_type: str) -> tuple[bytes, str]:
        if source_media_type in {"image/png", "image/webp"}:
            png_output = BytesIO()
            image.save(png_output, format="PNG", optimize=False)
            if png_output.tell() <= self._max_output_bytes:
                return png_output.getvalue(), "image/png"

        rgb = self._composite_rgb(image)
        for quality in (90, 80, 70):
            jpeg_output = BytesIO()
            rgb.save(
                jpeg_output,
                format="JPEG",
                quality=quality,
                optimize=False,
                progressive=False,
            )
            if jpeg_output.tell() <= self._max_output_bytes:
                return jpeg_output.getvalue(), "image/jpeg"
        raise ImageNormalizationError("normalized image exceeds the model byte budget")

    @staticmethod
    def _composite_rgb(image: Image.Image) -> Image.Image:
        if image.mode in {"RGBA", "LA"} or (
            image.mode == "P" and "transparency" in image.info
        ):
            rgba = image.convert("RGBA")
            background = Image.new("RGBA", rgba.size, "white")
            return Image.alpha_composite(background, rgba).convert("RGB")
        return image.convert("RGB")
