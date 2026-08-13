"""Route one Kafka consumer group to modality-specific processors."""

from __future__ import annotations

from collections.abc import Mapping

from rag_api.kafka.contracts import IngestTaskEvent
from rag_api.kafka.domain import IngestProcessor, PermanentIngestError


class RoutingIngestProcessor:
    """Dispatch by normalized media type without competing consumer groups."""

    def __init__(self, processors: Mapping[str, IngestProcessor]) -> None:
        if not processors:
            raise ValueError("at least one media processor is required")
        self._processors = {
            media_type.lower(): processor
            for media_type, processor in processors.items()
        }

    async def process(self, event: IngestTaskEvent) -> None:
        media_type = event.content_type.partition(";")[0].strip().lower()
        processor = self._processors.get(media_type)
        if processor is None:
            raise PermanentIngestError(
                "UNSUPPORTED_MEDIA_TYPE",
                f"no ingestion processor is registered for {media_type}",
            )
        await processor.process(event)
