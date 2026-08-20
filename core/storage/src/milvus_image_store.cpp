#include "rag_core/milvus_image_store.h"

#include <algorithm>
#include <cstdint>
#include <memory>
#include <mutex>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <utility>

#include "milvus/MilvusClientV2.h"

namespace multimodal::rag::core {
namespace {

constexpr char kImageId[] = "image_id";
constexpr char kTenantId[] = "tenant_id";
constexpr char kAclId[] = "acl_id";
constexpr char kAssetId[] = "asset_id";
constexpr char kAssetVersionId[] = "asset_version_id";
constexpr char kAssetVersion[] = "asset_version";
constexpr char kObjectKey[] = "object_key";
constexpr char kMediaType[] = "media_type";
constexpr char kWidth[] = "width";
constexpr char kHeight[] = "height";
constexpr char kCaption[] = "caption";
constexpr char kOcrText[] = "ocr_text";
constexpr char kContent[] = "content";
constexpr char kContentSha256[] = "content_sha256";
constexpr char kEmbeddingModelId[] = "embedding_model_id";
constexpr char kEmbeddingModelVersion[] = "embedding_model_version";
constexpr char kVisionModelId[] = "vision_model_id";
constexpr char kVisionModelVersion[] = "vision_model_version";
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
    throw ImageStoreError(operation + ": " + status.Message(),
                          Retryable(status));
  }
}

std::shared_ptr<milvus::CollectionSchema>
CollectionSchema(std::size_t dimension, const std::string &analyzer_params) {
  auto schema = std::make_shared<milvus::CollectionSchema>();
  schema->SetEnableDynamicField(false);
  schema->AddField(milvus::FieldSchema(kImageId, milvus::DataType::VARCHAR,
                                       "deterministic image id", true, false)
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
  schema->AddField(milvus::FieldSchema(kMediaType, milvus::DataType::VARCHAR)
                       .WithMaxLength(32));
  schema->AddField(milvus::FieldSchema(kWidth, milvus::DataType::INT64));
  schema->AddField(milvus::FieldSchema(kHeight, milvus::DataType::INT64));
  schema->AddField(milvus::FieldSchema(kCaption, milvus::DataType::VARCHAR)
                       .WithMaxLength(2048));
  schema->AddField(milvus::FieldSchema(kOcrText, milvus::DataType::VARCHAR)
                       .WithMaxLength(60'000));
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
  schema->AddField(milvus::FieldSchema(kVisionModelId, milvus::DataType::VARCHAR)
                       .WithMaxLength(256));
  schema->AddField(
      milvus::FieldSchema(kVisionModelVersion, milvus::DataType::VARCHAR)
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
                     const ImageQuery &query) {
  request->WithFilter(SearchFilter());
  RequireOk("bind tenant filter",
            request->AddFilterTemplate("tenant", query.tenant_id));
  RequireOk("bind ACL filter",
            request->AddFilterTemplate("acl_scope", query.allowed_acl_ids));
}

} // namespace

class MilvusImageStore::Impl {
public:
  explicit Impl(MilvusImageStoreConfig config)
      : config_(std::move(config)), client_(milvus::MilvusClientV2::Create()) {
    if (config_.uri.empty() || config_.database.empty() ||
        config_.analyzer_params.empty() || config_.rpc_deadline_ms == 0) {
      throw ImageStoreError("Milvus configuration must not be empty", false);
    }
    try {
      static_cast<void>(nlohmann::json::parse(config_.analyzer_params));
    } catch (const nlohmann::json::exception &error) {
      throw ImageStoreError(std::string("invalid Milvus analyzer_params: ") +
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

  std::string ReplaceAssetVersion(const ImageRecord &image) {
    const auto errors = Validate(image);
    if (!errors.empty()) {
      throw ImageStoreError(errors.front(), false);
    }
    const auto collection = ImageCollectionAlias(
        image.embedding_model_id, image.embedding_model_version,
        image.dense_embedding.size());
    EnsureCollection(collection, image.dense_embedding.size());

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
            {"tenant", image.tenant_id},
            {"asset_version", image.asset_version_id},
        });
    RequireOk("delete previous asset-version image",
              client_->Delete(delete_request, delete_response));

    milvus::EntityRow row;
    row[kImageId] = image.image_id;
    row[kTenantId] = image.tenant_id;
    row[kAclId] = image.acl_id;
    row[kAssetId] = image.asset_id;
    row[kAssetVersionId] = image.asset_version_id;
    row[kAssetVersion] = image.asset_version;
    row[kObjectKey] = image.object_key;
    row[kMediaType] = image.media_type;
    row[kWidth] = image.width;
    row[kHeight] = image.height;
    row[kCaption] = image.caption;
    row[kOcrText] = image.ocr_text;
    row[kContent] = image.content;
    row[kContentSha256] = image.content_sha256;
    row[kEmbeddingModelId] = image.embedding_model_id;
    row[kEmbeddingModelVersion] = image.embedding_model_version;
    row[kVisionModelId] = image.vision_model_id;
    row[kVisionModelVersion] = image.vision_model_version;
    row[kDense] = image.dense_embedding;
    milvus::EntityRows rows;
    rows.emplace_back(std::move(row));
    milvus::UpsertResponse upsert_response;
    auto upsert_request = milvus::UpsertRequest()
                              .WithDatabaseName(config_.database)
                              .WithCollectionName(collection)
                              .WithRowsData(std::move(rows));
    RequireOk("upsert image", client_->Upsert(upsert_request, upsert_response));
    return collection;
  }

  std::vector<ImageHit> HybridSearch(const ImageQuery &query) {
    const auto errors = Validate(query);
    if (!errors.empty()) {
      throw ImageStoreError(errors.front(), false);
    }
    const auto collection = ImageCollectionAlias(
        query.embedding_model_id, query.embedding_model_version,
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
        .AddOutputField(kImageId)
        .AddOutputField(kAssetId)
        .AddOutputField(kAssetVersionId)
        .AddOutputField(kObjectKey)
        .AddOutputField(kMediaType)
        .AddOutputField(kWidth)
        .AddOutputField(kHeight)
        .AddOutputField(kCaption)
        .AddOutputField(kOcrText)
        .AddOutputField(kContent)
        .AddOutputField(kContentSha256)
        .WithConsistencyLevel(milvus::ConsistencyLevel::BOUNDED);

    milvus::HybridSearchResponse response;
    RequireOk("hybrid image search", client_->HybridSearch(request, response));
    if (response.Results().Results().empty()) {
      return {};
    }
    const auto &result = response.Results().Results().front();
    milvus::EntityRows output_rows;
    RequireOk("decode hybrid image rows", result.OutputRows(output_rows));
    const auto &scores = result.Scores();
    if (output_rows.size() != scores.size()) {
      throw ImageStoreError("Milvus returned mismatched rows and scores", true);
    }

    std::vector<ImageHit> hits;
    hits.reserve(output_rows.size());
    try {
      for (std::size_t index = 0; index < output_rows.size(); ++index) {
        const auto &row = output_rows[index];
        hits.push_back({
            .image_id = row.at(kImageId).get<std::string>(),
            .asset_id = row.at(kAssetId).get<std::string>(),
            .asset_version_id = row.at(kAssetVersionId).get<std::string>(),
            .object_key = row.at(kObjectKey).get<std::string>(),
            .media_type = row.at(kMediaType).get<std::string>(),
            .width = row.at(kWidth).get<std::uint32_t>(),
            .height = row.at(kHeight).get<std::uint32_t>(),
            .caption = row.at(kCaption).get<std::string>(),
            .ocr_text = row.at(kOcrText).get<std::string>(),
            .content = row.at(kContent).get<std::string>(),
            .content_sha256 = row.at(kContentSha256).get<std::string>(),
            .score = scores[index],
        });
      }
    } catch (const nlohmann::json::exception &error) {
      throw ImageStoreError(
          std::string("invalid Milvus image search row: ") + error.what(), true);
    }
    return hits;
  }

private:
  bool CollectionExists(const std::string &collection) {
    milvus::HasCollectionResponse response;
    RequireOk("check Milvus image collection",
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
    RequireOk("load existing Milvus image collection",
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
      RequireOk("load existing Milvus image collection",
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
      RequireOk("create Milvus image collection", status);
    }

    auto dense_index = DenseIndex();
    auto sparse_index = SparseIndex();
    RequireOk("create Milvus image indexes",
              client_->CreateIndex(milvus::CreateIndexRequest()
                                       .WithDatabaseName(config_.database)
                                       .WithCollectionName(collection)
                                       .AddIndex(std::move(dense_index))
                                       .AddIndex(std::move(sparse_index))));
    RequireOk("load Milvus image collection",
              client_->LoadCollection(milvus::LoadCollectionRequest()
                                          .WithDatabaseName(config_.database)
                                          .WithCollectionName(collection)));
    ready_collections_.insert(collection);
  }

  MilvusImageStoreConfig config_;
  milvus::MilvusClientV2Ptr client_;
  std::mutex schema_mutex_;
  std::mutex replace_mutex_;
  std::unordered_set<std::string> ready_collections_;
};

MilvusImageStore::MilvusImageStore(MilvusImageStoreConfig config)
    : impl_(std::make_unique<Impl>(std::move(config))) {}

MilvusImageStore::~MilvusImageStore() = default;
MilvusImageStore::MilvusImageStore(MilvusImageStore &&) noexcept = default;
MilvusImageStore &
MilvusImageStore::operator=(MilvusImageStore &&) noexcept = default;

std::string MilvusImageStore::ReplaceAssetVersion(const ImageRecord &image) {
  return impl_->ReplaceAssetVersion(image);
}

std::vector<ImageHit>
MilvusImageStore::HybridSearch(const ImageQuery &query) {
  return impl_->HybridSearch(query);
}

} // namespace multimodal::rag::core
