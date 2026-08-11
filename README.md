# multimodal_rag_sys

多来源、多模态 RAG 系统。业务表层使用 Python，性能核心使用 C++20，二者通过版本化 gRPC/Protobuf 契约通信。

## 当前阶段

项目按模块迭代，长期决策与进度记录在 [`plan/global_memory.md`](plan/global_memory.md)。

当前已开始建设工程骨架与跨语言契约：

```text
React
  → Python API / Planner / Model & Web adapters
  → gRPC / Protobuf
  → C++20 RAG & Index Core
  → Milvus / MySQL / Redis / S3 / Kafka
```

## 基础验证

M00 不要求本机预装 CMake、Conan 或 protoc，可以直接运行：

```bash
./scripts/verify_foundation.sh
```

该脚本会执行 Python 领域模型与 Proto 契约测试，并使用系统 C++20 编译器构建和运行 C++ 领域模型测试。
