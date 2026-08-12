# multimodal_rag_sys

多来源、多模态 RAG 系统。业务表层使用 Python，性能核心使用 C++20，二者通过版本化 gRPC/Protobuf 契约通信。

## 当前阶段

项目按模块迭代，长期决策与进度记录在 [`plan/global_memory.md`](plan/global_memory.md)。

当前已完成工程骨架、跨语言契约、可复现的依赖与代码生成基线，提供可连通的
Python API 与 C++ Core 基础服务，并建立 MySQL 元数据及版本化迁移：

```text
React
  → Python API / Planner / Model & Web adapters
  → gRPC / Protobuf
  → C++20 RAG & Index Core
  → Milvus / MySQL / Redis / S3 / Kafka
```

MySQL 元数据表、事务边界和迁移方式见
[`docs/mysql_metadata.md`](docs/mysql_metadata.md)。

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

验证 MySQL 资产、版本、任务、会话和 ACL 模型，并使用 MySQL 方言离线编译
Alembic 的升级与回退迁移：

```bash
./scripts/verify_mysql_metadata.sh
```

M03 完整验证会编译并启动 C++ Core，使用 Python 调用 `Health` 与 `ExecutePlan`，并验证 HTTP 就绪检查：

```bash
./scripts/verify_core_service.sh
```

验证 Python API 的配置、健康检查、请求 ID、统一错误响应和 SSE 流式协议：

```bash
./scripts/verify_python_api.sh
```

完整验证会重新生成 Python 与 C++ 的 Protobuf/gRPC 代码，编译全部 C++ 测试，并运行双端契约检查：

```bash
./scripts/verify_codegen.sh
```

生成物只保留在 `build/generated/python` 和 `build/cpp/generated/cpp`，不提交到仓库。若只需运行不依赖第三方组件的 M00 快速验证：

```bash
./scripts/verify_foundation.sh
```

## 运行 Python API 与 C++ Core

先生成契约并编译 C++ 服务：

```bash
./scripts/generate_proto.sh
./build/cpp/core/grpc/rag_core_server --listen 127.0.0.1:50051
```

在另一个终端启动 Python API。Python 客户端需要能找到生成在 `build/generated/python` 下的 gRPC 模块：

```bash
PYTHONPATH="${PWD}/build/generated/python" \
  .venv/bin/uvicorn rag_api.main:app \
  --app-dir services/python_api/src \
  --host 127.0.0.1 \
  --port 8000
```

| 接口 | 用途 |
|---|---|
| `GET /health/live` | 进程存活检查 |
| `GET /health/ready` | 流量就绪检查；实时探测 C++ Core，不可用时返回 503 |
| `POST /api/v1/queries/stream` | SSE 流式协议骨架；当前明确返回 `pipeline_not_connected` |

所有响应都会返回 `X-Request-ID`。可通过 `RAG_ENVIRONMENT`、`RAG_API_PREFIX`、`RAG_DEBUG`、`RAG_CORE_GRPC_TARGET`、`RAG_CORE_GRPC_TIMEOUT_SECONDS` 和 `RAG_MYSQL_DSN` 等环境变量覆盖默认配置。

当前 gRPC 使用明文连接且默认只监听 `127.0.0.1`，用于本地开发与服务骨架验证；生产部署必须配合受控服务网络或补充 TLS。
