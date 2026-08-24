#pragma once

#include <cstdint>
#include <memory>
#include <string>

#include "rag_core/video_store.h"

namespace multimodal::rag::core {

struct MilvusVideoStoreConfig {
  std::string uri{"http://127.0.0.1:19530"};
  std::string token;
  std::string database{"default"};
  std::string analyzer_params{
      R"({"tokenizer":"icu","filter":["lowercase"]})"};
  std::uint64_t rpc_deadline_ms{30'000};
};

class MilvusVideoStore final : public VideoStore {
public:
  explicit MilvusVideoStore(MilvusVideoStoreConfig config);
  ~MilvusVideoStore() override;

  MilvusVideoStore(const MilvusVideoStore &) = delete;
  MilvusVideoStore &operator=(const MilvusVideoStore &) = delete;
  MilvusVideoStore(MilvusVideoStore &&) noexcept;
  MilvusVideoStore &operator=(MilvusVideoStore &&) noexcept;

  std::string
  ReplaceAssetVersion(const std::vector<VideoSegment> &segments) override;
  std::string
  AppendAssetVersion(const std::vector<VideoSegment> &segments) override;
  std::vector<VideoHit> HybridSearch(const VideoQuery &query) override;

private:
  class Impl;
  std::unique_ptr<Impl> impl_;
};

} // namespace multimodal::rag::core
