#include "rag_core/domain.h"

#include <unordered_set>

namespace multimodal::rag::core {

std::vector<std::string> Validate(const RetrievalRoute& route) {
  std::vector<std::string> errors;

  if (route.route_id.empty()) {
    errors.emplace_back("route_id must not be empty");
  }
  if (route.query.empty()) {
    errors.emplace_back("query must not be empty");
  }
  if (route.source_scope == SourceScope::kUnspecified) {
    errors.emplace_back("source_scope must be specified");
  }
  if (route.modality == Modality::kUnspecified) {
    errors.emplace_back("modality must be specified");
  }
  if (route.top_k == 0 || route.top_k > kMaxTopK) {
    errors.emplace_back("top_k must be between 1 and 200");
  }
  if (route.timeout_ms < kMinTimeoutMs || route.timeout_ms > kMaxTimeoutMs) {
    errors.emplace_back("timeout_ms must be between 100 and 30000");
  }

  return errors;
}

std::vector<std::string> Validate(const ExecutionPlan& plan) {
  std::vector<std::string> errors;

  if (plan.request_id.empty()) {
    errors.emplace_back("request_id must not be empty");
  }
  if (plan.routes.empty()) {
    errors.emplace_back("at least one route is required");
  }
  if (plan.routes.size() > kMaxRouteCount) {
    errors.emplace_back("route count must not exceed 6");
  }

  std::unordered_set<std::string> route_ids;
  for (const auto& route : plan.routes) {
    const auto route_errors = Validate(route);
    errors.insert(errors.end(), route_errors.begin(), route_errors.end());
    if (!route.route_id.empty() && !route_ids.insert(route.route_id).second) {
      errors.emplace_back("route_id must be unique");
    }
  }

  return errors;
}

}  // namespace multimodal::rag::core
