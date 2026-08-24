#include <atomic>
#include <chrono>
#include <csignal>
#include <cstdlib>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>
#include <thread>

#include <grpcpp/grpcpp.h>

#include "rag_core/document_store.h"
#include "rag_core/grpc_service.h"
#include "rag_core/image_store.h"
#ifdef RAG_HAS_MILVUS
#include "rag_core/milvus_document_store.h"
#include "rag_core/milvus_image_store.h"
#endif

namespace {

std::atomic<bool> shutdown_requested{false};

void HandleSignal(int signal) {
  static_cast<void>(signal);
  shutdown_requested.store(true, std::memory_order_relaxed);
}

std::string ParseListenAddress(int argc, char *argv[]) {
  std::string address = "127.0.0.1:50051";
  for (int index = 1; index < argc; ++index) {
    const std::string argument = argv[index];
    if (argument == "--listen" && index + 1 < argc) {
      address = argv[++index];
      continue;
    }
    throw std::invalid_argument("usage: rag_core_server [--listen HOST:PORT]");
  }
  if (address.empty()) {
    throw std::invalid_argument("listen address must not be empty");
  }
  return address;
}

std::string BoundAddress(const std::string &configured, int selected_port) {
  const auto separator = configured.rfind(':');
  if (separator == std::string::npos) {
    return configured + ':' + std::to_string(selected_port);
  }
  return configured.substr(0, separator + 1) + std::to_string(selected_port);
}

std::string GetEnv(const char *name, std::string fallback = {}) {
  const char *value = std::getenv(name);
  return value == nullptr ? std::move(fallback) : value;
}

std::unique_ptr<multimodal::rag::core::DocumentStore> CreateDocumentStore() {
  const auto backend = GetEnv("RAG_DOCUMENT_STORE", "memory");
  if (backend == "memory") {
    return std::make_unique<multimodal::rag::core::InMemoryDocumentStore>();
  }
  if (backend == "milvus") {
#ifdef RAG_HAS_MILVUS
    multimodal::rag::core::MilvusDocumentStoreConfig config{
        .uri = GetEnv("RAG_MILVUS_URI", "http://127.0.0.1:19530"),
        .token = GetEnv("RAG_MILVUS_TOKEN"),
        .database = GetEnv("RAG_MILVUS_DATABASE", "default"),
        .analyzer_params =
            GetEnv("RAG_MILVUS_ANALYZER_PARAMS",
                   R"({"tokenizer":"icu","filter":["lowercase"]})"),
    };
    return std::make_unique<multimodal::rag::core::MilvusDocumentStore>(
        std::move(config));
#else
    throw std::invalid_argument(
        "RAG_DOCUMENT_STORE=milvus requires RAG_ENABLE_MILVUS=ON");
#endif
  }
  throw std::invalid_argument(
      "RAG_DOCUMENT_STORE must be either memory or milvus");
}

std::unique_ptr<multimodal::rag::core::ImageStore> CreateImageStore() {
  const auto backend =
      GetEnv("RAG_IMAGE_STORE", GetEnv("RAG_DOCUMENT_STORE", "memory"));
  if (backend == "memory") {
    return std::make_unique<multimodal::rag::core::InMemoryImageStore>();
  }
  if (backend == "milvus") {
#ifdef RAG_HAS_MILVUS
    multimodal::rag::core::MilvusImageStoreConfig config{
        .uri = GetEnv("RAG_MILVUS_URI", "http://127.0.0.1:19530"),
        .token = GetEnv("RAG_MILVUS_TOKEN"),
        .database = GetEnv("RAG_MILVUS_DATABASE", "default"),
        .analyzer_params =
            GetEnv("RAG_MILVUS_ANALYZER_PARAMS",
                   R"({"tokenizer":"icu","filter":["lowercase"]})"),
    };
    return std::make_unique<multimodal::rag::core::MilvusImageStore>(
        std::move(config));
#else
    throw std::invalid_argument(
        "RAG_IMAGE_STORE=milvus requires RAG_ENABLE_MILVUS=ON");
#endif
  }
  throw std::invalid_argument(
      "RAG_IMAGE_STORE must be either memory or milvus");
}

} // namespace

int main(int argc, char *argv[]) {
  static_assert(std::atomic<bool>::is_always_lock_free);

  std::string listen_address;
  try {
    listen_address = ParseListenAddress(argc, argv);
  } catch (const std::invalid_argument &error) {
    std::cerr << error.what() << '\n';
    return EXIT_FAILURE;
  }

  std::unique_ptr<multimodal::rag::core::DocumentStore> document_store;
  std::unique_ptr<multimodal::rag::core::ImageStore> image_store;
  try {
    document_store = CreateDocumentStore();
    image_store = CreateImageStore();
  } catch (const std::exception &error) {
    std::cerr << "failed to configure retrieval stores: " << error.what()
              << '\n';
    return EXIT_FAILURE;
  }
  multimodal::rag::core::RagCoreServiceImpl service(document_store.get(),
                                                     image_store.get());
  multimodal::rag::core::IndexCoreServiceImpl index_service(
      document_store.get(), image_store.get());
  grpc::ServerBuilder builder;
  int selected_port = 0;
  builder.AddListeningPort(listen_address, grpc::InsecureServerCredentials(),
                           &selected_port);
  builder.RegisterService(&service);
  builder.RegisterService(&index_service);

  std::unique_ptr<grpc::Server> server = builder.BuildAndStart();
  if (server == nullptr || selected_port == 0) {
    std::cerr << "failed to start gRPC core on " << listen_address << '\n';
    return EXIT_FAILURE;
  }

  std::signal(SIGINT, HandleSignal);
  std::signal(SIGTERM, HandleSignal);

  std::cout << "RAG_CORE_READY address="
            << BoundAddress(listen_address, selected_port) << '\n';
  std::cout.flush();

  std::thread shutdown_watcher([&server] {
    while (!shutdown_requested.load(std::memory_order_relaxed)) {
      std::this_thread::sleep_for(std::chrono::milliseconds(25));
    }
    server->Shutdown();
  });

  server->Wait();
  shutdown_requested.store(true, std::memory_order_relaxed);
  shutdown_watcher.join();
  return EXIT_SUCCESS;
}
