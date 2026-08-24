"""Stable structure-aware character chunking for generic embedding APIs."""

from __future__ import annotations

from hashlib import sha256
from uuid import UUID, uuid5

from rag_api.documents.domain import DocumentChunk, ParsedBlock
from rag_api.ingestion.domain import truncate_utf8


CHUNK_ID_NAMESPACE = UUID("21076b8e-52f2-4f29-b106-55ab218e0c19")


class DocumentChunker:
    def __init__(self, *, max_chars: int, overlap_chars: int) -> None:
        if max_chars < 128:
            raise ValueError("max_chars must be at least 128")
        if not 0 <= overlap_chars < max_chars:
            raise ValueError("overlap_chars must be smaller than max_chars")
        self._max_chars = max_chars
        self._overlap_chars = overlap_chars

    def chunk(
        self,
        *,
        asset_version_id: str,
        blocks: tuple[ParsedBlock, ...],
    ) -> tuple[DocumentChunk, ...]:
        chunks: list[DocumentChunk] = []
        for block in blocks:
            text = "\n".join(
                line.rstrip() for line in block.content.replace("\r\n", "\n").split("\n")
            ).strip()
            start = 0
            while start < len(text):
                end = min(start + self._max_chars, len(text))
                if end < len(text):
                    boundary = max(
                        text.rfind("\n", start + self._max_chars // 2, end),
                        text.rfind(" ", start + self._max_chars // 2, end),
                    )
                    if boundary > start:
                        end = boundary
                content = text[start:end].strip()
                if content:
                    ordinal = len(chunks)
                    digest = sha256(content.encode("utf-8")).hexdigest()
                    chunk_id = str(
                        uuid5(
                            CHUNK_ID_NAMESPACE,
                            f"{asset_version_id}:{ordinal}:{digest}",
                        )
                    )
                    chunks.append(
                        DocumentChunk(
                            chunk_id=chunk_id,
                            ordinal=ordinal,
                            page_number=block.page_number,
                            title=truncate_utf8(block.title, 2_048),
                            content=content,
                            content_sha256=digest,
                        )
                    )
                if end >= len(text):
                    break
                next_start = max(start + 1, end - self._overlap_chars)
                start = next_start
        if not chunks:
            raise ValueError("document produced no non-empty chunks")
        return tuple(chunks)
