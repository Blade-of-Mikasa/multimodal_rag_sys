#pragma once

#include <cstdint>
#include <memory>
#include <string>
#include <vector>

#include "rag_core/image_store.h"

namespace multimodal::rag::core {

struct MilvusImageStoreConfig {
  std::string uri{"http://127.0.0.1:19530"};
  std::string token;
  std::string database{"default"};
  std::string analyzer_params{
      R"({"tokenizer":"icu","filter":["lowercase"]})"};
  std::uint64_t rpc_deadline_ms{30'000};
};

class MilvusImageStore final : public ImageStore {
public:
  explicit MilvusImageStore(MilvusImageStoreConfig config);
  ~MilvusImageStore() override;

  MilvusImageStore(const MilvusImageStore &) = delete;
  MilvusImageStore &operator=(const MilvusImageStore &) = delete;
  MilvusImageStore(MilvusImageStore &&) noexcept;
  MilvusImageStore &operator=(MilvusImageStore &&) noexcept;

  std::string ReplaceAssetVersion(const ImageRecord &image) override;
  std::vector<ImageHit> HybridSearch(const ImageQuery &query) override;

private:
  class Impl;
  std::unique_ptr<Impl> impl_;
};

} // namespace multimodal::rag::core
