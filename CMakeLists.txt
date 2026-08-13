cmake_minimum_required(VERSION 3.24)

project(multimodal_rag_sys LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20 CACHE STRING "C++ standard for all project dependencies" FORCE)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

option(RAG_ENABLE_GRPC "Build the generated Protobuf/gRPC contract" OFF)
option(RAG_ENABLE_MILVUS "Build the Milvus-backed retrieval stores" OFF)

if(RAG_ENABLE_MILVUS AND NOT RAG_ENABLE_GRPC)
    message(FATAL_ERROR "RAG_ENABLE_MILVUS requires RAG_ENABLE_GRPC=ON")
endif()

enable_testing()
add_subdirectory(core)

if(RAG_ENABLE_GRPC)
    find_package(Protobuf CONFIG REQUIRED)
    find_package(gRPC CONFIG REQUIRED)
    add_subdirectory(proto)

    if(RAG_ENABLE_MILVUS)
        include(FetchContent)
        set(BUILD_FROM_CONAN ON CACHE BOOL "Use Conan dependencies for Milvus SDK" FORCE)
        set(MILVUS_BUILD_TEST OFF CACHE BOOL "Disable Milvus SDK tests" FORCE)
        set(MILVUS_BUILD_EXAMPLES OFF CACHE BOOL "Disable Milvus SDK examples" FORCE)
        set(MILVUS_SDK_VERSION "v2.6.6" CACHE STRING "Pinned Milvus SDK version" FORCE)
        FetchContent_Declare(
            milvus_sdk_cpp
            GIT_REPOSITORY https://github.com/milvus-io/milvus-sdk-cpp.git
            GIT_TAG 9f630df38a55677795fc96d43744d2a6ecf386b8
            GIT_SHALLOW FALSE
        )
        # v2.6.6 unconditionally invokes Homebrew while discovering optional
        # lint tools on macOS. The SDK itself does not require Homebrew.
        set(_RAG_CMAKE_HOST_APPLE "${CMAKE_HOST_APPLE}")
        set(CMAKE_HOST_APPLE FALSE)
        FetchContent_MakeAvailable(milvus_sdk_cpp)
        set(CMAKE_HOST_APPLE "${_RAG_CMAKE_HOST_APPLE}")
        unset(_RAG_CMAKE_HOST_APPLE)
        add_subdirectory(core/storage)
    endif()

    add_subdirectory(core/grpc)
endif()
