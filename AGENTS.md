# Repository Guidelines

## Project Structure & Module Organization
`agent_os4law/` is the active codebase. Core runtime code lives in `agent_os/`:

| Module | Path | Responsibility |
|--------|------|---------------|
| `core/` | `agent_os/core/` | SessionManager, Session, event bus |
| `kernel/` | `agent_os/kernel/` | AgentLoop (ReAct), SubAgent, ResultFilterAgent |
| `memory/` | `agent_os/memory/` | ContextCompiler, EmbeddingClient, SessionRetriever, WorkspaceMemory |
| `storage/` | `agent_os/storage/` | SQLiteStore — sessions, messages, artifacts, FTS5, embeddings |
| `tools/` | `agent_os/tools/` | ToolRegistry, built-in tools, plugin system |
| `scheduler/` | `agent_os/scheduler/` | InterruptScheduler, reminder firing, Feishu notifications |
| `skills/` | `skills/` | SKILL.md files (auto-discovered, zero-code workflows) |

Documentation: `docs/` (architecture, REQUIRE.md).

## Build, Test, and Development Commands
Use `uv` only. Run from `agent_os4law/`, not workspace root.

```bash
# Run
uv run python cli.py                                        # CLI (interactive or pipe)
uv run python cli.py --non-tty < input.txt                  # Non-TTY batch mode

# Quick syntax check
uv run python -m compileall agent_os cli.py
```

## Key Architecture Rules

### Kernel is Generic
This project builds a **general AgentOS** for legal work, not a hard-coded workflow engine. Keep business flow out of the kernel:
- **Kernel**: sessions, ReAct loop, tool execution, context management, events, interrupts
- **Skills**: zero-code workflow definitions (pure Markdown)
- **Tools**: executable capabilities (Python, including auto-discovered plugins)

### Session = Case Workspace
Each session is a long-lived case workspace:
- Files under `data/sessions/{id}_{name}/` with `uploads/`, `drafts/`, `research/`, `raw_search/`
- Structured state in SQLite (messages, artifacts, todos, reminders)
- Derived artifacts preserve source lineage pointing back to `uploads/` originals

### Events are Ordered Structured Dicts
All agent output flows through typed events: `{type, phase, detail, payload}`. CLI, TUI, and API are pure consumers — they never affect the kernel.

### Sub-Agent Isolation
Sub-agents (`spawn` tool) create independent child sessions with `parent_session_id`. They share the parent's `work_dir` but have isolated message history. Interruptible via `interrupt_check` asyncio.Event.

### KV Cache Awareness
- Timestamp injects as **user message** after workspace tree, before history — keeps system prompt prefix intact
- `active_skills` changes trigger context cache invalidation (`cleanup_session`)
- `model.completed` event shows cache hit rate and compression threshold usage

## Coding Style & Naming Conventions
Target Python 3.11+. Use 4-space indentation, type hints on public methods, `snake_case` for functions/files, and `PascalCase` for classes. Keep modules direct and readable. Avoid unnecessary abstraction, silent fallbacks, and "defensive" wrappers that hide failures. Prefer explicit errors plus lightweight logging over `except: pass`.

## Testing Guidelines
No formal pytest suite; validate with pipe-mode inputs and manual chat sessions. Favor realistic checks over mock-heavy tests:
- Multi-turn chat
- Tool calling
- Reminder firing
- Artifact persistence and retrieval
- External law/case/web integrations

## Common Pitfalls
- **Module-level defs between class methods** silently close the class in Python — always check indentation when adding standalone functions
- **Terminal mode pollution**: prompt_toolkit sets raw mode; always call `stty sane` on exit to restore cooked mode
- **compileall PASS ≠ no runtime errors** — some indentation issues parse OK but create module-level code instead of class methods
- **Don't modify system prompt prefix** — it's part of the KV cache key. Inject dynamic content as user messages
- **Static methods must be inside class body** — if placed outside class, all subsequent class methods become orphaned module-level functions

## Security & Configuration Tips
Read secrets from `.env`; do not hardcode keys. Use `OPENAI_API_KEY` with DashScope-compatible endpoints. Keep external API usage optional where possible, but do not fake success when integrations fail.
- workspace path sandbox: `_safe_workspace_path()` rejects absolute paths and `..` traversal
- Sub-agent tools restrictable via `allowed_tools` parameter
- `disabled_tools` / `enabled_tools` in config.yaml for tool-level access control
