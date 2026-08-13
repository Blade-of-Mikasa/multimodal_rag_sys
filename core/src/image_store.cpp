#include "rag_core/image_store.h"

#include <algorithm>
#include <cmath>
#include <functional>
#include <iomanip>
#include <set>
#include <sstream>
#include <unordered_set>
#include <utility>

namespace multimodal::rag::core {
namespace {

bool IsFinite(const std::vector<float> &values) {
  return std::all_of(values.begin(), values.end(),
                     [](float value) { return std::isfinite(value); });
}

double CosineSimilarity(const std::vector<float> &left,
                        const std::vector<float> &right) {
  if (left.size() != right.size() || left.empty()) {
    return -1.0;
  }
  double dot = 0.0;
  double left_norm = 0.0;
  double right_norm = 0.0;
  for (std::size_t index = 0; index < left.size(); ++index) {
    dot += static_cast<double>(left[index]) * right[index];
    left_norm += static_cast<double>(left[index]) * left[index];
    right_norm += static_cast<double>(right[index]) * right[index];
  }
  if (left_norm == 0.0 || right_norm == 0.0) {
    return 0.0;
  }
  return dot / (std::sqrt(left_norm) * std::sqrt(right_norm));
}

std::vector<std::string> Terms(const std::string &text) {
  std::vector<std::string> terms;
  std::string current;
  for (const unsigned char value : text) {
    if ((value >= 'a' && value <= 'z') || (value >= '0' && value <= '9')) {
      current.push_back(static_cast<char>(value));
    } else if (value >= 'A' && value <= 'Z') {
      current.push_back(static_cast<char>(value - 'A' + 'a'));
    } else if (!current.empty()) {
      terms.push_back(std::move(current));
      current.clear();
    }
  }
  if (!current.empty()) {
    terms.push_back(std::move(current));
  }
  return terms;
}

double LexicalScore(const std::vector<std::string> &query_terms,
                    const std::string &content) {
  if (query_terms.empty()) {
    return 0.0;
  }
  const auto content_terms = Terms(content);
  const std::unordered_set<std::string> content_set(content_terms.begin(),
                                                    content_terms.end());
  double matched = 0.0;
  for (const auto &term : query_terms) {
    matched += content_set.contains(term) ? 1.0 : 0.0;
  }
  return matched / static_cast<double>(query_terms.size());
}

std::uint64_t Fnv1a(const std::string &value) {
  std::uint64_t hash = 14695981039346656037ULL;
  for (const unsigned char byte : value) {
    hash ^= byte;
    hash *= 1099511628211ULL;
  }
  return hash;
}

bool SupportedMediaType(const std::string &media_type) {
  return media_type == "image/jpeg" || media_type == "image/png" ||
         media_type == "image/webp";
}

} // namespace

ImageStoreError::ImageStoreError(std::string message, bool retryable)
    : std::runtime_error(std::move(message)), retryable_(retryable) {}

bool ImageStoreError::retryable() const noexcept { return retryable_; }

std::vector<std::string> Validate(const ImageRecord &image) {
  std::vector<std::string> errors;
  if (image.image_id.empty() || image.tenant_id.empty() ||
      image.acl_id.empty() || image.asset_id.empty() ||
      image.asset_version_id.empty()) {
    errors.emplace_back("image identity fields must not be empty");
  }
  if (image.asset_version == 0) {
    errors.emplace_back("asset_version must be positive");
  }
  if (image.object_key.empty() || image.caption.empty() ||
      image.content.empty()) {
    errors.emplace_back("image object_key, caption and content must not be empty");
  }
  if (!SupportedMediaType(image.media_type)) {
    errors.emplace_back("image media_type must be JPEG, PNG or WebP");
  }
  if (image.width == 0 || image.height == 0) {
    errors.emplace_back("image dimensions must be positive");
  }
  if (image.caption.size() > 2'048 || image.ocr_text.size() > 60'000 ||
      image.content.size() > 65'535) {
    errors.emplace_back("image text exceeds storage limits");
  }
  if (image.content_sha256.size() != 64) {
    errors.emplace_back("content_sha256 must contain 64 characters");
  }
  if (image.dense_embedding.empty() ||
      image.dense_embedding.size() > 65'536 ||
      !IsFinite(image.dense_embedding)) {
    errors.emplace_back("dense_embedding must be finite and non-empty");
  }
  if (image.embedding_model_id.empty() ||
      image.embedding_model_version.empty()) {
    errors.emplace_back("embedding model identity must not be empty");
  }
  if (image.vision_model_id.empty() || image.vision_model_version.empty()) {
    errors.emplace_back("vision model identity must not be empty");
  }
  return errors;
}

std::vector<std::string> Validate(const ImageQuery &query) {
  std::vector<std::string> errors;
  if (query.tenant_id.empty() || query.allowed_acl_ids.empty()) {
    errors.emplace_back("query tenant and ACL scope must not be empty");
  }
  if (query.text.empty() || query.top_k == 0 || query.top_k > 200) {
    errors.emplace_back("query text and top_k must be valid");
  }
  if (query.dense_embedding.empty() || !IsFinite(query.dense_embedding)) {
    errors.emplace_back("query dense_embedding must be finite and non-empty");
  }
  if (query.embedding_model_id.empty() ||
      query.embedding_model_version.empty()) {
    errors.emplace_back("query embedding model identity must not be empty");
  }
  return errors;
}

std::string ImageCollectionAlias(const std::string &model_id,
                                 const std::string &model_version,
                                 std::size_t dimension) {
  std::ostringstream output;
  output << "rag_image_v1_" << std::hex << std::setw(16) << std::setfill('0')
         << Fnv1a(model_id + ':' + model_version) << '_' << std::dec
         << dimension;
  return output.str();
}

std::string InMemoryImageStore::ReplaceAssetVersion(const ImageRecord &image) {
  const auto errors = Validate(image);
  if (!errors.empty()) {
    throw ImageStoreError(errors.front(), false);
  }
  std::scoped_lock lock(mutex_);
  std::erase_if(images_, [&image](const auto &item) {
    return item.second.tenant_id == image.tenant_id &&
           item.second.asset_version_id == image.asset_version_id;
  });
  images_.insert_or_assign(image.image_id, image);
  return ImageCollectionAlias(image.embedding_model_id,
                              image.embedding_model_version,
                              image.dense_embedding.size());
}

std::vector<ImageHit>
InMemoryImageStore::HybridSearch(const ImageQuery &query) {
  const auto errors = Validate(query);
  if (!errors.empty()) {
    throw ImageStoreError(errors.front(), false);
  }
  const std::set<std::string> acl_scope(query.allowed_acl_ids.begin(),
                                        query.allowed_acl_ids.end());
  const auto query_terms = Terms(query.text);
  struct Candidate {
    const ImageRecord *image;
    double dense;
    double lexical;
    double fused;
  };
  std::vector<Candidate> candidates;
  std::scoped_lock lock(mutex_);
  for (const auto &[image_id, image] : images_) {
    static_cast<void>(image_id);
    if (image.tenant_id != query.tenant_id ||
        !acl_scope.contains(image.acl_id) ||
        image.embedding_model_id != query.embedding_model_id ||
        image.embedding_model_version != query.embedding_model_version ||
        image.dense_embedding.size() != query.dense_embedding.size()) {
      continue;
    }
    candidates.push_back(
        {&image, CosineSimilarity(query.dense_embedding, image.dense_embedding),
         LexicalScore(query_terms, image.content), 0.0});
  }

  auto dense_rank = candidates;
  std::ranges::sort(dense_rank, std::greater{}, &Candidate::dense);
  auto lexical_rank = candidates;
  std::ranges::sort(lexical_rank, std::greater{}, &Candidate::lexical);
  std::unordered_map<std::string, double> rrf;
  for (std::size_t index = 0; index < dense_rank.size(); ++index) {
    rrf[dense_rank[index].image->image_id] += 1.0 / (61.0 + index);
  }
  for (std::size_t index = 0; index < lexical_rank.size(); ++index) {
    rrf[lexical_rank[index].image->image_id] += 1.0 / (61.0 + index);
  }
  for (auto &candidate : candidates) {
    candidate.fused = rrf[candidate.image->image_id];
  }
  std::ranges::sort(candidates, std::greater{}, &Candidate::fused);

  std::vector<ImageHit> hits;
  const auto count = std::min<std::size_t>(query.top_k, candidates.size());
  hits.reserve(count);
  for (std::size_t index = 0; index < count; ++index) {
    const auto &image = *candidates[index].image;
    hits.push_back({.image_id = image.image_id,
                    .asset_id = image.asset_id,
                    .asset_version_id = image.asset_version_id,
                    .object_key = image.object_key,
                    .media_type = image.media_type,
                    .width = image.width,
                    .height = image.height,
                    .caption = image.caption,
                    .ocr_text = image.ocr_text,
                    .content = image.content,
                    .score = candidates[index].fused});
  }
  return hits;
}

} // namespace multimodal::rag::core
