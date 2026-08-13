#pragma once

#include <cstdint>
#include <mutex>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <vector>

namespace multimodal::rag::core {

struct DocumentChunk {
  std::string chunk_id;
  std::string tenant_id;
  std::string acl_id;
  std::string asset_id;
  std::string asset_version_id;
  std::uint64_t asset_version{0};
  std::string object_key;
  std::uint32_t ordinal{0};
  std::uint32_t page_number{0};
  std::string title;
  std::string content;
  std::string content_sha256;
  std::vector<float> dense_embedding;
  std::string embedding_model_id;
  std::string embedding_model_version;
};

struct DocumentQuery {
  std::string tenant_id;
  std::vector<std::string> allowed_acl_ids;
  std::string text;
  std::vector<float> dense_embedding;
  std::string embedding_model_id;
  std::string embedding_model_version;
  std::uint32_t top_k{10};
};

struct DocumentHit {
  std::string chunk_id;
  std::string asset_id;
  std::string asset_version_id;
  std::string object_key;
  std::uint32_t ordinal{0};
  std::uint32_t page_number{0};
  std::string title;
  std::string content;
  double score{0.0};
};

class DocumentStoreError : public std::runtime_error {
public:
  DocumentStoreError(std::string message, bool retryable);
  [[nodiscard]] bool retryable() const noexcept;

private:
  bool retryable_;
};

class DocumentStore {
public:
  virtual ~DocumentStore() = default;

  virtual std::string
  ReplaceAssetVersion(const std::vector<DocumentChunk> &chunks) = 0;
  virtual std::string
  AppendAssetVersion(const std::vector<DocumentChunk> &chunks) = 0;
  virtual std::vector<DocumentHit> HybridSearch(const DocumentQuery &query) = 0;
};

class InMemoryDocumentStore final : public DocumentStore {
public:
  std::string
  ReplaceAssetVersion(const std::vector<DocumentChunk> &chunks) override;
  std::string
  AppendAssetVersion(const std::vector<DocumentChunk> &chunks) override;
  std::vector<DocumentHit> HybridSearch(const DocumentQuery &query) override;

private:
  std::mutex mutex_;
  std::unordered_map<std::string, DocumentChunk> chunks_;
};

std::vector<std::string> Validate(const DocumentChunk &chunk);
std::vector<std::string> Validate(const DocumentQuery &query);
std::string CollectionAlias(const std::string &model_id,
                            const std::string &model_version,
                            std::size_t dimension);

} // namespace multimodal::rag::core
