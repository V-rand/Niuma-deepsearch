"""
Built-in tool handlers.  Each tool is an async function + JSON schema dict,
registered at module level via ``registry.register()``.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from pathlib import Path

from ..core.filesystem import FileSystem
from ..core.session import format_local_timestamp, normalize_utc_timestamp
from ..ingest.mineru import LOCAL_TEXT_SUFFIXES, LOCAL_SPREADSHEET_SUFFIXES
from .registry import (
    ToolResult, get_session_id, get_session_work_dir,
    get_tool_dep, get_tool_registry,
)


# File read dedup cache: (path, mtime) → (content, total_lines)
# Re-reading unchanged files returns a lightweight stub (~500 chars vs full content).
_read_cache: dict[tuple[str, float], tuple[str, int]] = {}

def _invalidate_read_cache(path: str) -> None:
    """Clear all cached entries for *path* (mtime changes on write)."""
    keys = [k for k in _read_cache if k[0] == path]
    for k in keys:
        del _read_cache[k]


try:
    from .retrieval_untils import retrieve as _law_retrieve
except ImportError:
    _law_retrieve = None

try:
    from .untils_case import get_case_results as _case_retrieve
except ImportError:
    _case_retrieve = None


def _fs() -> FileSystem:
    return FileSystem(get_session_work_dir())


# ---------------------------------------------------------------------------
# File tools
# ---------------------------------------------------------------------------

async def file_read(path, offset=0, limit=None, encoding="utf-8", **kw) -> ToolResult:
    try:
        content = _fs().read_file(path, encoding)
        lines = content.splitlines()
        total = len(lines)
        mtime = (Path(get_session_work_dir()) / path).stat().st_mtime if get_session_work_dir() else 0.0
        cache_key = (path, mtime)

        # Dedup: same file + same mtime + full read → return lightweight stub
        if cache_key in _read_cache and offset == 0 and limit is None:
            _cached_content, _cached_lines = _read_cache[cache_key]
            return ToolResult.ok(data={
                "path": path, "unchanged": True,
                "size": len(_cached_content), "total_lines": _cached_lines,
                "note": "[文件未修改——与上次读取一致，省略内容。如需重新显示内容，请使用 file_read 并指定 offset 或 limit。]",
            })

        if offset and limit:
            sliced = lines[offset:offset + limit]
        elif offset:
            sliced = lines[offset:]
        elif limit:
            sliced = lines[:limit]
        else:
            sliced = lines

        # Cache full file content for dedup (only on full reads)
        if offset == 0 and limit is None:
            _read_cache[cache_key] = (content, total)

        r = {"path": path, "content": "\n".join(sliced), "size": len(content), "total_lines": total}
        if offset or (limit and limit < total):
            r["offset"] = offset
            r["limit"] = limit
            r["displayed_lines"] = len(sliced)
        return ToolResult.ok(data=r)
    except Exception as e:
        return ToolResult.fail(str(e))


async def file_write(path, content, encoding="utf-8", **kw) -> ToolResult:
    try:
        p = _fs().write_file(path, content, encoding)
        _invalidate_read_cache(path)
        return ToolResult.ok(data={"path": str(p), "size": len(content)})
    except Exception as e:
        return ToolResult.fail(str(e))


async def file_append(path, content, **kw) -> ToolResult:
    try:
        p = _fs().append_file(path, content)
        _invalidate_read_cache(path)
        return ToolResult.ok(data={"path": str(p), "appended_size": len(content)})
    except Exception as e:
        return ToolResult.fail(str(e))


async def file_delete(path, **kw) -> ToolResult:
    try:
        p = _fs().delete_file(path)
        _invalidate_read_cache(path)
        return ToolResult.ok(data={"path": str(p)})
    except Exception as e:
        return ToolResult.fail(str(e))


async def file_list(path=".", **kw) -> ToolResult:
    try:
        nodes = _fs().list_dir(path)
        return ToolResult.ok(data={"path": path, "items": [{"name": n.name, "type": n.type, "size": n.size, "lines": n.lines} for n in nodes], "count": len(nodes)})
    except Exception as e:
        return ToolResult.fail(str(e))


async def file_grep(pattern, path=".", file_pattern="*", head_limit=250, **kw) -> ToolResult:
    try:
        results = _fs().grep(pattern, path, file_pattern)
        total = len(results)
        truncated = head_limit > 0 and total > head_limit
        if truncated:
            results = results[:head_limit]
        return ToolResult.ok(data={
            "pattern": pattern, "results": results, "count": len(results),
            **({"truncated": True, "total_matches": total,
                "note": f"结果已截断至前 {head_limit} 条（共 {total} 条）。使用 head_limit=0 获取完整结果。"} if truncated else {}),
        })
    except Exception as e:
        return ToolResult.fail(str(e))


async def file_tree(path=".", max_depth=10, **kw) -> ToolResult:
    try:
        return ToolResult.ok(data=_fs().get_tree(path, max_depth))
    except Exception as e:
        return ToolResult.fail(str(e))


# ---------------------------------------------------------------------------
# Bash
# ---------------------------------------------------------------------------


_BASH_DANGEROUS_PATTERNS = [
    (r'\brm\s+-rf\s+(/|~|\$HOME)', "rm -rf 绝对路径或 ~ 被禁止"),
    (r'\brm\s+--no-preserve-root\b', "rm --no-preserve-root 被禁止"),
    (r'\bchmod\s+777\s+/', "全局 chmod 777 被禁止"),
    (r'\bdd\s+if=.*of=/dev/', "dd 直接写块设备被禁止"),
    (r'\.env(?!\.example)\b', "禁止直接操作 .env——密钥文件"),
]


def _check_bash_safety(command: str) -> str:
    for pattern, msg in _BASH_DANGEROUS_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            return msg
    return ""

async def bash(command, timeout=60, **kw) -> ToolResult:
    rejection = _check_bash_safety(command)
    if rejection:
        return ToolResult.fail(rejection)
    try:
        proc = await asyncio.create_subprocess_shell(
            command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            cwd=get_session_work_dir() or ".",
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return ToolResult.fail(f"Command timed out after {timeout} seconds")
        stdout = stdout.decode("utf-8", errors="ignore")
        stderr = stderr.decode("utf-8", errors="ignore")
        _PERSIST_THRESHOLD = 20_000
        _STDOUT_MAX = 100_000
        _STDERR_MAX = 20_000

        output_path = ""
        stdout_d = stdout
        if len(stdout) > _PERSIST_THRESHOLD:
            wd = get_session_work_dir()
            if wd:
                from datetime import datetime as _dt
                ts = _dt.now().strftime("%Y%m%d_%H%M%S")
                output_path = f"raw_search/bash/{ts}.txt"
                try:
                    out_dir = Path(wd) / "raw_search" / "bash"
                    out_dir.mkdir(parents=True, exist_ok=True)
                    (out_dir / f"{ts}.txt").write_text(stdout, encoding="utf-8")
                except OSError:
                    output_path = ""
            tail = stdout[-3000:] if len(stdout) > 3000 else stdout
            stdout_d = (f"[完整输出 {len(stdout):,} 字符已保存至: {output_path}]\n\n"
                        f"...[省略 {len(stdout) - len(tail):,} 字符]...\n\n{tail}" if output_path else
                        f"[输出 {len(stdout):,} 字符——超出显示上限，显示尾部 {len(tail):,} 字符]\n\n{tail}")

        if len(stdout_d) > _STDOUT_MAX:
            stdout_d = stdout_d[:_STDOUT_MAX] + f"\n...[truncated: {len(stdout):,} chars]..."
        stderr_d = stderr[:_STDERR_MAX]
        if len(stderr) > _STDERR_MAX:
            stderr_d += f"\n...[stderr truncated: {len(stderr):,} chars]..."
        data = {"command": command, "returncode": proc.returncode, "stdout": stdout_d, "stderr": stderr_d, "success": proc.returncode == 0}
        if proc.returncode == 0:
            return ToolResult.ok(data=data)
        return ToolResult.fail(f"Exit code {proc.returncode}", data=data)
    except Exception as e:
        return ToolResult.fail(str(e))


# ---------------------------------------------------------------------------
# External retrieval
# ---------------------------------------------------------------------------

async def law_retrieve(query, top_k=5, **kw) -> ToolResult:
    if _law_retrieve is None:
        return ToolResult.fail("law_retrieve not available")
    try:
        results = await asyncio.wait_for(asyncio.to_thread(_law_retrieve, query, top_k_rerank=top_k, size=top_k), timeout=30)
        return ToolResult.ok(data={"query": query, "results": [{
            "title": f"{item.get('laws_name') or item.get('lawsName', '')} {item.get('article_tag') or item.get('articleTag', '')}".strip(),
            "content": item.get("article_content") or item.get("articleContent", ""),
            "laws_name": item.get("laws_name") or item.get("lawsName", ""),
            "article_tag": item.get("article_tag") or item.get("articleTag", ""),
            "timeliness_name": item.get("timeliness_name") or item.get("timelinessName", ""),
            "active_date": item.get("active_date") or item.get("activeDate", ""),
        } for item in results], "count": len(results)})
    except asyncio.TimeoutError:
        return ToolResult.fail("law_retrieve timed out")
    except Exception as e:
        return ToolResult.fail(str(e))


async def case_retrieve(query, top_k=5, **kw) -> ToolResult:
    if _case_retrieve is None:
        return ToolResult.fail("case_retrieve not available")
    try:
        results = await asyncio.wait_for(asyncio.to_thread(_case_retrieve, query, top_k, False), timeout=30)
        return ToolResult.ok(data={"query": query, "results": [{
            "title": item.get("title", ""), "content": item.get("content", ""),
            "source": item.get("source", ""), "case_no": item.get("caseNo", ""),
            "court": item.get("court", ""),
        } for item in results], "count": len(results)})
    except asyncio.TimeoutError:
        return ToolResult.fail("case_retrieve timed out")
    except Exception as e:
        return ToolResult.fail(str(e))


# ---------------------------------------------------------------------------
# Workspace search
# ---------------------------------------------------------------------------

async def workspace_search(query, limit=8, **kw) -> ToolResult:
    retriever = get_tool_dep("retriever")
    if retriever is None:
        return ToolResult.fail("Workspace retriever not available")
    try:
        results = await retriever.search(get_session_id(), query, limit=limit, work_dir=get_session_work_dir())
        return ToolResult.ok(data={"query": query, "results": [{
            "source": r.source, "path": r.path, "content": r.content, "score": r.score,
            "artifact_type": r.artifact_type, "title": r.title, "summary": r.summary,
            "chunk_index": r.chunk_index, "metadata": r.metadata,
        } for r in results], "count": len(results)})
    except Exception as e:
        return ToolResult.fail(str(e))


async def reminder_create(title, message, fire_at, priority=2, reminder_type="reminder", **kw) -> ToolResult:
    sm = get_tool_dep("session_manager")
    if sm is None:
        return ToolResult.fail("Reminder service not available")
    try:
        nf = normalize_utc_timestamp(fire_at)
        rid = sm.create_reminder(session_id=get_session_id(), reminder_type=reminder_type, title=title, message=message, fire_at=nf, priority=priority)
        return ToolResult.ok(data={"id": rid, "fire_at": nf, "fire_at_display": format_local_timestamp(nf)})
    except Exception as e:
        return ToolResult.fail(str(e))


async def todowrite(todos, **kw) -> ToolResult:
    sm = get_tool_dep("session_manager")
    if sm is None:
        return ToolResult.fail("Todo service not available")
    try:
        s = await sm.get(get_session_id())
        if s is None:
            return ToolResult.fail("Session not found")
        validated = s.replace_todos(todos)
        await sm.update(s)
        active = len([t for t in validated if t.get("status") not in ("completed", "cancelled")])
        return ToolResult.ok(data={"todos": validated, "active_count": active, "total_count": len(validated)})
    except Exception as e:
        return ToolResult.fail(str(e))


# ---------------------------------------------------------------------------
# Upload parse
# ---------------------------------------------------------------------------

async def upload_parse(path, output_path="", language="ch", page_range="", **kw) -> ToolResult:
    sm = get_tool_dep("session_manager")
    wm = get_tool_dep("workspace_memory")
    mc = get_tool_dep("mineru_client")
    if not all([sm, wm, mc]):
        return ToolResult.fail("Upload parser not available")
    from ..tools.utils import _extract_pdf_markdown_from_path
    sid = get_session_id()
    if Path(path).is_absolute() or not path.startswith("uploads/"):
        return ToolResult.fail("upload_parse only accepts files under uploads/")
    session = await sm.get(sid)
    if session is None:
        return ToolResult.fail("Session not found")
    base = Path(session.work_dir).resolve()
    src = (base / path).resolve()
    up = (base / "uploads").resolve()
    try:
        up_rel = src.relative_to(up)
    except ValueError:
        return ToolResult.fail("upload_parse only accepts files under uploads/")
    norm = f"uploads/{up_rel.as_posix()}"
    if not src.exists():
        return ToolResult.fail(f"Upload not found: {path}")
    target = output_path.strip() or f"drafts/derived/{src.stem}__{src.suffix.lstrip('.').lower()}.md"
    try:
        parsed = await _do_parse(mc, src, language, page_range or None)
        content = _derived_md(source=norm, parser=str(parsed.get("parser", "")), md_url=parsed.get("markdown_url"), task_id=parsed.get("task_id"), body=str(parsed.get("content", "")))
        art = await wm.upsert_artifact(sid, path=target, content=content, artifact_type="derived_upload", title=src.name, summary=f"Derived from {norm}", metadata={"source_path": norm, "source_paths": [norm], "generated_by": "upload_parse", "parser": parsed.get("parser"), "markdown_url": parsed.get("markdown_url"), "task_id": parsed.get("task_id"), "lineage": {"source_paths": [norm], "generated_by": "upload_parse", "parser": parsed.get("parser")}})
        return ToolResult.ok(data={"source_path": norm, "output_path": target, "parser": parsed.get("parser"), "task_id": parsed.get("task_id"), "markdown_url": parsed.get("markdown_url"), "artifact_id": art.get("id"), "preview": content[:300]})
    except Exception as e:
        return ToolResult.fail(str(e))


async def _do_parse(mc, src, lang, pr):
    from ..ingest.mineru import LOCAL_SPREADSHEET_SUFFIXES, LOCAL_TEXT_SUFFIXES
    from ..tools.utils import _extract_pdf_markdown_from_path
    suff = src.suffix.lower()
    if suff in (LOCAL_TEXT_SUFFIXES | LOCAL_SPREADSHEET_SUFFIXES):
        return mc.parse_local_file(src, language=lang, page_range=pr)
    try:
        return await asyncio.to_thread(mc.parse_local_file, src, language=lang, page_range=pr)
    except Exception as exc:
        if suff == ".pdf":
            c = await asyncio.to_thread(_extract_pdf_markdown_from_path, src)
            if c.strip():
                return {"parser": "pymupdf4llm", "content": c, "task_id": None, "markdown_url": None, "fallback_reason": str(exc)}
        raise


def _derived_md(*, source, parser, md_url, task_id, body):
    h = [f"# 解析材料: {Path(source).name}", "", f"- 来源文件: {source}", f"- 解析方式: {parser}", f"- 解析时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S 北京时间')}"]
    if task_id: h.append(f"- 任务 ID: {task_id}")
    if md_url: h.append(f"- Markdown 来源: {md_url}")
    return "\n".join(h) + "\n\n---\n\n" + body.strip()


# ---------------------------------------------------------------------------
# Spawn
# ---------------------------------------------------------------------------

async def spawn(task, tools=None, subagent_type="general", **kw) -> ToolResult:
    agent_loop = get_tool_dep("agent_loop")
    if agent_loop is None:
        return ToolResult.fail("Agent loop not available")
    if tools is not None:
        if not isinstance(tools, list):
            return ToolResult.fail("tools must be a list of tool names")
        normalized_tools: list[str] = []
        for item in tools:
            if not isinstance(item, str) or not item.strip():
                return ToolResult.fail("tools must be a list of non-empty tool names")
            name = item.strip()
            if name not in normalized_tools:
                normalized_tools.append(name)
        tools = normalized_tools
    _TYPE_TOOLS = {
        "general": None,
        "explore": {"file_read", "file_list", "file_grep", "file_tree",
                    "workspace_search", "web_search", "web_read",
                    "law_retrieve", "case_retrieve"},
    }
    if subagent_type in _TYPE_TOOLS and _TYPE_TOOLS[subagent_type] is not None:
        allowed = set(_TYPE_TOOLS[subagent_type])
        if tools is None:
            tools = sorted(allowed)
        else:
            disallowed = sorted(set(tools) - allowed)
            if disallowed:
                return ToolResult.fail(f"Tool(s) not allowed for {subagent_type}: {disallowed}")
    if tools is not None:
        known = set(agent_loop.tools.get_all_tool_names()) - {"spawn"}
        unknown = sorted(set(tools) - known)
        if unknown:
            return ToolResult.fail(f"Unknown sub-agent tool(s): {unknown}")
    parent_session_id = get_session_id()
    if not parent_session_id:
        return ToolResult.fail("spawn requires an active session")
    try:
        sub_id, result = await agent_loop.spawn_sub_agent(task, tools, parent_session_id=parent_session_id)
        if not result.strip():
            return ToolResult.fail(f"Sub-agent {sub_id} returned empty result")
        archive_dir = f"raw_search/subagents/{sub_id}"
        return ToolResult.ok(data={
            "sub_task_id": sub_id,
            "status": "completed",
            "result": f"<task_result>\n{result}\n</task_result>",
            "sub_agent_archive_dir": archive_dir,
            "note": "使用 sub_task_id 可以通过 send_message 继续或 task_stop 停止此 Agent",
        })
    except Exception as e:
        return ToolResult.fail(str(e))


async def question(question_text, **kw) -> ToolResult:
    """向用户提问以获取澄清或补充信息。"""
    sm = get_tool_dep("session_manager")
    session_id = get_session_id()
    if sm is None or not session_id:
        return ToolResult.fail("Question service not available")
    await sm.add_intervention(session_id, content=question_text, metadata={"type": "question"})
    return ToolResult.ok(data={"question": question_text, "status": "awaiting_user"})


async def send_message(agent_id, message, **kw) -> ToolResult:
    """Continue a completed or running sub-agent by sending a follow-up message."""
    al = get_tool_dep("agent_loop")
    if al is None:
        return ToolResult.fail("Agent loop not available")
    sm = get_tool_dep("session_manager")
    if sm is None:
        return ToolResult.fail("Session manager not available")
    child = await sm.get(agent_id)
    if child is None:
        return ToolResult.fail(f"Agent not found: {agent_id}")
    if child.status not in ("active",):
        return ToolResult.fail(f"Agent {agent_id} is {child.status}, cannot send message")
    pending = al._pending_messages.setdefault(agent_id, [])
    pending.append(message)
    return ToolResult.ok(data={"agent_id": agent_id, "queued": True, "note": "消息已排队，子 Agent 下一轮会处理"})


async def task_stop(agent_id, **kw) -> ToolResult:
    """Stop a running sub-agent."""
    al = get_tool_dep("agent_loop")
    if al is None:
        return ToolResult.fail("Agent loop not available")
    sm = get_tool_dep("session_manager")
    if sm is None:
        return ToolResult.fail("Session manager not available")
    child = await sm.get(agent_id)
    if child is None:
        return ToolResult.fail(f"Agent not found: {agent_id}")
    interrupt = al._interrupt_events.get(agent_id)
    if interrupt is None:
        return ToolResult.fail(f"Agent {agent_id} has no active loop")
    interrupt.set()
    return ToolResult.ok(data={"agent_id": agent_id, "stopped": True, "note": "已发送中断信号，Agent 会在当前轮完成后停止"})


async def edit(path, old_string, new_string, **kw) -> ToolResult:
    """在文件中做 find-and-replace 编辑。
    
    优先精确匹配，失败时按行归一化后重试。
    """
    fs = FileSystem(get_session_work_dir())
    try:
        content = fs.read_file(path)
    except Exception as e:
        return ToolResult.fail(str(e))

    # 1) Exact match
    count = content.count(old_string)
    if count == 1:
        updated = content.replace(old_string, new_string, 1)
        fs.write_file(path, updated)
        return ToolResult.ok(data={"path": path, "diff": f"- {old_string[:200]}\n+ {new_string[:200]}"})
    if count > 1:
        return ToolResult.fail(f"old_string appears {count} times — must be unique. Provide more context.")

    # 2) Line-by-line normalized match (collapse whitespace)
    import re as _re
    def norm(s):
        return _re.sub(r'\s+', ' ', s.strip())

    query_norm = norm(old_string)
    content_lines = content.split("\n")
    norm_lines = [norm(l) for l in content_lines]
    matched = [i for i, nl in enumerate(norm_lines) if nl == query_norm]
    if len(matched) != 1:
        # Partial match: find closest line
        matched = [i for i, line in enumerate(content_lines) if query_norm[:30] in line]
        if len(matched) != 1:
            ctx_lines = content_lines[max(0, (matched or [0])[0] - 3):(matched or [0])[0] + 4] if matched else content_lines[:3]
            return ToolResult.fail(f"old_string not found. Nearby:\n" + "\n".join(ctx_lines))
    li = matched[0]
    updated = "\n".join(content_lines[:li] + [new_string] + content_lines[li + 1:])
    fs.write_file(path, updated)
    return ToolResult.ok(data={"path": path, "diff": f"- {content_lines[li][:200]}\n+ {new_string[:200]}"})


# ---------------------------------------------------------------------------
# Register all tools
# ---------------------------------------------------------------------------

_DESC_DIR = Path(__file__).resolve().parent / "descriptions"

def _load_desc(name: str) -> str:
    path = _DESC_DIR / f"{name}.txt"
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return ""


def register_base_tools(r) -> None:

    r.register("file_read", "filesystem", {
        "name": "file_read",
        "description": _load_desc("file_read"),
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "文件路径（相对于工作目录）"},
            "offset": {"type": "integer", "description": "起始行号（从0计数），用于分段读取大文件"},
            "limit": {"type": "integer", "description": "最大返回行数，大文件建议每次 500-1000 行"},
            "encoding": {"type": "string", "description": "文件编码，默认 utf-8"},
        }, "required": ["path"]},
    }, file_read, concurrency_safe=True, read_only=True)

    r.register("file_write", "filesystem", {
        "name": "file_write",
        "description": _load_desc("file_write"),
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "文件路径（相对于工作目录）"},
            "content": {"type": "string", "description": "要写入的内容"},
            "encoding": {"type": "string", "description": "文件编码，默认 utf-8"},
        }, "required": ["path", "content"]},
    }, file_write)

    r.register("file_append", "filesystem", {
        "name": "file_append",
        "description": _load_desc("file_append"),
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "文件路径"},
            "content": {"type": "string", "description": "要追加的内容"},
        }, "required": ["path", "content"]},
    }, file_append)

    r.register("file_delete", "filesystem", {
        "name": "file_delete",
        "description": _load_desc("file_delete"),
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "文件路径"},
        }, "required": ["path"]},
    }, file_delete)

    r.register("file_list", "filesystem", {
        "name": "file_list",
        "description": _load_desc("file_list"),
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "目录路径，默认为当前目录"},
        }},
    }, file_list, concurrency_safe=True, read_only=True)

    r.register("file_grep", "filesystem", {
        "name": "file_grep",
        "description": _load_desc("file_grep"),
        "parameters": {"type": "object", "properties": {
            "pattern": {"type": "string", "description": "搜索的正则表达式"},
            "path": {"type": "string", "description": "搜索路径（相对于工作目录），默认当前目录"},
            "file_pattern": {"type": "string", "description": "文件名匹配模式（glob），默认 *"},
            "head_limit": {"type": "integer", "description": "最大返回结果数，默认 250。设置为 0 表示不限制"},
        }, "required": ["pattern"]},
    }, file_grep, concurrency_safe=True, read_only=True)

    r.register("file_tree", "filesystem", {
        "name": "file_tree",
        "description": _load_desc("file_tree"),
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "目录路径"},
            "max_depth": {"type": "integer", "description": "最大递归深度"},
        }},
    }, file_tree, concurrency_safe=True, read_only=True)

    r.register("bash", "execution", {
        "name": "bash",
        "description": _load_desc("bash"),
        "parameters": {"type": "object", "properties": {
            "command": {"type": "string", "description": "要执行的 bash 命令"},
            "timeout": {"type": "integer", "description": "超时时间（秒）"},
        }, "required": ["command"]},
    }, bash)

    if _law_retrieve is not None:
        r.register("law_retrieve", "retrieval", {
            "name": "law_retrieve",
            "description": _load_desc("law_retrieve"),
            "parameters": {"type": "object", "properties": {
                "query": {"type": "string", "description": "法律问题、主题或关键词"},
                "top_k": {"type": "integer", "description": "返回结果数量"},
            }, "required": ["query"]},
        }, law_retrieve, concurrency_safe=True, read_only=True)

    if _case_retrieve is not None:
        r.register("case_retrieve", "retrieval", {
            "name": "case_retrieve",
            "description": _load_desc("case_retrieve"),
            "parameters": {"type": "object", "properties": {
                "query": {"type": "string", "description": "案例检索问题或关键词"},
                "top_k": {"type": "integer", "description": "返回结果数量"},
            }, "required": ["query"]},
        }, case_retrieve, concurrency_safe=True, read_only=True)

    r.register("workspace_search", "retrieval", {
        "name": "workspace_search",
        "description": _load_desc("workspace_search"),
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": _load_desc("workspace_search")},
            "limit": {"type": "integer", "description": "最多返回结果数"},
        }, "required": ["query"]},
    }, workspace_search, concurrency_safe=True, read_only=True)

    r.register("reminder_create", "workspace", {
        "name": "reminder_create",
        "description": _load_desc("reminder_create"),
        "parameters": {"type": "object", "properties": {
            "title": {"type": "string", "description": "提醒标题"},
            "message": {"type": "string", "description": "提醒内容"},
            "fire_at": {"type": "string", "description": "触发时间 ISO 字符串"},
            "priority": {"type": "integer", "description": "优先级 1（高）-3（低）"},
            "reminder_type": {"type": "string", "description": "提醒类型"},
        }, "required": ["title", "message", "fire_at"]},
    }, reminder_create, concurrency_safe=True, read_only=True)

    r.register("todowrite", "workspace", {
        "name": "todowrite",
        "description": _load_desc("todowrite"),
        "parameters": {"type": "object", "properties": {
            "todos": {"type": "array", "description": _load_desc("todowrite"), "items": {
                "type": "object", "properties": {
                    "content": {"type": "string", "description": "任务描述"},
                    "activeForm": {"type": "string", "description": "进行中表示，如 Writing tests"},
                    "status": {"type": "string", "enum": ["pending", "in_progress", "completed", "blocked", "cancelled"]},
                    "priority": {"type": "string", "enum": ["high", "medium", "low"]},
                }, "required": ["content", "status"],
            }},
        }, "required": ["todos"]},
    }, todowrite)

    r.register("upload_parse", "filesystem", {
        "name": "upload_parse",
        "description": _load_desc("upload_parse"),
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "uploads/ 下的文件路径"},
            "output_path": {"type": "string", "description": "输出 Markdown 路径，默认 drafts/derived/<文件名>.md"},
            "language": {"type": "string", "description": "解析语言，默认 ch"},
            "page_range": {"type": "string", "description": "PDF 页码范围，如 1-10"},
        }, "required": ["path"]},
    }, upload_parse)

    r.register("spawn", "execution", {
        "name": "spawn",
        "description": _load_desc("spawn"),
        "parameters": {"type": "object", "properties": {
            "task": {"type": "string", "description": _load_desc("spawn")},
            "tools": {"type": "array", "description": "允许使用的工具列表", "items": {"type": "string"}},
            "subagent_type": {"type": "string", "enum": ["general", "explore"], "description": "子Agent类型：general=全工具，explore=只读探索"},
        }, "required": ["task"]},
    }, spawn, concurrency_safe=True)

    r.register("send_message", "execution", {
        "name": "send_message",
        "description": _load_desc("send_message"),
        "parameters": {"type": "object", "properties": {
            "agent_id": {"type": "string", "description": "子 Agent 的 session ID（spawn 返回的 sub_task_id）"},
            "message": {"type": "string", "description": "要发送的后续指令"},
        }, "required": ["agent_id", "message"]},
    }, send_message)

    r.register("task_stop", "execution", {
        "name": "task_stop",
        "description": _load_desc("task_stop"),
        "parameters": {"type": "object", "properties": {
            "agent_id": {"type": "string", "description": "要停止的子 Agent 的 session ID"},
        }, "required": ["agent_id"]},
    }, task_stop)

    r.register("question", "workspace", {
        "name": "question",
        "description": _load_desc("question"),
        "parameters": {"type": "object", "properties": {
            "question_text": {"type": "string", "description": "要向用户提出的问题"},
        }, "required": ["question_text"]},
    }, question)

    r.register("edit", "filesystem", {
        "name": "edit",
        "description": _load_desc("edit"),
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "文件路径"},
            "old_string": {"type": "string", "description": "要被替换的原文（必须唯一）"},
            "new_string": {"type": "string", "description": "替换后的新内容"},
        }, "required": ["path", "old_string", "new_string"]},
    }, edit)
