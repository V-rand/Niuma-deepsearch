"""
Message serialization, token estimation, and formatting utilities for AgentLoop.
All functions are pure (no instance state) and can be unit-tested in isolation.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from openai import APIError, APIConnectionError, APITimeoutError

_SURROGATE_RE = re.compile(r'[\ud800-\udfff]')
# Surrogate characters are rejected by Kimi/GLM providers in certain multi-turn
# scenarios where tool_result content contains single surrogates split across chunks.
# Stripping them before sending to the API avoids 400 errors.
# Each provider returns a different error code when content is rejected by safety
# filters. This set covers DashScope, DeepSeek, and OpenAI-compatible providers.
_CONTENT_FILTER_CODES = {
    "DataInspectionFailed",
    "data_inspection_failed",
    "content_filter",
    "ContentFilter",
    "SafetyInspectionFailed",
}


# ---------------------------------------------------------------------------
# Exception formatting
# ---------------------------------------------------------------------------

def format_exception(exc: BaseException) -> str:
    exc_type = type(exc).__name__
    if isinstance(exc, APIError):
        code = getattr(exc, "code", None)
        msg = getattr(exc, "message", None) or str(exc)
        body = getattr(exc, "body", None)
        if _is_content_filter_error(code=code, message=msg, body=body):
            code_text = str(code or _extract_error_field(body, "code") or "content_filter")
            body_text = f" body={body}" if body else ""
            req_id = getattr(exc, "request_id", None)
            req_text = f" (request_id: {req_id})" if req_id else ""
            return (
                f"Provider content filter blocked the request [{code_text}]. "
                f"raw_message={msg}{body_text}{req_text} "
                "建议：保留报错原文排查 provider 审查命中，同时把任务改写成更中性的描述后重试。"
            )
        parts = [f"OpenAI {exc_type}"]
        if code:
            parts.append(f"[{code}]")
        if msg:
            parts.append(msg)
        if body and body != msg:
            parts.append(f"body={body}")
        req_id = getattr(exc, "request_id", None)
        if req_id:
            parts.append(f"(request_id: {req_id})")
        return " ".join(parts)
    if isinstance(exc, APIConnectionError):
        return f"OpenAI connection error ({exc_type}): {exc}"
    if isinstance(exc, (APITimeoutError, TimeoutError)):
        return f"Request timed out ({exc_type}): {exc}"
    return f"{exc_type}: {exc}"


def is_content_filter_exception(exc: BaseException) -> bool:
    if not isinstance(exc, APIError):
        return False
    code = getattr(exc, "code", None)
    msg = getattr(exc, "message", None) or str(exc)
    body = getattr(exc, "body", None)
    return _is_content_filter_error(code=code, message=msg, body=body)


def _extract_error_field(body: Any, field: str) -> str | None:
    if isinstance(body, dict):
        value = body.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
        error = body.get("error")
        if isinstance(error, dict):
            nested = error.get(field)
            if isinstance(nested, str) and nested.strip():
                return nested.strip()
    return None


def _is_content_filter_error(*, code: Any, message: str, body: Any) -> bool:
    code_text = str(code or _extract_error_field(body, "code") or "").strip()
    if code_text in _CONTENT_FILTER_CODES:
        return True
    haystack = " ".join(
        part for part in [
            code_text,
            message or "",
            _extract_error_field(body, "message") or "",
            str(body) if body else "",
        ] if part
    ).lower()
    return any(token in haystack for token in (
        "datainspectionfailed",
        "data_inspection_failed",
        "inappropriate content",
        "content filter",
        "content exists risk",
        "content_filter",
        "敏感内容",
        "内容审查",
    ))


# ---------------------------------------------------------------------------
# Safe data access
# ---------------------------------------------------------------------------

def safe_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {}


# ---------------------------------------------------------------------------
# Message content extraction
# ---------------------------------------------------------------------------

def extract_message_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
            else:
                text = getattr(item, "text", None)
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return str(content)


# ---------------------------------------------------------------------------
# Tool call serialization
# ---------------------------------------------------------------------------

def serialize_tool_calls(tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "id": call["id"],
            "type": "function",
            "function": {
                "name": call["name"],
                "arguments": call["arguments"],
            },
        }
        for call in tool_calls
    ]


def convert_tools_for_model(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [tool for tool in tools if tool.get("function")]


# ---------------------------------------------------------------------------
# Deterministic tool call ID for KV cache prefix matching
# ---------------------------------------------------------------------------

def deterministic_tool_call_id(fn_name: str, arguments: str, index: int = 0) -> str:
    seed = f"{fn_name}:{arguments}:{index}"
    digest = hashlib.sha256(seed.encode("utf-8", errors="replace")).hexdigest()[:12]
    return f"call_{digest}"


# ---------------------------------------------------------------------------
# Message normalization for KV cache
# ---------------------------------------------------------------------------

def normalize_messages_for_cache(messages: list[dict[str, Any]]) -> None:
    """Normalize messages for bit-perfect KV cache prefix matching.

    1. Strip leading/trailing whitespace from string content.
    2. Re-serialize tool_call arguments with sorted keys and compact
       separators so identical tool calls produce identical byte prefixes
       across turns, regardless of original key ordering from the model.
    """
    for msg in messages:
        if isinstance(msg.get("content"), str):
            msg["content"] = msg["content"].strip()
        tcs = msg.get("tool_calls")
        if not tcs:
            continue
        for tc in tcs:
            if not isinstance(tc, dict) or "function" not in tc:
                continue
            fn = tc["function"]
            raw_args = fn.get("arguments")
            if not isinstance(raw_args, str):
                continue
            try:
                args_obj = json.loads(raw_args)
                fn["arguments"] = json.dumps(args_obj, separators=(",", ":"), sort_keys=True)
            except (json.JSONDecodeError, TypeError, ValueError):
                pass


def sanitize_messages_surrogates(messages: list[dict[str, Any]]) -> bool:
    """Replace lone surrogate code points in messages.

    Kimi/GLM thinking models can emit surrogates that crash json.dumps()
    inside the OpenAI SDK. Returns True if any replacements were made.
    """
    found = False
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if isinstance(content, str) and _SURROGATE_RE.search(content):
            msg["content"] = _SURROGATE_RE.sub('\ufffd', content)
            found = True
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    text = part.get("text")
                    if isinstance(text, str) and _SURROGATE_RE.search(text):
                        part["text"] = _SURROGATE_RE.sub('\ufffd', text)
                        found = True
        tool_calls = msg.get("tool_calls")
        if isinstance(tool_calls, list):
            for tc in tool_calls:
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function")
                if isinstance(fn, dict):
                    fn_args = fn.get("arguments")
                    if isinstance(fn_args, str) and _SURROGATE_RE.search(fn_args):
                        fn["arguments"] = _SURROGATE_RE.sub('\ufffd', fn_args)
                        found = True
        for key, value in msg.items():
            if key in {"content", "tool_calls", "role"}:
                continue
            if isinstance(value, str) and _SURROGATE_RE.search(value):
                msg[key] = _SURROGATE_RE.sub('\ufffd', value)
                found = True
    return found


# ---------------------------------------------------------------------------
# Message reconstruction from DB for KV cache
# ---------------------------------------------------------------------------

def reconstruct_messages_from_db(db_messages: list[dict[str, Any]], *, need_reasoning_roundtrip: bool = False) -> list[dict[str, Any]]:
    """Reconstruct API-format message dicts from DB rows for KV cache prefix matching.

    When *need_reasoning_roundtrip* is True (DeepSeek native API), restores
    reasoning_content from metadata for tool-call turns per provider requirement.
    DashScope and other OpenAI-compatible providers ignore this field.
    """
    result: list[dict[str, Any]] = []
    for msg in db_messages:
        kind = msg.get("kind", "chat")
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        meta = msg.get("metadata", {}) or {}
        if kind == "tool":
            result.append({
                "role": "tool",
                "tool_call_id": meta.get("tool_call_id"),
                "content": content,
            })
        else:
            item: dict[str, Any] = {"role": role, "content": content}
            if kind == "chat":
                tool_calls = meta.get("tool_calls")
                if tool_calls is not None:
                    item["tool_calls"] = tool_calls
                # _iteration is internal metadata, strip it for provider compatibility
            if need_reasoning_roundtrip:
                reasoning_content = meta.get("reasoning_content")
                if reasoning_content and tool_calls is not None:
                    item["reasoning_content"] = reasoning_content
            result.append(item)

    # Strip orphaned tool_calls: assistant messages whose tool_call_ids
    # don't have matching tool-result messages immediately following.
    # This can happen when a tool-call turn was interrupted mid-flight.
    i = 0
    while i < len(result):
        msg = result[i]
        if msg.get("role") != "assistant":
            i += 1
            continue
        tcs = msg.get("tool_calls")
        if not tcs:
            i += 1
            continue
        expected = {tc["id"] for tc in tcs}
        j = i + 1
        found: set[str] = set()
        while j < len(result) and result[j].get("role") == "tool":
            tid = result[j].get("tool_call_id")
            if tid:
                found.add(tid)
            j += 1
        if expected != found:
            # Prune missing tool_call_ids from the assistant message
            valid = [tc for tc in tcs if tc["id"] in found]
            if valid:
                msg["tool_calls"] = valid
            else:
                msg.pop("tool_calls", None)
        i += 1

    return result


# ---------------------------------------------------------------------------
# Token estimation (language-aware heuristics)
# ---------------------------------------------------------------------------

def estimate_text_tokens(text: str) -> int:
    """CJK ~1.5 tokens/char, ASCII ~0.25 tokens/char."""
    if not text:
        return 0
    cjk_count = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    ascii_count = sum(1 for ch in text if ch.isascii())
    other_count = len(text) - cjk_count - ascii_count
    return int(cjk_count * 1.5 + ascii_count * 0.25 + other_count * 1.0)


def estimate_messages_tokens(messages: list[dict[str, Any]]) -> int:
    total = 0
    for msg in messages:
        total += 4  # message structure overhead
        content = msg.get("content", "")
        if isinstance(content, str):
            total += estimate_text_tokens(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, str):
                    total += estimate_text_tokens(part)
                elif isinstance(part, dict):
                    total += estimate_text_tokens(part.get("text", "") or "")
        tool_calls = msg.get("tool_calls") or []
        for tc in tool_calls:
            if isinstance(tc, dict):
                total += 3
                fn = tc.get("function", {})
                total += estimate_text_tokens(fn.get("arguments", ""))
    return total


# ---------------------------------------------------------------------------
# Slug helper for file naming
# ---------------------------------------------------------------------------

def slug(value: str) -> str:
    safe: list[str] = []
    for char in value.lower():
        if char.isalnum():
            safe.append(char)
        elif char in {"-", "_"}:
            safe.append(char)
        elif char.isspace():
            safe.append("_")
    return "".join(safe)[:48] or "query"


async def resolve_tool_content_for_messages(
    *,
    tool_name: str,
    result: Any,
    user_message: str,
    result_filter: Any,
    filterable: bool,
    agent_context: str = "",
) -> str:
    """Shared tool result filtering for AgentLoop and SubAgent.

    Below threshold: keep full JSON. Above threshold: LLM citation-grounded filter.
    agent_context: session research objective / recent context for relevance.
    """
    from .result_filter import _PRUNE_CHAR_THRESHOLD

    if not result.success:
        return json.dumps(result.to_dict(), ensure_ascii=False)

    if not filterable:
        return json.dumps(result.to_dict(), ensure_ascii=False)

    data = result.data or {}
    raw_text = json.dumps(data, ensure_ascii=False)

    if len(raw_text) < _PRUNE_CHAR_THRESHOLD:
        return json.dumps(result.to_dict(), ensure_ascii=False)

    query = str(data.get("query", "")) or str(data.get("url", ""))
    archived_path = data.get("archived_path", "")
    results = data.get("results")
    if not isinstance(results, list) or not results:
        content = data.get("content", "")
        if isinstance(content, str) and len(content) > _PRUNE_CHAR_THRESHOLD:
            try:
                item = {"content": content, "url": data.get("url", "")}
                if archived_path:
                    item["archived_path"] = archived_path
                filtered = await result_filter.filter(
                    tool_name=tool_name,
                    query=query or "web_read",
                    results=[item],
                    user_query=user_message,
                    agent_context=agent_context,
                )
                formatted = result_filter.format_for_messages(filtered)
                return json.dumps(
                    {"success": True, "data": {"query": query, "filtered_summary": formatted, "_filtered": True, "original_count": 1}},
                    ensure_ascii=False,
                )
            except Exception:
                pass
        return json.dumps(result.to_dict(), ensure_ascii=False)

    try:
        if archived_path:
            results = [{**r, "archived_path": archived_path} if isinstance(r, dict) and "archived_path" not in r else r for r in results]
        filtered = await result_filter.filter(
            tool_name=tool_name,
            query=query,
            results=results,
            user_query=user_message,
            agent_context=agent_context,
        )
        formatted = result_filter.format_for_messages(filtered)
        return json.dumps(
            {"success": True, "data": {"query": query, "filtered_summary": formatted, "_filtered": True, "original_count": len(results)}},
            ensure_ascii=False,
        )
    except Exception:
        return json.dumps(result.to_dict(), ensure_ascii=False)


def format_compression_summary(raw: str) -> str:
    """Strip <analysis> drafting scratchpad, extract <summary> content.

    The compression prompt asks the model to output <analysis> (draft)
    followed by <summary> (final).  We strip the analysis and keep only
    the summary for injection into the conversation.  If the model
    doesn't produce <summary> tags (e.g. older models), the raw output
    is used as-is.
    """
    import re

    stripped = raw.strip()

    summary_match = re.search(r"<summary>(.*?)</summary>", stripped, re.DOTALL)
    if summary_match:
        content = summary_match.group(1).strip()
        return content

    analysis_match = re.search(r"<analysis>.*?</analysis>", stripped, re.DOTALL)
    if analysis_match:
        cleaned = stripped[: analysis_match.start()] + stripped[analysis_match.end() :]
        return cleaned.strip()

    return stripped


def build_compact_handoff(summary: str, transcript_path: str = "", version: int = 1) -> str:
    """Build the handoff message injected as user message after compaction.

    The handoff tells the model three things:
    1. This is a continuation (not a fresh start) — pick up where left off
    2. A full transcript is available if granular details are needed
    3. Don't waste tokens acknowledging the summary
    """
    lines = [
        f'<compaction version="{version}">',
        "本会话因上下文窗口超过处理上限而压缩。以下是之前对话的结构化摘要。",
        "请直接从中断处继续工作——不要回应这份摘要，不要复盘压缩前做了什么，",
        '不要以"我将继续"或类似表述开头。从你离开的地方直接开始。',
        "",
        summary,
    ]

    if transcript_path:
        lines.extend([
            "",
            "如需查看压缩前被省略的完整细节（原始工具输出、精确数据、引用原文），",
            f"可读取压缩前的完整记录: {transcript_path}",
        ])

    lines.append("</compaction>")
    return "\n".join(lines)
