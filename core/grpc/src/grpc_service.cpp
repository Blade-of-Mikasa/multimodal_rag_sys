#include "rag_core/grpc_service.h"

#include <string>

namespace multimodal::rag::core {

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
  response->set_partial_failure(false);
  return grpc::Status::OK;
}

} // namespace multimodal::rag::core
