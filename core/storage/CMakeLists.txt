add_library(rag_core_milvus_store
    src/milvus_document_store.cpp
    src/milvus_image_store.cpp
)

target_include_directories(rag_core_milvus_store
    PUBLIC
        ${CMAKE_CURRENT_SOURCE_DIR}/include
)

target_link_libraries(rag_core_milvus_store
    PUBLIC
        rag_core_domain
    PRIVATE
        milvus_sdk
)
target_compile_features(rag_core_milvus_store PUBLIC cxx_std_20)

if(MSVC)
    target_compile_options(rag_core_milvus_store PRIVATE /W4 /WX)
else()
    target_compile_options(
        rag_core_milvus_store
        PRIVATE
            -Wall
            -Wextra
            -Wpedantic
            -Werror
    )
endif()
