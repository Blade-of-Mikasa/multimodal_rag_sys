"""Deterministic parsers for the first document ingestion milestone."""

from __future__ import annotations

from io import BytesIO
import re

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from rag_api.documents.domain import DocumentParseError, ParsedBlock


PLAIN_TEXT_TYPES = {"text/plain"}
MARKDOWN_TYPES = {"text/markdown", "text/x-markdown"}
PDF_TYPES = {"application/pdf"}
HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


class DocumentParser:
    """Dispatch bytes by media type and preserve page/heading structure."""

    def parse(self, payload: bytes, content_type: str) -> tuple[ParsedBlock, ...]:
        media_type = content_type.partition(";")[0].strip().lower()
        if media_type in PDF_TYPES:
            return self._parse_pdf(payload)
        if media_type in MARKDOWN_TYPES:
            return self._parse_markdown(self._decode_text(payload))
        if media_type in PLAIN_TEXT_TYPES:
            text = self._decode_text(payload).strip()
            if not text:
                raise DocumentParseError("document contains no text")
            return (ParsedBlock(content=text),)
        raise DocumentParseError(f"unsupported document content type: {media_type}")

    @staticmethod
    def _decode_text(payload: bytes) -> str:
        try:
            return payload.decode("utf-8-sig").replace("\x00", "")
        except UnicodeDecodeError as error:
            raise DocumentParseError("text document must be UTF-8") from error

    @staticmethod
    def _parse_pdf(payload: bytes) -> tuple[ParsedBlock, ...]:
        try:
            reader = PdfReader(BytesIO(payload), strict=False)
            if reader.is_encrypted:
                raise DocumentParseError("encrypted PDF is not supported")
            blocks = tuple(
                ParsedBlock(
                    content=(page.extract_text() or "").replace("\x00", "").strip(),
                    title=f"Page {page_number}",
                    page_number=page_number,
                )
                for page_number, page in enumerate(reader.pages, start=1)
            )
        except DocumentParseError:
            raise
        except (PdfReadError, OSError, ValueError) as error:
            raise DocumentParseError("invalid PDF document") from error
        non_empty = tuple(block for block in blocks if block.content)
        if not non_empty:
            raise DocumentParseError("PDF contains no extractable text")
        return non_empty

    @staticmethod
    def _parse_markdown(text: str) -> tuple[ParsedBlock, ...]:
        blocks: list[ParsedBlock] = []
        title = ""
        lines: list[str] = []

        def flush() -> None:
            content = "\n".join(lines).strip()
            if content:
                blocks.append(ParsedBlock(content=content, title=title))
            lines.clear()

        for line in text.splitlines():
            heading = HEADING_PATTERN.match(line)
            if heading:
                flush()
                title = heading.group(2).strip()
                lines.append(line.strip())
            else:
                lines.append(line.rstrip())
        flush()
        if not blocks:
            raise DocumentParseError("Markdown document contains no text")
        return tuple(blocks)
