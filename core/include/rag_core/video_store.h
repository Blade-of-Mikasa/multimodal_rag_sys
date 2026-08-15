#pragma once

#include <cstdint>
#include <mutex>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <vector>

namespace multimodal::rag::core {

struct VideoSegment {
  std::string segment_id;
  std::string tenant_id;
  std::string acl_id;
  std::string asset_id;
  std::string asset_version_id;
  std::uint64_t asset_version{0};
  std::string object_key;
  std::uint32_t ordinal{0};
  std::string media_type;
  std::uint64_t duration_ms{0};
  std::uint32_t width{0};
  std::uint32_t height{0};
  std::uint64_t start_ms{0};
  std::uint64_t end_ms{0};
  std::uint64_t keyframe_ms{0};
  std::string caption;
  std::string ocr_text;
  std::string transcript;
  std::string content;
  std::string content_sha256;
  std::vector<float> dense_embedding;
  std::string embedding_model_id;
  std::string embedding_model_version;
  std::string vision_model_id;
  std::string vision_model_version;
  std::string speech_model_id;
  std::string speech_model_version;
};

struct VideoQuery {
  std::string tenant_id;
  std::vector<std::string> allowed_acl_ids;
  std::string text;
  std::vector<float> dense_embedding;
  std::string embedding_model_id;
  std::string embedding_model_version;
  std::uint32_t top_k{10};
};

struct VideoHit {
  std::string segment_id;
  std::string asset_id;
  std::string asset_version_id;
  std::string object_key;
  std::uint32_t ordinal{0};
  std::string media_type;
  std::uint64_t duration_ms{0};
  std::uint32_t width{0};
  std::uint32_t height{0};
  std::uint64_t start_ms{0};
  std::uint64_t end_ms{0};
  std::uint64_t keyframe_ms{0};
  std::string caption;
  std::string ocr_text;
  std::string transcript;
  std::string content;
  double score{0.0};
};

class VideoStoreError : public std::runtime_error {
public:
  VideoStoreError(std::string message, bool retryable);
  [[nodiscard]] bool retryable() const noexcept;

private:
  bool retryable_;
};

class VideoStore {
public:
  virtual ~VideoStore() = default;

  virtual std::string
  ReplaceAssetVersion(const std::vector<VideoSegment> &segments) = 0;
  virtual std::string
  AppendAssetVersion(const std::vector<VideoSegment> &segments) = 0;
  virtual std::vector<VideoHit> HybridSearch(const VideoQuery &query) = 0;
};

class InMemoryVideoStore final : public VideoStore {
public:
  std::string
  ReplaceAssetVersion(const std::vector<VideoSegment> &segments) override;
  std::string
  AppendAssetVersion(const std::vector<VideoSegment> &segments) override;
  std::vector<VideoHit> HybridSearch(const VideoQuery &query) override;

private:
  std::mutex mutex_;
  std::unordered_map<std::string, VideoSegment> segments_;
};

std::vector<std::string> Validate(const VideoSegment &segment);
std::vector<std::string> Validate(const VideoQuery &query);
std::string VideoCollectionAlias(const std::string &model_id,
                                 const std::string &model_version,
                                 std::size_t dimension);

} // namespace multimodal::rag::core
