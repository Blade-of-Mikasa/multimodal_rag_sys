#include "rag_core/video_store.h"

#include <cassert>
#include <string>

using multimodal::rag::core::InMemoryVideoStore;
using multimodal::rag::core::Validate;
using multimodal::rag::core::VideoCollectionAlias;
using multimodal::rag::core::VideoQuery;
using multimodal::rag::core::VideoSegment;

namespace {

VideoSegment ExampleSegment() {
  return {.segment_id = "segment-1",
          .tenant_id = "tenant-1",
          .acl_id = "acl-private",
          .asset_id = "asset-1",
          .asset_version_id = "version-1",
          .asset_version = 1,
          .object_key = "tenant-1/videos/one.mp4",
          .ordinal = 0,
          .media_type = "video/mp4",
          .duration_ms = 120'000,
          .width = 1920,
          .height = 1080,
          .start_ms = 0,
          .end_ms = 60'000,
          .keyframe_ms = 0,
          .caption = "A speaker explains hybrid retrieval",
          .ocr_text = "RRF",
          .transcript = "Dense and sparse candidates are fused",
          .content = "hybrid retrieval RRF dense sparse candidates",
          .content_sha256 = std::string(64, 'a'),
          .dense_embedding = {1.0F, 0.0F},
          .embedding_model_id = "embed-general",
          .embedding_model_version = "v1",
          .vision_model_id = "vision-general",
          .vision_model_version = "v1",
          .speech_model_id = "speech-general",
          .speech_model_version = "v1"};
}

} // namespace

int main() {
  auto segment = ExampleSegment();
  assert(Validate(segment).empty());
  assert(VideoCollectionAlias("embed-general", "v1", 2).starts_with(
      "rag_video_v1_"));

  InMemoryVideoStore store;
  const auto alias = store.ReplaceAssetVersion({segment});
  assert(alias.starts_with("rag_video_v1_"));
  const VideoQuery query{.tenant_id = "tenant-1",
                         .allowed_acl_ids = {"acl-private"},
                         .text = "hybrid retrieval",
                         .dense_embedding = {1.0F, 0.0F},
                         .embedding_model_id = "embed-general",
                         .embedding_model_version = "v1",
                         .top_k = 5};
  const auto hits = store.HybridSearch(query);
  assert(hits.size() == 1);
  assert(hits.front().segment_id == "segment-1");
  assert(hits.front().end_ms == 60'000);

  auto second = segment;
  second.segment_id = "segment-2";
  second.ordinal = 1;
  second.start_ms = 60'000;
  second.end_ms = 120'000;
  second.keyframe_ms = 60'000;
  store.AppendAssetVersion({second});
  assert(store.HybridSearch(query).size() == 2);

  auto replacement = second;
  replacement.segment_id = "segment-new";
  store.ReplaceAssetVersion({replacement});
  const auto replacement_hits = store.HybridSearch(query);
  assert(replacement_hits.size() == 1);
  assert(replacement_hits.front().segment_id == "segment-new");

  auto invalid = segment;
  invalid.end_ms = invalid.start_ms;
  assert(!Validate(invalid).empty());

  const VideoQuery denied{.tenant_id = "tenant-1",
                          .allowed_acl_ids = {"acl-other"},
                          .text = "retrieval",
                          .dense_embedding = {1.0F, 0.0F},
                          .embedding_model_id = "embed-general",
                          .embedding_model_version = "v1",
                          .top_k = 5};
  assert(store.HybridSearch(denied).empty());
  return 0;
}
