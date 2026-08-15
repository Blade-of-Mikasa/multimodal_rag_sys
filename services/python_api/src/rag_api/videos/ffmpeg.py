"""Bounded FFprobe/FFmpeg adapter for video normalization."""

from __future__ import annotations

import asyncio
import json
from math import isfinite
from pathlib import Path
import re

from rag_api.images.domain import ImageNormalizationError
from rag_api.images.normalizer import ImageNormalizer
from rag_api.videos.domain import (
    AudioChunk,
    Keyframe,
    VideoMedia,
    VideoProcessingError,
)


_TIMESTAMP_PATTERN = re.compile(r"(?:^|\s)pts_time:([-+0-9.eE]+)")
_CONTAINER_FORMATS = {
    "video/mp4": {"mov", "mp4", "m4a", "3gp", "3g2", "mj2"},
    "video/quicktime": {"mov", "mp4", "m4a", "3gp", "3g2", "mj2"},
    "video/webm": {"matroska", "webm"},
}
_COMMAND_OUTPUT_MAX_BYTES = 1_000_000
_AUDIO_CHUNK_MAX_BYTES = 25_000_000


class _CommandOutputTooLarge(RuntimeError):
    pass


class FFmpegVideoToolchain:
    def __init__(
        self,
        *,
        normalizer: ImageNormalizer,
        ffmpeg_binary: str,
        ffprobe_binary: str,
        command_timeout_seconds: float,
        max_duration_seconds: int,
        max_pixels: int,
        max_dimension: int,
        audio_chunk_seconds: int,
        scene_threshold: float,
        keyframe_max_gap_seconds: int,
        max_keyframes: int,
    ) -> None:
        self._normalizer = normalizer
        self._ffmpeg = ffmpeg_binary
        self._ffprobe = ffprobe_binary
        self._timeout = command_timeout_seconds
        self._max_duration_ms = max_duration_seconds * 1000
        self._max_pixels = max_pixels
        self._max_dimension = max_dimension
        self._audio_chunk_ms = audio_chunk_seconds * 1000
        self._scene_threshold = scene_threshold
        self._keyframe_gap_seconds = keyframe_max_gap_seconds
        self._max_keyframes = max_keyframes

    async def probe(self, source: Path, media_type: str) -> VideoMedia:
        stdout, _ = await self._run(
            self._ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=format_name,duration:stream=codec_type,width,height",
            "-of",
            "json",
            str(source),
        )
        try:
            payload = json.loads(stdout)
            format_info = payload["format"]
            if not isinstance(format_info, dict):
                raise TypeError("format must be an object")
            format_name = str(format_info["format_name"])
            duration_seconds = float(format_info["duration"])
            streams = payload["streams"]
            if not isinstance(streams, list):
                raise TypeError("streams must be an array")
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise VideoProcessingError(
                "ffprobe returned invalid video metadata", retryable=False
            ) from error
        allowed_formats = _CONTAINER_FORMATS.get(media_type)
        detected_formats = set(format_name.split(","))
        if not allowed_formats or not detected_formats.intersection(allowed_formats):
            raise VideoProcessingError(
                "video container does not match the declared media type",
                retryable=False,
            )
        video_streams = [
            stream
            for stream in streams
            if isinstance(stream, dict) and stream.get("codec_type") == "video"
        ]
        if len(video_streams) != 1:
            raise VideoProcessingError(
                "video must contain exactly one video stream", retryable=False
            )
        try:
            width = int(video_streams[0]["width"])
            height = int(video_streams[0]["height"])
        except (KeyError, TypeError, ValueError) as error:
            raise VideoProcessingError(
                "video dimensions are unavailable", retryable=False
            ) from error
        duration_ms = round(duration_seconds * 1000)
        if (
            not isfinite(duration_seconds)
            or not 1 <= duration_ms <= self._max_duration_ms
            or width <= 0
            or height <= 0
            or width > self._max_dimension
            or height > self._max_dimension
            or width * height > self._max_pixels
        ):
            raise VideoProcessingError(
                "video duration or dimensions exceed the ingestion budget",
                retryable=False,
            )
        return VideoMedia(
            media_type=media_type,
            format_name=format_name,
            duration_ms=duration_ms,
            width=width,
            height=height,
            has_audio=any(
                isinstance(stream, dict) and stream.get("codec_type") == "audio"
                for stream in streams
            ),
        )

    async def extract_audio_chunks(
        self, source: Path, media: VideoMedia, output_dir: Path
    ) -> tuple[AudioChunk, ...]:
        if not media.has_audio:
            return ()
        chunks: list[AudioChunk] = []
        for index, start_ms in enumerate(
            range(0, media.duration_ms, self._audio_chunk_ms)
        ):
            duration_ms = min(self._audio_chunk_ms, media.duration_ms - start_ms)
            path = output_dir / f"audio-{index:04d}.wav"
            await self._run(
                self._ffmpeg,
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                _seconds(start_ms),
                "-t",
                _seconds(duration_ms),
                "-i",
                str(source),
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "pcm_s16le",
                "-y",
                str(path),
            )
            if (
                not path.is_file()
                or path.stat().st_size == 0
                or path.stat().st_size > _AUDIO_CHUNK_MAX_BYTES
            ):
                raise VideoProcessingError(
                    "FFmpeg produced an invalid audio chunk", retryable=False
                )
            chunks.append(
                AudioChunk(path=path, start_ms=start_ms, duration_ms=duration_ms)
            )
        return tuple(chunks)

    async def extract_keyframes(
        self, source: Path, media: VideoMedia, output_dir: Path
    ) -> tuple[Keyframe, ...]:
        frame_dir = output_dir / "frames"
        frame_dir.mkdir()
        metadata_path = output_dir / "keyframes.txt"
        select = (
            "select=eq(n\\,0)+gt(scene\\,"
            f"{self._scene_threshold:g})+gte(t-prev_selected_t\\,"
            f"{self._keyframe_gap_seconds}),"
            "scale=1280:1280:force_original_aspect_ratio=decrease,"
            f"metadata=print:file={metadata_path}"
        )
        await self._run(
            self._ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-map",
            "0:v:0",
            "-an",
            "-vf",
            select,
            "-fps_mode",
            "vfr",
            "-frames:v",
            str(self._max_keyframes),
            "-q:v",
            "3",
            "-y",
            str(frame_dir / "frame-%06d.jpg"),
        )
        paths = tuple(sorted(frame_dir.glob("frame-*.jpg")))
        try:
            metadata = metadata_path.read_text(encoding="utf-8")
        except OSError as error:
            raise VideoProcessingError(
                "FFmpeg did not produce keyframe timestamps", retryable=False
            ) from error
        timestamps = _parse_timestamps(metadata, media.duration_ms)
        if not paths or len(paths) != len(timestamps):
            raise VideoProcessingError(
                "FFmpeg keyframes and timestamps do not match", retryable=False
            )
        maximum_gap_ms = self._keyframe_gap_seconds * 1000 + 5_000
        boundaries = (0, *timestamps, media.duration_ms)
        if any(
            right - left > maximum_gap_ms
            for left, right in zip(boundaries, boundaries[1:])
        ):
            raise VideoProcessingError(
                "video exhausted the keyframe budget before covering its duration",
                retryable=False,
            )
        keyframes: list[Keyframe] = []
        for path, timestamp_ms in zip(paths, timestamps, strict=True):
            try:
                payload = path.read_bytes()
                image = self._normalizer.normalize(payload, "image/jpeg")
            except (ImageNormalizationError, OSError) as error:
                raise VideoProcessingError(
                    "extracted keyframe is not a valid bounded image",
                    retryable=False,
                ) from error
            keyframes.append(Keyframe(timestamp_ms=timestamp_ms, image=image))
        return tuple(keyframes)

    async def _run(self, *command: str) -> tuple[str, str]:
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except (FileNotFoundError, PermissionError) as error:
            raise VideoProcessingError(
                f"video binary is unavailable: {command[0]}", retryable=False
            ) from error
        stdout_task = asyncio.create_task(_read_bounded(process.stdout))
        stderr_task = asyncio.create_task(_read_bounded(process.stderr))
        try:
            return_code, stdout, stderr = await asyncio.wait_for(
                asyncio.gather(process.wait(), stdout_task, stderr_task),
                timeout=self._timeout,
            )
        except TimeoutError as error:
            await _stop_process(process, stdout_task, stderr_task)
            raise VideoProcessingError(
                "video processing command timed out", retryable=True
            ) from error
        except _CommandOutputTooLarge as error:
            await _stop_process(process, stdout_task, stderr_task)
            raise VideoProcessingError(
                "video processing command output exceeded its byte budget",
                retryable=False,
            ) from error
        except BaseException:
            await _stop_process(process, stdout_task, stderr_task)
            raise
        if return_code != 0:
            detail = stderr.decode("utf-8", errors="replace")[-1_000:]
            raise VideoProcessingError(
                f"video processing command failed: {detail}", retryable=False
            )
        return (
            stdout.decode("utf-8", errors="strict"),
            stderr.decode("utf-8", errors="replace"),
        )


async def _read_bounded(stream: asyncio.StreamReader | None) -> bytes:
    if stream is None:
        return b""
    output = bytearray()
    while True:
        chunk = await stream.read(64 * 1024)
        if not chunk:
            return bytes(output)
        if len(output) + len(chunk) > _COMMAND_OUTPUT_MAX_BYTES:
            raise _CommandOutputTooLarge
        output.extend(chunk)


async def _stop_process(
    process: asyncio.subprocess.Process,
    *tasks: asyncio.Task[bytes],
) -> None:
    if process.returncode is None:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        await process.wait()
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


def _seconds(milliseconds: int) -> str:
    return f"{milliseconds / 1000:.3f}"


def _parse_timestamps(metadata: str, duration_ms: int) -> tuple[int, ...]:
    timestamps: list[int] = []
    for match in _TIMESTAMP_PATTERN.finditer(metadata):
        try:
            seconds = float(match.group(1))
        except ValueError:
            continue
        timestamp_ms = round(seconds * 1000)
        if (
            isfinite(seconds)
            and 0 <= timestamp_ms < duration_ms
            and (not timestamps or timestamp_ms > timestamps[-1])
        ):
            timestamps.append(timestamp_ms)
    return tuple(timestamps)
