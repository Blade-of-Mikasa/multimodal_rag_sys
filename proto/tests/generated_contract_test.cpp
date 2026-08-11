#include <cstdlib>
#include <iostream>
#include <string>

#include "rag_core.grpc.pb.h"
#include "rag_core.pb.h"

int main() {
  multimodal::rag::v1::ExecutePlanRequest request;
  request.set_request_id("req-m01-contract");
  request.set_user_id("user-m01");

  auto* route = request.add_routes();
  route->set_route_id("route-local-doc");
  route->set_query("contract smoke test");
  route->set_source_scope(multimodal::rag::v1::SOURCE_SCOPE_LOCAL);
  route->set_modality(multimodal::rag::v1::MODALITY_DOCUMENT);
  route->set_top_k(8);

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

  if (decoded.request_id() != "req-m01-contract" || decoded.routes_size() != 1 ||
      decoded.routes(0).top_k() != 8) {
    std::cerr << "generated message round trip changed values\n";
    return EXIT_FAILURE;
  }

  const auto* service =
      multimodal::rag::v1::RagCoreService::service_full_name();
  if (service != std::string("multimodal.rag.v1.RagCoreService")) {
    std::cerr << "unexpected generated gRPC service name: " << service << '\n';
    return EXIT_FAILURE;
  }

  std::cout << "rag_proto_contract_test: PASS\n";
  return EXIT_SUCCESS;
}
