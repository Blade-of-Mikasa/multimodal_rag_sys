#include "rag_core/domain.h"

#include <cstdlib>
#include <iostream>
#include <string>

namespace {

using multimodal::rag::core::ExecutionPlan;
using multimodal::rag::core::Modality;
using multimodal::rag::core::RetrievalRoute;
using multimodal::rag::core::SourceScope;
using multimodal::rag::core::Validate;

void Require(bool condition, const std::string &message) {
  if (!condition) {
    std::cerr << "FAILED: " << message << '\n';
    std::exit(EXIT_FAILURE);
  }
}

RetrievalRoute ValidRoute() {
  return RetrievalRoute{
      .route_id = "route-1",
      .query = "multimodal RAG architecture",
      .source_scope = SourceScope::kLocal,
      .modality = Modality::kDocument,
      .top_k = 20,
      .timeout_ms = 1500,
      .dense_embedding = {1.0F, 0.0F},
      .embedding_model_id = "embedding-general",
      .embedding_model_version = "v1",
  };
}

void TestValidPlan() {
  ExecutionPlan plan{
      .request_id = "request-1",
      .user_id = "user-1",
      .conversation_id = "conversation-1",
      .tenant_id = "tenant-1",
      .routes = {ValidRoute()},
      .allowed_acl_ids = {"public"},
  };

  Require(Validate(plan).empty(), "valid plan should pass validation");
}

void TestInvalidRoute() {
  auto route = ValidRoute();
  route.query.clear();
  route.top_k = 0;
  route.timeout_ms = 50;

  Require(Validate(route).size() == 3,
          "invalid route should report three errors");
}

void TestDuplicateRouteId() {
  auto first = ValidRoute();
  auto second = ValidRoute();
  second.query = "second query";

  ExecutionPlan plan{
      .request_id = "request-2",
      .tenant_id = "tenant-1",
      .routes = {first, second},
      .allowed_acl_ids = {"public"},
  };

  const auto errors = Validate(plan);
  Require(errors.size() == 1, "duplicate route IDs should be rejected");
  Require(errors.front() == "route_id must be unique",
          "duplicate ID error should be stable");
}

} // namespace

int main() {
  TestValidPlan();
  TestInvalidRoute();
  TestDuplicateRouteId();
  std::cout << "rag_core_domain_test: PASS\n";
  return EXIT_SUCCESS;
}
