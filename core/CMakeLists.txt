add_library(rag_core_domain
    src/domain.cpp
    src/evidence.cpp
    src/document_store.cpp
    src/image_store.cpp
    src/video_store.cpp
)

target_include_directories(rag_core_domain
    PUBLIC
        ${CMAKE_CURRENT_SOURCE_DIR}/include
)

target_compile_features(rag_core_domain PUBLIC cxx_std_20)

if(MSVC)
    target_compile_options(rag_core_domain PRIVATE /W4 /WX)
else()
    target_compile_options(rag_core_domain PRIVATE -Wall -Wextra -Wpedantic -Werror)
endif()

add_executable(rag_core_domain_test
    tests/domain_test.cpp
)

target_link_libraries(rag_core_domain_test PRIVATE rag_core_domain)
target_compile_features(rag_core_domain_test PRIVATE cxx_std_20)

add_test(NAME rag_core_domain_test COMMAND rag_core_domain_test)

add_executable(rag_core_evidence_test
    tests/evidence_test.cpp
)

target_link_libraries(rag_core_evidence_test PRIVATE rag_core_domain)
target_compile_features(rag_core_evidence_test PRIVATE cxx_std_20)

add_test(NAME rag_core_evidence_test COMMAND rag_core_evidence_test)

add_executable(rag_core_document_store_test
    tests/document_store_test.cpp
)

target_link_libraries(rag_core_document_store_test PRIVATE rag_core_domain)
target_compile_features(rag_core_document_store_test PRIVATE cxx_std_20)

add_test(NAME rag_core_document_store_test COMMAND rag_core_document_store_test)

add_executable(rag_core_image_store_test
    tests/image_store_test.cpp
)

target_link_libraries(rag_core_image_store_test PRIVATE rag_core_domain)
target_compile_features(rag_core_image_store_test PRIVATE cxx_std_20)

add_test(NAME rag_core_image_store_test COMMAND rag_core_image_store_test)

add_executable(rag_core_video_store_test
    tests/video_store_test.cpp
)

target_link_libraries(rag_core_video_store_test PRIVATE rag_core_domain)
target_compile_features(rag_core_video_store_test PRIVATE cxx_std_20)

add_test(NAME rag_core_video_store_test COMMAND rag_core_video_store_test)
