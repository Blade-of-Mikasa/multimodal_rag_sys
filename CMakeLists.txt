cmake_minimum_required(VERSION 3.24)

project(multimodal_rag_sys LANGUAGES CXX)

option(RAG_ENABLE_GRPC "Build the generated Protobuf/gRPC contract" OFF)

enable_testing()
add_subdirectory(core)

if(RAG_ENABLE_GRPC)
    find_package(Protobuf CONFIG REQUIRED)
    find_package(gRPC CONFIG REQUIRED)
    add_subdirectory(proto)
    add_subdirectory(core/grpc)
endif()
