# M13：分阶段评估与 OpenTelemetry 可观测性

M13 把“效果评估”和“线上可观测性”拆成两条互补链路：前者用固定快照回答哪个环节
丢失了质量，后者用 trace/metric 回答一次真实请求在哪里变慢或失败。两者都不计算统一
的 RAG 总分，避免一个数字掩盖漏召回、证据误删或回答幻觉。

```text
JSONL 评估快照                         在线 POST-over-SSE 请求
      │                                         │
      ▼                                         ▼
严格案例/观察契约                    HTTP span（路由与状态码）
      │                                         │
      ▼                                         ▼
逐层 micro-average                  rag.query 根 span
规划 → 召回 → 证据 → 冲突 → 回答       ├─ planning
      │                                ├─ retrieving
      ▼                                └─ generating
summary.json + report.md                        │
                                               ▼
                                  OTLP/HTTP traces + metrics
```

## 1. 评估契约

输入是 UTF-8 JSONL，每行包含 `case` 和 `observation`。`case` 保存标准答案侧的稳定
标识；`observation` 保存某个代码、模型和参数版本运行后的完整中间判断。正文可以变化，
评估关联应优先使用版本化 evidence/claim/conflict ID，而不是模糊字符串匹配。

`case` 的核心字段：

| 字段 | 含义 |
|---|---|
| `expected_routes` | 必须开启的 `scope:modality` 路由 |
| `expected_evidence` | 回答所需的证据 ID |
| `expected_claims` | 标准答案需要覆盖的原子事实 ID |
| `expected_conflicts` | 应识别的结构化冲突 ID；无冲突时为空 |
| `reference_answer` | 供人工 Review 的参考文本，不参与字符串打分 |

`observation` 同时保存实际路由、召回证据、进入上下文的保留证据、识别到的冲突、
端到端/分阶段延迟、Token、成本和失败码。每条回答事实必须标注：

- `correct`：事实本身是否正确；
- `supported_by_evidence`：提供给回答模型的证据是否真的支持它。

这两个标签应来自人工验收或一个单独、版本化的评审流程。当前 CLI 不偷偷调用在线 LLM
裁判，因此同一份 JSONL 会得到完全一致的结果。

## 2. 指标口径

所有比例使用整个数据集的分子/分母做 micro-average，不先计算每题百分比再平均。没有
适用样本时返回 `null/N/A`，不会把零分母伪装成 100%。

| 指标 | 计算方式 |
|---|---|
| 召回规划覆盖率 | 已开启必要路由 / 全部必要路由 |
| 无效召回率 | 实际路由中不必要的路由 / 全部实际路由 |
| 必要证据覆盖率 | 找到的必要证据 / 全部必要证据 |
| 有效证据误删率 | 已召回但未进入上下文的必要证据 / 已召回必要证据 |
| 冲突发现率 | 已识别预期冲突 / 全部预期冲突 |
| 最终答案正确率 | 正确回答事实 / 全部回答事实 |
| 证据忠实度 | 有证据支持的回答事实 / 全部回答事实 |
| P95 + 成本 | 端到端最近秩 P95；总成本 / 请求数 |

报告另给出答案完整率、P50/P99、各阶段 P95、Token 合计和失败码分布作为诊断项。

运行仓库内的可复现样例：

```bash
PYTHONPATH=services/python_api/src .venv/bin/python -m rag_api.evaluation.cli \
  --input evaluation/fixtures/m13_smoke_replay.jsonl \
  --output-dir build/evaluation/m13-smoke \
  --run-id m13-smoke
```

输出目录包含机器可读的 `summary.json` 和中文 `report.md`。摘要记录输入文件 SHA-256；
传入 `--baseline previous/summary.json` 后，报告会显示逐指标差值。`run-id` 应包含代码
commit、模型版本或参数版本，原始 JSONL 应作为对应运行工件一起保存。

## 3. OpenTelemetry 接入

实现使用显式 OpenTelemetry API/SDK 埋点，通过 OTLP/HTTP 发送到 Collector。官方文档
将 Python trace 和 metric 都标为稳定信号，并建议批量导出 span；Prometheus 场景优先
让 Collector 或 Prometheus OTLP receiver 接收指标，而不是在业务进程另开抓取端口：

- [OpenTelemetry Python instrumentation](https://opentelemetry.io/docs/languages/python/instrumentation/)
- [OpenTelemetry Python exporters](https://opentelemetry.io/docs/languages/python/exporters/)

默认关闭遥测且不会联网。启用示例：

```bash
export RAG_TELEMETRY_ENABLED=true
export RAG_OTEL_EXPORTER_OTLP_ENDPOINT=https://otel-collector.example/otel
export RAG_OTEL_TRACE_SAMPLE_RATIO=0.1
export RAG_OTEL_METRIC_EXPORT_INTERVAL_MS=60000
export RAG_CHAT_INPUT_COST_PER_MILLION_TOKENS_USD=2.0
export RAG_CHAT_OUTPUT_COST_PER_MILLION_TOKENS_USD=8.0
```

应用会在 base endpoint 后分别追加 `/v1/traces` 和 `/v1/metrics`。staging/production 强制
HTTPS；超时、导出周期、采样率和每百万 Token 单价均有边界校验。关闭时使用 OpenTelemetry
NoOp provider，业务行为不变。

### Trace

- `POST /api/v1/queries/stream` 的 `rag.query` span 从开始消费 SSE generator 持续到
  `done/error/cancelled`，覆盖真实流式生命周期；
- 子 span 为 `rag.query.planning`、`rag.query.retrieving`、
  `rag.query.generating`；
- 入站 W3C trace context 会成为 HTTP span 的 parent，RAG query 再继承该 trace；
- span 可以保存 request ID 用于单次排障，但不保存 tenant/user/问题正文。

HTTP middleware 的 duration 只覆盖生成响应对象的时间；流式端到端 SLO 必须使用
`rag.query.duration`。

### Metric

| 名称 | 用途 |
|---|---|
| `rag.query.duration` | 流式端到端延迟，可在后端计算 P50/P95/P99 |
| `rag.query.stage.duration` | planning/retrieving/generating 分阶段延迟 |
| `rag.query.requests` / `rag.query.failures` | 终态吞吐和服务端稳定错误码失败率；客户端取消只计前者 |
| `rag.retrieval.route.failures` | 混合检索中被降级的单路失败 |
| `rag.model.tokens` | planner/generation 的输入输出 Token |
| `rag.query.estimated_cost` | 按配置单价计算的单请求估算成本 |
| `rag.query.evidence.count` | 进入回答阶段的证据数量 |

Metric attributes 只使用范围、阶段、终态、稳定错误码等有限枚举；严禁添加 request ID、
tenant、user、query、URL 或 evidence ID，防止高基数拖垮指标后端。供应商返回的任意
finish/error 文本会先归一为有限集合或 `other/UNKNOWN_ERROR`。

## 4. 验证

```bash
./scripts/verify_evaluation_observability.sh
```

脚本运行 M13 专项与受影响的 M12/API 回归、生成一次临时评估报告、执行 `pip check` 和
`git diff --check`。它不会启动 Codebase Pipeline 的人工编译阶段，也不会连接真实
Collector、模型、Bing、Milvus 或 C++ Core。
