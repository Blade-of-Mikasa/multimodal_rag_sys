# MySQL 元数据设计

## 技术选择

M04 使用 MySQL 8.0+、SQLAlchemy 2.0、asyncmy 和 Alembic：

- SQLAlchemy 是 Python 表层统一的 ORM 和 SQL 工具，业务代码不直接拼接 SQL。
- asyncmy 为 FastAPI 提供异步 MySQL 驱动，等待数据库时不会阻塞事件循环。
- Alembic 维护可 Review、可升级、可回退的数据库版本；线上禁止用
  `Base.metadata.create_all()` 代替迁移。
- 所有业务表统一使用 InnoDB、`utf8mb4` 和
  `utf8mb4_0900_ai_ci`。状态约束依赖 MySQL 8.0 对 `CHECK` 的执行能力。

MySQL 只保存强一致的业务元数据。原始文件进入 S3 协议对象存储，向量和
BM25 索引进入 Milvus；数据库中只保留对象键、摘要、版本和处理状态。

## 表与职责

| 表 | 职责 |
|---|---|
| `access_control_lists` | 租户范围内可复用的权限集合 |
| `access_control_entries` | 用户、用户组或服务主体的 read/write/admin 授权 |
| `assets` | 文件、图片、音视频等逻辑资产及当前版本 |
| `asset_versions` | 不可变对象版本、SHA-256、大小和入库状态 |
| `ingest_tasks` | 入库任务、重试次数、幂等键和 Kafka 投递结果 |
| `conversations` | 带租户、所有者和 ACL 的问答会话 |
| `conversation_messages` | 不可变消息、request_id 和引用快照 |

用户与用户组身份由后续鉴权模块及外部身份系统提供，本模块不复制用户主数据；
ACL 只保存稳定的主体 ID。资产和 ACL 都带 `tenant_id`，应用服务必须在同一
事务内校验二者租户一致。

资产或会话删除 ACL 时使用 `RESTRICT`，避免权限记录被误删；ACL 条目、资产
版本、入库任务和会话消息属于父记录，随父记录 `CASCADE`。业务删除优先使用
`status` 与 `deleted_at` 软删除，物理级联主要服务于数据清理。

`ingest_tasks.dedupe_key` 是唯一键。后续 M06 会把同一事务创建的 `queued`
任务视作待投递记录：调度器发送 Kafka 后记录 topic/partition/offset 和
`published_at`；崩溃窗口允许重复投递，由唯一幂等键和任务状态保证至少一次
消费不会重复入库。

## 迁移命令

只编译 MySQL 方言 SQL、不连接数据库：

```bash
PYTHONPATH=services/python_api/src \
  .venv/bin/alembic -c services/python_api/alembic.ini upgrade head --sql
```

对指定数据库执行升级：

```bash
RAG_MYSQL_DSN='mysql+asyncmy://user:password@host:3306/multimodal_rag?charset=utf8mb4' \
  .venv/bin/alembic -c services/python_api/alembic.ini upgrade head
```

生产环境的 DSN 必须通过密钥系统注入，不应写入仓库或日志。应用配置将 DSN
声明为 `SecretStr`，默认输出不会泄露密码。

完整验证：

```bash
./scripts/verify_mysql_metadata.sh
```
