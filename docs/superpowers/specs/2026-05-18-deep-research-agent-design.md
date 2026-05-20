# Deep Research Agent：设计文档

> **目标：** 将 AgentOS4Law 改造为通用的 deep research agent，用于学术论文产出，同时保留现有架构。

**状态：** 设计阶段 · **目标 Benchmark：** GAIA, BrowseComp, DeepResearch Bench, HLE, SimpleQA

---

## 1. 竞品分析

### 1.1 商业 Deep Research 系统

| 系统 | 基座模型 | 核心差异点 |
|--------|----------|-------------------|
| OpenAI Deep Research | o3/o4-mini | 多步网页浏览；5-30 分钟会话；带引用的研究报告 |
| Google Deep Research | Gemini 2.5 Pro | 深度整合 Google 搜索；1M 上下文窗口 |
| Perplexity Deep Research | Sonar + 自研 | HLE 21.1%，SimpleQA 93.9%；~3 分钟/任务 |
| Kimi Deep Research | Kimi K2 Thinking | 开源权重；BrowseComp 60.2%；HLE 44.9% |
| Claude Research | Claude Opus 4 | 强推理；多 agent 配置 |

### 1.2 开源实现方案

| 项目 | 框架 | 架构 | 关键设计选择 |
|---------|-----------|-------------|-------------------|
| smolagents Open Deep Research | Hugging Face smolagents | CodeAgent — 用 Python 代码作为行动 | 代码即行动，天然可组合 |
| LangChain Open Deep Research | LangGraph | 多 agent：Supervisor + Researchers | 基于图的编排；MCP 支持 |
| Kimi K2 | 自研 MoE 1T/32B | 单一 agent 整体式 | 大规模 RL 从 agentic 轨迹学习 |
| MCP-Agent Deep Orchestrator | MCP + 自研 | Orchestrator + 工具服务器 | 通用 MCP 架构 |
| deep-researcher (qx-labs) | OpenAI Agents SDK | 规划 → 子主题 → 并行研究 | 异步并行执行 |

### 1.3 Benchmark SOTA（2026 年 5 月）

| Benchmark | 题目数 | 最佳成绩 | 最佳系统 | 评测内容 |
|-----------|--------|-----------|------------|-----------------|
| **GAIA** | 466 | 52.3%（模型）/ 92.4%（agent 系统） | Claude Mythos / OPS-Agentic-Search | 多步真实任务 + 工具使用 |
| **BrowseComp** | 1,266 | 90.1% | GPT-5.5 Pro | 网页浏览持久性、创意搜索 |
| **DeepResearch Bench** | 100（博士级） | 排行榜进行中 | Kimi-Researcher / Claude-Researcher | 报告质量（RACE）+ 引用准确率（FACT） |
| **HLE** | 2,500 | 64.7% | Claude Mythos Preview | 研究生级专家推理 |
| **SimpleQA** | 4,326 | 97.1% | DeepSeek V3.2-Exp | 短问答事实准确性 |

### 1.4 架构模式总结

所有成功的 deep research 系统共同点：

1. **迭代式多轮研究循环** — 绝不是单次完成
2. **查询分解** — 将复杂问题拆解为子问题
3. **并行搜索执行** — 跨子主题并发搜索
4. **来源评估与引用追踪** — 按可信度给来源打分
5. **结构化报告生成** — 不是纯文本，而是引用详尽的分析师级输出
6. **上下文管理** — 通过摘要/分块避免上下文窗口溢出

---

## 2. 架构设计

### 2.1 系统概览

```
                    ┌─────────────────────────────┐
                    │      AgentLoop (内核)        │
                    │   ReAct + 研究工具集         │
                    └──────────┬──────────────────┘
                               │
               ┌───────────────┼───────────────┐
               ▼               ▼               ▼
        ┌──────────┐   ┌──────────────┐   ┌────────────┐
        │ 研究规划  │   │   知识检索    │   │  来源评估  │
        │ 器       │   │              │   │            │
        └──────────┘   └──────────────┘   └────────────┘
               │               │               │
               ▼               ▼               ▼
        ┌──────────┐   ┌──────────────┐   ┌────────────┐
        │ 并行搜索  │   │  跨 session  │   │  质量评分  │
        │          │   │    记忆      │   │            │
        └──────────┘   └──────────────┘   └────────────┘
               │               │               │
               └───────────────┼───────────────┘
                               ▼
                    ┌─────────────────────────────┐
                    │       报告生成器             │
                    │  （结构化、带引用输出）      │
                    └─────────────────────────────┘
```

### 2.2 核心循环不变

**决策：ReAct 循环不改。** 四阶段研究流程（理解→探索→综合→优化）通过以下方式约束，不动内核代码：

1. **System prompt** (`agent_system.txt`) — 定义研究方法和阶段
2. **工具集** — 只暴露研究相关工具，自然引导行为
3. **`RESEARCHER.md`** — 提供详细工作流指引
4. **Workspace memory** — 通过 AGENT/SOUL/MEMORY 保持研究纪律和项目偏好

各阶段逻辑如下：

```
阶段 1: 理解
  └─ 解析查询 → 确定范围、领域、深度
  └─ 分解为子问题（研究规划器）

阶段 2: 探索（迭代）
  └─ 对每个子问题：
  │    ├─ 搜索网络（并行）
  │    ├─ 阅读并提取关键主张
  │    └─ 评估来源可信度
  └─ 识别知识缺口 → 优化子问题
  └─ 重复直到收集到足够证据

阶段 3: 综合
  └─ 按主题组织发现
  └─ 生成带行内引用的报告
  └─ 质量检查：引用准确性、覆盖率、逻辑一致性

阶段 4: 优化（可选）
  └─ 用户提供反馈/追问
  └─ 回到探索或综合阶段
```

### 2.3 内核无需大改

内核（`agent_os/kernel/`、`agent_os/core/`、`agent_os/memory/`、`agent_os/storage/`）已经是通用的。改动有限：

| 模块 | 改动类型 | 说明 |
|--------|------------|-------------|
| `kernel/agent_loop.py` | 移除法律相关分支 | 通用化归档逻辑 |
| `kernel/sub_agent.py` | 移除法律相关分支 | 通用化子 agent 归档 |
| `kernel/result_filter.py` | 更新默认 prompt | 移除法律引用 |

### 2.4 改动范围汇总

| 层级 | 文件 | 操作 |
|-------|-------|--------|
| **Prompts** | `agent_system.txt`, `SOUL.md`, `AGENT.md`, `sub_agent.txt`, `result_filter.txt` | 全部替换为研究导向内容 |
| **工具** | 新增 `research_plan.py`, `source_evaluate.py`, `knowledge_search.py`, `report_generator.py` | 新 deep research 工具 |
| **工具** | `retrieval_untils.py`, `untils_case.py` | 保留但默认禁用 |
| **配置** | `config.yaml` 默认值 | 修改默认工具列表、阶段名称 |
| **Prompts** | `AGENT.md`, `SOUL.md`, `memory_guidance.txt` | 吸收 deep research 工作流与记忆规则 |
| **文档** | `ARCHITECTURE.md`, `PROJECT.md` | 重写为通用研究 |
| **CLI** | `cli.py` | 更新 session 阶段默认值 |

---

## 3. Prompt 设计

### 3.1 agent_system.txt（核心 System Prompt）

**当前：** 法律研究助手角色，聚焦法条、判例、法律推理。

**新版：** 通用 deep research 助手。

关键要素：
- **身份：** "你是一个 deep research agent。你的目标是产出全面、准确、引用规范的研究报告。"
- **研究方法论：**
  1. 理解查询范围和深度
  2. 分解为研究子问题
  3. 系统性地持续搜索
  4. 评估来源可信度
  5. 综合发现并规范引用
  6. 产出结构化报告
- **质量标准：**
  - 每个事实性主张必须有来源引用
  - 如未找到可靠来源，明确说明不确定性
  - 区分确凿事实、新兴证据和推测
  - 对关键发现报告置信度
- **引用格式：** `[来源: 标题, URL, 可信度]`
- **思考协议：** 在工具调用前使用 thinking 进行规划和推理

### 3.2 SOUL.md → RESEARCH_VALUES.md

**当前：** 法律职业价值观（公平、勤勉、客户信任、法律伦理）

**新版：** 研究诚信价值观：
- **求真：** 追求准确信息，而非确认偏见
- **学术诚实：** 公正呈现对立观点
- **来源透明：** 始终引用，绝不捏造
- **不确定性校准：** 诚实地表达置信度
- **迭代深度：** 当证据薄弱或矛盾时深入挖掘
- **可复现性：** 记录研究路径，以便他人验证

### 3.3 AGENT.md → RESEARCHER.md

**当前：** 法律工作流指引（案例分析、文档审阅、法律写作）

**新版：** 研究工作流指引：
- **研究规划：** 如何分解问题、确定搜索策略
- **来源评估：** 可信度启发式方法（时效性、同行评审、权威性、交叉验证）
- **综合方法：** 主题分析、比较分析、时间线叙事
- **报告结构：** 执行摘要、方法论、发现、分析、结论
- **引用管理：** 追踪来源出处、格式化引用

### 3.4 sub_agent.txt

**当前：** 法律子 agent 角色（文档审阅员、判例研究员等）

**新版：** 研究子 agent 角色：
- **SearchAgent：** 执行网络搜索、阅读页面、提取关键主张
- **AnalysisAgent：** 评估来源、交叉验证主张、识别矛盾
- **SynthesisAgent：** 组织发现、起草报告章节
- **ReviewAgent：** 质量检查引用、覆盖率、逻辑

### 3.5 result_filter.txt

**当前：** 法律相关度过滤（法条引用、判例精确性）

**新版：** 研究质量过滤：
- 与研究问题的相关度
- 来源可信度评分
- 事实准确性（交叉验证）
- 时效性
- 替代视角的覆盖
- 引用完整性

---

## 4. 新工具设计

### 4.1 research_plan.py

```
用途：将研究问题分解为子问题和搜索策略

输入：research_question, domain_hints=None, depth="standard"
输出：{
  "main_question": "...",
  "sub_questions": [
    {"id": "sq1", "question": "...", "rationale": "...", "search_keywords": [...]},
    ...
  ],
  "suggested_sources": ["学术", "新闻", "政府", "行业"],
  "estimated_depth": 3  // 研究迭代次数
}
```

### 4.2 source_evaluate.py

```
用途：对来源的可信度和相关度打分

输入：url, title, content_excerpt, metadata
输出：{
  "credibility_score": 0.0-1.0,
  "factors": {
    "domain_authority": 0.8,    // .edu/.gov > .org > .com
    "recency": 0.9,              // 时效性
    "peer_reviewed": True,
    "citation_count": 42,
    "author_expertise": 0.7,
    "cross_validation": 0.85     // 被其他来源佐证
  },
  "verdict": "highly_credible" | "moderately_credible" | "low_credibility" | "unverifiable"
}
```

### 4.3 knowledge_search.py

```
用途：跨 session 知识检索 — 搜索过往研究结果

输入：query, session_id=None, limit=5
输出：[
  {
    "session_id": "...",
    "question": "原始研究问题",
    "finding": "关键发现",
    "sources": [...],
    "confidence": "high" | "medium" | "low",
    "timestamp": "..."
  },
  ...
]
```

### 4.4 report_generator.py

```
用途：从收集到的研究发现生成结构化研究报告

输入：research_plan, findings: List[Finding], format="markdown"
输出：{
  "report": "## 执行摘要\n...\n## 方法论\n...\n## 发现\n...\n## 分析\n...\n## 结论\n...\n## 参考文献\n...",
  "citation_count": 15,
  "effective_citations": 12,
  "section_count": 6,
  "word_count": 3500
}
```

### 4.5 工具集成

所有新工具遵循现有的 `@tool` 装饰器模式（位于 `agent_os/tools/`），自动注册到 `ToolRegistry`。

现有法律工具（`retrieval_untils.py`、`untils_case.py`）保留但默认 `enabled=False`，通过配置开关重新启用。

---

## 5. Benchmark 适配策略

### 5.1 GAIA（多步、工具使用）

**挑战：** 评估分解真实任务和使用工具的能力（网络搜索、文件操作、代码执行）。

**适配：**
- GAIA 问题通常涉及寻找特定数据（如"X 在 Y 年发表了多少篇论文？"）
- 我们的研究规划器 + 迭代搜索循环直接对应 GAIA 的多步需求
- 代码执行工具用于数据分析任务（已支持）
- **目标分数：** >40%（对标 GPT-5 Mini 的 44.8%）

### 5.2 BrowseComp（网页持久搜索）

**挑战：** 1,266 道题，需要创造性、持续性的网络导航来寻找隐藏信息。

**适配：**
- 需要跨多个网站进行激进的持续搜索
- 我们的并行搜索器 + 来源评估器直接解决这个问题
- 关键能力：识别何时需要转换搜索策略
- **目标分数：** >30%（通过好的策略超越 GPT-5 的 20.1%）

### 5.3 DeepResearch Bench（报告质量 + 引用）

**挑战：** 100 个博士级任务；通过 RACE（报告质量）和 FACT（引用准确性）评估。

**适配：**
- 这是我们的主要 benchmark——与论文目标直接对齐
- RACE 分数取决于报告结构、深度、证据质量
- FACT 分数取决于引用准确性和有效引用数量
- 我们的 `report_generator` 带结构化输出 + 引用追踪是关键
- **目标分数：** 排行榜前 5

### 5.4 HLE（专家推理）

**挑战：** 2,500 道研究生级问题，涵盖 100+ 学科。

**适配：**
- HLE 问题需要深度推理，而非网络搜索
- 我们的 thinking 协议 + 推理能力是核心
- 网络搜索可辅助验证事实，但推理是核心
- **目标分数：** >25%（对标 GPT-5 的 25.3%）

### 5.5 SimpleQA（事实准确性）

**挑战：** 4,326 道事实性问题，有唯一确定答案。

**适配：**
- 直接测试我们的信息检索准确性
- 来源评估器 + 交叉验证应有所帮助
- 网络搜索为事实性问题提供依据
- **目标分数：** >50%（对标 o3 的 50.5%）

---

## 6. 实施路线图

### 阶段 1：Prompts 与身份（第 1 周）
1. 重写 `agent_system.txt` 为通用研究
2. 创建 `RESEARCH_VALUES.md`（替代 SOUL.md）
3. 创建 `RESEARCHER.md`（替代 AGENT.md）
4. 更新 `sub_agent.txt` 为研究子 agent
5. 更新 `result_filter.txt` 为研究质量过滤

### 阶段 2：新工具（第 2 周）
6. 实现 `research_plan.py` — 查询分解
7. 实现 `source_evaluate.py` — 可信度评分
8. 实现 `knowledge_search.py` — 跨 session 检索
9. 实现 `report_generator.py` — 结构化报告输出

### 阶段 3：内核通用化（第 2-3 周）
10. 清理 `agent_loop.py` — 移除法律归档分支
11. 清理 `sub_agent.py` — 移除法律归档分支
12. 更新 `config.py` 默认值（阶段名、默认工具）
13. 更新 `core/session.py` 默认阶段

### 阶段 4：配置与常驻研究协议（第 3 周）
14. 将 deep research 工作流吸收到 system prompt、AGENT/SOUL 和 memory guidance
15. 更新 `config.yaml` — 新工具列表、benchmark 模式
16. 默认禁用 `law_retrieve`/`case_retrieve`（配置开关）

### 阶段 5：文档与完善（第 3-4 周）
17. 重写 `docs/ARCHITECTURE.md` 为通用研究
18. 重写 `docs/PROJECT.md` 为通用研究
19. 接入 SimpleQA 评测脚本
20. 接入 GAIA 评测脚本
21. 接入 DeepResearch Bench 评测

### 阶段 6：Benchmark 优化（第 4 周+）
22. 针对 BrowseComp 调优 prompt
23. 优化并行搜索策略
24. 提高引用准确性（FACT）
25. 迭代报告质量（RACE）

---

## 7. 关键设计决策

1. **代码即行动 vs JSON 工具调用：** smolagents 使用 CodeAgent（Python 代码作为行动）。我们的内核使用 JSON 工具调用（ReAct）。我们保持 JSON 工具调用的兼容性，但新增 `python_execute` 工具用于代码计算。

2. **单一 agent vs 多 agent：** Kimi K2 使用单一 agent；LangChain 使用多 agent。我们保持单一 agent 以简化，但在需要时使用子 agent（`spawn`）进行并行子任务。

3. **知识库：** 通过 `knowledge_search.py` 实现的跨 session 记忆，接入现有的 `SessionRetriever` 和 `WorkspaceMemory`。无需新基础设施。

4. **引用格式：** 使用 `[来源: 标题, URL, 可信度]` 行内引用。这天然对应 DeepResearch Bench 的 FACT 评估。

5. **迭代控制：** 研究循环有可配置的最大迭代次数（默认 3），在置信度达标时可提前终止。

6. **法律工具保留：** `law_retrieve`/`case_retrieve` 保留在代码库中，配置中禁用。用户可通过 `LAW_TOOLS_ENABLED=true` 重新启用。

---

## 8. 风险与应对

| 风险 | 影响 | 应对措施 |
|------|--------|-----------|
| Benchmark 过拟合 | 高 | 使用多样化 benchmark；优先 DeepResearch Bench |
| 长时间研究导致上下文窗口溢出 | 中 | 每次迭代进行摘要；使用滑动窗口 |
| 来源幻觉（捏造引用） | 高 | FACT 评估；交叉验证；明确的 `source_evaluate` |
| 并行搜索的 API 成本 | 中 | 可配置的并行度；缓存结果 |
| GAIA 第 3 级任务太难 | 低 | 先优先第 1-2 级；迭代改进 |
| 移除法律工具破坏现有用户 | 低 | 保留工具；配置禁用；提供清晰迁移指南 |
