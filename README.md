# multimodal_rag_sys

多来源、多模态 RAG 系统。业务表层使用 Python，性能核心使用 C++20，二者通过版本化 gRPC/Protobuf 契约通信。

## 当前阶段

项目按模块迭代，长期决策与进度记录在 [`plan/global_memory.md`](plan/global_memory.md)。

当前已完成工程骨架、跨语言契约，以及可复现的依赖与代码生成基线：

```text
React
  → Python API / Planner / Model & Web adapters
  → gRPC / Protobuf
  → C++20 RAG & Index Core
  → Milvus / MySQL / Redis / S3 / Kafka
```

## 初始化开发环境

需要 Python 3.11+ 和支持 C++20 的编译器。脚本会创建 `.venv`，安装锁定的 Python 工具链，并通过 Conan 安装锁定的 CMake、gRPC/Protobuf 及其 C++ 依赖：

```bash
./scripts/bootstrap_dependencies.sh
```

如果兼容的 Python 不在默认命令中，可显式指定：

```bash
RAG_PYTHON=/path/to/python3 ./scripts/bootstrap_dependencies.sh
```

首次执行可能需要从源码编译 C++ 依赖，后续会复用仓库 `build/conan-home` 下的本地缓存。

## 验证

完整验证会重新生成 Python 与 C++ 的 Protobuf/gRPC 代码，编译全部 C++ 测试，并运行双端契约检查：

```bash
./scripts/verify_codegen.sh
```

生成物只保留在 `build/generated/python` 和 `build/cpp/generated/cpp`，不提交到仓库。若只需运行不依赖第三方组件的 M00 快速验证：

```bash
./scripts/verify_foundation.sh
```
