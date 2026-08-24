from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from PIL import Image

from rag_api.images.normalizer import ImageNormalizer
from rag_api.videos.domain import VideoMedia, VideoProcessingError
from rag_api.videos.ffmpeg import FFmpegVideoToolchain, _parse_timestamps


def jpeg_payload() -> bytes:
    output = BytesIO()
    Image.new("RGB", (32, 18), "blue").save(output, format="JPEG")
    return output.getvalue()


class FakeFFmpegToolchain(FFmpegVideoToolchain):
    def __init__(self, probe_payload: object) -> None:
        super().__init__(
            normalizer=ImageNormalizer(
                max_pixels=1_000_000,
                max_dimension=512,
                max_output_bytes=1_000_000,
            ),
            ffmpeg_binary="ffmpeg-test",
            ffprobe_binary="ffprobe-test",
            command_timeout_seconds=10,
            max_duration_seconds=1_000,
            max_pixels=2_000_000,
            max_dimension=4_096,
            audio_chunk_seconds=30,
            scene_threshold=0.35,
            keyframe_max_gap_seconds=60,
            max_keyframes=10,
        )
        self.probe_payload = probe_payload
        self.commands: list[tuple[str, ...]] = []

    async def _run(self, *command: str) -> tuple[str, str]:
        self.commands.append(command)
        if command[0] == "ffprobe-test":
            return json.dumps(self.probe_payload), ""
        output = Path(command[-1])
        if output.suffix == ".wav":
            output.write_bytes(b"wav")
        else:
            frame_dir = output.parent
            (frame_dir / "frame-000001.jpg").write_bytes(jpeg_payload())
            (frame_dir / "frame-000002.jpg").write_bytes(jpeg_payload())
            (frame_dir.parent / "keyframes.txt").write_text(
                "frame:0 pts:0 pts_time:0\n"
                "lavfi.scene_score=0.0\n"
                "frame:1 pts:90000 pts_time:60.0\n",
                encoding="utf-8",
            )
        return "", ""


def probe_payload(format_name: str = "mov,mp4,m4a,3gp,3g2,mj2") -> object:
    return {
        "format": {"format_name": format_name, "duration": "65.5"},
        "streams": [
            {"codec_type": "video", "width": 1280, "height": 720},
            {"codec_type": "audio"},
        ],
    }


class FFmpegVideoToolchainTest(unittest.IsolatedAsyncioTestCase):
    async def test_probe_validates_container_and_media_budgets(self) -> None:
        toolchain = FakeFFmpegToolchain(probe_payload())

        media = await toolchain.probe(Path("source.mp4"), "video/mp4")

        self.assertEqual(65_500, media.duration_ms)
        self.assertEqual((1280, 720), (media.width, media.height))
        self.assertTrue(media.has_audio)
        self.assertIn("format=format_name,duration:stream=codec_type,width,height", toolchain.commands[0])

        with self.assertRaises(VideoProcessingError):
            await FakeFFmpegToolchain(probe_payload("matroska,webm")).probe(
                Path("source.mp4"), "video/mp4"
            )

    async def test_audio_chunks_are_bounded_and_keep_global_offsets(self) -> None:
        toolchain = FakeFFmpegToolchain(probe_payload())
        media = VideoMedia(
            media_type="video/mp4",
            format_name="mov,mp4",
            duration_ms=65_500,
            width=1280,
            height=720,
            has_audio=True,
        )
        with TemporaryDirectory() as temporary:
            chunks = await toolchain.extract_audio_chunks(
                Path("source.mp4"), media, Path(temporary)
            )

            self.assertEqual(
                ((0, 30_000), (30_000, 30_000), (60_000, 5_500)),
                tuple((chunk.start_ms, chunk.duration_ms) for chunk in chunks),
            )
            self.assertTrue(all(chunk.path.is_file() for chunk in chunks))

    async def test_keyframes_have_scene_timestamps_and_safe_images(self) -> None:
        toolchain = FakeFFmpegToolchain(probe_payload())
        media = VideoMedia(
            media_type="video/mp4",
            format_name="mov,mp4",
            duration_ms=65_500,
            width=1280,
            height=720,
            has_audio=False,
        )
        with TemporaryDirectory() as temporary:
            frames = await toolchain.extract_keyframes(
                Path("source.mp4"), media, Path(temporary)
            )

            self.assertEqual((0, 60_000), tuple(frame.timestamp_ms for frame in frames))
            self.assertEqual("image/jpeg", frames[0].image.media_type)
            filter_value = toolchain.commands[0][
                toolchain.commands[0].index("-vf") + 1
            ]
            self.assertIn("gt(scene\\,0.35)", filter_value)
            self.assertIn("gte(t-prev_selected_t\\,60)", filter_value)

        incomplete = VideoMedia(
            media_type="video/mp4",
            format_name="mov,mp4",
            duration_ms=200_000,
            width=1280,
            height=720,
            has_audio=False,
        )
        with TemporaryDirectory() as temporary:
            with self.assertRaises(VideoProcessingError):
                await FakeFFmpegToolchain(probe_payload()).extract_keyframes(
                    Path("source.mp4"), incomplete, Path(temporary)
                )

    def test_timestamp_parser_rejects_duplicates_and_out_of_range_values(self) -> None:
        parsed = _parse_timestamps(
            "pts_time:0\npts_time:0\npts_time:1.25\npts_time:99\n",
            2_000,
        )
        self.assertEqual((0, 1_250), parsed)


if __name__ == "__main__":
    unittest.main()
