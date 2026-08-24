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
对象存储直传、完整性校验和资产登记流程见
[`docs/object_storage_upload.md`](docs/object_storage_upload.md)。
文档解析、通用 Embedding 与 Milvus dense+BM25 混合检索见
[`docs/document_milvus_retrieval.md`](docs/document_milvus_retrieval.md)。
图片安全归一化、通用 Vision 适配与独立 Milvus 图片检索见
[`docs/image_milvus_retrieval.md`](docs/image_milvus_retrieval.md)。
视频流式下载、FFmpeg 场景切分、通用 ASR 与 Milvus 时间片检索见
[`docs/video_milvus_retrieval.md`](docs/video_milvus_retrieval.md)。
证据约束生成、POST-over-SSE 协议、React 问答台和架构流程演示见
[`docs/answer_generation_ui.md`](docs/answer_generation_ui.md)。
分阶段离线评估、报告口径、OpenTelemetry traces/metrics 与成本估算见
[`docs/evaluation_observability.md`](docs/evaluation_observability.md)。

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

验证视频流式下载、FFmpeg 命令契约、带时间戳 ASR、关键帧、Python/C++ 时间片
闭环和 Milvus 视频适配器编译：

```bash
./scripts/verify_video_retrieval.sh
```

验证图片安全解码、Caption/OCR、通用 Embedding、统一 Kafka 路由、Python/C++
闭环和 Milvus 图片适配器编译：

```bash
./scripts/verify_image_retrieval.sh
```

验证文档下载、解析、稳定切片、通用 Embedding、Python/C++ 入库检索闭环，
并编译 Milvus C++ 适配器：

```bash
./scripts/verify_document_retrieval.sh
```

验证 Kafka 事务 outbox、版本化消息、幂等消费、重试、DLQ 和手动提交顺序：

```bash
./scripts/verify_kafka_ingestion.sh
```

验证 S3 预签名、对象完整性检查、上传 API 和幂等任务登记：

```bash
./scripts/verify_object_storage.sh
```

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

验证 M12 查询规划、通用 ChatModel、回答编排、引用审计、SSE 及前端流解析：

```bash
./scripts/verify_answer_ui.sh
```

该脚本默认不执行前端生产构建；按 Codebase Pipeline 的人工编译约定，可在人工启动时
设置 `RAG_VERIFY_FRONTEND_BUILD=1`。

验证 M13 评估报告与 OpenTelemetry 埋点（不会启动编译阶段或外部服务）：

```bash
./scripts/verify_evaluation_observability.sh
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
| `POST /api/v1/assets/uploads` | 登记资产并创建受大小、类型、SHA-256 约束的预签名 PUT |
| `POST /api/v1/assets/{asset_id}/versions/{version}/complete` | 校验对象并幂等创建入库任务 |
| `POST /api/v1/queries/stream` | 查询规划、本地/联网检索、证据治理、回答与引用的 POST-over-SSE 流 |

所有响应都会返回 `X-Request-ID`。可通过 `RAG_ENVIRONMENT`、`RAG_API_PREFIX`、
`RAG_DEBUG`、`RAG_CORE_GRPC_TARGET`、`RAG_CORE_GRPC_TIMEOUT_SECONDS`、
`RAG_MYSQL_DSN`、`RAG_OBJECT_STORAGE_*`、`RAG_EMBEDDING_*`、`RAG_CHAT_*` 和
`RAG_VISION_*`、`RAG_SPEECH_*`、`RAG_VIDEO_*` 等环境变量覆盖默认配置。

本地启动前端（Vite 开发代理会注入仅供本地使用的演示身份头）：

```bash
cd services/web_ui
npm install
npm run dev
```

生产环境必须由完成认证的可信网关删除客户端同名头后重新注入
`X-Tenant-ID`、`X-User-ID` 与 `X-ACL-IDs`，不能直接暴露当前 API 让浏览器自报身份。

当前 gRPC 使用明文连接且默认只监听 `127.0.0.1`，用于本地开发与服务骨架验证；生产部署必须配合受控服务网络或补充 TLS。
