# 多来源多模态 RAG 全局记忆

> 这是项目推进的长期事实源。每完成一个模块，都必须在模块分支中简明更新本文件，推送分支并创建 MR；`main` 只通过 Review 后的 MR 合并更新。

## 1. 固定架构决策

- 展示层：React + TypeScript + Vite。
- 业务表层：Python 3.11+，负责 FastAPI、鉴权、会话、Query Planner、模型通用接口、联网搜索与网页抽取、Kafka 任务编排、评估和流式回答。
- 核心层：C++20，负责多路召回调度、Milvus 混合检索、结果归一化、指纹与去重、确定性冲突规则、上下文与引用构建、批量索引写入。
- Python/C++ 边界：默认使用 gRPC + Protobuf 独立进程通信；仅无阻塞、无独立扩缩容需求的纯本地算法考虑 pybind11。
- 模型：应用只依赖 `ChatModel`、`EmbeddingModel`、`VisionModel`、`SpeechToTextModel` 内部接口，供应商实现可替换。
- 数据组件：Milvus、MySQL、Redis、S3 协议对象存储、Kafka。
- MySQL 基线：MySQL 8.0+、InnoDB、utf8mb4；Python 使用 SQLAlchemy 2.0 + asyncmy，数据库版本只通过 Alembic 迁移。
- 对象存储基线：应用依赖自有 `ObjectStore` 接口，默认适配 S3 SigV4；直传必须签入大小、类型、SHA-256、资产标识和条件写入，完成回调只信任服务端 `HeadObject` 结果。
- 本地文本检索：Milvus dense vector + BM25，不在 MVP 额外部署 Elasticsearch。
- 文档索引：Python 负责有界下载、PDF/TXT/Markdown 解析、稳定切片和通用
  Embedding API；C++ `DocumentStore` 负责 Milvus collection、批量 upsert、
  HNSW/COSINE + BM25 双路召回、RRF 融合，以及 tenant/ACL 参数化过滤。
- 图片索引：Python 使用 Pillow 对 JPEG/PNG/WebP 做签名校验、像素预算、EXIF
  纠正、缩放和无元数据重编码；通用 Vision API 生成 Caption/OCR，再由与查询相同
  的文本 Embedding API 生成图片向量。C++ `ImageStore` 使用独立
  `rag_image_v1_*` collection 做 dense+BM25+RRF 检索并返回图片 Evidence。
- 视频索引：Python 把原视频流式写入受控临时目录，使用 FFprobe/FFmpeg 做容器与
  资源预算校验、8 分钟音频分片、场景关键帧和 60 秒最大间隔兜底；通用
  `SpeechToTextModel` 返回带时间戳 ASR，复用 `VisionModel` 生成关键帧 Caption/OCR，
  再组合为时间片文本并向量化。C++ `VideoStore` 使用独立 `rag_video_v1_*`
  collection 做 dense+BM25+RRF 检索，并返回 `start_ms/end_ms/keyframe_ms` Evidence。
- 文档与图片处理器必须在同一个 Kafka consumer group 进程内按媒体类型路由；禁止
  部署多个“只认识一种媒体”的竞争消费者，以免 Kafka 随机把消息分给错误处理器。
- Embedding 模型 ID、版本和维度共同定义 collection 边界；模型升级新建 collection
  并回灌切流，禁止把不同语义空间的向量混入同一集合。
- 联网检索：统一 `SearchProvider` 接口；传统 Bing Search API 已退役，不作为可直接调用的默认实现。
- 原始文件只进入对象存储；Kafka 消息只传 `asset_id`、`version`、`object_key` 等任务元数据。
- 代码集成：每个模块使用独立 `codex/...` 分支；模块分支推送后创建 MR，后续修正继续推送同一 MR，Review 通过后再合并，禁止直接推送 `main`；PR 描述、变更摘要、验证结果和 Review 重点统一使用中文。

## 2. 模块完成规则

一个模块只有同时满足以下条件才算完成：

1. 模块代码、契约或文档已落库。
2. 有可重复执行的验证方式，并已在本地通过。
3. 本文件的“模块状态”和“更新日志”已同步更新。
4. 变更已提交并推送模块分支，已创建 MR；Review 通过后由 MR 合并到 `main`。
5. 遇到有复用价值或排查成本较高的问题时，更新本地
   `plan/engineering_journal.local.md`；该文件由 `.gitignore` 排除，禁止提交。

## 3. 模块状态

| ID | 模块 | 状态 | 完成标准 |
|---|---|---|---|
| M00 | 工程骨架与跨语言契约 | 已完成 | Python/C++ 目录、v1 Proto 契约、双端领域模型与基础验证通过 |
| M01 | 依赖与代码生成基线 | 已完成 | Python 环境、CMake/Conan、Protobuf/gRPC 双端代码生成可复现 |
| M02 | Python API 基础服务 | 已完成 | 配置、健康检查、request_id、错误模型和流式响应骨架可运行 |
| M03 | C++ Core gRPC 基础服务 | 已完成 | Health 与 ExecutePlan 空实现可由 Python 调通 |
| M04 | MySQL 元数据与迁移 | 已完成 | 资产、版本、任务、会话、权限基础表与迁移完成 |
| M05 | 对象存储与上传链路 | 已完成 | 预签名上传、资产登记、文件校验完成 |
| M06 | Kafka 入库任务链路 | Review 中 | ingest/retry/DLQ、幂等消费与状态流转完成 |
| M07 | 文档入库与 Milvus 检索 | 已完成（随 PR #7 合入 M06 分支） | 文档解析、切片、Embedding、dense+BM25 召回闭环完成 |
| M08 | 图片入库与召回 | 已完成（随 PR #8 合入 M06 分支） | Caption、OCR、向量化与图片证据返回完成 |
| M09 | 视频入库与召回 | Review 中 | ASR、场景切分、关键帧与时间片段召回完成 |
| M10 | 联网搜索与网页抽取 | 待开始 | SearchProvider、正文抽取、来源时间与失败降级完成 |
| M11 | 证据治理与上下文构建 | 待开始 | 去重、冲突规则、Token 预算与 citation 映射完成 |
| M12 | 最终生成与前端问答 | 待开始 | 基于证据生成、流式回答和引用展示完成 |
| M13 | 评估与可观测性 | 待开始 | 分阶段评估、OpenTelemetry、核心指标和报告完成 |

## 4. 当前工作快照

- 当前模块：M09 视频入库与召回开发、文档和验证已完成，
  [PR #9](https://github.com/Blade-of-Mikasa/multimodal_rag_sys/pull/9) Review 中。
  [PR #8](https://github.com/Blade-of-Mikasa/multimodal_rag_sys/pull/8) 已合入
  `codex/m06-kafka-ingestion`；[PR #6](https://github.com/Blade-of-Mikasa/multimodal_rag_sys/pull/6)
  仍是 M06+M07+M08 进入 `main` 的集成路径。
- 当前分支：`codex/m09-video-ingestion-retrieval`，目标分支为
  `codex/m06-kafka-ingestion`；PR #6 合入主线后应把 M09 PR base 切回 `main`。
- 依赖基线：Python 工具链由 `requirements/tooling.lock` 锁定；C++ 工具链由 `conanfile.py` 和 `conan.lock` 锁定，CMake 也由 Conan 提供，不依赖系统预装。
- API/Core 基线：FastAPI 通过异步 `GrpcCoreClient` 调用独立 C++ Core 进程；Core 提供 `Health` 和空结果 `ExecutePlan`，HTTP `/health/ready` 实时探测 Core，不可用时返回 503。
- MySQL 基线：7 张基础表覆盖 ACL、资产、版本、入库任务、会话和消息；任务唯一幂等键及 Kafka 投递字段为 M06 的至少一次消费预留事务边界。
- 上传基线：前端通过短期预签名 URL 直传对象存储；Python 两阶段 API 负责资产登记、服务端对象校验，以及在同一 MySQL 事务中更新处理状态并创建唯一入库任务。当前单次 PUT 上限 5 GB。
- Kafka 基线：Python 使用 aiokafka 正式 AsyncIO API；生产者固定幂等与 `acks=all`，消费者关闭自动提交。MySQL transactional outbox 通过租约发布 ingest/retry/DLQ，消费者先落库再提交 offset，并以任务状态、attempt、处理租约 heartbeat 和有界指数退避实现至少一次下的幂等恢复。
- 文档入库基线：独立 Python worker 从 S3 有界下载并复核大小/SHA-256，解析 PDF、
  UTF-8 TXT/Markdown，按结构稳定切片并分批调用 OpenAI-compatible Embedding；
  C++ `IndexCoreService` 以稳定 chunk ID 写入 `DocumentStore`。Python 按 Protobuf
  实际字节数把 gRPC 索引请求控制在 3 MB，首批 replace、续批 append，任务重试从
  replace 首批重新构建。
- 文档检索基线：Milvus C++ SDK 固定 v2.6.6 精确 commit；每个模型 ID/版本/维度
  使用独立 collection，dense HNSW/COSINE 与服务端 BM25 sparse 候选通过 RRF
  融合，两路均强制 tenant/ACL filter。默认 ICU analyzer 适配中英混合文本。
- 图片入库基线：统一 `rag-ingest-worker` 有界下载并复核图片大小/SHA-256；Pillow
  12.3.0 只解码 JPEG/PNG/WebP 静态图，限制 20 MB/2500 万像素，应用 EXIF
  orientation 后最长边缩至 4096 并重编码。通用 Responses 形状的 Vision 接口以
  严格 JSON Schema 返回 Caption/OCR，组合文本再进入通用 Embedding 接口。
- 图片检索基线：C++ 内存/Milvus `ImageStore` 使用独立模型版本化 collection，保存
  原图尺寸、媒体类型、Caption、OCR 与模型溯源；dense HNSW/COSINE 和 Caption+OCR
  服务端 BM25 经 RRF 融合，并在两路召回前执行 tenant/ACL 参数化过滤。
- 视频入库基线：统一 `rag-ingest-worker` 支持 MP4/QuickTime/WebM，把对象流式写入
  临时文件并复核大小/SHA-256。FFprobe 限制容器、单视频流、4 小时时长、单边尺寸
  和像素预算；FFmpeg 生成 16 kHz 单声道 WAV 分片，以及场景变化或最长 60 秒间隔
  的 JPEG 关键帧。子进程不用 shell，并限制总超时和 stdout/stderr 字节数。
- 视频语义基线：通用 ASR multipart 接口必须返回 segment 时间戳，分片局部时间会
  转换为视频全局毫秒；关键帧 Caption/OCR 与区间 Transcript 组合成稳定 UUIDv5
  时间片，再进入通用文本 Embedding。关键帧预算若无法覆盖完整时间轴会明确失败，
  禁止静默生成超长尾片段。
- 视频检索基线：C++ 内存/Milvus `VideoStore` 保存时间片范围、关键帧、三类模型
  溯源和组合文本；独立 `rag_video_v1_*` collection 使用 HNSW/COSINE、服务端
  BM25、RRF 和 tenant/ACL 前置过滤，Video Evidence 携带播放器跳转所需毫秒范围。
- 传输安全：当前 gRPC 使用明文连接且默认只监听 `127.0.0.1`，仅作为本地与服务骨架基线；生产部署需使用受控服务网络或 TLS。
- 环境说明：Apple Clang 21 环境首次初始化需要从源码构建部分 C++ 依赖；缓存位于仓库 `build/conan-home`，后续可复用。
- 最近验证：`./scripts/verify_video_retrieval.sh` 通过；全量 106 项 Python 测试中
  101 项通过、5 项仅因常规发现阶段未启动 Core 跳过，脚本启动真实 C++ Core 后
  5 项 Python→C++ 进程级测试全部通过（含视频索引与 VIDEO route 召回）；普通与
  `RAG_ENABLE_MILVUS=ON` 两套 C++ 构建均 6/6 测试通过，官方 Milvus C++ SDK、
  文档/图片/视频适配器和服务端完成编译。`pip check` 与 `git diff --check` 通过。
  开发机未安装 FFmpeg，真实编解码 smoke 明确跳过；命令、时间戳和覆盖预算由 fake
  process 单测验证。开发机也未启动真实 Milvus/模型网关/Kafka/MySQL，因此视频
  collection 在线创建、实际编解码、供应商 ASR/Vision 兼容性及故障注入仍需集成环境验证。
- 下一步：Review [PR #9](https://github.com/Blade-of-Mikasa/multimodal_rag_sys/pull/9)；
  PR #6 合入 `main` 后将 M09 PR base 切回 `main`。
  M09 合并后开始 M10 联网搜索与网页抽取。

## 5. 更新日志

- 2026-08-11：创建全局记忆，冻结 Python 表层 / C++20 内核边界，建立 M00-M13 模块清单；开始 M00。
- 2026-08-11：完成 M00。新增 v1 Protobuf 服务契约、Python/C++ 领域模型、CMake 骨架和无第三方依赖的基础验证脚本；验证全部通过。
- 2026-08-11：完成 M01。锁定 Python 与 Conan/C++ 依赖，新增一键依赖初始化、Python/C++ Protobuf/gRPC 代码生成及双端生成契约测试；完整验证通过。
- 2026-08-11：M02 开发完成，[MR #1](https://github.com/Blade-of-Mikasa/multimodal_rag_sys/pull/1) Review 中。新增 FastAPI 配置与应用工厂、健康检查、请求 ID 中间件、统一错误包络、SSE 流式响应骨架和 14 项 Python 测试；跨模块回归与真实 HTTP 冒烟测试通过。
- 2026-08-12：M02 通过 [MR #1](https://github.com/Blade-of-Mikasa/multimodal_rag_sys/pull/1) 合并，状态更新为已完成。
- 2026-08-12：M03 开发完成，[PR #2](https://github.com/Blade-of-Mikasa/multimodal_rag_sys/pull/2) Review 中。新增 C++ Core gRPC 服务进程、`Health` 与空结果 `ExecutePlan` 实现、Python 异步 Core 客户端、真实 Core 就绪检查和进程级集成测试；完整验证通过。
- 2026-08-12：M03 通过 [PR #2](https://github.com/Blade-of-Mikasa/multimodal_rag_sys/pull/2) 合并，状态更新为已完成。
- 2026-08-12：M04 开发完成，[PR #3](https://github.com/Blade-of-Mikasa/multimodal_rag_sys/pull/3) Review 中。新增 7 张 MySQL 8.0 基础表、异步 SQLAlchemy 会话、首版 Alembic 升降级迁移和专项验证；离线 MySQL 方言及全量基础回归通过。
- 2026-08-12：M04 通过 [PR #3](https://github.com/Blade-of-Mikasa/multimodal_rag_sys/pull/3) 合并，状态更新为已完成。
- 2026-08-12：M05 开发完成，[PR #4](https://github.com/Blade-of-Mikasa/multimodal_rag_sys/pull/4) Review 中。新增 S3 兼容异步适配、两阶段直传 API、服务端 SHA-256/大小/类型校验、条件写入保护，以及事务化幂等任务登记；专项与基础回归通过。
- 2026-08-13：M06 开发完成，[PR #5](https://github.com/Blade-of-Mikasa/multimodal_rag_sys/pull/5) Review 中。新增版本化 Kafka 契约、MySQL transactional outbox、幂等生产与手动提交消费、处理租约 heartbeat、有界重试及 poison/终态 DLQ；71 项 Python 测试与基础回归通过。PR 暂时堆叠在尚未合并的 PR #4 上。
- 2026-08-13：M05 通过 [PR #4](https://github.com/Blade-of-Mikasa/multimodal_rag_sys/pull/4) 合并，状态更新为已完成。因堆叠 PR #5 随后只合入旧 M05 分支，新增 [PR #6](https://github.com/Blade-of-Mikasa/multimodal_rag_sys/pull/6) 将同一批 M06 内容补入 `main`。
- 2026-08-13：M07 开发完成，[PR #7](https://github.com/Blade-of-Mikasa/multimodal_rag_sys/pull/7) Review 中。新增文档 worker、有界对象下载、
  PDF/TXT/Markdown 解析、稳定切片、通用 Embedding HTTP 适配、按字节分批的
  IndexAsset gRPC 协议，以及 C++ 内存/Milvus `DocumentStore`；实现 HNSW dense、
  服务端 BM25、RRF、tenant/ACL 过滤与模型版本化 collection。完整 Python、
  Python→C++ 进程级闭环及 Milvus-enabled C++ 编译测试通过。
- 2026-08-13：M08 开发完成，[PR #8](https://github.com/Blade-of-Mikasa/multimodal_rag_sys/pull/8)
  Review 中。新增统一媒体入库 worker、Pillow 图片安全
  归一化、通用 Vision Responses 适配、Caption/OCR+文本 Embedding 管线，以及
  C++ 内存/Milvus `ImageStore`；图片使用独立 collection，通过 HNSW dense、服务端
  BM25、RRF、tenant/ACL 过滤返回 Evidence。94 项 Python 测试、4 项真实
  Python→C++ 集成测试和普通/Milvus-enabled 两套 5 项 C++ 测试通过。
- 2026-08-15：M08 通过 [PR #8](https://github.com/Blade-of-Mikasa/multimodal_rag_sys/pull/8)
  合入 `codex/m06-kafka-ingestion`，M09 从包含 M06-M08 的完整分支继续开发。
- 2026-08-15：M09 开发完成，[PR #9](https://github.com/Blade-of-Mikasa/multimodal_rag_sys/pull/9)
  Review 中。新增视频流式落盘、FFprobe/FFmpeg 安全
  探测、8 分钟 WAV 分片、通用带时间戳 ASR、场景关键帧+最大间隔兜底、
  Caption/OCR/Transcript 时间片，以及 C++ 内存/Milvus `VideoStore`；Video
  Evidence 返回可播放区间。106 项 Python 测试、5 项真实 Python→C++ 集成测试及
  普通/Milvus-enabled 两套 6 项 C++ 测试通过；本机因未安装 FFmpeg 跳过真实编解码。
