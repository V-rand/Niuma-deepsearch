from __future__ import annotations

from textual.app import App

from agent_os import AgentOS, check_api_keys
from tui.screen import ChatScreen


class AgentOSTuiApp(App):
    CSS_PATH = "theme.tcss"
    TITLE = "AgentOS TUI"

    def __init__(self, *, data_dir: str = "./data") -> None:
        super().__init__()
        self.data_dir = data_dir
        self.agent: AgentOS | None = None

    async def on_mount(self) -> None:
        ok, error = check_api_keys()
        if not ok:
            self.exit(message=error)
            return
        self.agent = AgentOS(data_dir=self.data_dir)
        await self.agent.start()
        await self.push_screen(ChatScreen(self.agent))

    async def on_unmount(self) -> None:
        if self.agent is not None:
            await self.agent.stop()
