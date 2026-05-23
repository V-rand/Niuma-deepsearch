from __future__ import annotations

from rich.text import Text
from textual.widgets import RichLog


class ToolLog(RichLog):
    DEFAULT_CSS = """
    ToolLog {
        height: 0;
        border: solid #30363d;
        padding: 0 1;
    }
    ToolLog.expanded {
        height: 10;
    }
    """

    def write_tool(self, message: str, style: str = "") -> None:
        rich_style = {
            "model": "bright_magenta",
            "tool": "bright_yellow",
            "ok": "green",
            "error": "bold red",
            "warn": "yellow",
            "summary": "bold bright_cyan",
            "info": "dim",
        }.get(style, "")
        self.write(Text(message, style=rich_style), scroll_end=True)

    def set_expanded(self, expanded: bool) -> None:
        self.set_class(expanded, "expanded")
