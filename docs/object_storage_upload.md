# 对象存储与上传链路

## 为什么让前端直传对象存储

Python API 只处理身份、元数据和短期授权，不转发文件字节。这样上传大图片、
音频或视频时，不会占用 API 进程的内存和网络带宽，也能独立扩容 API 与对象
存储。

```text
前端 ──1.申请上传──> Python API ──登记 pending──> MySQL
  │                       │
  │<──2.预签名 PUT URL────┘
  │
  ├──3.PUT 文件──────────> S3 兼容对象存储
  │
  └──4.完成回调──────────> Python API ──HeadObject 校验──> 对象存储
                                      └──事务更新状态并创建任务──> MySQL
```

原始文件只存在对象存储。MySQL 保存稳定的 `asset_id`、版本、对象键、SHA-256、
大小和任务状态；Kafka 消息在 M06 中只会携带这些元数据。

## 两阶段接口

### 1. 申请上传

```http
POST /api/v1/assets/uploads
X-Tenant-ID: tenant-1
X-User-ID: user-1
Content-Type: application/json

{
  "file_name": "report.pdf",
  "content_type": "application/pdf",
  "size_bytes": 12345,
  "content_sha256": "64位十六进制SHA-256"
}
```

API 在一个 MySQL 事务中创建 ACL、资产和首个资产版本，返回有效期默认 15
分钟的 PUT URL 与签名请求头。URL 是 bearer token，不应进入日志、埋点或聊天
消息。

前端应以原始 `Blob` 为请求体，并携带响应里的请求头。浏览器会根据 Blob 自动
设置 `Content-Length`；前端不应修改这个浏览器保留头，但实际长度必须与响应
中的值相同。对象存储的 CORS 规则需要允许：

- 来自前端域名的 `PUT`；
- `Content-Type`、`If-None-Match`、`x-amz-checksum-sha256`；
- `x-amz-meta-asset-id`、`x-amz-meta-asset-version-id`。

### 2. 确认完成

```http
POST /api/v1/assets/{asset_id}/versions/1/complete
X-Tenant-ID: tenant-1
X-User-ID: user-1
```

API 使用 `HeadObject` 检查以下事实：

- 服务端记录的 Content-Length 与申请值一致；
- Content-Type 一致；
- 服务端 SHA-256 与客户端申请值一致；
- 对象元数据中的资产 ID 和版本 ID 一致。

全部通过后，在一个 MySQL 事务内把资产和版本置为 `processing`，并创建带唯一
`dedupe_key` 的 `queued` 入库任务。重复调用完成接口返回同一个任务，不会重复
建任务。校验失败的对象会被拒绝并尽力清理，对应资产和版本标记为 `failed`。

## 完整性与覆盖保护

预签名请求固定了 Content-Length、Content-Type、SHA-256、资产元数据和
`If-None-Match: *`。对象存储在 SHA-256 不匹配时拒绝写入；条件写入防止同一
预签名 URL 在完成校验后再次覆盖对象，从而关闭“先校验、后替换”的竞态窗口。

这里不使用 ETag 代替 SHA-256。ETag 在分片上传或部分服务端加密场景下不是
整个对象的内容摘要。M05 仅支持最大 5 GB 的单次 PUT；更大的视频需要后续加入
带完整对象 checksum 的 multipart 流程。

目标 S3 兼容服务必须支持 SigV4、条件 `PutObject`、SHA-256 additional
checksum，以及带 `ChecksumMode=ENABLED` 的 `HeadObject`。如果供应商缺少
这些能力，应新增对应适配器并保持应用层 `ObjectStore` 接口不变，不能降级为
仅信任客户端上报摘要。

## 配置

常用环境变量：

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `RAG_OBJECT_STORAGE_ENDPOINT_URL` | `http://127.0.0.1:9000` | S3 兼容端点 |
| `RAG_OBJECT_STORAGE_REGION` | `us-east-1` | 签名区域 |
| `RAG_OBJECT_STORAGE_BUCKET` | `multimodal-rag` | 原始资产桶 |
| `RAG_OBJECT_STORAGE_ADDRESSING_STYLE` | `path` | `path` 或 `virtual` |
| `RAG_UPLOAD_URL_EXPIRES_SECONDS` | `900` | 预签名 URL 有效期，60–3600 秒 |
| `RAG_UPLOAD_MAX_BYTES` | `5000000000` | 单次 PUT 上限 |

访问密钥、秘密密钥和可选会话令牌分别通过
`RAG_OBJECT_STORAGE_ACCESS_KEY`、`RAG_OBJECT_STORAGE_SECRET_KEY` 和
`RAG_OBJECT_STORAGE_SESSION_TOKEN` 注入。它们使用 `SecretStr`，不会出现在
配置对象的默认日志表示中。生产环境也可不显式提供应用密钥，改用 aiobotocore
的标准 AWS 凭证链和工作负载身份。

当前 `X-Tenant-ID` / `X-User-ID` 必须由受信任网关注入；禁止把这两个头直接
暴露给不可信公网客户端。后续接入鉴权模块时，路由无需改变，只替换
`RequestPrincipal` 的解析来源。

## 验证

```bash
./scripts/verify_object_storage.sh
```

验证包括真实 aiobotocore SigV4 预签名生成、存储端口测试、上传服务状态流转、
HTTP 契约、MySQL 模型与现有 API 回归；不需要连接真实对象存储。
