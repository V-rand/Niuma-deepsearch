# AgentOS4Law 架构文档

## 总览

AgentOS4Law 是一个通用 Agent 操作系统内核。法律案件管理只是默认产品入口，底层不包含任何业务流程代码。

```
用户 / API
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│                    Agent Loop (ReAct)                    │
│  ┌──────────────────────────────────────────────────┐   │
│  │ Compile System Prompt                            │   │
│  │   role → principles → planning → taxonomy        │   │
│  │   → tools → reflection → quality                 │   │
│  │   → calibration → workspace → session            │   │
│  └──────────────────────────────────────────────────┘   │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐             │
│  │  Plan    │→ │  Action  │→ │ Observe  │→ Reflect    │
│  └──────────┘  └──────────┘  └──────────┘             │
│        ↑                                       │        │
│        └───────────────────────────────────────┘        │
│  iteration limit: 16  |  token threshold: 210k          │
└─────────────────────────────────────────────────────────┘
    │                   │                    │
    ▼                   ▼                    ▼
┌──────────┐   ┌──────────────┐   ┌────────────────┐
│ Session  │   │ SubAgent     │   │ Scheduler      │
│ + work_dir│   │ spawn → isolate│  │ reminders      │
│ + SQLite  │   │ <task_result>│  │ inject_message  │
│ + fork    │   │ no nesting   │  │ request_interrupt│
└──────────┘   └──────────────┘   └────────────────┘
    │
    ├── Tools (23 built-in)
    ├── Skills (optional user/project workflows)
    ├── Memory (FTS + embedding + RRF)
    └── Storage (SQLite: sessions, messages, artifacts, reminders)
```

## 数据流

```
用户消息 → process()
              │
              ├── check interrupts / scheduled messages
              ├── estimate tokens → 超阈值→compress+fork
              ├── context_compiler.compile()
              │     ├── agent_system.txt (10 XML sections)
              │     ├── SOUL.md / AGENT.md / MEMORY.md (frozen snapshot)
              │     ├── skill profile (YAML frontmatter)
              │     ├── skills index
              │     └── <system-reminder> (dynamic, append-only)
              │
              ├── model chat.completions.create(function calling)
              │
              ├── tool execution
              │     ├── external (web, law, case) → archive to raw_search/
              │     └── internal (file, search, todo) → work_dir + DB
              │
              ├── reflection → quality check → output
               └── on task complete → file_write research/memory/*.md
```

## Session 管理

### 创建与隔离
- 每个 session 有独立 `work_dir`、`messages`、`artifacts`、`reminders`
- Session 间完全隔离（法律保密性要求）
- 默认 workspace 自动生成：`SOUL.md`、`AGENT.md`、`MEMORY.md`、`todo.md`

### 压缩链
```
Session A (compressed) → fork → Session B (work_dir = A.work_dir)
                                → fork → Session C (work_dir = A.work_dir)
```
- `compression_version` 递增追踪压缩次数
- work_dir 共享确保文件/工作区跨压缩链持续可见
- 父 session 标记 `status = "compressed"`，完整消息历史保留在 SQLite
- 压缩状态写入 `compression_state.md`

### 上下文编译
- System prompt 在 session 创建时编译冻结（KV-cache 安全）
- 动态内容（文件上传通知、todo 提醒、中断消息）通过 `<system-reminder>` 附在消息尾部
- 绝不在已有消息之间插入新消息（避免 KV-cache 前缀偏移）

## 工具系统

### 注册与可用性
- ToolRegistry 单例，按 name 注册
- 工具实现 `check_available()` 方法，注册前检查（API key、网络等）
- 不可用工具自动跳过，不阻塞启动

### 工具分类

| 类别 | 工具 | 说明 |
|------|------|------|
| 文件操作 | file_read/write/append/delete/list/grep/tree | 工作区内文件管理 |
| 文档解析 | upload_parse | PDF/DOCX/XLSX/图片/DOC → Markdown |
| 搜索 | workspace_search (内部) / law_retrieve (法规) / case_retrieve (案例) / web_search (外部) | 四类搜索，语义明确 |
| 网页 | web_read | Jina/Firecrawl/Trafilatura 多引擎回退 |
| 任务管理 | todowrite / reminder_create / artifact_upsert | 结构化任务/工件/提醒 |
| AI 协作 | spawn | 派发子 agent，不写父 session |
| 系统 | bash / skill_use | Shell + skill 管理 |

### 结果过滤
- 外部检索结果（law/case/web）通过 `result_filter` 精简后返回模型
- 原始完整结果归档到 `raw_search/`，后续可通过 file_read 获取全文

## 混合检索

### 索引管道
- 工件创建时触发后台 embedding 生成
- DashScope `text-embedding-v4`，1024 维向量
- SQLite FTS5 全文索引 + embedding 向量存储

### 查询流程
```
用户查询
    ├── FTS5 关键词匹配
    ├── embedding 语义检索（cosine similarity）
    └── RRF 融合排序
         └── 返回 top-N 结果（含 source_path 溯源）
```

### 跨压缩链查询
- artifact 表用 `session_id` 关联（级联删除），查询用 `work_dir` JOIN
- 一次查询覆盖压缩链上所有 session 的工件（O(1) SQL，不做 O(N) 循环）

## 上下文压缩

### 触发条件
- 消息 token 估算超 `AGENT_OS_CONTEXT_TOKEN_THRESHOLD`（默认 210k）
- 中日韩字符按 1.5 token、ASCII 按 0.25 token 估算

### 压缩流程
1. 固定头尾切片 + 语义识别：user 消息、file_write 调用、含决策信号 tool 输出 → **保留原样**
2. 其余中间回合序列化 → LLM 压缩为 `<chronology>` 时间顺序摘要
3. 摘要保留因果推理链："做了什么 → 发现了什么 → 因此做了什么 → 得到了什么"
4. fork 新 session（继承 work_dir），注入 `[COMPACTION vN]` + XML 摘要
5. 压缩状态写入 `compression_state.md`

### 保留策略
| 保留 | 丢失 |
|------|------|
| 系统 prompt（原样） | 中间回合精确文本（替换为摘要） |
| 头回合（可配，默认 3） | 中间回合的 token 开销 |
| 尾回合（可配，默认 12） | 旧 session 的上下文缓存（evict） |
| LLM 生成的时序摘要 | |
| work_dir 全部文件 | |

## 子 Agent（spawn）

```
主 Agent
    │
    ├── spawn(任务描述)
    │   │
    │   ├── 独立 Agent Loop（自己的 session）
    │   ├── 可用工具子集（workspace_search / law_retrieve / web_search / file_read / file_write）
    │   ├── 不能 spawn（禁止嵌套）
    │   └── 返回 <task_result> XML
    │
    └── 审核结果 → 写入工作区 / 反馈用户
```

- 子 agent 结果用 `<task_result>` 包装，不写父 session 的 messages 表
- 父 agent 审核后决定是否采纳、写入哪些文件

## Skills 系统

Skills 是可选扩展机制，不承载默认 deep research 方法论。当前产品的研究流程已经吸收到 `agent_system.txt`、`AGENT.md`、`SOUL.md` 和 memory guidance 中；只有项目需要额外领域工作流时才添加 skill。

### 结构
```
skills/
└── <domain_or_project>/
    └── <workflow>/SKILL.md  → optional prompt_append / profile
```

### SKILL.md 格式
```yaml
---
name: legal-case
layer: domain
description: 法律案件工作区
allowed-tools: [law_retrieve, case_retrieve, ...]
profile:
  directories:
    - name: uploads
      description: 客户上传材料
    - name: research
      description: 法律检索与判例研究
  files:
    - name: facts.md
      description: 案件事实梳理
---
# Skill 内容
...
```

- `layer` 决定加载优先级（system > domain）
- `profile` 定义 workspace 目录结构（由 context compiler 注入 system prompt）
- `prompt_append`（前 YAML 之后的内容）附加到 prompt 尾部

## 调度与中断

### 定时提醒（reminder_create）
- 存储在 SQLite，scheduler 后台轮询（间隔可配，默认 30s）
- 到达时间后注入 `<scheduled_message>` 到 session

### 运行中中断
- `inject_message`: 在 agent 循环下一次迭代注入消息（用户/外部干预）
- `request_interrupt`: 在当前迭代完成后通知 agent 暂停（不强制终止）

## 持久化

### SQLite 表结构
```
sessions      ← session_id, name, work_dir, status, compression_version
messages      ← session_id, role, content, kind, created_at
artifacts     ← session_id, path, content, artifact_type, metadata
chunks        ← artifact_id, content, embedding (JSON), source_path
reminders     ← session_id, reminder_type, title, fire_at, fired
interventions ← session_id, role, content, kind, status
```

### 文件系统
```
{data_dir}/
└── sessions/
    └── {id[:8]}_{name}/
        ├── SOUL.md
        ├── AGENT.md
        ├── MEMORY.md
        ├── todo.md
        ├── uploads/          ← 上传材料（只读区）
        ├── drafts/           ← AI 生成输出
        │   └── derived/      ← 解析结果
        ├── research/         ← 研究成果
        ├── raw_search/       ← 外部检索原始内容
        │   ├── law/
        │   ├── case/
        │   └── web_search/
        ├── evidence/         ← 证据分析
        └── logs/             ← 运行日志
```

## 配置

运行参数在 `config.yaml`（中文注释，改完重启生效）。密钥在 `.env`。

### config.yaml（运行参数）

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `model` | deepseek-v4-flash | 模型名称 |
| `base_url` | dashscope.aliyuncs.com/compatible-mode/v1 | API 地址 |
| `model_timeout_seconds` | 200 | 单次请求超时 |
| `max_iterations` | 64 | 单次 ReAct 最大迭代 |
| `context_token_threshold` | 250000 | 触发压缩的 token 阈值 |
| `max_context_messages` | 8 | 上下文摄入消息数 |
| `max_context_items` | 12 | 上下文摄入检索条目 |
| `compress_head_turns` | 3 | 压缩保留头部回合数 |
| `compress_tail_turns` | 6 | 压缩保留尾部回合数 |
| `scheduler_interval_seconds` | 30 | 调度器轮询间隔 |
| `mineru_*` | 见 yaml | MinerU 文档解析参数 |

### .env（密钥）

| 变量 | 说明 |
|------|------|
| `OPENAI_API_KEY` | DashScope API Key（必填） |
| `TAVILY_API_KEY` | 网络搜索 |
| `JINA_API_KEY` | 网页读取 |
| `MINERU_API_TOKEN` | MinerU v4 解析回退 |
| `FEISHU_WEBHOOK` / `FEISHU_SECRET` | 飞书通知 |

## License

MIT
