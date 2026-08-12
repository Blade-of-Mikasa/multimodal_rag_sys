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

#include "rag_core/grpc_service.h"

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

  multimodal::rag::core::RagCoreServiceImpl service;
  grpc::ServerBuilder builder;
  int selected_port = 0;
  builder.AddListeningPort(listen_address, grpc::InsecureServerCredentials(),
                           &selected_port);
  builder.RegisterService(&service);

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
