from __future__ import annotations

import unittest

from rag_api.documents.chunking import DocumentChunker
from rag_api.documents.domain import DocumentParseError
from rag_api.documents.parsers import DocumentParser


class DocumentParsingTest(unittest.TestCase):
    def test_markdown_preserves_headings_and_chunks_are_stable(self) -> None:
        parser = DocumentParser()
        blocks = parser.parse(
            b"# Overview\n\nMilvus hybrid search.\n\n## Flow\n\nPython to C++.",
            "text/markdown; charset=utf-8",
        )

        self.assertEqual(["Overview", "Flow"], [block.title for block in blocks])
        chunker = DocumentChunker(max_chars=128, overlap_chars=16)
        first = chunker.chunk(asset_version_id="version-1", blocks=blocks)
        second = chunker.chunk(asset_version_id="version-1", blocks=blocks)

        self.assertEqual(first, second)
        self.assertEqual([0, 1], [chunk.ordinal for chunk in first])
        self.assertEqual(64, len(first[0].content_sha256))

    def test_long_text_is_bounded_and_overlapped(self) -> None:
        text = ("0123456789 " * 40).encode()
        blocks = DocumentParser().parse(text, "text/plain")
        chunks = DocumentChunker(max_chars=128, overlap_chars=16).chunk(
            asset_version_id="version-1", blocks=blocks
        )

        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk.content) <= 128 for chunk in chunks))
        self.assertIn(chunks[0].content[-8:], chunks[1].content)

    def test_unsupported_and_invalid_documents_are_rejected(self) -> None:
        parser = DocumentParser()
        with self.assertRaises(DocumentParseError):
            parser.parse(b"image", "image/png")
        with self.assertRaises(DocumentParseError):
            parser.parse(b"not-a-pdf", "application/pdf")
        with self.assertRaises(DocumentParseError):
            parser.parse(b"\xff", "text/plain")


if __name__ == "__main__":
    unittest.main()
