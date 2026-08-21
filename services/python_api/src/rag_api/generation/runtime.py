"""Runtime assembly for the provider-neutral answer pipeline."""

from __future__ import annotations

from rag_api.config import Settings
from rag_api.core_client import CoreClient
from rag_api.documents.embeddings import HttpEmbeddingModel
from rag_api.web.runtime import build_web_search_service

from .openai_responses import OpenAIResponsesChatModel
from .planner import ModelQueryPlanner
from .service import AnswerService


def build_answer_service(
    settings: Settings, core_client: CoreClient
) -> AnswerService:
    chat_model = OpenAIResponsesChatModel(
        endpoint_url=settings.chat_endpoint_url,
        api_key=(
            settings.chat_api_key.get_secret_value()
            if settings.chat_api_key is not None
            else None
        ),
        model_id=settings.chat_model_id,
        model_version=settings.chat_model_version,
        timeout_seconds=settings.chat_timeout_seconds,
    )
    embedding_model = HttpEmbeddingModel(
        endpoint_url=settings.embedding_endpoint_url,
        api_key=(
            settings.embedding_api_key.get_secret_value()
            if settings.embedding_api_key is not None
            else None
        ),
        model_id=settings.embedding_model_id,
        model_version=settings.embedding_model_version,
        dimension=settings.embedding_dimension,
        timeout_seconds=settings.embedding_timeout_seconds,
    )
    web_searcher = None
    if (
        settings.bing_foundry_responses_url is not None
        and settings.bing_foundry_model_deployment is not None
        and settings.bing_grounding_connection_id is not None
        and settings.bing_foundry_access_token is not None
    ):
        web_searcher = build_web_search_service(settings)
    return AnswerService(
        planner=ModelQueryPlanner(
            chat_model,
            max_output_tokens=settings.planner_max_output_tokens,
        ),
        embedding_model=embedding_model,
        core_client=core_client,
        chat_model=chat_model,
        web_searcher=web_searcher,
        local_top_k=settings.answer_local_top_k,
        local_timeout_ms=settings.answer_local_timeout_ms,
        web_result_count=settings.answer_web_result_count,
        context_token_budget=settings.answer_context_token_budget,
        max_evidence_tokens=settings.answer_max_evidence_tokens,
        max_output_tokens=settings.chat_max_output_tokens,
    )
