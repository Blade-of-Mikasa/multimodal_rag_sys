from __future__ import annotations

import unittest

from rag_api.config import Settings
from rag_api.web.runtime import build_web_search_service
from rag_api.web.service import WebSearchService


class FakeTokenProvider:
    async def get_token(self) -> str:
        return "managed-token"


class WebSearchRuntimeTest(unittest.TestCase):
    def test_requires_connection_configuration_only_when_service_is_built(self) -> None:
        settings = Settings(_env_file=None)

        with self.assertRaisesRegex(ValueError, "Bing Grounding configuration"):
            build_web_search_service(settings)

    def test_builds_with_static_or_injected_token_provider(self) -> None:
        common = {
            "bing_foundry_responses_url": "https://project.ai.azure.com/openai/v1/responses",
            "bing_foundry_model_deployment": "grounding-model",
            "bing_grounding_connection_id": "/subscriptions/s/connections/bing",
            "_env_file": None,
        }
        static_service = build_web_search_service(
            Settings(bing_foundry_access_token="short-lived-token", **common)
        )
        managed_service = build_web_search_service(
            Settings(**common), token_provider=FakeTokenProvider()
        )

        self.assertIsInstance(static_service, WebSearchService)
        self.assertIsInstance(managed_service, WebSearchService)


if __name__ == "__main__":
    unittest.main()
