set(RAG_PROTO_FILE "${CMAKE_CURRENT_SOURCE_DIR}/rag_core.proto")
set(RAG_CPP_GENERATED_DIR "${CMAKE_BINARY_DIR}/generated/cpp")

set(RAG_PROTO_SOURCES
    "${RAG_CPP_GENERATED_DIR}/rag_core.pb.cc"
    "${RAG_CPP_GENERATED_DIR}/rag_core.grpc.pb.cc"
)
set(RAG_PROTO_HEADERS
    "${RAG_CPP_GENERATED_DIR}/rag_core.pb.h"
    "${RAG_CPP_GENERATED_DIR}/rag_core.grpc.pb.h"
)

add_custom_command(
    OUTPUT ${RAG_PROTO_SOURCES} ${RAG_PROTO_HEADERS}
    COMMAND "${CMAKE_COMMAND}" -E make_directory "${RAG_CPP_GENERATED_DIR}"
    COMMAND $<TARGET_FILE:protobuf::protoc>
    ARGS
        "--proto_path=${CMAKE_CURRENT_SOURCE_DIR}"
        "--cpp_out=${RAG_CPP_GENERATED_DIR}"
        "--grpc_out=${RAG_CPP_GENERATED_DIR}"
        "--plugin=protoc-gen-grpc=$<TARGET_FILE:gRPC::grpc_cpp_plugin>"
        "${RAG_PROTO_FILE}"
    DEPENDS "${RAG_PROTO_FILE}" protobuf::protoc gRPC::grpc_cpp_plugin
    COMMENT "Generating C++ Protobuf and gRPC sources from rag_core.proto"
    VERBATIM
)

add_library(rag_proto STATIC ${RAG_PROTO_SOURCES} ${RAG_PROTO_HEADERS})
target_include_directories(rag_proto PUBLIC "${RAG_CPP_GENERATED_DIR}")
target_link_libraries(rag_proto PUBLIC protobuf::libprotobuf gRPC::grpc++)
target_compile_features(rag_proto PUBLIC cxx_std_20)

add_executable(rag_proto_contract_test tests/generated_contract_test.cpp)
target_link_libraries(rag_proto_contract_test PRIVATE rag_proto)
target_compile_features(rag_proto_contract_test PRIVATE cxx_std_20)

add_test(NAME rag_proto_contract_test COMMAND rag_proto_contract_test)
