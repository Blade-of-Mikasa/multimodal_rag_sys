#pragma once

#include "rag_core/domain.h"

#include <cstdint>
#include <map>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

namespace multimodal::rag::core {

struct EvidenceItem {
  std::string evidence_id;
  std::string content;
  Modality modality{Modality::kUnspecified};
  SourceScope source_scope{SourceScope::kUnspecified};
  std::string title;
  std::string source;
  std::string url;
  std::int64_t published_at_unix_ms{0};
  std::int64_t retrieved_at_unix_ms{0};
  double score{0.0};
  std::map<std::string, std::string> metadata;
  std::string content_sha256;
};

struct ConflictRecord {
  std::vector<std::string> evidence_ids;
  std::string type;
  std::string reason;
};

struct CitationRecord {
  std::uint32_t citation_id{0};
  std::string evidence_id;
  std::string source;
  std::string url;
  std::string title;
  Modality modality{Modality::kUnspecified};
  std::map<std::string, std::string> metadata;
};

struct EvidenceDecision {
  std::string evidence_id;
  std::string disposition;
  std::string representative_evidence_id;
  std::string reason;
};

struct EvidenceContextOptions {
  std::uint32_t context_token_budget{12'000};
  std::uint32_t max_evidence_tokens{2'000};
  double near_duplicate_threshold{0.95};
};

struct EvidenceContextResult {
  std::vector<EvidenceItem> evidence;
  std::vector<ConflictRecord> conflicts;
  std::string context;
  std::vector<CitationRecord> citations;
  std::vector<EvidenceDecision> decisions;
  std::uint32_t context_token_count{0};
  bool context_truncated{false};
  std::string token_count_method;
};

class EvidenceProcessorError : public std::runtime_error {
public:
  using std::runtime_error::runtime_error;
};

class TokenCounter {
public:
  virtual ~TokenCounter() = default;
  [[nodiscard]] virtual std::uint32_t Count(std::string_view text) const = 0;
  [[nodiscard]] virtual std::string Method() const = 0;
};

class Utf8ByteUpperBoundTokenCounter final : public TokenCounter {
public:
  [[nodiscard]] std::uint32_t Count(std::string_view text) const override;
  [[nodiscard]] std::string Method() const override;
};

class EvidenceProcessor {
public:
  explicit EvidenceProcessor(const TokenCounter *token_counter = nullptr);

  [[nodiscard]] EvidenceContextResult
  Process(const std::vector<EvidenceItem> &input,
          const EvidenceContextOptions &options) const;

private:
  Utf8ByteUpperBoundTokenCounter default_token_counter_;
  const TokenCounter *token_counter_;
};

std::vector<std::string> Validate(const EvidenceItem &evidence);
std::vector<std::string> Validate(const EvidenceContextOptions &options);

} // namespace multimodal::rag::core
