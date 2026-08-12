#pragma once

#include <grpcpp/grpcpp.h>

#include "rag_core.grpc.pb.h"

namespace multimodal::rag::core {

inline constexpr char kCoreServiceName[] = "multimodal-rag-core";
inline constexpr char kCoreServiceVersion[] = "0.1.0";

class RagCoreServiceImpl final
    : public multimodal::rag::v1::RagCoreService::Service {
public:
  grpc::Status Health(grpc::ServerContext *context,
                      const multimodal::rag::v1::HealthRequest *request,
                      multimodal::rag::v1::HealthResponse *response) override;

  grpc::Status
  ExecutePlan(grpc::ServerContext *context,
              const multimodal::rag::v1::ExecutePlanRequest *request,
              multimodal::rag::v1::ExecutePlanResponse *response) override;
};

} // namespace multimodal::rag::core
