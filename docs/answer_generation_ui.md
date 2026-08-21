# 最终生成与前端问答

M12 把前序模块连成可用问答链：Python 负责不可信输入边界、查询规划、模型与 Web
适配、SSE 传输；C++20 负责本地多模态召回和确定性证据治理；React 只消费稳定事件，
不接触模型密钥，也不自行决定用户权限。

## 1. 技术栈与选型

| 层次 | 技术 | 负责什么 | 为什么放在这里 |
|---|---|---|---|
| 前端 | React 19.2.8、TypeScript 7.0.2、Vite 8.2.1 | 问答交互、POST 流解析、引用/冲突展示、架构流程演示 | 组件和状态模型清晰，开发服务轻量；依赖使用 npm 官方 registry 的稳定标签精确锁定 |
| Python 表层 | Python 3.11+、FastAPI、aiohttp、Pydantic | HTTP/SSE、身份边界、Planner、通用模型适配、Bing、编排与稳定错误码 | 迭代快，生态适合模型 API、网页抽取和业务协议 |
| C++ 内核 | C++20、gRPC/Protobuf | 多路召回、RRF、去重、冲突、来源多样性、Token 预算、citation | 对延迟敏感的统一治理逻辑可控且可独立扩缩容 |
| 模型 | `ChatModel`、`EmbeddingModel`、`VisionModel`、`SpeechToTextModel` | 规划、生成、向量、图片理解、ASR | 应用依赖内部接口和模型 ID/版本，不把厂商 SDK 扩散到业务层 |
| 检索 | Milvus dense + BM25；Microsoft Foundry `bing_grounding` | 私有知识库多模态检索；公网检索与 citation | Milvus 统一向量与全文召回；Bing 只作为可替换 `SearchProvider` |
| 数据与任务 | MySQL、S3 协议对象存储、Kafka | 元数据/会话、原文件、可靠异步入库 | 大文件不穿过消息队列，任务通过 transactional outbox 保持可恢复 |

前端版本以 2026-08-21 的 npm 官方页面为准：
[React](https://www.npmjs.com/package/react)、
[Vite](https://www.npmjs.com/package/vite)、
[TypeScript](https://www.npmjs.com/package/typescript)、
[Vitest](https://www.npmjs.com/package/vitest)。生产依赖和开发依赖均使用精确版本，
`package-lock.json` 负责完整传递依赖锁定。

Redis 仍是缓存、分布式限流和短期会话状态的候选组件，但 M12 没有为了“架构完整”而
强行引入。当前请求状态由流连接持有，持久业务状态归 MySQL；出现明确的多实例共享
需求后再增加 Redis，避免双写和过早复杂化。

## 2. 在线回答链

```text
React
  │ POST /api/v1/queries/stream（请求体只含问题、范围、模态、会话 ID）
  ▼
可信网关 ── 覆盖并注入 tenant / user / readable ACL
  ▼
FastAPI ── accepted / heartbeat / 稳定 error
  ▼
ModelQueryPlanner ── 严格 JSON Schema，最多 6 路
  ├─ LOCAL → Query Embedding → gRPC RetrievalRoute
  └─ WEB   → Bing Grounding → 安全抓取/抽取 → ExternalEvidence
  ▼
C++20 Core
  ├─ tenant + ACL 前置过滤
  ├─ route 内归一化与跨路融合
  ├─ 保守去重、结构化冲突、来源多样性
  └─ Token 预算、JSON 转义上下文、连续 citation
  ▼
ChatModel.stream ── 只允许使用编号证据
  ▼
Python 引用审计 ── valid / invalid / uncited
  ▼
React ── token、来源卡片、冲突和降级状态
```

Planner 的模型输出不是最终授权。Python 会再次应用请求偏好：`local` 不允许 Web，
`web` 不允许本地；当前 Web 只支持 document。`hybrid` 若关闭 document 会直接返回
`INVALID_PREFERENCES`，而不是悄悄扩大范围。模型生成的路由也不能携带 tenant、ACL、
top-k 或超时等安全/资源参数。

混合模式下，单条 Bing 路由失败会记录 `route_error_codes` 并让可用本地证据继续；纯
Web 且所有路由失败时返回可重试的 `WEB_SEARCH_FAILED`。内核返回零条证据时不调用
ChatModel，而是输出固定的“证据不足”结果。

## 3. 通用模型接口

`OpenAIResponsesChatModel` 是 `ChatModel` 的一个 HTTP 实现，不是业务契约本身。它
支持完整响应和 SSE 增量响应，传递固定的 `model_id`，并把模型版本作为应用侧审计
字段。Planner 使用严格 JSON Schema；最终回答使用普通文本流。替换供应商时只需实现：

```python
class ChatModel(Protocol):
    model_id: str
    model_version: str
    async def complete(self, request: ChatRequest) -> ChatCompletion: ...
    def stream(self, request: ChatRequest) -> AsyncIterator[ChatDelta]: ...
```

适配器能处理任意网络分块和跨块 UTF-8 字符，区分 429/5xx 等可重试错误与永久契约
错误。上游错误正文不会原样进入对外 SSE。

## 4. SSE 契约

浏览器 `EventSource` 只支持 GET，无法自然提交结构化问题，因此前端使用 `fetch(POST)`
读取 `ReadableStream` 并解析 SSE。事件顺序由递增 `sequence` 审计：

| 事件 | 关键数据 | 含义 |
|---|---|---|
| `accepted` | `conversation_id`、范围、模态 | 请求和可信身份已通过 HTTP 校验 |
| `planning` | `status`、结构化 routes | Planner 开始/完成 |
| `retrieving` | `status`、`evidence_count` | 表层取证及 C++ 执行状态 |
| `sources` | citations、conflicts、partial failure、上下文预算 | 在回答前先交付证据审计信息 |
| `delta` | `text` | 最终模型 token/文本片段 |
| `heartbeat` | 空对象 | 长步骤期间防止代理把连接判空闲 |
| `done` | 完整 answer、finish reason、引用审计、模型身份、usage | 唯一成功终态 |
| `error` | code、中文安全消息、retryable | 唯一失败终态 |

心跳等待不会取消正在执行的 `anext()`：超时时只发送 heartbeat，仍保留同一个异步任务。
客户端断开时才取消 pending task 并关闭生成器，避免每次心跳都重启一次检索或模型调用。

## 5. 身份、ACL 与提示注入边界

- 请求体不接受 `user_id`、`tenant_id` 或 ACL；这些值只能来自可信请求头。
- 生产网关必须先删除客户端传入的同名头，再基于已验证身份重新注入。仓库中的 Vite
  proxy header 只用于 `127.0.0.1` 本地演示。
- C++ 本地检索路由没有 `allowed_acl_ids` 时拒绝执行；不能用“空 ACL 代表全部可读”。
- 网页正文和本地正文都按不可信数据进入内核上下文，最终 system prompt 明确禁止证据
  覆盖指令。
- React 以文本节点渲染回答，只识别 `[证据 N]` 标记，不把模型文本当 HTML 注入。
- 最终 `done` 重新扫描回答引用：不存在的编号进入 `invalid_citation_ids`；有来源却未引用
  时设置 `uncited_answer`，前端显示人工复核提示。

## 6. 配置与本地运行

主要环境变量：

| 配置 | 默认值 | 用途 |
|---|---:|---|
| `RAG_CHAT_ENDPOINT_URL` | `http://127.0.0.1:8080/v1/responses` | 通用 Responses 端点 |
| `RAG_CHAT_MODEL_ID` / `VERSION` | `chat-general` / `local` | 固定调用身份与审计版本 |
| `RAG_CHAT_MAX_OUTPUT_TOKENS` | 2048 | 最终输出上限 |
| `RAG_PLANNER_MAX_OUTPUT_TOKENS` | 1024 | 结构化规划上限 |
| `RAG_ANSWER_CONTEXT_TOKEN_BUDGET` | 12000 | C++ 总上下文预算 |
| `RAG_ANSWER_MAX_EVIDENCE_TOKENS` | 2000 | 单条证据预算 |
| `RAG_ANSWER_LOCAL_TOP_K` | 8 | 每条本地路由候选数 |
| `RAG_ANSWER_WEB_RESULT_COUNT` | 5 | 每条 Web 路由结果数 |
| `RAG_SSE_HEARTBEAT_SECONDS` | 15 | SSE 空闲心跳周期 |

启动 Python API 后，在 `services/web_ui` 执行 `npm install && npm run dev`。Vite 默认
把 `/api` 转发到 `127.0.0.1:8000`。生产静态资源构建由 Codebase Pipeline 编译阶段
人工启动；模块验证脚本默认只运行后端和前端单测。
