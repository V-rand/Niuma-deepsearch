from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from rich.table import Table
from textual.widgets import Static


class SubAgentList(Static, can_focus=True):
    DEFAULT_CSS = """
    SubAgentList {
        width: 1fr;
        height: 1fr;
        border: solid #30363d;
        padding: 0 1;
    }
    SubAgentList.hidden {
        display: none;
    }
    """

    def __init__(self) -> None:
        super().__init__("")
        self.expanded = False
        self._last_status: list[dict[str, Any]] = []
        self.set_class(True, "hidden")

    def update_from_work_dir(self, work_dir: str) -> None:
        statuses = read_subagent_statuses(work_dir)
        self._last_status = statuses
        self.set_class(not bool(statuses), "hidden")
        if not statuses:
            self.update("")
            return
        self.update(render_status_table(statuses, expanded=self.expanded))

    def action_toggle_expanded(self) -> None:
        self.expanded = not self.expanded
        self.update(render_status_table(self._last_status, expanded=self.expanded))


def read_subagent_statuses(work_dir: str) -> list[dict[str, Any]]:
    agents_dir = Path(work_dir) / "raw_search" / "subagents"
    if not agents_dir.exists():
        return []
    statuses: list[dict[str, Any]] = []
    for entry in sorted(agents_dir.iterdir()):
        if not entry.is_dir():
            continue
        status_file = entry / "_status.jsonl"
        if not status_file.exists():
            continue
        latest = None
        history = []
        try:
            with status_file.open("r", encoding="utf-8") as f:
                for row in f:
                    row = row.strip()
                    if not row:
                        continue
                    data = json.loads(row)
                    history.append(data)
                    latest = data
        except (OSError, json.JSONDecodeError):
            continue
        if latest:
            latest = dict(latest)
            latest["id"] = entry.name
            latest["history"] = history[-6:]
            statuses.append(latest)
    return statuses


def render_status_table(statuses: list[dict[str, Any]], *, expanded: bool) -> Table:
    table = Table(title="子 Agent", show_edge=False, expand=True)
    table.add_column("ID", no_wrap=True)
    table.add_column("状态", no_wrap=True)
    table.add_column("轮次", no_wrap=True)
    table.add_column("工具/摘要")
    for status in statuses:
        tools = ", ".join(status.get("tool_names") or []) or "-"
        text = tools
        thinking = status.get("thinking") or ""
        if thinking:
            text += f"\n{thinking[:80]}"
        if expanded:
            lines = []
            for item in status.get("history") or []:
                item_tools = ", ".join(item.get("tool_names") or []) or "-"
                lines.append(f"轮{item.get('iteration', '?')}: {item_tools}")
            text += "\n" + "\n".join(lines)
        table.add_row(
            str(status.get("id") or status.get("sub_agent_id") or "-"),
            str(status.get("status") or "running"),
            str(status.get("iteration", "?")),
            text,
        )
    return table
