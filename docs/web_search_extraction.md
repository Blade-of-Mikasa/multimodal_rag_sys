# M10 联网搜索与网页抽取

## 1. 目标与边界

M10 把“找到网页”和“把网页变成可治理证据”拆成两个独立阶段：

1. Python 表层通过统一 `SearchProvider` 获取候选来源；
2. 对每个来源执行有界、安全的 HTML 下载；
3. 使用 Trafilatura 提取正文，并独立解析显式发布时间；
4. 单个网页失败时降级保留搜索引用，不使整次联网检索失败。

本模块产出 `WebSearchBundle`，供后续 Query Planner 和 M11 证据治理消费，暂不把
网页写入 Milvus，也不在本模块直接生成最终答案。

## 2. Bing 接入选择

传统 Bing Web Search API 已于 2025-08-11 退役。默认实现改为 Microsoft Foundry
Responses API 的 `bing_grounding` 工具，仍然由 Bing 提供实时公网搜索能力，但应用
只依赖自有 `SearchProvider` 契约。

Foundry Grounding 不向开发者返回原始 Bing 结果，只返回模型生成的 Grounded 文本和
`url_citation` 注解。因此系统不会把 Grounded 文本伪装成网页正文，而是：

- 将引用注解映射为有序 `SearchCitation`；
- 原样保留来源 URL 和 Bing 查询 URL，供前端按 Microsoft 展示要求渲染；
- 独立下载引用页面并提取正文；
- 把引用覆盖的回答片段命名为 `cited_text`，仅在正文失败时作为降级证据。

认证被抽象为 `AccessTokenProvider`。仓库提供短期静态 Bearer Token 适配用于部署接线；
生产环境可注入基于 Managed Identity/Entra ID 的自动刷新实现，而不改搜索业务代码。

## 3. 处理流程

```text
SearchQuery
    |
    v
SearchProvider ---------------------------------------------+
    | Foundry bing_grounding                                |
    v                                                       |
SearchResponse                                              |
  - grounded_text                                           |
  - Bing search URLs                                        |
  - ordered URL citations                                   |
    |                                                       |
    +--> bounded concurrency --> SafeWebFetcher             |
                                  - HTTP(S) only             |
                                  - no credentials           |
                                  - public DNS/IP only       |
                                  - validate every redirect  |
                                  - no HTTPS downgrade       |
                                  - HTML/byte/time limits    |
                                           |                 |
                                           v                 |
                                  TrafilaturaPageExtractor   |
                                  - main text                |
                                  - same-host canonical URL  |
                                  - explicit source times    |
                                           |                 |
                    +----------------------+-----------------+
                    v
              WebSearchBundle
              - full sources
              - citation_only degraded sources
```

## 4. URL 与下载安全

联网来源全部视为不可信输入。`SafeWebFetcher` 执行以下约束：

- 只允许不含用户名/密码的绝对 HTTP(S) URL，默认只开放 80/443 端口；
- 字面量 IP 和 DNS 所有返回地址都必须是公网地址，拦截 loopback、私网、链路本地、
  保留地址、CGNAT 和 IPv4-mapped IPv6 绕过；
- 关闭环境代理继承，使用带公网地址校验的 aiohttp resolver；
- 禁止自动跳转，每一跳重新执行 URL/DNS 校验，并禁止 HTTPS 降级到 HTTP；
- 只接受 `text/html`、`application/xhtml+xml`，同时按响应头和实际解压后字节数限流；
- 总请求超时、跳转次数、单主机连接数和提取正文长度均有界。

这是一条按用户查询访问少量引用页面的检索链路，不是通用爬虫。批量抓取、站点级缓存、
robots.txt 策略和站点特定授权应在启用相应采集场景前单独实现并评审。

## 5. 来源时间模型

时间语义不能混用：

| 字段 | 含义 | 来源 |
|---|---|---|
| `published_time` | 发布者声明的首次发布时间 | JSON-LD、`article:published_time`、显式 `<time>` |
| `modified_time` | 发布者声明的修改时间；缺失时可退回 HTTP 修改时间 | JSON-LD、Meta、`Last-Modified` |
| `fetched_at` | 本系统实际取得页面的时间 | UTC 系统时钟 |

`SourceTime` 同时保存值、原始字符串、来源、精度和 `timezone_assumed`。只有显式 ISO-8601
时间会进入来源时间字段；无时区值会按 UTC 规范化并明确标记为“假定时区”，避免把抓取
时间或搜索排序时间误当成发布时间。

## 6. 失败降级

搜索供应商整体失败会抛出带 `retryable` 的 `SearchProviderError`。拿到引用后，每个页面
独立处理：

- 完整成功：`status=full`，返回正文、内容 SHA-256、时间和 canonical URL；
- 下载或抽取失败：`status=citation_only`，保留引用 URL、标题、`cited_text` 和稳定
  `failure_code`；
- 并发任务使用 `asyncio.gather` 保持供应商排名，单页失败不会改变其他来源顺序。

常见失败码包括 `SSRF_BLOCKED`、`URL_REJECTED`、`HTTP_STATUS`、
`RESPONSE_TOO_LARGE`、`UNSUPPORTED_CONTENT_TYPE`、`NO_MAIN_CONTENT` 和
`HTML_PARSE_ERROR`。

## 7. 配置

环境变量使用统一 `RAG_` 前缀：

- `RAG_BING_FOUNDRY_RESPONSES_URL`
- `RAG_BING_FOUNDRY_MODEL_DEPLOYMENT`
- `RAG_BING_GROUNDING_CONNECTION_ID`
- `RAG_BING_FOUNDRY_ACCESS_TOKEN`（生产建议改用可刷新的 TokenProvider）
- `RAG_BING_DEFAULT_MARKET`、`RAG_BING_DEFAULT_LANGUAGE`
- `RAG_WEB_SEARCH_TIMEOUT_SECONDS`
- `RAG_WEB_FETCH_TIMEOUT_SECONDS`
- `RAG_WEB_FETCH_MAX_BYTES`、`RAG_WEB_FETCH_MAX_REDIRECTS`
- `RAG_WEB_EXTRACT_MAX_CHARS`、`RAG_WEB_EXTRACT_MAX_CONCURRENCY`

调用 `rag_api.web.runtime.build_web_search_service(settings)` 可得到完整默认管线。没有配置
Foundry 连接时应用基础服务仍可启动，只有实际构建联网检索服务时才会明确报缺失项。

## 8. 验证

执行：

```bash
./scripts/verify_web_search.sh
```

专项测试覆盖 Foundry 请求/响应契约、引用去重、HTTP 重试语义、URL 与 DNS SSRF 拦截、
逐跳重定向、正文与时间抽取、并发上限和逐来源降级。真实 Foundry/Bing 调用需要部署方
提供 Azure 项目、模型、Bing connection 和 Entra Token，未在本地测试中伪造外部成功。
