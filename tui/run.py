"""Entry point for the AgentOS Textual TUI."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def should_launch_tui(*, argv: list[str] | None = None, stdin_isatty: bool | None = None) -> bool:
    args = list(sys.argv if argv is None else argv)
    is_tty = sys.stdin.isatty() if stdin_isatty is None else stdin_isatty
    # TUI is legacy/frozen; CLI is the maintained default path.
    return is_tty and "--tui-legacy" in args and "--non-tty" not in args


def main() -> None:
    if not should_launch_tui():
        from cli import main as cli_main

        if "--tui-legacy" not in sys.argv:
            print("[notice] Textual TUI is deprecated/frozen; falling back to CLI.")
        asyncio.run(cli_main())
        return

    from tui.app import AgentOSTuiApp

    AgentOSTuiApp().run()


if __name__ == "__main__":
    main()
