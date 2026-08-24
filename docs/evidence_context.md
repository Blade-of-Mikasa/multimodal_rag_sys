# 证据治理与上下文构建

M11 把本地多模态召回结果和 Python 已安全获取的网页来源统一交给 C++ 内核治理。
内核只执行确定性、可审计的规则，不调用 LLM 猜测事实，也不替回答模型裁决哪一方
“正确”。

## 处理边界

```text
Python 表层
  ├─ M10 SearchProvider → 安全抓取/正文抽取 → ExternalEvidence(WEB)
  └─ ExecutionPlan + tenant/ACL + Token 预算
                              │ gRPC/Protobuf
                              ▼
C++ 内核
  ├─ Milvus 本地召回（tenant/ACL 前置过滤）
  ├─ 跨路由分数归一化与同 ID 融合
  ├─ 精确/近似去重与确定性冲突识别
  ├─ 冲突优先、来源多样性优先的预算选择
  └─ 不可信内容封装 → context + citations + decisions
```

`external_evidence` 只接受 `SOURCE_SCOPE_WEB`。调用方不能把本地内容伪装成外部证据，
绕过 `DocumentStore`、`ImageStore`、`VideoStore` 的 tenant/ACL 过滤。本地命中继续由
C++ Store 产生，网页下载仍留在具有 SSRF 防护的 Python 表层。

## 分数归一化

不同召回路由的原始分数不可直接比较：Milvus dense、BM25/RRF、网页排序和未来的
重排器量纲都可能不同。内核先在每个 `route_id` 内按原始分数排序，再使用
`1 / (60 + rank)` 转为统一的 reciprocal-rank 分数。同一 `evidence_id` 被多条路由
命中时累加分数，并在 `route_ids`、`raw_score` 中保留贡献路径。

如果同一 ID 对应不同正文、模态或来源范围，内核直接拒绝请求。这比静默覆盖更安全，
也能尽早暴露不稳定 ID 或调用方映射错误。

## 去重策略

处理顺序如下：

1. 相同 SHA-256 或规范化正文相同，视为精确重复。
2. URL 去掉 fragment、统一 scheme/authority 大小写后相同，且没有版本、范围、时间等
   差异，视为重复来源。
3. 足够长的正文使用三字符 shingle SimHash；相似度达到 0.95 且不存在冲突线索时，
   视为近似重复。

代表证据按归一化分数、`source_authority`、发布时间、抓取时间和稳定 ID 确定。对每个
被删除候选都会返回 `EvidenceDecision`，包含 `exact_duplicate`/`near_duplicate`、
代表 ID 和原因。

近似去重必须保守。只要 `claim_key`、`claim_value`、`version`、`scope`、
`statistic_basis` 或明确发布时间不同，就保留双方，避免“一句话只改了数字”却被当成
重复文本删除。

## 冲突模型

当前内核只识别结构化、确定性的冲突。上游可以为证据附加：

- `claim_key`：同一可比较声明，如 `product.request_limit`；
- `claim_value`：规范化后的值；
- `version`、`scope`、`statistic_basis`：用于解释差异的上下文。

同一 `claim_key` 存在多个 `claim_value` 时，内核保留全部证据，并依次归类为版本差异、
统计口径差异、适用范围差异、时间差异或直接冲突。它不做多数投票，也不会仅因来源
分数更高就删除另一方。普通网页正文不会由 C++ 自动做语义事实抽取；后续如需扩大
冲突覆盖，应在 Python 模型适配层输出受约束的结构化 claim，再复用同一套内核规则。

## Token 预算与来源多样性

请求可以设置总 `context_token_budget` 和单条 `max_evidence_tokens`；零值使用服务默认
值 12000/2000。选择顺序是：冲突相关证据、每个不同来源的首条证据、其余证据。
无法放入预算的候选返回 `budget_excluded`，正文被截断的已选证据带
`content_truncated=true`。

内核通过 `TokenCounter` 端口隔离具体模型 tokenizer。当前默认实现按 UTF-8 字节数
计数，方法名为 `utf8_byte_upper_bound`。它对常见 tokenizer 是保守上界，避免内核
绑定某家模型库；响应同时返回计数方法、实际计数和是否截断。接入最终生成模型时，
可以注入该模型的精确 tokenizer，而不改变证据处理流程。

## 上下文与引用安全

生成的 context 首行明确标记后续内容为不可信证据数据。每条正文使用 JSON string
转义后写入 `content_untrusted_json`，因此网页中的换行、引号、伪造的 `[证据 N]` 或
“忽略之前指令”不能突破结构边界。该设计降低提示注入风险，但不能替代最终生成层的
系统指令、工具权限隔离和输出校验。

引用编号按最终入选顺序从 1 连续生成。`Citation` 保留 evidence ID、标题、模态、来源、
URL 和定位元数据；`EvidenceDecision` 解释每个候选为何入选、去重或因预算排除；
`Conflict` 保留冲突双方及分类。前端和审计日志不需要从拼接后的 prompt 反向解析这些
信息。
