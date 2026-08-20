"""Maps extracted web sources into the C++ evidence-governance contract."""

from __future__ import annotations

from datetime import datetime
from hashlib import sha256
from urllib.parse import urlsplit

from ..domain import ExternalEvidence, Modality, SourceScope
from .domain import ExtractionStatus, SourceTime, WebSearchBundle, WebSource


def web_bundle_to_evidence(
    bundle: WebSearchBundle, *, retrieved_at: datetime
) -> tuple[ExternalEvidence, ...]:
    """Convert source material, excluding the provider's synthesized answer."""

    _require_aware(retrieved_at)
    return tuple(
        _source_to_evidence(bundle, source, retrieved_at=retrieved_at)
        for source in bundle.sources
    )


def _source_to_evidence(
    bundle: WebSearchBundle,
    source: WebSource,
    *,
    retrieved_at: datetime,
) -> ExternalEvidence:
    original_content = source.text.strip() or source.title.strip() or source.url
    content = _truncate_utf8(original_content, 1_000_000)
    content_sha256 = sha256(content.encode("utf-8")).hexdigest()
    stable_key = f"{source.url}\0{content_sha256}".encode("utf-8")
    source_time = source.published_time
    effective_retrieved_at = source.fetched_at or retrieved_at

    metadata: list[tuple[str, str]] = [
        ("provider", _truncate_utf8(bundle.provider.strip(), 16_384)),
        ("query", _truncate_utf8(bundle.query.strip(), 16_384)),
        ("rank", str(source.rank)),
        ("route_id", _truncate_utf8(f"web:{bundle.provider.strip()}", 16_384)),
        ("source_authority", "curated"),
        ("extraction_status", source.status.value),
        ("search_url_count", str(len(bundle.search_urls))),
    ]
    if source.failure_code is not None:
        metadata.append(("failure_code", source.failure_code))
    if source_time is not None:
        metadata.extend(_source_time_metadata("published", source_time))
    if source.modified_time is not None:
        metadata.extend(_source_time_metadata("modified", source.modified_time))
    if len(content.encode("utf-8")) < len(original_content.encode("utf-8")):
        metadata.append(("content_truncated_by_surface", "true"))
    if source.status is ExtractionStatus.CITATION_ONLY:
        metadata.append(("citation_only", "true"))

    return ExternalEvidence(
        evidence_id=f"web-{sha256(stable_key).hexdigest()[:40]}",
        content=content,
        modality=Modality.DOCUMENT,
        source_scope=SourceScope.WEB,
        title=_truncate_utf8(source.title.strip(), 4_096),
        source=(urlsplit(source.url).hostname or "").lower(),
        url=source.url,
        published_at_unix_ms=_unix_ms(source_time.value) if source_time else 0,
        retrieved_at_unix_ms=_unix_ms(effective_retrieved_at),
        score=1.0 / source.rank,
        metadata=tuple(metadata),
        content_sha256=content_sha256,
    )


def _source_time_metadata(
    prefix: str, source_time: SourceTime
) -> tuple[tuple[str, str], ...]:
    return (
        (f"{prefix}_time_source", source_time.source),
        (f"{prefix}_time_precision", source_time.precision),
        (f"{prefix}_timezone_assumed", str(source_time.timezone_assumed).lower()),
    )


def _unix_ms(value: datetime) -> int:
    _require_aware(value)
    return int(value.timestamp() * 1_000)


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("retrieved_at must be timezone-aware")


def _truncate_utf8(value: str, max_bytes: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    return encoded[:max_bytes].decode("utf-8", errors="ignore")
