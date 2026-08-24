#include <cstdlib>
#include <iostream>
#include <string>

#include <grpcpp/grpcpp.h>

#include "rag_core/grpc_service.h"

int main() {
  multimodal::rag::core::InMemoryDocumentStore store;
  multimodal::rag::core::InMemoryImageStore image_store;
  multimodal::rag::core::RagCoreServiceImpl service(&store, &image_store);
  multimodal::rag::core::IndexCoreServiceImpl index_service(&store,
                                                             &image_store);
  grpc::ServerContext context;

  multimodal::rag::v1::HealthRequest health_request;
  multimodal::rag::v1::HealthResponse health_response;
  const grpc::Status health_status =
      service.Health(&context, &health_request, &health_response);
  if (!health_status.ok() || !health_response.ready() ||
      health_response.service() != multimodal::rag::core::kCoreServiceName) {
    std::cerr << "unexpected Health response\n";
    return EXIT_FAILURE;
  }

  multimodal::rag::v1::IndexAssetRequest index_request;
  index_request.set_request_id("req-index");
  index_request.set_tenant_id("tenant-1");
  index_request.set_acl_id("acl-a");
  index_request.set_asset_id("asset-1");
  index_request.set_asset_version_id("version-1");
  index_request.set_asset_version(1);
  index_request.set_object_key("tenant-1/asset-1/v1/document.txt");
  auto *unit = index_request.add_units();
  unit->set_unit_id("chunk-1");
  unit->set_modality(multimodal::rag::v1::MODALITY_DOCUMENT);
  unit->set_content("Milvus hybrid retrieval architecture");
  unit->set_title("Architecture");
  unit->set_ordinal(0);
  unit->set_page_number(1);
  unit->set_content_sha256(std::string(64, 'a'));
  unit->add_dense_embedding(1.0F);
  unit->add_dense_embedding(0.0F);
  unit->set_embedding_model_id("embedding-general");
  unit->set_embedding_model_version("v1");
  multimodal::rag::v1::IndexAssetResponse index_response;
  const grpc::Status index_status =
      index_service.IndexAsset(&context, &index_request, &index_response);
  if (!index_status.ok() || index_response.indexed_unit_count() != 1) {
    std::cerr << "unexpected IndexAsset response\n";
    return EXIT_FAILURE;
  }

  multimodal::rag::v1::IndexAssetRequest append_request = index_request;
  append_request.clear_units();
  append_request.set_append_to_asset_version(true);
  auto *appended_unit = append_request.add_units();
  appended_unit->set_unit_id("chunk-2");
  appended_unit->set_modality(multimodal::rag::v1::MODALITY_DOCUMENT);
  appended_unit->set_content("A later bounded gRPC batch");
  appended_unit->set_title("Batching");
  appended_unit->set_ordinal(1);
  appended_unit->set_page_number(2);
  appended_unit->set_content_sha256(std::string(64, 'b'));
  appended_unit->add_dense_embedding(0.8F);
  appended_unit->add_dense_embedding(0.2F);
  appended_unit->set_embedding_model_id("embedding-general");
  appended_unit->set_embedding_model_version("v1");
  multimodal::rag::v1::IndexAssetResponse append_response;
  const grpc::Status append_status =
      index_service.IndexAsset(&context, &append_request, &append_response);
  if (!append_status.ok() || append_response.indexed_unit_count() != 1) {
    std::cerr << "unexpected append IndexAsset response\n";
    return EXIT_FAILURE;
  }

  multimodal::rag::v1::ExecutePlanRequest plan_request;
  plan_request.set_request_id("req-m07-unit");
  plan_request.set_tenant_id("tenant-1");
  plan_request.add_allowed_acl_ids("acl-a");
  auto *route = plan_request.add_routes();
  route->set_route_id("document-local");
  route->set_query("Milvus architecture");
  route->set_source_scope(multimodal::rag::v1::SOURCE_SCOPE_LOCAL);
  route->set_modality(multimodal::rag::v1::MODALITY_DOCUMENT);
  route->set_top_k(5);
  route->set_timeout_ms(1000);
  route->add_dense_embedding(1.0F);
  route->add_dense_embedding(0.0F);
  route->set_embedding_model_id("embedding-general");
  route->set_embedding_model_version("v1");
  multimodal::rag::v1::ExecutePlanResponse plan_response;
  const grpc::Status plan_status =
      service.ExecutePlan(&context, &plan_request, &plan_response);
  if (!plan_status.ok() || plan_response.request_id() != "req-m07-unit" ||
      plan_response.partial_failure() || plan_response.evidence_size() != 2 ||
      plan_response.evidence(0).evidence_id() != "chunk-1") {
    std::cerr << "unexpected ExecutePlan response\n";
    return EXIT_FAILURE;
  }

  multimodal::rag::v1::IndexAssetRequest image_request;
  image_request.set_request_id("req-image-index");
  image_request.set_tenant_id("tenant-1");
  image_request.set_acl_id("acl-a");
  image_request.set_asset_id("asset-image");
  image_request.set_asset_version_id("version-image");
  image_request.set_asset_version(1);
  image_request.set_object_key("tenant-1/asset-image/v1/image.png");
  auto *image_unit = image_request.add_units();
  image_unit->set_unit_id("image-1");
  image_unit->set_modality(multimodal::rag::v1::MODALITY_IMAGE);
  image_unit->set_content("A red bicycle\nOCR:\nOPEN");
  image_unit->set_title("A red bicycle");
  image_unit->set_content_sha256(std::string(64, 'c'));
  image_unit->add_dense_embedding(1.0F);
  image_unit->add_dense_embedding(0.0F);
  image_unit->set_embedding_model_id("embedding-general");
  image_unit->set_embedding_model_version("v1");
  (*image_unit->mutable_metadata())["media_type"] = "image/png";
  (*image_unit->mutable_metadata())["width"] = "800";
  (*image_unit->mutable_metadata())["height"] = "600";
  (*image_unit->mutable_metadata())["ocr_text"] = "OPEN";
  (*image_unit->mutable_metadata())["vision_model_id"] = "vision-general";
  (*image_unit->mutable_metadata())["vision_model_version"] = "v1";
  multimodal::rag::v1::IndexAssetResponse image_index_response;
  const grpc::Status image_index_status = index_service.IndexAsset(
      &context, &image_request, &image_index_response);
  if (!image_index_status.ok() ||
      image_index_response.indexed_unit_count() != 1 ||
      !image_index_response.collection_alias().starts_with("rag_image_v1_")) {
    std::cerr << "unexpected image IndexAsset response\n";
    return EXIT_FAILURE;
  }

  multimodal::rag::v1::ExecutePlanRequest image_plan_request;
  image_plan_request.set_request_id("req-image-query");
  image_plan_request.set_tenant_id("tenant-1");
  image_plan_request.add_allowed_acl_ids("acl-a");
  auto *image_route = image_plan_request.add_routes();
  image_route->set_route_id("image-local");
  image_route->set_query("red bicycle");
  image_route->set_source_scope(multimodal::rag::v1::SOURCE_SCOPE_LOCAL);
  image_route->set_modality(multimodal::rag::v1::MODALITY_IMAGE);
  image_route->set_top_k(5);
  image_route->set_timeout_ms(1000);
  image_route->add_dense_embedding(1.0F);
  image_route->add_dense_embedding(0.0F);
  image_route->set_embedding_model_id("embedding-general");
  image_route->set_embedding_model_version("v1");
  multimodal::rag::v1::ExecutePlanResponse image_plan_response;
  const grpc::Status image_plan_status = service.ExecutePlan(
      &context, &image_plan_request, &image_plan_response);
  if (!image_plan_status.ok() || image_plan_response.partial_failure() ||
      image_plan_response.evidence_size() != 1 ||
      image_plan_response.evidence(0).modality() !=
          multimodal::rag::v1::MODALITY_IMAGE ||
      image_plan_response.evidence(0).metadata().at("ocr_text") != "OPEN") {
    std::cerr << "unexpected image ExecutePlan response\n";
    return EXIT_FAILURE;
  }

  multimodal::rag::v1::ExecutePlanRequest invalid_request;
  multimodal::rag::v1::ExecutePlanResponse invalid_response;
  const grpc::Status invalid_status =
      service.ExecutePlan(&context, &invalid_request, &invalid_response);
  if (invalid_status.error_code() != grpc::StatusCode::INVALID_ARGUMENT) {
    std::cerr << "empty request_id must be rejected\n";
    return EXIT_FAILURE;
  }

  std::cout << "rag_core_grpc_service_test: PASS\n";
  return EXIT_SUCCESS;
}
