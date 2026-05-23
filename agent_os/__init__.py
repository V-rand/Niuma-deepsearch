"""
Agent OS — 通用深度研究智能体操作系统

核心概念：
- AgentLoop: ReAct 核心循环
- Session: 独立的"进程"，有自己的工作目录和历史
- Tool: 标准工具接口
- SubAgent: 子 Agent，不能创建子 Agent
- Interrupt: 中断机制（定时提醒）
"""

from .core import Session, SessionManager, FileSystem, EventBus, EventType
from .kernel import AgentLoop, SubAgent
from .tools import ToolRegistry, get_tool_registry, Tool, ToolResult
from .skills import SkillLoader, get_skill_loader
from .scheduler import InterruptScheduler, InterruptType
from .memory import ContextCompiler, WorkspaceMemory, SessionRetriever
from .agent_os import AgentOS, check_api_keys
from .config import Settings

__version__ = "0.2.0"

__all__ = [
    # Main
    "AgentOS",
    "check_api_keys",
    # Core
    "Session",
    "SessionManager",
    "FileSystem",
    "EventBus",
    "EventType",
    # Kernel
    "AgentLoop",
    "SubAgent",
    # Tools
    "ToolRegistry",
    "get_tool_registry",
    "Tool",
    "ToolResult",
    # Skills
    "SkillLoader",
    "get_skill_loader",
    # Scheduler
    "InterruptScheduler",
    "InterruptType",
    # Memory
    "ContextCompiler",
    "WorkspaceMemory",
    "SessionRetriever",
    "Settings",
]
