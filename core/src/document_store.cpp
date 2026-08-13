#include "rag_core/document_store.h"

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
  std::unordered_set<std::string> content_set(content_terms.begin(),
                                              content_terms.end());
  double matched = 0.0;
  for (const auto &term : query_terms) {
    matched += content_set.contains(term) ? 1.0 : 0.0;
  }
  return matched / static_cast<double>(query_terms.size());
}

bool IsFinite(const std::vector<float> &values) {
  return std::all_of(values.begin(), values.end(),
                     [](float value) { return std::isfinite(value); });
}

std::uint64_t Fnv1a(const std::string &value) {
  std::uint64_t hash = 14695981039346656037ULL;
  for (const unsigned char byte : value) {
    hash ^= byte;
    hash *= 1099511628211ULL;
  }
  return hash;
}

const DocumentChunk &ValidateBatch(const std::vector<DocumentChunk> &chunks) {
  if (chunks.empty()) {
    throw DocumentStoreError("at least one chunk is required", false);
  }
  const auto &first = chunks.front();
  for (const auto &chunk : chunks) {
    const auto errors = Validate(chunk);
    if (!errors.empty()) {
      throw DocumentStoreError(errors.front(), false);
    }
    if (chunk.tenant_id != first.tenant_id || chunk.acl_id != first.acl_id ||
        chunk.asset_id != first.asset_id ||
        chunk.asset_version_id != first.asset_version_id ||
        chunk.asset_version != first.asset_version ||
        chunk.object_key != first.object_key ||
        chunk.embedding_model_id != first.embedding_model_id ||
        chunk.embedding_model_version != first.embedding_model_version ||
        chunk.dense_embedding.size() != first.dense_embedding.size()) {
      throw DocumentStoreError("chunk batch identity is inconsistent", false);
    }
  }
  return first;
}

void InsertChunks(std::unordered_map<std::string, DocumentChunk> &destination,
                  const std::vector<DocumentChunk> &chunks) {
  for (const auto &chunk : chunks) {
    destination.insert_or_assign(chunk.chunk_id, chunk);
  }
}

} // namespace

DocumentStoreError::DocumentStoreError(std::string message, bool retryable)
    : std::runtime_error(std::move(message)), retryable_(retryable) {}

bool DocumentStoreError::retryable() const noexcept { return retryable_; }

std::vector<std::string> Validate(const DocumentChunk &chunk) {
  std::vector<std::string> errors;
  if (chunk.chunk_id.empty() || chunk.tenant_id.empty() ||
      chunk.acl_id.empty() || chunk.asset_id.empty() ||
      chunk.asset_version_id.empty()) {
    errors.emplace_back("chunk identity fields must not be empty");
  }
  if (chunk.asset_version == 0) {
    errors.emplace_back("asset_version must be positive");
  }
  if (chunk.object_key.empty() || chunk.content.empty()) {
    errors.emplace_back("chunk object_key and content must not be empty");
  }
  if (chunk.content_sha256.size() != 64) {
    errors.emplace_back("content_sha256 must contain 64 characters");
  }
  if (chunk.dense_embedding.empty() || chunk.dense_embedding.size() > 65'536 ||
      !IsFinite(chunk.dense_embedding)) {
    errors.emplace_back("dense_embedding must be finite and non-empty");
  }
  if (chunk.embedding_model_id.empty() ||
      chunk.embedding_model_version.empty()) {
    errors.emplace_back("embedding model identity must not be empty");
  }
  return errors;
}

std::vector<std::string> Validate(const DocumentQuery &query) {
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

std::string CollectionAlias(const std::string &model_id,
                            const std::string &model_version,
                            std::size_t dimension) {
  std::ostringstream output;
  output << "rag_document_v1_" << std::hex << std::setw(16) << std::setfill('0')
         << Fnv1a(model_id + ':' + model_version) << '_' << std::dec
         << dimension;
  return output.str();
}

std::string InMemoryDocumentStore::ReplaceAssetVersion(
    const std::vector<DocumentChunk> &chunks) {
  const auto &first = ValidateBatch(chunks);

  std::scoped_lock lock(mutex_);
  std::erase_if(chunks_, [&first](const auto &item) {
    return item.second.tenant_id == first.tenant_id &&
           item.second.asset_version_id == first.asset_version_id;
  });
  InsertChunks(chunks_, chunks);
  return CollectionAlias(first.embedding_model_id,
                         first.embedding_model_version,
                         first.dense_embedding.size());
}

std::string InMemoryDocumentStore::AppendAssetVersion(
    const std::vector<DocumentChunk> &chunks) {
  const auto &first = ValidateBatch(chunks);
  std::scoped_lock lock(mutex_);
  InsertChunks(chunks_, chunks);
  return CollectionAlias(first.embedding_model_id,
                         first.embedding_model_version,
                         first.dense_embedding.size());
}

std::vector<DocumentHit>
InMemoryDocumentStore::HybridSearch(const DocumentQuery &query) {
  const auto errors = Validate(query);
  if (!errors.empty()) {
    throw DocumentStoreError(errors.front(), false);
  }
  const std::set<std::string> acl_scope(query.allowed_acl_ids.begin(),
                                        query.allowed_acl_ids.end());
  const auto query_terms = Terms(query.text);
  struct Candidate {
    const DocumentChunk *chunk;
    double dense;
    double lexical;
    double fused;
  };
  std::vector<Candidate> candidates;
  std::scoped_lock lock(mutex_);
  for (const auto & [ chunk_id, chunk ] : chunks_) {
    static_cast<void>(chunk_id);
    if (chunk.tenant_id != query.tenant_id ||
        !acl_scope.contains(chunk.acl_id) ||
        chunk.embedding_model_id != query.embedding_model_id ||
        chunk.embedding_model_version != query.embedding_model_version ||
        chunk.dense_embedding.size() != query.dense_embedding.size()) {
      continue;
    }
    candidates.push_back(
        {&chunk, CosineSimilarity(query.dense_embedding, chunk.dense_embedding),
         LexicalScore(query_terms, chunk.content), 0.0});
  }

  auto dense_rank = candidates;
  std::ranges::sort(dense_rank, std::greater{}, &Candidate::dense);
  auto lexical_rank = candidates;
  std::ranges::sort(lexical_rank, std::greater{}, &Candidate::lexical);
  std::unordered_map<std::string, double> rrf;
  for (std::size_t index = 0; index < dense_rank.size(); ++index) {
    rrf[dense_rank[index].chunk->chunk_id] += 1.0 / (60.0 + index + 1.0);
  }
  for (std::size_t index = 0; index < lexical_rank.size(); ++index) {
    rrf[lexical_rank[index].chunk->chunk_id] += 1.0 / (60.0 + index + 1.0);
  }
  for (auto &candidate : candidates) {
    candidate.fused = rrf[candidate.chunk->chunk_id];
  }
  std::ranges::sort(candidates, std::greater{}, &Candidate::fused);

  std::vector<DocumentHit> hits;
  const auto count = std::min<std::size_t>(query.top_k, candidates.size());
  hits.reserve(count);
  for (std::size_t index = 0; index < count; ++index) {
    const auto &candidate = candidates[index];
    hits.push_back({.chunk_id = candidate.chunk->chunk_id,
                    .asset_id = candidate.chunk->asset_id,
                    .asset_version_id = candidate.chunk->asset_version_id,
                    .object_key = candidate.chunk->object_key,
                    .ordinal = candidate.chunk->ordinal,
                    .page_number = candidate.chunk->page_number,
                    .title = candidate.chunk->title,
                    .content = candidate.chunk->content,
                    .score = candidate.fused});
  }
  return hits;
}

} // namespace multimodal::rag::core
