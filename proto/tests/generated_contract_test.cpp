#include <cstdlib>
#include <iostream>
#include <string>

#include "rag_core.grpc.pb.h"
#include "rag_core.pb.h"

int main() {
  multimodal::rag::v1::ExecutePlanRequest request;
  request.set_request_id("req-m01-contract");
  request.set_user_id("user-m01");
  request.set_tenant_id("tenant-m07");
  request.add_allowed_acl_ids("acl-m07");

  auto *route = request.add_routes();
  route->set_route_id("route-local-doc");
  route->set_query("contract smoke test");
  route->set_source_scope(multimodal::rag::v1::SOURCE_SCOPE_LOCAL);
  route->set_modality(multimodal::rag::v1::MODALITY_DOCUMENT);
  route->set_top_k(8);
  route->add_dense_embedding(1.0F);
  route->add_dense_embedding(0.0F);
  route->set_embedding_model_id("embedding-general");
  route->set_embedding_model_version("v1");

  std::string encoded;
  if (!request.SerializeToString(&encoded)) {
    std::cerr << "failed to serialize generated request\n";
    return EXIT_FAILURE;
  }

  multimodal::rag::v1::ExecutePlanRequest decoded;
  if (!decoded.ParseFromString(encoded)) {
    std::cerr << "failed to parse generated request\n";
    return EXIT_FAILURE;
  }

  if (decoded.request_id() != "req-m01-contract" ||
      decoded.tenant_id() != "tenant-m07" || decoded.routes_size() != 1 ||
      decoded.routes(0).top_k() != 8 ||
      decoded.routes(0).dense_embedding_size() != 2) {
    std::cerr << "generated message round trip changed values\n";
    return EXIT_FAILURE;
  }

  const auto *service =
      multimodal::rag::v1::RagCoreService::service_full_name();
  if (service != std::string("multimodal.rag.v1.RagCoreService")) {
    std::cerr << "unexpected generated gRPC service name: " << service << '\n';
    return EXIT_FAILURE;
  }

  std::cout << "rag_proto_contract_test: PASS\n";
  return EXIT_SUCCESS;
}
