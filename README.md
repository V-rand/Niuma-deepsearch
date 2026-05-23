# LawClaw — 法律 AI Agent 操作系统

LawClaw 是一个**通用法律 AI Agent 运行时**，为律师和法务团队提供自主规划、多源检索、结构化研究的智能工作伙伴。

## 设计哲学

- **内核通用，业务外挂**：kernel 只管理 ReAct 循环、工具调度、上下文编排；法律知识和流程通过 skill 和 tool 注入
- **Session 即案件工作区**：每个 session 对应一个长期存活的法律案件，独立文件系统 + SQLite 状态持久化
- **事件驱动**：所有状态变化通过结构化事件流上报，CLI 完全解耦
- **溯源优先**：每个结论要求可回溯的来源路径

## 快速开始

```bash
# 安装依赖
uv sync

# 配置 API key
cp .env.example .env
# 编辑 .env 填入 DEEPSEEK_API_KEY（DeepSeek 原生）

# 运行 CLI
uv run python cli.py
```

## 架构

```
┌──────────────────────────────────────────────────────┐
│                    CLI (cli.py)                      │
├──────────────────────────────────────────────────────┤
│                   AgentOS 组装层                      │
│  SessionManager  Config  SkillLoader  ToolRegistry   │
├──────────────────────────────────────────────────────┤
│                   Kernel (核心引擎)                    │
│  AgentLoop (ReAct)  SubAgent (spawn)  ResultFilter   │
├──────────────────────────────────────────────────────┤
│                   Memory (记忆系统)                    │
│  ContextCompiler  WorkspaceMemory  SessionRetriever   │
├──────────────────────────────────────────────────────┤
│                   Storage (持久化)                     │
│  SQLite (FTS5 + Embedding)                           │
└──────────────────────────────────────────────────────┘
```

## 核心模块

| 模块 | 路径 | 职责 |
|------|------|------|
| AgentLoop | `agent_os/kernel/agent_loop.py` | ReAct 主循环：模型调用 → 流式解析 → 工具执行 |
| SubAgent | `agent_os/kernel/sub_agent.py` | 独立子 Agent：上下文隔离、并行执行、可中断 |
| ContextCompiler | `agent_os/memory/context_compiler.py` | 编译 system prompt：注入 skills 索引、profile、工作区文件 |
| WorkspaceMemory | `agent_os/memory/workspace.py` | 文件→artifact 同步、分块索引、embedding 后台任务 |
| ToolRegistry | `agent_os/tools/registry.py` | 工具注册、schema 生成、contextvars 会话注入 |
| SkillLoader | `agent_os/skills/loader.py` | skills/ 目录自动发现、frontmatter 解析、XML 索引生成 |
| SessionManager | `agent_os/core/session.py` | Session CRUD、workspace 模板、沙盒路径校验 |
| SQLiteStore | `agent_os/storage/sqlite_store.py` | 6 表 + 2 FTS5 虚拟表、embedding JSON 列 |

## Provider

```yaml
# config.yaml
provider: "deepseek"    # DeepSeek 原生（KV cache 稳定 90%+，支持 reasoning_effort）
```

| 特性 | DeepSeek |
|------|----------|
| KV Cache | 显式前缀，稳定 (90%-98%+) |
| 推理模式 | `reasoning_effort: high/max` |
| 并行工具调用 | 完整支持 |
| API Key | `DEEPSEEK_API_KEY` |

## 事件流

AgentLoop.process() 产出 10 种结构化事件，CLI 消费这些事件渲染 UI：

| 事件 | type | 含义 |
|------|------|------|
| ActivityEvent | `activity` | 进度标记（context compiled, run started, model completed 等） |
| ThinkingStreamEvent | `thinking_stream` | 模型思维链输出 |
| ContentStreamEvent | `content_stream` | 模型最终回答输出 |
| ToolCallEvent | `tool_call` | 工具调用开始 |
| ToolResultEvent | `tool_result` | 工具调用结果 |
| ContentEvent | `content` | 最终回答 |
| ErrorEvent | `error` | 运行失败 |
| InterventionEvent | `intervention` | 人工干预 |
| QuestionEvent | `question` | 问题类型干预 |
| SessionCompressedEvent | `session.compressed` | Token 预算触发压缩 |

完整类型定义见 `agent_os/kernel/event_types.py`，可直接映射为 TypeScript 的 `interface`。

## 工具系统

| 分类 | 工具 | 用途 |
|------|------|------|
| 文件 | `file_read` `file_write` `file_append` `file_delete` `file_list` `file_grep` `file_tree` `edit` | 工作区文件 CRUD + 浏览 + 编辑 |
| 检索 | `workspace_search` `law_retrieve` `case_retrieve` `web_search` `web_read` | FTS5+Embedding 混合检索 / 法规 / 案例 / 网络 |
| 任务 | `todowrite` `reminder_create` | 任务管理 / 提醒 |
| 子 Agent | `spawn` `send_message` `task_stop` | 子 Agent 派发 / 通信 / 终止 |
| 执行 | `bash` `upload_parse` | Shell / 文档解析 |
| Skills | `skill_use` `skill_propose` | 加载 skill / 主动推荐 |
| 交互 | `question` | 向用户提问 |

### 工具插件

`agent_os/tools/plugins/` 目录下 `.py` 文件自动发现。每个插件需暴露 `register(tool_registry)` 函数。

> **注意**：skills 目录不支持 `.py` 工具脚本，工具统一走 `tools/plugins/` 注册。

## Skills 系统

Skills 是**零代码工作流定义**（纯 Markdown），位于 `skills/` 目录：

```
skills/
├── legal/                  # 法律场景
│   └── legal_case/         # 案件工作区
│       └── SKILL.md
└── research/               # 研究场景
    └── deep_research/      # 深度研究
        └── SKILL.md
```

每个 SKILL.md 包含 YAML frontmatter（`description`、`allowed-tools` 等）和 Markdown 正文。

**工作流**：system prompt 的 `<available_skills>` 索引展示所有 skill 的 name+description+path → 模型调用 `skill_use(name="...")` → ` 返回 <skill_content>` 正文 → 模型按照 skill 指令工作。skill 内容作为 tool result 进入对话历史，不修改 system prompt，不影响 KV cache。

## 目录结构

```
LawClaw/
├── cli.py                   # CLI 入口
├── config.yaml              # 运行配置（带中文注释）
├── pyproject.toml           # 依赖声明
├── agent_os/                # 核心运行时
│   ├── agent_os.py          # DI 组装器
│   ├── config.py            # 配置解析
│   ├── core/                # Session / Event / FileSystem
│   ├── kernel/              # AgentLoop / SubAgent / Helpers / event_types
│   ├── memory/              # ContextCompiler / Workspace / Retriever
│   ├── storage/             # SQLiteStore (FTS5 + Embedding)
│   ├── tools/               # ToolRegistry + 22 内置工具 + plugins/
│   ├── scheduler/           # InterruptScheduler (飞书提醒)
│   ├── ingest/              # MinerU 文档解析
│   ├── skills/              # SkillLoader (Markdown 工作流)
│   └── prompts/             # System prompt 模板
├── skills/                  # 零代码 workflow 定义
└── docs/                    # 技术文档
```

## 技术文档

- [总体架构](docs/ARCHITECTURE.md)
- [项目规划](docs/PROJECT.md)

## 技术栈

| 层 | 技术 |
|:---|:---|
| 语言 | Python 3.11+ |
| 异步 | asyncio (AsyncGenerator, ContextVar, asyncio.Lock/gather/Event) |
| 模型 | DeepSeek v4 (OpenAI 兼容 API) |
| 数据库 | SQLite (FTS5 + embedding_json) |
| 向量 | text-embedding-v4 (1024 维) |
| 终端 | Rich + prompt_toolkit + readline |
| 包管理 | uv |
| 配置 | YAML (config.yaml) + .env |

## 设计原则

1. **Kernel 无业务** — 只管理 session、工具、检索、文件、提醒、中断；业务通过 skill + tool 注入
2. **Session 隔离** — 每个 session 独立 work_dir，法律保密性要求 session 间不共享状态
3. **ReAct Loop** — Plan → Act → Observe → Reflect → 循环
4. **KV-cache 安全** — 系统 prompt 编译冻结，动态内容追加在消息尾部
5. **显式失败** — 不用 `except: pass`，外部 API 失败不伪造成功
6. **溯源优先** — 每个论断可回溯到来源文件路径或法条出处

