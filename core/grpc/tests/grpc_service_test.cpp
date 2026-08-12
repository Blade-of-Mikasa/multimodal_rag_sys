#include <cstdlib>
#include <iostream>
#include <string>

#include <grpcpp/grpcpp.h>

#include "rag_core/grpc_service.h"

int main() {
  multimodal::rag::core::RagCoreServiceImpl service;
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

  multimodal::rag::v1::ExecutePlanRequest plan_request;
  plan_request.set_request_id("req-m03-unit");
  multimodal::rag::v1::ExecutePlanResponse plan_response;
  const grpc::Status plan_status =
      service.ExecutePlan(&context, &plan_request, &plan_response);
  if (!plan_status.ok() || plan_response.request_id() != "req-m03-unit" ||
      plan_response.partial_failure() || !plan_response.context().empty()) {
    std::cerr << "unexpected ExecutePlan response\n";
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
