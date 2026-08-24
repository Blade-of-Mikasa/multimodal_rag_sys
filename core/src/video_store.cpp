#include "rag_core/video_store.h"

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
  return media_type == "video/mp4" || media_type == "video/quicktime" ||
         media_type == "video/webm";
}

const VideoSegment &
ValidateBatch(const std::vector<VideoSegment> &segments) {
  if (segments.empty()) {
    throw VideoStoreError("at least one video segment is required", false);
  }
  const auto &first = segments.front();
  for (const auto &segment : segments) {
    const auto errors = Validate(segment);
    if (!errors.empty()) {
      throw VideoStoreError(errors.front(), false);
    }
    if (segment.tenant_id != first.tenant_id ||
        segment.acl_id != first.acl_id ||
        segment.asset_id != first.asset_id ||
        segment.asset_version_id != first.asset_version_id ||
        segment.asset_version != first.asset_version ||
        segment.object_key != first.object_key ||
        segment.media_type != first.media_type ||
        segment.duration_ms != first.duration_ms ||
        segment.embedding_model_id != first.embedding_model_id ||
        segment.embedding_model_version != first.embedding_model_version ||
        segment.dense_embedding.size() != first.dense_embedding.size()) {
      throw VideoStoreError("video segment batch identity is inconsistent",
                            false);
    }
  }
  return first;
}

void InsertSegments(
    std::unordered_map<std::string, VideoSegment> &destination,
    const std::vector<VideoSegment> &segments) {
  for (const auto &segment : segments) {
    destination.insert_or_assign(segment.segment_id, segment);
  }
}

} // namespace

VideoStoreError::VideoStoreError(std::string message, bool retryable)
    : std::runtime_error(std::move(message)), retryable_(retryable) {}

bool VideoStoreError::retryable() const noexcept { return retryable_; }

std::vector<std::string> Validate(const VideoSegment &segment) {
  std::vector<std::string> errors;
  if (segment.segment_id.empty() || segment.tenant_id.empty() ||
      segment.acl_id.empty() || segment.asset_id.empty() ||
      segment.asset_version_id.empty()) {
    errors.emplace_back("video segment identity fields must not be empty");
  }
  if (segment.asset_version == 0 || segment.object_key.empty()) {
    errors.emplace_back("video asset version and object key must be valid");
  }
  if (!SupportedMediaType(segment.media_type)) {
    errors.emplace_back("video media_type must be MP4, QuickTime or WebM");
  }
  if (segment.duration_ms == 0 || segment.duration_ms > 86'400'000 ||
      segment.width == 0 || segment.height == 0 ||
      segment.width > 32'768 || segment.height > 32'768 ||
      static_cast<std::uint64_t>(segment.width) * segment.height >
          268'435'456 ||
      segment.start_ms >= segment.end_ms ||
      segment.end_ms > segment.duration_ms ||
      segment.keyframe_ms < segment.start_ms ||
      segment.keyframe_ms >= segment.end_ms) {
    errors.emplace_back("video dimensions and segment timestamps are invalid");
  }
  if (segment.caption.empty() || segment.content.empty() ||
      segment.caption.size() > 8'192 || segment.ocr_text.size() > 60'000 ||
      segment.transcript.size() > 60'000 || segment.content.size() > 65'535) {
    errors.emplace_back("video segment text is empty or exceeds storage limits");
  }
  if (segment.content_sha256.size() != 64) {
    errors.emplace_back("content_sha256 must contain 64 characters");
  }
  if (segment.dense_embedding.empty() ||
      segment.dense_embedding.size() > 65'536 ||
      !IsFinite(segment.dense_embedding)) {
    errors.emplace_back("dense_embedding must be finite and non-empty");
  }
  if (segment.embedding_model_id.empty() ||
      segment.embedding_model_version.empty() ||
      segment.vision_model_id.empty() ||
      segment.vision_model_version.empty() ||
      segment.speech_model_id.empty() ||
      segment.speech_model_version.empty()) {
    errors.emplace_back("video model identities must not be empty");
  }
  return errors;
}

std::vector<std::string> Validate(const VideoQuery &query) {
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

std::string VideoCollectionAlias(const std::string &model_id,
                                 const std::string &model_version,
                                 std::size_t dimension) {
  std::ostringstream output;
  output << "rag_video_v1_" << std::hex << std::setw(16) << std::setfill('0')
         << Fnv1a(model_id + ':' + model_version) << '_' << std::dec
         << dimension;
  return output.str();
}

std::string InMemoryVideoStore::ReplaceAssetVersion(
    const std::vector<VideoSegment> &segments) {
  const auto &first = ValidateBatch(segments);
  std::scoped_lock lock(mutex_);
  std::erase_if(segments_, [&first](const auto &item) {
    return item.second.tenant_id == first.tenant_id &&
           item.second.asset_version_id == first.asset_version_id;
  });
  InsertSegments(segments_, segments);
  return VideoCollectionAlias(first.embedding_model_id,
                              first.embedding_model_version,
                              first.dense_embedding.size());
}

std::string InMemoryVideoStore::AppendAssetVersion(
    const std::vector<VideoSegment> &segments) {
  const auto &first = ValidateBatch(segments);
  std::scoped_lock lock(mutex_);
  InsertSegments(segments_, segments);
  return VideoCollectionAlias(first.embedding_model_id,
                              first.embedding_model_version,
                              first.dense_embedding.size());
}

std::vector<VideoHit>
InMemoryVideoStore::HybridSearch(const VideoQuery &query) {
  const auto errors = Validate(query);
  if (!errors.empty()) {
    throw VideoStoreError(errors.front(), false);
  }
  const std::set<std::string> acl_scope(query.allowed_acl_ids.begin(),
                                        query.allowed_acl_ids.end());
  const auto query_terms = Terms(query.text);
  struct Candidate {
    const VideoSegment *segment;
    double dense;
    double lexical;
    double fused;
  };
  std::vector<Candidate> candidates;
  std::scoped_lock lock(mutex_);
  for (const auto &[segment_id, segment] : segments_) {
    static_cast<void>(segment_id);
    if (segment.tenant_id != query.tenant_id ||
        !acl_scope.contains(segment.acl_id) ||
        segment.embedding_model_id != query.embedding_model_id ||
        segment.embedding_model_version != query.embedding_model_version ||
        segment.dense_embedding.size() != query.dense_embedding.size()) {
      continue;
    }
    candidates.push_back(
        {&segment,
         CosineSimilarity(query.dense_embedding, segment.dense_embedding),
         LexicalScore(query_terms, segment.content), 0.0});
  }
  auto dense_rank = candidates;
  std::ranges::sort(dense_rank, std::greater{}, &Candidate::dense);
  auto lexical_rank = candidates;
  std::ranges::sort(lexical_rank, std::greater{}, &Candidate::lexical);
  std::unordered_map<std::string, double> rrf;
  for (std::size_t index = 0; index < dense_rank.size(); ++index) {
    rrf[dense_rank[index].segment->segment_id] += 1.0 / (61.0 + index);
  }
  for (std::size_t index = 0; index < lexical_rank.size(); ++index) {
    rrf[lexical_rank[index].segment->segment_id] += 1.0 / (61.0 + index);
  }
  for (auto &candidate : candidates) {
    candidate.fused = rrf[candidate.segment->segment_id];
  }
  std::ranges::sort(candidates, std::greater{}, &Candidate::fused);

  std::vector<VideoHit> hits;
  const auto count = std::min<std::size_t>(query.top_k, candidates.size());
  hits.reserve(count);
  for (std::size_t index = 0; index < count; ++index) {
    const auto &segment = *candidates[index].segment;
    hits.push_back({.segment_id = segment.segment_id,
                    .asset_id = segment.asset_id,
                    .asset_version_id = segment.asset_version_id,
                    .object_key = segment.object_key,
                    .ordinal = segment.ordinal,
                    .media_type = segment.media_type,
                    .duration_ms = segment.duration_ms,
                    .width = segment.width,
                    .height = segment.height,
                    .start_ms = segment.start_ms,
                    .end_ms = segment.end_ms,
                    .keyframe_ms = segment.keyframe_ms,
                    .caption = segment.caption,
                    .ocr_text = segment.ocr_text,
                    .transcript = segment.transcript,
                    .content = segment.content,
                    .content_sha256 = segment.content_sha256,
                    .score = candidates[index].fused});
  }
  return hits;
}

} // namespace multimodal::rag::core
