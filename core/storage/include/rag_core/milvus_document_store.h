#pragma once

#include <cstdint>
#include <memory>
#include <string>
#include <vector>

#include "rag_core/document_store.h"

namespace multimodal::rag::core {

struct MilvusDocumentStoreConfig {
  std::string uri{"http://127.0.0.1:19530"};
  std::string token;
  std::string database{"default"};
  std::string analyzer_params{
      R"({"tokenizer":"icu","filter":["lowercase"]})"};
  std::uint64_t rpc_deadline_ms{30'000};
};

class MilvusDocumentStore final : public DocumentStore {
public:
  explicit MilvusDocumentStore(MilvusDocumentStoreConfig config);
  ~MilvusDocumentStore() override;

  MilvusDocumentStore(const MilvusDocumentStore &) = delete;
  MilvusDocumentStore &operator=(const MilvusDocumentStore &) = delete;
  MilvusDocumentStore(MilvusDocumentStore &&) noexcept;
  MilvusDocumentStore &operator=(MilvusDocumentStore &&) noexcept;

  std::string
  ReplaceAssetVersion(const std::vector<DocumentChunk> &chunks) override;
  std::string
  AppendAssetVersion(const std::vector<DocumentChunk> &chunks) override;
  std::vector<DocumentHit> HybridSearch(const DocumentQuery &query) override;

private:
  class Impl;
  std::unique_ptr<Impl> impl_;
};

} // namespace multimodal::rag::core
