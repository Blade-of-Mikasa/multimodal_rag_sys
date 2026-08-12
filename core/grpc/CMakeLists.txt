add_library(rag_core_grpc_service
    src/grpc_service.cpp
)

target_include_directories(rag_core_grpc_service
    PUBLIC
        ${CMAKE_CURRENT_SOURCE_DIR}/include
)

target_link_libraries(rag_core_grpc_service PUBLIC rag_proto)
target_compile_features(rag_core_grpc_service PUBLIC cxx_std_20)

if(MSVC)
    target_compile_options(rag_core_grpc_service PRIVATE /W4 /WX)
else()
    target_compile_options(
        rag_core_grpc_service
        PRIVATE
            -Wall
            -Wextra
            -Wpedantic
            -Werror
    )
endif()

add_executable(rag_core_server src/server_main.cpp)
target_link_libraries(rag_core_server PRIVATE rag_core_grpc_service)
target_compile_features(rag_core_server PRIVATE cxx_std_20)

if(MSVC)
    target_compile_options(rag_core_server PRIVATE /W4 /WX)
else()
    target_compile_options(
        rag_core_server
        PRIVATE
            -Wall
            -Wextra
            -Wpedantic
            -Werror
    )
endif()

add_executable(rag_core_grpc_service_test tests/grpc_service_test.cpp)
target_link_libraries(rag_core_grpc_service_test PRIVATE rag_core_grpc_service)
target_compile_features(rag_core_grpc_service_test PRIVATE cxx_std_20)

add_test(NAME rag_core_grpc_service_test COMMAND rag_core_grpc_service_test)
