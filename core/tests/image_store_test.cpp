#include "rag_core/image_store.h"

#include <cassert>
#include <string>

using multimodal::rag::core::ImageCollectionAlias;
using multimodal::rag::core::ImageQuery;
using multimodal::rag::core::ImageRecord;
using multimodal::rag::core::InMemoryImageStore;
using multimodal::rag::core::Validate;

namespace {

ImageRecord ExampleImage() {
  return {.image_id = "image-1",
          .tenant_id = "tenant-1",
          .acl_id = "acl-private",
          .asset_id = "asset-1",
          .asset_version_id = "version-1",
          .asset_version = 1,
          .object_key = "tenant-1/images/one.png",
          .media_type = "image/png",
          .width = 800,
          .height = 600,
          .caption = "A red bicycle beside a cafe",
          .ocr_text = "OPEN 24 HOURS",
          .content = "A red bicycle beside a cafe\nOCR:\nOPEN 24 HOURS",
          .content_sha256 = std::string(64, 'a'),
          .dense_embedding = {1.0F, 0.0F},
          .embedding_model_id = "embed-general",
          .embedding_model_version = "v1",
          .vision_model_id = "vision-general",
          .vision_model_version = "v1"};
}

} // namespace

int main() {
  auto image = ExampleImage();
  assert(Validate(image).empty());
  assert(ImageCollectionAlias("embed-general", "v1", 2).starts_with(
      "rag_image_v1_"));

  InMemoryImageStore store;
  const auto alias = store.ReplaceAssetVersion(image);
  assert(alias.starts_with("rag_image_v1_"));
  const ImageQuery query{.tenant_id = "tenant-1",
                         .allowed_acl_ids = {"acl-private"},
                         .text = "red bicycle",
                         .dense_embedding = {1.0F, 0.0F},
                         .embedding_model_id = "embed-general",
                         .embedding_model_version = "v1",
                         .top_k = 5};
  const auto hits = store.HybridSearch(query);
  assert(hits.size() == 1);
  assert(hits.front().image_id == "image-1");
  assert(hits.front().ocr_text == "OPEN 24 HOURS");

  auto replacement = image;
  replacement.image_id = "image-2";
  replacement.caption = "A blue train";
  replacement.content = replacement.caption;
  store.ReplaceAssetVersion(replacement);
  const auto replacement_hits = store.HybridSearch(query);
  assert(replacement_hits.size() == 1);
  assert(replacement_hits.front().image_id == "image-2");

  auto invalid = image;
  invalid.media_type = "image/gif";
  assert(!Validate(invalid).empty());

  const ImageQuery denied{.tenant_id = "tenant-1",
                          .allowed_acl_ids = {"acl-other"},
                          .text = "train",
                          .dense_embedding = {1.0F, 0.0F},
                          .embedding_model_id = "embed-general",
                          .embedding_model_version = "v1",
                          .top_k = 5};
  assert(store.HybridSearch(denied).empty());
  return 0;
}
