#pragma once

#include <grpcpp/grpcpp.h>

#include "rag_core.grpc.pb.h"
#include "rag_core/document_store.h"

namespace multimodal::rag::core {

inline constexpr char kCoreServiceName[] = "multimodal-rag-core";
inline constexpr char kCoreServiceVersion[] = "0.1.0";

class RagCoreServiceImpl final
    : public multimodal::rag::v1::RagCoreService::Service {
public:
  explicit RagCoreServiceImpl(DocumentStore *document_store = nullptr);

  grpc::Status Health(grpc::ServerContext *context,
                      const multimodal::rag::v1::HealthRequest *request,
                      multimodal::rag::v1::HealthResponse *response) override;

  grpc::Status
  ExecutePlan(grpc::ServerContext *context,
              const multimodal::rag::v1::ExecutePlanRequest *request,
              multimodal::rag::v1::ExecutePlanResponse *response) override;

private:
  DocumentStore *document_store_;
};

class IndexCoreServiceImpl final
    : public multimodal::rag::v1::IndexCoreService::Service {
public:
  explicit IndexCoreServiceImpl(DocumentStore *document_store);

  grpc::Status
  IndexAsset(grpc::ServerContext *context,
             const multimodal::rag::v1::IndexAssetRequest *request,
             multimodal::rag::v1::IndexAssetResponse *response) override;

private:
  DocumentStore *document_store_;
};

} // namespace multimodal::rag::core
