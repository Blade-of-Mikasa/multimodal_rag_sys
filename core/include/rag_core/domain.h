#pragma once

#include <cstdint>
#include <string>
#include <vector>

namespace multimodal::rag::core {

enum class SourceScope : std::uint8_t {
  kUnspecified = 0,
  kLocal = 1,
  kWeb = 2,
};

enum class Modality : std::uint8_t {
  kUnspecified = 0,
  kDocument = 1,
  kImage = 2,
  kVideo = 3,
};

struct RetrievalRoute {
  std::string route_id;
  std::string query;
  SourceScope source_scope{SourceScope::kUnspecified};
  Modality modality{Modality::kUnspecified};
  std::uint32_t top_k{10};
  std::uint32_t timeout_ms{2000};
};

struct ExecutionPlan {
  std::string request_id;
  std::string user_id;
  std::string conversation_id;
  std::vector<RetrievalRoute> routes;
  std::vector<std::string> allowed_acl_ids;
};

inline constexpr std::uint32_t kMaxRouteCount = 6;
inline constexpr std::uint32_t kMaxTopK = 200;
inline constexpr std::uint32_t kMinTimeoutMs = 100;
inline constexpr std::uint32_t kMaxTimeoutMs = 30'000;

std::vector<std::string> Validate(const RetrievalRoute& route);
std::vector<std::string> Validate(const ExecutionPlan& plan);

}  // namespace multimodal::rag::core
