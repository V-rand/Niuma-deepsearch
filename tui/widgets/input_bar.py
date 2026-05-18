from __future__ import annotations

from textual.widgets import Input


class InputBar(Input):
    DEFAULT_CSS = """
    InputBar {
        height: 3;
        border: tall #3d6ea8;
        padding: 0 2;
        background: #101418;
    }
    """

    def __init__(self, *args, **kwargs) -> None:
        kwargs.pop("placeholder", None)
        super().__init__(*args, placeholder="AgentOS> 输入消息或 /help", **kwargs)

    def set_running(self, running: bool) -> None:
        self.placeholder = "运行中：输入内容注入给当前任务；/interrupt 中断；/tools 展开工具日志" if running else "AgentOS> 输入消息或 /help"
