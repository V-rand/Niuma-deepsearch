from __future__ import annotations

from rich.panel import Panel
from rich.text import Text
from textual.widgets import RichLog


class ChatHistory(RichLog):
    DEFAULT_CSS = """
    ChatHistory {
        width: 2fr;
        height: 1fr;
        border: solid #30363d;
        padding: 0 1;
    }
    """

    def write_user(self, message: str) -> None:
        self.write(Panel(message, title="您", title_align="left", border_style="cyan"))

    def write_chat(self, message: str, style: str = "") -> None:
        rich_style = {
            "dim": "dim",
            "agent": "bright_green",
            "info": "bright_blue",
        }.get(style, "")
        self.write(Text(message, style=rich_style), scroll_end=True)

    def write_error(self, message: str) -> None:
        self.write(Text(f"错误: {message}", style="bold red"), scroll_end=True)
