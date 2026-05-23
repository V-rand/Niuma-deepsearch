# AgentOS4Law 项目文档

## 1. 需求与动机

### 1.1 背景

法律案件工作中，律师面临大量重复性劳动——法规检索、类案研究、证据梳理、文书撰写。传统法律科技产品多采用固定工作流模板，适应度差；直接调用 LLM 则缺少案件组织、会话隔离、长上下文管理等基础设施。

### 1.2 设计目标

1. **通用 AgentOS** — 不是写死的法律 workflow 引擎，而是可扩展的底层操作系统。法律场景是默认产品入口，但不是唯一形态。
2. **长期案件工作区** — 单次 session 可能持续数天到数月，需要会话隔离、上下文压缩、工件持久化。
3. **人机协作** — 不是全自动，而是律师主导 + agent 辅助。人工修正（interventions）、中断调度、审核机制是核心能力。
4. **可定制** — Skills 系统允许客户自定义工作区结构、工具集和行为提示。Prompt 模板化，不硬编码在 kernel。

### 1.3 约束

- 法律保密性要求 session 完全隔离，无跨 session 记忆
- 首次响应 ≤5 秒，复杂任务 ≤30 秒
- 模型 API 不可用时降级但不停服
- KV cache 保护是最高优先级——system prompt 编译后冻结，不动态重新组装

## 2. 实现架构

### 2.1 分层模型

```
┌─────────────────────────────────────────────────────────┐
│                     Agent Loop (ReAct)                   │
│  每次迭代: Plan → Action → Observe → Reflect → Output   │
│  Max 16 步, token 超 250K 自动压缩 fork                 │
├─────────────────────────────────────────────────────────┤
│  System Prompt (XML 结构化, 10 节, ~5000 chars)         │
│  calibration → role → principles → planning → taxonomy   │
│  → tools → reflection → quality → workspace → session   │
├─────────────────────────────────────────────────────────┤
│  Tools (23) | SubAgent | Skills | Memory | Scheduler    │
├─────────────────────────────────────────────────────────┤
│  SQLite (5 表) + File System (work_dir)                 │
└─────────────────────────────────────────────────────────┘
```

### 2.2 核心模块

| 模块 | 文件 | 职责 |
|------|------|------|
| `AgentOS` | `agent_os/agent_os.py` | 系统入口，协调所有组件初始化 |
| `AgentLoop` | `agent_os/kernel/agent_loop.py` | ReAct 循环：规划→执行→观察→反思→输出 |
| `SubAgent` | `agent_os/kernel/sub_agent.py` | 隔离的子 agent，不能嵌套，返回 `<task_result>` |
| `SessionManager` | `agent_os/core/session.py` | 会话 CRUD、fork、work_dir 管理、自动创建工作区 |
| `ContextCompiler` | `agent_os/memory/context_compiler.py` | 从 work_dir 文件 + skill profile 编译 XML system prompt |
| `SessionRetriever` | `agent_os/memory/retriever.py` | FTS + embedding(1024d) + RRF 混合检索 |
| `EmbeddingClient` | `agent_os/memory/embedding.py` | DashScope text-embedding-v4 |
| `ToolRegistry` | `agent_os/tools/registry.py` | 工具注册、发现、check_available 检查 |
| `SkillLoader` | `agent_os/skills/loader.py` | 2 级分类发现 + YAML frontmatter 解析 |
| `SQLiteStore` | `agent_os/storage/sqlite_store.py` | 持久化 6 表：sessions/messages/artifacts/chunks/reminders/interventions |
| `InterruptScheduler` | `agent_os/scheduler/scheduler.py` | 定时提醒轮询 + inject_message 注入 |
| `ResultFilter` | `agent_os/kernel/result_filter.py` | 外部检索结果精简后返回模型 |
| `FileSystem` | `agent_os/core/filesystem.py` | work_dir 内的文件操作（含 uploads 只读保护） |
| `MineruClient` | `agent_os/ingest/mineru.py` | 文档解析：v1 agent API + v4 premium 回退 + doc2txt(.doc) |

### 2.3 数据流

```
用户消息 → process()
  → 检查 interrupts/scheduled messages
  → estimate_tokens → >250K → compress + fork session
  → context_compiler.compile(session, message)
    → 10 XML 节的 system prompt
    → SOUL.md / AGENT.md / MEMORY.md (frozen snapshot)
    → skill profile + skills index
    → <system-reminder> (动态追加，不破坏 KV cache)
  → model.chat.completions.create(function calling, tools=[
    → tool execution
      → 外部(web/law/case) → 归档 raw_search/
      → 内部(file/todo/search) → work_dir + DB
  → 反思 → 质量检查 → 输出
  → 任务完成 → file_write research/memory/*.md
```

### 2.4 Session 生命周期

```
create_session()
  → work_dir/{id[:8]}_{name}/
  → _ensure_default_workspace(profile)
    → SOUL.md + AGENT.md + MEMORY.md + cases.md + todo.md
    → directories: uploads/research/drafts/raw_search/evidence/logs
    → artifact index write (索引摘要)
  → SQLite: sessions row

chat() → messages accumulate
  token > 250K →
    compress(): 
      → 语义切片: 保留 user + file_write + decision 原始回合
      → LLM 压缩中间回合为 `<chronology>` 时序摘要
      → fork_session(work_dir=parent.work_dir)
      → 注入 `[COMPACTION vN]`
      → 父 session status = "compressed"
      → 压缩状态写入 compression_state.md
```

### 2.5 工具索引 (23)

| 类别 | 工具 | 后端 |
|------|------|------|
| 文件 | read/write/append/delete/list/grep/tree | work_dir |
| 搜索 | law_retrieve / case_retrieve | 得理 RAG API |
| 搜索 | web_search | Tavily/Serper |
| 搜索 | workspace_search | FTS+Embedding+RRF |
| 网页 | web_read | Jina/Firecrawl/Trafilatura |
| 解析 | upload_parse | MinerU v1+v4+doc2txt |
| 任务 | todowrite / reminder_create / artifact_upsert | SQLite + work_dir |
| Agent | spawn | 独立 AgentLoop |
| 系统 | bash | subprocess |
| Skills | skill_use | SkillLoader |
| OCR | ocr_parse | 内部 OCR (未注册) |

### 2.6 配置系统

```
config.yaml               ← 所有运行参数（带中文注释）
  model: deepseek-v4-flash
  context_token_threshold: 250000
  model_timeout_seconds: 200
  ...

.env                      ← 仅放密钥
  OPENAI_API_KEY=sk-xxx
  TAVILY_API_KEY=tvly-xxx
  ...

加载优先级：
  config.yaml → dataclass 字段默认值 → Settings.from_env()
  密钥仅从环境变量读取，不出现在 config.yaml
```

### 2.7 上下文压缩

```
触发: total_tokens > context_token_threshold (250K)
流程:
  1. 固定头(3) + 尾(12×2) 回合，保留原样
  2. 语义识别中间回合中的 user 消息/file_write/decision → 保留
  3. 其余回合序列化 → LLM 压缩为 <chronology> 时序摘要
  4. fork_session(work_dir=parent.work_dir)
  5. 注入 [COMPACTION vN] + XML 摘要
  6. 写 compression_state.md → work_dir

KV cache 保护:
  - 不在已有消息之间插入新消息 (不破坏 prefix)
  - <system-reminder> 仅追加在消息尾部
  - 压缩使用 cache-aligned 方式 (在原上下文末尾追加 summarize 请求)

与 DeepSeek V4 配合:
  - 当前 250K 阈值 (DeepSeek 有 1M 窗口, 97% KV cache hit rate)
  - 长上下文任务自动带 thinking 模式 (extra_body)
  - cache hit 价格 0.02元/M vs miss 1元/M (Flash)
  - 连续会话不做 routine compaction → 保持 prefix 稳定
```

### 2.8 Skills 系统

Skills 现在是可选的项目/领域扩展机制。默认 deep research 行为不再依赖 `deep_research` skill，而是常驻在 system prompt、AGENT/SOUL 和 memory guidance 中。

```
skills/
└── <domain_or_project>/
    └── <workflow>/SKILL.md        → optional workflow extension

SKILL.md 结构:
  ---
  name: workflow-name
  layer: domain
  allowed-tools: [...]
  profile:
    folders: [uploads, research, drafts, ...]
    files:
      case_overview.md: "# {{session_name}}"
  triggers: [诉讼, 仲裁, 证据, 起诉]
  ---
  (之后的内容为 prompt_append，注入 system prompt 尾部)

配置在 SKILL frontmatter，不在 Python。公司可直接定制目录结构和行为提示。
```

### 2.9 混合检索

```
embedding(text-embedding-v4, 1024维)
  + FTS5 (SQLite 全文索引)
  + RRF (Reciprocal Rank Fusion)

查询: query Q → FTS hit + embedding cosine similarity → RRF 合并 → top-N

索引: 工件创建时触发后台 embedding, 存储为 JSON 在 chunks 表

跨压缩链: work_dir JOIN 查询, 一次 SQL 覆盖所有 fork session
  SELECT c.* FROM chunks c
  JOIN artifacts a ON c.artifact_id = a.id
  JOIN sessions s ON a.session_id = s.id
  WHERE s.work_dir = ?
```

### 2.10 人工干预

```
inject_message(session_id, text)      → 下一次迭代注入 (不打断当前)
request_interrupt(session_id, reason)  → 当前迭代完成后通知暂停

interventions 表: role, content, kind, status
CLI: /correct 指令 (standalone tool)
API: POST /sessions/{id}/interventions
```

## 3. 当前问题与边界

### 3.1 已知失败

| 测试 | 失败原因 | 严重度 |
|------|---------|--------|
| `chat_tool_call` | deepseek-v4-flash 未调用 file_read (模型行为) | 低 |
| `sub_agent_completion` | 子 agent 30s 超时 (需等待模型响应) | 中 |
| `sub_agent_event` / `reminder_event` | EventBus 纯 pub/sub，不存储历史 | 低 |

### 3.2 架构局限

- **EventBus 无持久化** — 事件发布后无法回溯
- **Todowrite 不持久** — todo 在内存，不写入 SQLite (cli session 使用)
- **无 metrics/telemetry** — 无 KV cache hit/miss 监控，无法做压缩净收益决策
- **无批处理队列** — 所有请求同步等待

## 4. 未来工作

### 4.1 Memory 增强

| 方向 | 说明 | 优先级 |
|------|------|--------|
| **分层上下文** | State 层(8K) + 工作层(200K) + 证据层(爆发1M)，替换平铺模式 | P1 |
| **MEMORY.md 自动提炼** | 每 N 次 file_write 后压缩 MEMORY.md，保留关键事实，删除冗余 | P1 |
| **cache telemetry** | 读取 API 返回的 `prompt_cache_hit_tokens`，计算有效成本 | P1 |
| **跨 session 记忆 (可选)** | 仅同一案件(work_dir)下的不同 session 可共享 SOUL.md/AGENT.md | P2 |

### 4.2 上层工作流接入

| 方向 | 说明 | 优先级 |
|------|------|--------|
| **Workflow Contract** | 定义一组阶段(stage)+每个阶段的输入/输出/检查项，Skills profile 注入 | P1 |
| **阶段感知** | Context compiler 根据 stage 自动选择 skill profile | P1 |
| **自动阶段推进** | agent 完成任务后检测退出条件 → 推进 stage | P2 |
| **UI 工作台** | FastAPI 静态工作台可做：阶段面板、文件树、对话流、提醒面板 | P2 |

### 4.3 文件管理

| 方向 | 说明 | 优先级 |
|------|------|--------|
| **版本控制** | 文件写入时自动备份旧版本 (drafts/.history/) | P1 |
| **批量上传** | 目录上传、zip 解压、批量解析 | P1 |
| **文件变更事件** | 文件被 agent 修改时 publish event | P2 |
| **元数据索引** | 文件名 + 类型 + 大小 + 创建时间 + embedding → 可搜索 | P2 |
| **大文件流式读取** | >10MB 文件分块读，不一次加载到内存 | P2 |

### 4.4 知识图谱

| 方向 | 说明 | 优先级 |
|------|------|--------|
| **实体提取** | 从法律文档提取 当事人/法院/案由/法条/金额 → 结构化 | P2 |
| **关系构建** | 当事人↔合同 ↔ 金额 ↔ 法条等关系 | P3 |
| **图查询** | "找出 A 公司与 B 公司之间的合同和诉讼" | P3 |
| **推理** | 基于图谱的事实一致性检查、冲突检测 | P3 |
| **可视化** | 导出力导向图供法官/律师查看 | P3 |

### 4.5 工具链

| 方向 | 说明 | 优先级 |
|------|------|--------|
| **Tool Flow** | 工具编排：顺序执行、条件分支、重试策略 | P2 |
| **Tool 并行执行** | 多工具并行（如同时 law_retrieve + case_retrieve + web_search） | P2 |
| **Human-in-the-loop** | 工具执行前请求人工确认 (合同风险高时) | P2 |
| **Tool 结果缓存** | 相同参数的工具调用结果短时缓存 (避免重复检索) | P2 |

### 4.6 质量与测试

| 方向 | 说明 | 优先级 |
|------|------|--------|
| **pytest 迁移** | 脚本 → pytest 套件，含 fixture、mock、parametrize | P1 |
| **CI/CD** | GitHub Actions 自动运行语法检查 + 集成测试 | P1 |
| **Benchmark** | 压缩质量评估、检索准确率、模型响应延迟 | P2 |
| **压力测试** | 100+ 并发 session、10M+ 消息、大文件 | P3 |
