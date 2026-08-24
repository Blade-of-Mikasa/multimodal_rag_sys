# Kafka 入库任务链路

## 可靠性目标

Kafka 与 MySQL 之间没有分布式事务。本模块采用“事务 outbox + 至少一次消费 +
任务级幂等”组合，允许重复消息，但不允许任务静默丢失：

1. M05 在更新资产状态的同一个 MySQL 事务中创建 `queued` 任务；
2. outbox 发布器用 `FOR UPDATE SKIP LOCKED` 批量租约任务；
3. Kafka 确认写入后，才把 topic、partition、offset 和 `published_at` 写回 MySQL；
4. 消费者关闭自动提交，业务结果落库或毒消息写入 DLQ 后才提交 offset；
5. 消息重放由 `task_id`、`dedupe_key`、`attempt`、`last_event_id` 和数据库状态吸收。

生产者固定开启 `enable_idempotence=True` 和 `acks=all`。这能避免生产者重试导致
的常规重复，但无法覆盖“Kafka 已成功、MySQL ACK 写回失败”的跨系统窗口，所以
消费者幂等仍然是必要条件。处理中的任务每隔约三分之一租约时间续租；续租失败会
取消当前处理且不提交 offset，防止长文档、音视频任务超过固定租约后并发执行。

## 数据流

```text
上传完成事务
    │ 创建 queued ingest_task
    ▼
MySQL outbox ──租约──> Publisher ──ingest.requested──> rag.ingest.v1
                                              │
                                              ▼
                                  手动提交的 IngestWorker
                                   │         │          │
                            succeeded      retry       failed
                                   │         │          │
                                   │         └──outbox──> rag.ingest.retry.v1
                                   │                    │
                                   └──先写 MySQL        └──outbox──> rag.ingest.dlq.v1
                                       再提交 offset

契约错误/任务身份不匹配 ──稳定 poison ID──> rag.ingest.dlq.v1
                                      └──DLQ 成功后提交原 offset
```

Kafka 消息只包含对象键和任务元数据，不传输文件内容。key 使用
`asset_version_id`，同一资产版本的初始与重试事件进入同一分区，保持有序。

## 状态流转

| 当前状态 | 动作 | 下一个状态 | 说明 |
|---|---|---|---|
| `queued` | outbox 发布成功 | `queued` | 写入 Kafka 坐标与 `published_at` |
| `queued/retry` | worker 获得处理租约 | `running` | `attempt_count + 1` |
| `running` | 处理成功 | `succeeded` | 任务、资产、版本在同一事务中完成 |
| `running` | 可重试失败且未达上限 | `retry` | 指数退避，清空发布坐标等待 outbox |
| `running` | 永久失败或达到上限 | `failed` | 资产与版本标记失败，等待 DLQ 发布 |
| `failed` | DLQ 发布成功 | `dead_letter` | 记录 DLQ Kafka 坐标 |

指数退避为 `base * 2^(attempt-1)`，默认从 5 秒开始，最大 900 秒。发布失败不增加
业务尝试次数，只释放发布租约并延迟再次发布。

## 消息契约与毒消息

`IngestTaskEvent` 是 `schema_version=1` 的严格 JSON 契约，拒绝未知字段，并携带：

- 事件 ID、事件类型、UTC 发生时间；
- task、tenant、asset、asset version 和幂等键；
- object key、媒体类型、字节数和 SHA-256；
- 当前尝试次数、最大尝试次数及可选错误上下文。

消费者会把消息身份与 MySQL 中的任务、资产和版本逐字段比对。格式错误、未知任务
或身份不一致的记录不会修改真实任务，而是包装成 `ingest.poisoned` 事件写入 DLQ。
poison event ID 由原 topic、partition、offset 通过 UUIDv5 生成；原始 key/value 使用
Base64 保存，便于安全重放和去重。为避免超大 poison 消息经过 Base64 膨胀后再次
超过 Kafka 限制，原 value 最多保留前 64 KiB，同时记录原长度、完整 SHA-256 和
截断标记。

## 配置

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `RAG_KAFKA_BOOTSTRAP_SERVERS` | `127.0.0.1:9092` | 逗号分隔的 broker 地址 |
| `RAG_KAFKA_INGEST_TOPIC` | `rag.ingest.v1` | 初始入库事件 |
| `RAG_KAFKA_RETRY_TOPIC` | `rag.ingest.retry.v1` | 到期重试事件 |
| `RAG_KAFKA_DLQ_TOPIC` | `rag.ingest.dlq.v1` | 终态及 poison 事件 |
| `RAG_KAFKA_CONSUMER_GROUP` | `multimodal-rag-ingest-v1` | 入库消费组 |
| `RAG_KAFKA_OUTBOX_BATCH_SIZE` | `100` | 单次租约任务数 |
| `RAG_KAFKA_PUBLISH_LEASE_SECONDS` | `30` | 发布租约时间 |
| `RAG_KAFKA_PROCESSING_LEASE_SECONDS` | `300` | 单次处理租约时间 |
| `RAG_KAFKA_RETRY_BASE_SECONDS` | `5` | 重试基础延迟 |
| `RAG_KAFKA_RETRY_MAX_SECONDS` | `900` | 重试延迟上限 |

安全协议支持 `PLAINTEXT`、`SSL`、`SASL_PLAINTEXT` 和 `SASL_SSL`。SASL 用户名、
密码分别通过 `RAG_KAFKA_SASL_USERNAME` 和 `RAG_KAFKA_SASL_PASSWORD` 注入，配置
对象以 `SecretStr` 保存。生产环境应使用 TLS，并在 Kafka ACL 中只授予所需 topic
和 consumer group 权限。

## 运行边界

先执行 Alembic 迁移并创建三个 topic，再启动 outbox：

```bash
rag-kafka-outbox
```

`create_ingest_worker(settings, session_factory=..., processor=...)` 接受调用方管理的
数据库会话工厂和业务处理器。M06 完成可靠消息编排；文档、图片及后续视频处理器
由一个统一 worker 在进程内按媒体类型路由，复用同一消费状态机，不在消息层耦合
解析或模型供应商。不能用同一 consumer group 分别部署“只认识一种媒体”的竞争
消费者，否则 Kafka 可能把图片消息交给文档进程。

生产部署还应配置 topic 副本数、`min.insync.replicas`、容量告警和 DLQ 保留策略。
本地专项验证不需要真实 Kafka/MySQL：它验证真实 aiokafka 客户端参数、MySQL 方言
锁 SQL、消息契约、状态流转和提交顺序。集成环境需要额外执行 broker 与数据库的
故障注入测试，覆盖 ACK 丢失、进程退出和消费组再均衡。

## 验证

```bash
./scripts/verify_kafka_ingestion.sh
```
