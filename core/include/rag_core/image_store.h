#pragma once

#include <cstdint>
#include <mutex>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <vector>

namespace multimodal::rag::core {

struct ImageRecord {
  std::string image_id;
  std::string tenant_id;
  std::string acl_id;
  std::string asset_id;
  std::string asset_version_id;
  std::uint64_t asset_version{0};
  std::string object_key;
  std::string media_type;
  std::uint32_t width{0};
  std::uint32_t height{0};
  std::string caption;
  std::string ocr_text;
  std::string content;
  std::string content_sha256;
  std::vector<float> dense_embedding;
  std::string embedding_model_id;
  std::string embedding_model_version;
  std::string vision_model_id;
  std::string vision_model_version;
};

struct ImageQuery {
  std::string tenant_id;
  std::vector<std::string> allowed_acl_ids;
  std::string text;
  std::vector<float> dense_embedding;
  std::string embedding_model_id;
  std::string embedding_model_version;
  std::uint32_t top_k{10};
};

struct ImageHit {
  std::string image_id;
  std::string asset_id;
  std::string asset_version_id;
  std::string object_key;
  std::string media_type;
  std::uint32_t width{0};
  std::uint32_t height{0};
  std::string caption;
  std::string ocr_text;
  std::string content;
  std::string content_sha256;
  double score{0.0};
};

class ImageStoreError : public std::runtime_error {
public:
  ImageStoreError(std::string message, bool retryable);
  [[nodiscard]] bool retryable() const noexcept;

private:
  bool retryable_;
};

class ImageStore {
public:
  virtual ~ImageStore() = default;

  virtual std::string ReplaceAssetVersion(const ImageRecord &image) = 0;
  virtual std::vector<ImageHit> HybridSearch(const ImageQuery &query) = 0;
};

class InMemoryImageStore final : public ImageStore {
public:
  std::string ReplaceAssetVersion(const ImageRecord &image) override;
  std::vector<ImageHit> HybridSearch(const ImageQuery &query) override;

private:
  std::mutex mutex_;
  std::unordered_map<std::string, ImageRecord> images_;
};

std::vector<std::string> Validate(const ImageRecord &image);
std::vector<std::string> Validate(const ImageQuery &query);
std::string ImageCollectionAlias(const std::string &model_id,
                                 const std::string &model_version,
                                 std::size_t dimension);

} // namespace multimodal::rag::core
