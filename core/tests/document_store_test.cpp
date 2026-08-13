#include "rag_core/document_store.h"

#include <cstdlib>
#include <iostream>
#include <string>
#include <vector>

namespace {

using multimodal::rag::core::CollectionAlias;
using multimodal::rag::core::DocumentChunk;
using multimodal::rag::core::DocumentQuery;
using multimodal::rag::core::DocumentStoreError;
using multimodal::rag::core::InMemoryDocumentStore;

void Require(bool condition, const std::string &message) {
  if (!condition) {
    std::cerr << "FAILED: " << message << '\n';
    std::exit(EXIT_FAILURE);
  }
}

DocumentChunk Chunk(std::string chunk_id, std::string tenant_id,
                    std::string acl_id, std::string asset_version_id,
                    std::string content, std::vector<float> embedding,
                    std::uint32_t ordinal = 0) {
  return {
      .chunk_id = std::move(chunk_id),
      .tenant_id = std::move(tenant_id),
      .acl_id = std::move(acl_id),
      .asset_id = "asset-1",
      .asset_version_id = std::move(asset_version_id),
      .asset_version = 1,
      .object_key = "tenant-1/asset-1/v1/document.txt",
      .ordinal = ordinal,
      .page_number = 1,
      .title = "Architecture",
      .content = std::move(content),
      .content_sha256 = std::string(64, 'a'),
      .dense_embedding = std::move(embedding),
      .embedding_model_id = "embedding-general",
      .embedding_model_version = "v1",
  };
}

DocumentQuery Query(std::string tenant_id, std::string acl_id) {
  return {
      .tenant_id = std::move(tenant_id),
      .allowed_acl_ids = {std::move(acl_id)},
      .text = "milvus architecture",
      .dense_embedding = {1.0F, 0.0F},
      .embedding_model_id = "embedding-general",
      .embedding_model_version = "v1",
      .top_k = 10,
  };
}

void TestReplaceAndHybridSearch() {
  InMemoryDocumentStore store;
  const auto alias = store.ReplaceAssetVersion({
      Chunk("chunk-dense", "tenant-1", "acl-a", "version-1",
            "dense vector storage", {0.8F, 0.6F}),
      Chunk("chunk-keyword", "tenant-1", "acl-a", "version-1",
            "milvus architecture guide", {1.0F, 0.0F}, 1),
  });
  store.ReplaceAssetVersion({
      Chunk("chunk-private", "tenant-1", "acl-b", "version-private",
            "milvus architecture secret", {1.0F, 0.0F}),
  });
  store.ReplaceAssetVersion({
      Chunk("chunk-other-tenant", "tenant-2", "acl-a", "version-other",
            "milvus architecture foreign", {1.0F, 0.0F}),
  });

  Require(alias == CollectionAlias("embedding-general", "v1", 2),
          "replace should select collection by model version and dimension");
  const auto hits = store.HybridSearch(Query("tenant-1", "acl-a"));
  Require(hits.size() == 2, "tenant and ACL filters must isolate results");
  Require(hits.front().chunk_id == "chunk-keyword",
          "RRF should fuse dense and lexical ranks");

  store.AppendAssetVersion({
      Chunk("chunk-appended", "tenant-1", "acl-a", "version-1",
            "later gRPC batch", {0.5F, 0.5F}, 2),
  });
  Require(store.HybridSearch(Query("tenant-1", "acl-a")).size() == 3,
          "later batches must append without deleting the first batch");

  store.ReplaceAssetVersion({
      Chunk("chunk-replaced", "tenant-1", "acl-a", "version-1",
            "replacement content", {1.0F, 0.0F}),
  });
  const auto replaced = store.HybridSearch(Query("tenant-1", "acl-a"));
  Require(replaced.size() == 1 && replaced.front().chunk_id == "chunk-replaced",
          "retrying from the first batch must replace all previous chunks");
}

void TestInvalidBatch() {
  InMemoryDocumentStore store;
  auto second = Chunk("chunk-2", "tenant-1", "acl-a", "version-2", "second",
                      {1.0F, 0.0F});
  bool rejected = false;
  try {
    store.ReplaceAssetVersion({
        Chunk("chunk-1", "tenant-1", "acl-a", "version-1", "first",
              {1.0F, 0.0F}),
        second,
    });
  } catch (const DocumentStoreError &error) {
    rejected = !error.retryable();
  }
  Require(rejected, "mixed asset versions must be rejected permanently");
}

void TestCollectionAliasTracksModelChanges() {
  Require(CollectionAlias("model", "v1", 2) !=
              CollectionAlias("model", "v2", 2),
          "model version changes require a distinct collection");
  Require(CollectionAlias("model", "v1", 2) !=
              CollectionAlias("model", "v1", 3),
          "embedding dimension changes require a distinct collection");
}

} // namespace

int main() {
  TestReplaceAndHybridSearch();
  TestInvalidBatch();
  TestCollectionAliasTracksModelChanges();
  std::cout << "rag_core_document_store_test: PASS\n";
  return EXIT_SUCCESS;
}
