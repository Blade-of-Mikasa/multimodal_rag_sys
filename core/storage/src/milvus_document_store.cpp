#include "rag_core/milvus_document_store.h"

#include <algorithm>
#include <cstdint>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <utility>

#include "milvus/MilvusClientV2.h"

namespace multimodal::rag::core {
namespace {

constexpr char kChunkId[] = "chunk_id";
constexpr char kTenantId[] = "tenant_id";
constexpr char kAclId[] = "acl_id";
constexpr char kAssetId[] = "asset_id";
constexpr char kAssetVersionId[] = "asset_version_id";
constexpr char kAssetVersion[] = "asset_version";
constexpr char kObjectKey[] = "object_key";
constexpr char kOrdinal[] = "ordinal";
constexpr char kPageNumber[] = "page_number";
constexpr char kTitle[] = "title";
constexpr char kContent[] = "content";
constexpr char kContentSha256[] = "content_sha256";
constexpr char kEmbeddingModelId[] = "embedding_model_id";
constexpr char kEmbeddingModelVersion[] = "embedding_model_version";
constexpr char kDense[] = "dense";
constexpr char kSparse[] = "sparse";

bool Retryable(const milvus::Status &status) {
  return status.Code() != milvus::StatusCode::INVALID_ARGUMENT &&
         status.Code() != milvus::StatusCode::INVALID_AGUMENT &&
         status.Code() != milvus::StatusCode::DIMENSION_NOT_EQUAL &&
         status.Code() != milvus::StatusCode::VECTOR_IS_EMPTY &&
         status.Code() != milvus::StatusCode::JSON_PARSE_ERROR &&
         status.Code() != milvus::StatusCode::DATA_UNMATCH_SCHEMA;
}

void RequireOk(const std::string &operation, const milvus::Status &status) {
  if (!status.IsOk()) {
    throw DocumentStoreError(operation + ": " + status.Message(),
                             Retryable(status));
  }
}

std::shared_ptr<milvus::CollectionSchema>
CollectionSchema(std::size_t dimension, const std::string &analyzer_params) {
  auto schema = std::make_shared<milvus::CollectionSchema>();
  schema->SetEnableDynamicField(false);
  schema->AddField(milvus::FieldSchema(kChunkId, milvus::DataType::VARCHAR,
                                       "deterministic chunk id", true, false)
                       .WithMaxLength(128));
  schema->AddField(milvus::FieldSchema(kTenantId, milvus::DataType::VARCHAR)
                       .WithMaxLength(128));
  schema->AddField(milvus::FieldSchema(kAclId, milvus::DataType::VARCHAR)
                       .WithMaxLength(128));
  schema->AddField(milvus::FieldSchema(kAssetId, milvus::DataType::VARCHAR)
                       .WithMaxLength(128));
  schema->AddField(
      milvus::FieldSchema(kAssetVersionId, milvus::DataType::VARCHAR)
          .WithMaxLength(128));
  schema->AddField(milvus::FieldSchema(kAssetVersion, milvus::DataType::INT64));
  schema->AddField(milvus::FieldSchema(kObjectKey, milvus::DataType::VARCHAR)
                       .WithMaxLength(2048));
  schema->AddField(milvus::FieldSchema(kOrdinal, milvus::DataType::INT64));
  schema->AddField(milvus::FieldSchema(kPageNumber, milvus::DataType::INT64));
  schema->AddField(milvus::FieldSchema(kTitle, milvus::DataType::VARCHAR)
                       .WithMaxLength(2048));
  schema->AddField(
      milvus::FieldSchema(kContent, milvus::DataType::VARCHAR)
          .WithMaxLength(65'535)
          .EnableAnalyzer(true)
          .WithAnalyzerParams(nlohmann::json::parse(analyzer_params)));
  schema->AddField(
      milvus::FieldSchema(kContentSha256, milvus::DataType::VARCHAR)
          .WithMaxLength(64));
  schema->AddField(
      milvus::FieldSchema(kEmbeddingModelId, milvus::DataType::VARCHAR)
          .WithMaxLength(256));
  schema->AddField(
      milvus::FieldSchema(kEmbeddingModelVersion, milvus::DataType::VARCHAR)
          .WithMaxLength(256));
  schema->AddField(milvus::FieldSchema(kDense, milvus::DataType::FLOAT_VECTOR)
                       .WithDimension(static_cast<std::uint32_t>(dimension)));
  schema->AddField(
      milvus::FieldSchema(kSparse, milvus::DataType::SPARSE_FLOAT_VECTOR));

  auto bm25 = std::make_shared<milvus::Function>("content_bm25",
                                                 milvus::FunctionType::BM25);
  RequireOk("configure BM25 input", bm25->AddInputFieldName(kContent));
  RequireOk("configure BM25 output", bm25->AddOutputFieldName(kSparse));
  schema->AddFunction(std::move(bm25));
  return schema;
}

milvus::IndexDesc DenseIndex() {
  milvus::IndexDesc index(kDense, "dense_hnsw", milvus::IndexType::HNSW,
                          milvus::MetricType::COSINE);
  RequireOk("configure HNSW M", index.AddExtraParam("M", "32"));
  RequireOk("configure HNSW efConstruction",
            index.AddExtraParam("efConstruction", "200"));
  return index;
}

milvus::IndexDesc SparseIndex() {
  milvus::IndexDesc index(kSparse, "content_bm25_inverted",
                          milvus::IndexType::SPARSE_INVERTED_INDEX,
                          milvus::MetricType::BM25);
  RequireOk("configure BM25 inverted algorithm",
            index.AddExtraParam("inverted_index_algo", "DAAT_MAXSCORE"));
  RequireOk("configure BM25 k1", index.AddExtraParam("bm25_k1", "1.2"));
  RequireOk("configure BM25 b", index.AddExtraParam("bm25_b", "0.75"));
  return index;
}

std::string SearchFilter() {
  return std::string(kTenantId) + " == {tenant} and " + kAclId +
         " in {acl_scope}";
}

void AddSearchFilter(const std::shared_ptr<milvus::SubSearchRequest> &request,
                     const DocumentQuery &query) {
  request->WithFilter(SearchFilter());
  RequireOk("bind tenant filter",
            request->AddFilterTemplate("tenant", query.tenant_id));
  RequireOk("bind ACL filter",
            request->AddFilterTemplate("acl_scope", query.allowed_acl_ids));
}

} // namespace

class MilvusDocumentStore::Impl {
public:
  explicit Impl(MilvusDocumentStoreConfig config)
      : config_(std::move(config)), client_(milvus::MilvusClientV2::Create()) {
    if (config_.uri.empty() || config_.database.empty() ||
        config_.analyzer_params.empty() || config_.rpc_deadline_ms == 0) {
      throw DocumentStoreError("Milvus configuration must not be empty", false);
    }
    try {
      static_cast<void>(nlohmann::json::parse(config_.analyzer_params));
    } catch (const nlohmann::json::exception &error) {
      throw DocumentStoreError(std::string("invalid Milvus analyzer_params: ") +
                                   error.what(),
                               false);
    }
    milvus::ConnectParam connect(config_.uri, config_.token);
    connect.WithRpcDeadlineMs(config_.rpc_deadline_ms);
    RequireOk("connect to Milvus", client_->Connect(connect));
  }

  ~Impl() {
    if (client_) {
      static_cast<void>(client_->Disconnect());
    }
  }

  std::string ReplaceAssetVersion(const std::vector<DocumentChunk> &chunks) {
    ValidateBatch(chunks);
    const auto &first = chunks.front();
    const auto collection =
        CollectionAlias(first.embedding_model_id, first.embedding_model_version,
                        first.dense_embedding.size());
    EnsureCollection(collection, first.dense_embedding.size());

    std::scoped_lock lock(replace_mutex_);
    milvus::DeleteResponse delete_response;
    auto delete_request =
        milvus::DeleteRequest()
            .WithDatabaseName(config_.database)
            .WithCollectionName(collection)
            .WithFilter(std::string(kTenantId) + " == {tenant} and " +
                        kAssetVersionId + " == {asset_version}");
    delete_request.SetFilterTemplates(
        std::unordered_map<std::string, nlohmann::json>{
            {"tenant", first.tenant_id},
            {"asset_version", first.asset_version_id},
        });
    RequireOk("delete previous asset-version chunks",
              client_->Delete(delete_request, delete_response));

    UpsertChunks(collection, chunks);
    return collection;
  }

  std::string AppendAssetVersion(const std::vector<DocumentChunk> &chunks) {
    ValidateBatch(chunks);
    const auto &first = chunks.front();
    const auto collection =
        CollectionAlias(first.embedding_model_id, first.embedding_model_version,
                        first.dense_embedding.size());
    EnsureCollection(collection, first.dense_embedding.size());
    std::scoped_lock lock(replace_mutex_);
    UpsertChunks(collection, chunks);
    return collection;
  }

  std::vector<DocumentHit> HybridSearch(const DocumentQuery &query) {
    const auto errors = Validate(query);
    if (!errors.empty()) {
      throw DocumentStoreError(errors.front(), false);
    }
    const auto collection =
        CollectionAlias(query.embedding_model_id, query.embedding_model_version,
                        query.dense_embedding.size());
    if (!LoadCollectionIfExists(collection)) {
      return {};
    }

    const auto candidate_count = std::min<std::uint32_t>(
        200, std::max<std::uint32_t>(query.top_k, query.top_k * 4));
    auto dense = std::make_shared<milvus::SubSearchRequest>();
    dense->WithAnnsField(kDense)
        .WithMetricType(milvus::MetricType::COSINE)
        .WithLimit(candidate_count)
        .AddFloatVector(query.dense_embedding);
    RequireOk("configure dense search ef", dense->AddExtraParam("ef", "128"));
    AddSearchFilter(dense, query);

    auto sparse = std::make_shared<milvus::SubSearchRequest>();
    sparse->WithAnnsField(kSparse)
        .WithMetricType(milvus::MetricType::BM25)
        .WithLimit(candidate_count)
        .AddEmbeddedText(query.text);
    AddSearchFilter(sparse, query);

    milvus::HybridSearchRequest request;
    request.WithDatabaseName(config_.database)
        .WithCollectionName(collection)
        .AddSubRequest(dense)
        .AddSubRequest(sparse)
        .WithRerank(std::make_shared<milvus::RRFRerank>(60))
        .WithLimit(query.top_k)
        .AddOutputField(kChunkId)
        .AddOutputField(kAssetId)
        .AddOutputField(kAssetVersionId)
        .AddOutputField(kObjectKey)
        .AddOutputField(kOrdinal)
        .AddOutputField(kPageNumber)
        .AddOutputField(kTitle)
        .AddOutputField(kContent)
        .AddOutputField(kContentSha256)
        .WithConsistencyLevel(milvus::ConsistencyLevel::BOUNDED);

    milvus::HybridSearchResponse response;
    RequireOk("hybrid search", client_->HybridSearch(request, response));
    if (response.Results().Results().empty()) {
      return {};
    }
    const auto &result = response.Results().Results().front();
    milvus::EntityRows output_rows;
    RequireOk("decode hybrid search rows", result.OutputRows(output_rows));
    const auto &scores = result.Scores();
    if (output_rows.size() != scores.size()) {
      throw DocumentStoreError("Milvus returned mismatched rows and scores",
                               true);
    }

    std::vector<DocumentHit> hits;
    hits.reserve(output_rows.size());
    try {
      for (std::size_t index = 0; index < output_rows.size(); ++index) {
        const auto &row = output_rows[index];
        hits.push_back({
            .chunk_id = row.at(kChunkId).get<std::string>(),
            .asset_id = row.at(kAssetId).get<std::string>(),
            .asset_version_id = row.at(kAssetVersionId).get<std::string>(),
            .object_key = row.at(kObjectKey).get<std::string>(),
            .ordinal = row.at(kOrdinal).get<std::uint32_t>(),
            .page_number = row.at(kPageNumber).get<std::uint32_t>(),
            .title = row.at(kTitle).get<std::string>(),
            .content = row.at(kContent).get<std::string>(),
            .content_sha256 = row.at(kContentSha256).get<std::string>(),
            .score = scores[index],
        });
      }
    } catch (const nlohmann::json::exception &error) {
      throw DocumentStoreError(
          std::string("invalid Milvus search row: ") + error.what(), true);
    }
    return hits;
  }

private:
  void UpsertChunks(const std::string &collection,
                    const std::vector<DocumentChunk> &chunks) {
    milvus::EntityRows rows;
    rows.reserve(chunks.size());
    for (const auto &chunk : chunks) {
      milvus::EntityRow row;
      row[kChunkId] = chunk.chunk_id;
      row[kTenantId] = chunk.tenant_id;
      row[kAclId] = chunk.acl_id;
      row[kAssetId] = chunk.asset_id;
      row[kAssetVersionId] = chunk.asset_version_id;
      row[kAssetVersion] = chunk.asset_version;
      row[kObjectKey] = chunk.object_key;
      row[kOrdinal] = chunk.ordinal;
      row[kPageNumber] = chunk.page_number;
      row[kTitle] = chunk.title;
      row[kContent] = chunk.content;
      row[kContentSha256] = chunk.content_sha256;
      row[kEmbeddingModelId] = chunk.embedding_model_id;
      row[kEmbeddingModelVersion] = chunk.embedding_model_version;
      row[kDense] = chunk.dense_embedding;
      rows.emplace_back(std::move(row));
    }
    milvus::UpsertResponse upsert_response;
    auto upsert_request = milvus::UpsertRequest()
                              .WithDatabaseName(config_.database)
                              .WithCollectionName(collection)
                              .WithRowsData(std::move(rows));
    RequireOk("upsert document chunks",
              client_->Upsert(upsert_request, upsert_response));
  }
  static void ValidateBatch(const std::vector<DocumentChunk> &chunks) {
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
  }

  bool CollectionExists(const std::string &collection) {
    milvus::HasCollectionResponse response;
    RequireOk("check Milvus collection",
              client_->HasCollection(milvus::HasCollectionRequest()
                                         .WithDatabaseName(config_.database)
                                         .WithCollectionName(collection),
                                     response));
    return response.Has();
  }

  bool LoadCollectionIfExists(const std::string &collection) {
    std::scoped_lock lock(schema_mutex_);
    if (ready_collections_.contains(collection)) {
      return true;
    }
    if (!CollectionExists(collection)) {
      return false;
    }
    RequireOk("load existing Milvus document collection",
              client_->LoadCollection(milvus::LoadCollectionRequest()
                                          .WithDatabaseName(config_.database)
                                          .WithCollectionName(collection)));
    ready_collections_.insert(collection);
    return true;
  }

  void EnsureCollection(const std::string &collection, std::size_t dimension) {
    std::scoped_lock lock(schema_mutex_);
    if (ready_collections_.contains(collection)) {
      return;
    }
    if (CollectionExists(collection)) {
      RequireOk("load existing Milvus document collection",
                client_->LoadCollection(milvus::LoadCollectionRequest()
                                            .WithDatabaseName(config_.database)
                                            .WithCollectionName(collection)));
      ready_collections_.insert(collection);
      return;
    }
    auto status =
        client_->CreateCollection(milvus::CreateCollectionRequest()
                                      .WithDatabaseName(config_.database)
                                      .WithCollectionName(collection)
                                      .WithCollectionSchema(CollectionSchema(
                                          dimension, config_.analyzer_params)));
    if (!status.IsOk() && !CollectionExists(collection)) {
      RequireOk("create Milvus document collection", status);
    }

    auto dense_index = DenseIndex();
    auto sparse_index = SparseIndex();
    RequireOk("create Milvus document indexes",
              client_->CreateIndex(milvus::CreateIndexRequest()
                                       .WithDatabaseName(config_.database)
                                       .WithCollectionName(collection)
                                       .AddIndex(std::move(dense_index))
                                       .AddIndex(std::move(sparse_index))));
    RequireOk("load Milvus document collection",
              client_->LoadCollection(milvus::LoadCollectionRequest()
                                          .WithDatabaseName(config_.database)
                                          .WithCollectionName(collection)));
    ready_collections_.insert(collection);
  }

  MilvusDocumentStoreConfig config_;
  milvus::MilvusClientV2Ptr client_;
  std::mutex schema_mutex_;
  std::mutex replace_mutex_;
  std::unordered_set<std::string> ready_collections_;
};

MilvusDocumentStore::MilvusDocumentStore(MilvusDocumentStoreConfig config)
    : impl_(std::make_unique<Impl>(std::move(config))) {}

MilvusDocumentStore::~MilvusDocumentStore() = default;
MilvusDocumentStore::MilvusDocumentStore(MilvusDocumentStore &&) noexcept =
    default;
MilvusDocumentStore &MilvusDocumentStore::
operator=(MilvusDocumentStore &&) noexcept = default;

std::string MilvusDocumentStore::ReplaceAssetVersion(
    const std::vector<DocumentChunk> &chunks) {
  return impl_->ReplaceAssetVersion(chunks);
}

std::string MilvusDocumentStore::AppendAssetVersion(
    const std::vector<DocumentChunk> &chunks) {
  return impl_->AppendAssetVersion(chunks);
}

std::vector<DocumentHit>
MilvusDocumentStore::HybridSearch(const DocumentQuery &query) {
  return impl_->HybridSearch(query);
}

} // namespace multimodal::rag::core
