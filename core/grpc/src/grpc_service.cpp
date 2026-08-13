#include "rag_core/grpc_service.h"

#include <charconv>
#include <cstdint>
#include <limits>
#include <string>
#include <utility>
#include <vector>

namespace multimodal::rag::core {
namespace {

grpc::Status StoreErrorStatus(const DocumentStoreError &error) {
  return {error.retryable() ? grpc::StatusCode::UNAVAILABLE
                            : grpc::StatusCode::INVALID_ARGUMENT,
          error.what()};
}

grpc::Status StoreErrorStatus(const ImageStoreError &error) {
  return {error.retryable() ? grpc::StatusCode::UNAVAILABLE
                            : grpc::StatusCode::INVALID_ARGUMENT,
          error.what()};
}

std::string Metadata(const multimodal::rag::v1::NormalizedUnit &unit,
                     const std::string &key) {
  const auto item = unit.metadata().find(key);
  return item == unit.metadata().end() ? std::string{} : item->second;
}

bool ParsePositiveUint32(const std::string &value, std::uint32_t &output) {
  std::uint64_t parsed = 0;
  const auto [end, error] =
      std::from_chars(value.data(), value.data() + value.size(), parsed);
  if (error != std::errc{} || end != value.data() + value.size() ||
      parsed == 0 || parsed > std::numeric_limits<std::uint32_t>::max()) {
    return false;
  }
  output = static_cast<std::uint32_t>(parsed);
  return true;
}

} // namespace

RagCoreServiceImpl::RagCoreServiceImpl(DocumentStore *document_store,
                                       ImageStore *image_store)
    : document_store_(document_store), image_store_(image_store) {}

grpc::Status
RagCoreServiceImpl::Health(grpc::ServerContext *context,
                           const multimodal::rag::v1::HealthRequest *request,
                           multimodal::rag::v1::HealthResponse *response) {
  static_cast<void>(context);
  static_cast<void>(request);

  response->set_service(kCoreServiceName);
  response->set_version(kCoreServiceVersion);
  response->set_ready(true);
  return grpc::Status::OK;
}

grpc::Status RagCoreServiceImpl::ExecutePlan(
    grpc::ServerContext *context,
    const multimodal::rag::v1::ExecutePlanRequest *request,
    multimodal::rag::v1::ExecutePlanResponse *response) {
  static_cast<void>(context);

  if (request->request_id().empty()) {
    return {
        grpc::StatusCode::INVALID_ARGUMENT, "request_id must not be empty",
    };
  }

  response->set_request_id(request->request_id());
  response->set_context("");
  for (const auto &route : request->routes()) {
    if (route.source_scope() != multimodal::rag::v1::SOURCE_SCOPE_LOCAL) {
      continue;
    }
    if (route.modality() == multimodal::rag::v1::MODALITY_DOCUMENT &&
        document_store_ == nullptr) {
      auto *error = response->add_route_errors();
      error->set_route_id(route.route_id());
      error->set_code("DOCUMENT_STORE_UNAVAILABLE");
      error->set_message("document store is not configured");
      error->set_retryable(true);
      continue;
    }
    if (route.modality() == multimodal::rag::v1::MODALITY_DOCUMENT) {
      DocumentQuery query{
          .tenant_id = request->tenant_id(),
          .allowed_acl_ids =
              std::vector<std::string>(request->allowed_acl_ids().begin(),
                                       request->allowed_acl_ids().end()),
          .text = route.query(),
          .dense_embedding = std::vector<float>(route.dense_embedding().begin(),
                                                route.dense_embedding().end()),
          .embedding_model_id = route.embedding_model_id(),
          .embedding_model_version = route.embedding_model_version(),
          .top_k = route.top_k(),
      };
      try {
        for (const auto &hit : document_store_->HybridSearch(query)) {
          auto *evidence = response->add_evidence();
          evidence->set_evidence_id(hit.chunk_id);
          evidence->set_content(hit.content);
          evidence->set_modality(multimodal::rag::v1::MODALITY_DOCUMENT);
          evidence->set_source_scope(multimodal::rag::v1::SOURCE_SCOPE_LOCAL);
          evidence->set_title(hit.title);
          evidence->set_source(hit.object_key);
          evidence->set_score(hit.score);
          auto *metadata = evidence->mutable_metadata();
          (*metadata)["asset_id"] = hit.asset_id;
          (*metadata)["asset_version_id"] = hit.asset_version_id;
          (*metadata)["ordinal"] = std::to_string(hit.ordinal);
          (*metadata)["page_number"] = std::to_string(hit.page_number);
          (*metadata)["route_id"] = route.route_id();
        }
      } catch (const DocumentStoreError &store_error) {
        auto *error = response->add_route_errors();
        error->set_route_id(route.route_id());
        error->set_code("DOCUMENT_RETRIEVAL_FAILED");
        error->set_message(store_error.what());
        error->set_retryable(store_error.retryable());
      }
      continue;
    }
    if (route.modality() != multimodal::rag::v1::MODALITY_IMAGE) {
      continue;
    }
    if (image_store_ == nullptr) {
      auto *error = response->add_route_errors();
      error->set_route_id(route.route_id());
      error->set_code("IMAGE_STORE_UNAVAILABLE");
      error->set_message("image store is not configured");
      error->set_retryable(true);
      continue;
    }
    ImageQuery query{
        .tenant_id = request->tenant_id(),
        .allowed_acl_ids =
            std::vector<std::string>(request->allowed_acl_ids().begin(),
                                     request->allowed_acl_ids().end()),
        .text = route.query(),
        .dense_embedding = std::vector<float>(route.dense_embedding().begin(),
                                              route.dense_embedding().end()),
        .embedding_model_id = route.embedding_model_id(),
        .embedding_model_version = route.embedding_model_version(),
        .top_k = route.top_k(),
    };
    try {
      for (const auto &hit : image_store_->HybridSearch(query)) {
        auto *evidence = response->add_evidence();
        evidence->set_evidence_id(hit.image_id);
        evidence->set_content(hit.content);
        evidence->set_modality(multimodal::rag::v1::MODALITY_IMAGE);
        evidence->set_source_scope(multimodal::rag::v1::SOURCE_SCOPE_LOCAL);
        evidence->set_title(hit.caption);
        evidence->set_source(hit.object_key);
        evidence->set_score(hit.score);
        auto *metadata = evidence->mutable_metadata();
        (*metadata)["asset_id"] = hit.asset_id;
        (*metadata)["asset_version_id"] = hit.asset_version_id;
        (*metadata)["media_type"] = hit.media_type;
        (*metadata)["width"] = std::to_string(hit.width);
        (*metadata)["height"] = std::to_string(hit.height);
        (*metadata)["ocr_text"] = hit.ocr_text;
        (*metadata)["route_id"] = route.route_id();
      }
    } catch (const ImageStoreError &store_error) {
      auto *error = response->add_route_errors();
      error->set_route_id(route.route_id());
      error->set_code("IMAGE_RETRIEVAL_FAILED");
      error->set_message(store_error.what());
      error->set_retryable(store_error.retryable());
    }
  }
  response->set_partial_failure(response->route_errors_size() > 0);
  return grpc::Status::OK;
}

IndexCoreServiceImpl::IndexCoreServiceImpl(DocumentStore *document_store,
                                           ImageStore *image_store)
    : document_store_(document_store), image_store_(image_store) {}

grpc::Status IndexCoreServiceImpl::IndexAsset(
    grpc::ServerContext *context,
    const multimodal::rag::v1::IndexAssetRequest *request,
    multimodal::rag::v1::IndexAssetResponse *response) {
  static_cast<void>(context);
  if (request->request_id().empty() || request->tenant_id().empty() ||
      request->acl_id().empty() || request->asset_id().empty() ||
      request->asset_version_id().empty() || request->asset_version() == 0 ||
      request->object_key().empty() || request->units().empty()) {
    return {grpc::StatusCode::INVALID_ARGUMENT,
            "index asset identity and units must not be empty"};
  }

  const auto modality = request->units().begin()->modality();
  for (const auto &unit : request->units()) {
    if (unit.modality() != modality) {
      return {grpc::StatusCode::INVALID_ARGUMENT,
              "index units must share one modality"};
    }
  }

  if (modality == multimodal::rag::v1::MODALITY_IMAGE) {
    if (image_store_ == nullptr) {
      return {grpc::StatusCode::FAILED_PRECONDITION,
              "image store is not configured"};
    }
    if (request->units_size() != 1 || request->append_to_asset_version()) {
      return {grpc::StatusCode::INVALID_ARGUMENT,
              "one image asset version requires one replacement unit"};
    }
    const auto &unit = request->units(0);
    std::uint32_t width = 0;
    std::uint32_t height = 0;
    if (!ParsePositiveUint32(Metadata(unit, "width"), width) ||
        !ParsePositiveUint32(Metadata(unit, "height"), height)) {
      return {grpc::StatusCode::INVALID_ARGUMENT,
              "image width and height metadata must be positive integers"};
    }
    ImageRecord image{
        .image_id = unit.unit_id(),
        .tenant_id = request->tenant_id(),
        .acl_id = request->acl_id(),
        .asset_id = request->asset_id(),
        .asset_version_id = request->asset_version_id(),
        .asset_version = request->asset_version(),
        .object_key = request->object_key(),
        .media_type = Metadata(unit, "media_type"),
        .width = width,
        .height = height,
        .caption = unit.title(),
        .ocr_text = Metadata(unit, "ocr_text"),
        .content = unit.content(),
        .content_sha256 = unit.content_sha256(),
        .dense_embedding = std::vector<float>(unit.dense_embedding().begin(),
                                              unit.dense_embedding().end()),
        .embedding_model_id = unit.embedding_model_id(),
        .embedding_model_version = unit.embedding_model_version(),
        .vision_model_id = Metadata(unit, "vision_model_id"),
        .vision_model_version = Metadata(unit, "vision_model_version"),
    };
    try {
      const auto collection_alias = image_store_->ReplaceAssetVersion(image);
      response->set_request_id(request->request_id());
      response->set_asset_id(request->asset_id());
      response->set_asset_version(request->asset_version());
      response->set_indexed_unit_count(1);
      response->set_collection_alias(collection_alias);
      return grpc::Status::OK;
    } catch (const ImageStoreError &error) {
      return StoreErrorStatus(error);
    }
  }
  if (modality != multimodal::rag::v1::MODALITY_DOCUMENT) {
    return {grpc::StatusCode::INVALID_ARGUMENT,
            "IndexAsset accepts document or image units only"};
  }
  if (document_store_ == nullptr) {
    return {grpc::StatusCode::FAILED_PRECONDITION,
            "document store is not configured"};
  }

  std::vector<DocumentChunk> chunks;
  chunks.reserve(static_cast<std::size_t>(request->units_size()));
  for (const auto &unit : request->units()) {
    DocumentChunk chunk{
        .chunk_id = unit.unit_id(),
        .tenant_id = request->tenant_id(),
        .acl_id = request->acl_id(),
        .asset_id = request->asset_id(),
        .asset_version_id = request->asset_version_id(),
        .asset_version = request->asset_version(),
        .object_key = request->object_key(),
        .ordinal = unit.ordinal(),
        .page_number = unit.page_number(),
        .title = unit.title(),
        .content = unit.content(),
        .content_sha256 = unit.content_sha256(),
        .dense_embedding = std::vector<float>(unit.dense_embedding().begin(),
                                              unit.dense_embedding().end()),
        .embedding_model_id = unit.embedding_model_id(),
        .embedding_model_version = unit.embedding_model_version(),
    };
    const auto errors = Validate(chunk);
    if (!errors.empty()) {
      return {grpc::StatusCode::INVALID_ARGUMENT, errors.front()};
    }
    chunks.push_back(std::move(chunk));
  }

  try {
    const auto collection_alias =
        request->append_to_asset_version()
            ? document_store_->AppendAssetVersion(chunks)
            : document_store_->ReplaceAssetVersion(chunks);
    response->set_request_id(request->request_id());
    response->set_asset_id(request->asset_id());
    response->set_asset_version(request->asset_version());
    response->set_indexed_unit_count(static_cast<std::uint32_t>(chunks.size()));
    response->set_collection_alias(collection_alias);
    return grpc::Status::OK;
  } catch (const DocumentStoreError &error) {
    return StoreErrorStatus(error);
  }
}

} // namespace multimodal::rag::core
