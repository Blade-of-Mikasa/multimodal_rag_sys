from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import unittest

from rag_api.domain import Modality, SourceScope
from rag_api.web.domain import (
    ExtractionStatus,
    SourceTime,
    SourceTimeKind,
    WebSearchBundle,
    WebSource,
)
from rag_api.web.evidence import web_bundle_to_evidence


class WebEvidenceTest(unittest.TestCase):
    def test_maps_full_and_citation_only_sources_with_stable_provenance(self) -> None:
        observed_at = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
        published = SourceTime(
            kind=SourceTimeKind.PUBLISHED,
            value=datetime(2026, 8, 18, tzinfo=timezone.utc),
            source="json_ld.datePublished",
            precision="date",
        )
        text = "Official architecture documentation"
        bundle = WebSearchBundle(
            provider="microsoft-foundry-bing-grounding",
            query="RAG architecture",
            search_urls=("https://search.example/run/1",),
            grounded_text="Provider synthesis is deliberately excluded.",
            sources=(
                WebSource(
                    rank=1,
                    url="https://docs.example/architecture",
                    title="Architecture",
                    text=text,
                    status=ExtractionStatus.FULL,
                    fetched_at=observed_at,
                    content_sha256=sha256(text.encode()).hexdigest(),
                    published_time=published,
                ),
                WebSource(
                    rank=2,
                    url="https://news.example/result",
                    title="Result",
                    text="Quoted search snippet",
                    status=ExtractionStatus.CITATION_ONLY,
                    failure_code="ROBOTS_DENIED",
                ),
            ),
        )

        first = web_bundle_to_evidence(bundle, retrieved_at=observed_at)
        second = web_bundle_to_evidence(bundle, retrieved_at=observed_at)

        self.assertEqual(first, second)
        self.assertEqual(2, len(first))
        self.assertEqual(Modality.DOCUMENT, first[0].modality)
        self.assertEqual(SourceScope.WEB, first[0].source_scope)
        self.assertEqual("docs.example", first[0].source)
        self.assertEqual(1_787_011_200_000, first[0].published_at_unix_ms)
        self.assertEqual(1_787_227_200_000, first[0].retrieved_at_unix_ms)
        self.assertEqual([], first[0].validate())
        self.assertIn(("citation_only", "true"), first[1].metadata)
        self.assertIn(("failure_code", "ROBOTS_DENIED"), first[1].metadata)
        self.assertNotIn(bundle.grounded_text, tuple(item.content for item in first))

    def test_requires_an_aware_retrieval_time(self) -> None:
        bundle = WebSearchBundle(
            provider="provider",
            query="query",
            search_urls=(),
            grounded_text="",
            sources=(),
        )

        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            web_bundle_to_evidence(
                bundle, retrieved_at=datetime(2026, 8, 20)
            )


if __name__ == "__main__":
    unittest.main()
