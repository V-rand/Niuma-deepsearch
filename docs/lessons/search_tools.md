# 检索工具踩坑经验

## 1. 核心发现：模型不会选工具

模型本能用 `web_search` 搜一切，从不主动用 `arxiv_search`、`openalex_works`、`wikipedia_lookup`。即使 arxiv_search 支持结构化字段（author/venue/year/comment），模型仍然发 `web_search("ICML 2022 Singapore China 6 authors")` 这种宽泛关键词。

**解法**：工具描述说明"何时不该用"，system prompt 把工具选择表格化，AGENT.md 加检索配方速查。

## 2. 核心发现：Keywords Soup ≠ 检索

模型只有一种检索方式：把问题拼成 keywords soup。真正有效的检索是三阶段渐进：

1. **候选发现**：域锁定 `site:` + 1-2 约束 → 缩小到少量候选
2. **指纹验证**：用已知具体信息（参考文献标题、作者名）精确匹配
3. **确认**：交叉验证

ChatGPT 用 `site:proceedings.mlr.press/v162 "Singapore" "USTC"` 直接锁到 ICML 2022，再 `site:proceedings.mlr.press/v162 "GPT-3" "SimCLR"` 用引用指纹定靶。我们的模型只会 `web_search("ICML 2022 Singapore China")` × 20 遍。

**解法**：写了 `retrieval_strategy` skill 和检索配方文档。

## 3. httpx AsyncClient 在中国 DNS 不稳定

| 测试方式 | 结果 |
|---------|------|
| curl | 0.15s ✅ |
| httpx.get (sync) | 0.2s ✅ |
| httpx.AsyncClient (async) | 超时 >30s ❌ |
| requests.get | 0.2s ✅ |
| requests + run_in_executor | 0.2s ✅ |

curl 和 requests 用系统 DNS（glibc `getaddrinfo`），httpx AsyncClient 用 `anyio` → `asyncio` 事件循环 DNS，在中国解析某些域名（export.arxiv.org）不稳定。

**解法**：所有 HTTP 调用统一用 `requests` + `loop.run_in_executor(None, ...)` 模式。不依赖 async httpx。

附：同目录下的 `search.py` 用的是 `aiohttp`（`aiohttp.ClientSession`）——它没有这个问题，因为 aiohttp 用不同的 DNS 后端。openalex.py 用的 `httpx.AsyncClient` 加了 `connect=15.0` timeout 也能通，但慢。

## 4. arXiv 限速严格，不能并行

- curl: 0.15s
- 429 后必须等冷却期（通常数分钟）
- 3s 间隔只是"建议"，绕过也不行。连续测试会触发更严格限速。
- 24h 缓存很关键：同一 query 一天内 arXiv 也不更新

**解法**：3s 全局限流器 `_last_arxiv_call` + 24h 缓存 `_ARXIV_CACHE_TTL`。

## 5. OpenAlex venue 标签不全

OpenAlex 有 170 万 arXiv 论文，但 `locations` 字段只标 arXiv，不标会议 venue（ICML/NeurIPS 等）。`primary_location.source.id=S4306419644`（ICML）只能匹配到 8 篇 ICML 2022 论文。

**解法**：OpenAlex 不适合按会议 venue 搜索。用 arxiv_search（有 comment/journal_ref 字段）搜会议论文，用 openalex_works 按 author/institution/topic/references 搜。

## 6. OpenAlex 引用指纹是最强技巧

`openalex_works(references="paper1,paper2")` 自动解析为 OpenAlex ID，查找同时引用全部指定论文的论文。这是唯一确定论文的方式——论文的引用列表就是它的 DNA。

ICML 那道题的最佳路径：
```
openalex_works(references="Language Models are Few-Shot Learners, A Simple Framework for Contrastive Learning", year="2022")
→ 仅 14 篇 → 结合 6 作者约束 → 直接锁定
```

## 7. 各工具定位总结

| 工具 | 强项 | 弱项 | 用在哪 |
|------|------|------|-------|
| `arxiv_search` | comment/journal_ref 含会议录用信息，cat 分类过滤 | 仅搜 arXiv，不能并行 | 精确找会议论文，arXiv 预印本 |
| `openalex_works` | 引用指纹搜索，author/institution 自动解析，270M 全库 | venue 标签不全，title 搜索贵 | 按作者/机构/主题/引用列表搜论文 |
| `web_search` | domain-locking `site:`，`include_domains`，`source=scholar` | 非结构化，噪音多 | 候选发现、新闻、探索 |
| `wikipedia_lookup` | infobox 结构化数据 | 仅 Wikipedia | 人物出生地、剧集首播年份等事实 |

## 8. Prompt 架构教训

- 工具描述太短 → 模型不会用。每个 tool description 需要"何时用/何时不用"表格。
- 检索策略不应该散落在 system prompt 里 → 抽成 `retrieval_strategy` skill。
- 短答案和长报告的纪律是不同的 → 抽成 `short_answer_research` 和 `long_form_research` skill。
- 通用循环（PARSE→VERIFY_PREMISES→REASON→SEARCH）是所有模式的基础 → 放 system prompt。
- 不要让测试驱动内容——测试服务于内容，不是反过来。
