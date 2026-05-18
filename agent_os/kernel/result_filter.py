"""
Result filtering — LLM-based compression for long retrieval results.

When a tool returns a large result set (law/case/web), an LLM produces
citation-grounded summaries.  The filter MUST quote original text before
summarizing to prevent hallucination.

Small results (<2000 chars) are kept in full — the model needs original
content to reason correctly.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any

from openai import AsyncOpenAI

from ..prompts import result_filter


# Threshold: below this, keep full JSON; above this, trigger LLM filtering
# Set higher to avoid unnecessary LLM calls — most law/case retrievals are
# 3-8 items × 500-1500 chars = 1500-12000 chars.  We only filter when the
# result set is genuinely large enough to blow up context.
_PRUNE_CHAR_THRESHOLD = int(os.getenv("AGENT_OS_PRUNE_CHAR_THRESHOLD", "5000"))

# Content truncation for individual result items in the filter prompt.
# Set high enough for full law articles (typically 2k-8k chars).
_ITEM_CONTENT_MAX = int(os.getenv("AGENT_OS_ITEM_CONTENT_MAX", "12000"))
_ITEM_CONTENT_HEAD = int(os.getenv("AGENT_OS_ITEM_CONTENT_HEAD", "8000"))
_ITEM_CONTENT_TAIL = int(os.getenv("AGENT_OS_ITEM_CONTENT_TAIL", "4000"))


def _truncate_item_content(value: str, max_chars: int = _ITEM_CONTENT_MAX) -> str:
    """Truncate a long string value while preserving head and tail."""
    if len(value) <= max_chars:
        return value
    head = _ITEM_CONTENT_HEAD
    tail = min(_ITEM_CONTENT_TAIL, max_chars - head - 20)
    return value[:head] + "\n...[truncated]...\n" + value[-tail:]


class ResultFilterAgent:
    """Compresses long retrieval results into citation-grounded summaries."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: int = 60,
    ):
        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout_seconds,
            max_retries=0,
        )
        self.model = model
        self.timeout_seconds = timeout_seconds

    async def filter(
        self,
        *,
        tool_name: str,
        query: str,
        results: list[dict[str, Any]],
        user_query: str = "",
    ) -> dict[str, Any]:
        """Filter and compress retrieval results.

        Returns a compact dict suitable for injection into the main conversation.
        """
        if not results:
            return {
                "tool_name": tool_name,
                "query": query,
                "total_results": 0,
                "filtered_results": [],
                "coverage_note": "无结果",
            }

        user_prompt = self._build_user_prompt(
            tool_name=tool_name,
            query=query,
            results=results,
            user_query=user_query,
        )

        response = await asyncio.wait_for(
            self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": result_filter},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.1,
                max_tokens=6000,
            ),
            timeout=self.timeout_seconds,
        )

        raw_content = response.choices[0].message.content or ""
        parsed = self._parse_json_response(raw_content)

        parsed.setdefault("tool_name", tool_name)
        parsed.setdefault("query", query)
        parsed.setdefault("total_results", len(results))
        parsed.setdefault("filtered_results", [])
        parsed.setdefault("coverage_note", "")

        return parsed

    def format_for_messages(self, filtered: dict[str, Any]) -> str:
        """Format filtered results as a compact string for injection into messages."""
        lines = [
            f"[{filtered['tool_name']}] 查询: {filtered['query']}",
            f"共 {filtered['total_results']} 条结果，过滤后保留 {len(filtered['filtered_results'])} 条:",
            "",
        ]
        for i, item in enumerate(filtered["filtered_results"], 1):
            lines.extend([
                f"{i}. [{item['relevance']}] {item['source']}",
                f"   {item['quote']}",
                f"   → {item['summary']}",
                "",
            ])
        if filtered.get("coverage_note"):
            lines.append(f"注: {filtered['coverage_note']}")
        return "\n".join(lines)

    @staticmethod
    def _build_user_prompt(
        *,
        tool_name: str,
        query: str,
        results: list[dict[str, Any]],
        user_query: str,
    ) -> str:
        lines = [
            f"工具: {tool_name}",
            f"原始查询: {query}",
        ]
        if user_query:
            lines.append(f"用户当前问题: {user_query}")
        lines.append(f"结果数量: {len(results)}")
        lines.append("")
        lines.append("--- 原始结果 ---")
        lines.append("")

        for i, item in enumerate(results, 1):
            lines.append(f"### 结果 {i}")
            for key, value in item.items():
                if value is not None and value != "":
                    val_str = str(value)
                    val_str = _truncate_item_content(val_str, _ITEM_CONTENT_MAX)
                    lines.append(f"- {key}: {val_str}")
            lines.append("")

        lines.append("--- 结束 ---")
        lines.append("")
        lines.append("请按规则输出过滤后的 JSON。")
        return "\n".join(lines)

    @staticmethod
    def _parse_json_response(raw: str) -> dict[str, Any]:
        """Extract JSON from the model response, handling markdown code blocks."""
        stripped = raw.strip()
        if stripped.startswith("{"):
            try:
                return json.loads(stripped)
            except json.JSONDecodeError:
                pass

        match = re.search(r"```(?:json)?\s*\n(.*?)\n```", raw, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass

        start = stripped.find("{")
        end = stripped.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(stripped[start : end + 1])
            except json.JSONDecodeError:
                pass

        return {
            "tool_name": "unknown",
            "query": "",
            "total_results": 0,
            "filtered_results": [
                {
                    "source": "[过滤解析失败 — 不可引用]",
                    "quote": '> "[无有效引用]"',
                    "summary": (
                        "⚠️ 过滤 Agent 返回的 JSON 解析失败，以下为原始数据片段，"
                        "请勿将其作为过滤结果引用，应回退到完整原始结果。"
                        f" 原始片段: {raw[:500]}"
                    ),
                    "relevance": "low",
                }
            ],
            "coverage_note": "过滤 Agent JSON 解析失败 — 建议回退到完整原始数据",
        }
