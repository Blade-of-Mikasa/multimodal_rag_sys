"""Reliable Kafka ingestion primitives."""

from rag_api.kafka.contracts import DeadLetterEvent, IngestTaskEvent

__all__ = ["DeadLetterEvent", "IngestTaskEvent"]
