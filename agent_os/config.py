"""
Runtime configuration for AgentOS4Law.

Source priority:
  1. config.yaml — all behavioral settings (model, timeouts, thresholds, etc.)
  2. .env / env vars — secrets only (API keys, tokens, webhooks)

Provider selection via config.yaml `provider: "dashscope"` or `"deepseek"`.
base_url and api_key are auto-derived. No AGENT_OS_* env vars needed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(_PROJECT_ROOT / ".env")

_CONFIG_PATH = _PROJECT_ROOT / "config.yaml"


def _load_yaml() -> dict[str, Any]:
    if _CONFIG_PATH.exists():
        with open(_CONFIG_PATH, encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    return {}


_yaml = _load_yaml()


def _y(key: str, default: Any) -> Any:
    return _yaml.get(key, default)


# Mask proxy for model API calls — httpx/openai picks up HTTP_PROXY from env
# (needed for jina/jina web reader), which breaks the DeepSeek API connection.
# We only set NO_PROXY for known API endpoints; jina et al still use the proxy.
_no_proxy = os.environ.get("NO_PROXY", "").strip()
_api_hosts = {"api.deepseek.com", "dashscope.aliyuncs.com"}
_extra = ",".join(h for h in _api_hosts if h not in _no_proxy)
if _extra:
    os.environ["NO_PROXY"] = (_no_proxy + "," + _extra) if _no_proxy else _extra


_provider = _y("provider", "deepseek")
_yaml_reasoning = _y("reasoning_effort", None)
if _provider == "deepseek":
    _base_url = "https://api.deepseek.com"
    _api_key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY")
    _reasoning = _yaml_reasoning or "high"  # YAML 配置优先，否则默认 high
else:
    _base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    _api_key = os.getenv("OPENAI_API_KEY")
    # DashScope 用 enable_thinking 控制，reasoning_effort 不适用
    _reasoning = _yaml_reasoning or "high"

_ws = _y("workspace", {}) or {}
_ws_folders = _ws.get("folders", ["uploads", "research", "drafts", "raw_search", "logs"])
_ws_files = _ws.get("files", {"workspace.md": "# {{session_name}}\n\n## 目标\n\n"})


@dataclass
class Settings:
    # --- secrets (from env only) ---
    api_key: str | None = _api_key
    tavily_api_key: str | None = os.getenv("TAVILY_API_KEY")
    jina_api_key: str | None = os.getenv("JINA_API_KEY")
    feishu_webhook: str | None = os.getenv("FEISHU_WEBHOOK")
    feishu_secret: str | None = os.getenv("FEISHU_SECRET")
    mineru_api_token: str | None = os.getenv("MINERU_API_TOKEN") or os.getenv("MINERU_API_KEY")

    # --- model ---
    provider: str = _provider
    base_url: str = _base_url
    model: str = _y("model", "deepseek-v4-flash")
    reasoning_effort: str | None = _reasoning
    log_level: str = _y("log_level", "WARNING")
    disabled_tools: list[str] | None = field(default=None)
    enable_explicit_cache: bool = _y("enable_explicit_cache", False)

    model_timeout_seconds: int = _y("model_timeout_seconds", 200)
    max_iterations: int = _y("max_iterations", 64)

    # --- tool limits (from config.yaml) ---
    _tt = _y("tool_timeouts", {}) or {}
    _tl = _y("tool_output_limits", {}) or {}
    tool_timeout_bash: int = _tt.get("bash_default", 60)
    tool_timeout_law_retrieve: int = _tt.get("law_retrieve", 30)
    tool_timeout_case_retrieve: int = _tt.get("case_retrieve", 30)
    tool_timeout_web_search: int = _tt.get("web_search", 20)
    tool_timeout_web_read: int = _tt.get("web_read", 20)
    bash_stdout_max_chars: int = _tl.get("bash_stdout_max_chars", 100_000)
    bash_stderr_max_chars: int = _tl.get("bash_stderr_max_chars", 20_000)
    result_filter_threshold: int = _tl.get("result_filter_threshold", 5000)
    sub_agent_max_iterations: int = _y("sub_agent_max_iterations", 32)

    # --- workspace (from config.yaml, evaluated at class-def time) ---
    workspace_folders: list[str] = field(default_factory=lambda wf=_ws_folders: list(wf))
    workspace_files: dict[str, str] = field(default_factory=lambda wf=_ws_files: dict(wf))

    # --- context & compression (from config.yaml) ---
    context_token_threshold: int = _y("context_token_threshold", 250000)
    compress_head_turns: int = _y("compress_head_turns", 3)
    compress_tail_turns: int = _y("compress_tail_turns", 6)
    preserve_recent_tokens: int = _y("preserve_recent_tokens", 4000)
    max_context_messages: int = _y("max_context_messages", 8)
    max_context_items: int = _y("max_context_items", 12)

    # --- doc parsing (from config.yaml) ---
    mineru_base_url: str = _y("mineru_base_url", "https://mineru.net/api/v1/agent")
    mineru_v4_base_url: str = _y("mineru_v4_base_url", "https://mineru.net/api/v4")
    mineru_premium_model_version: str = _y("mineru_premium_model_version", "vlm")
    mineru_timeout_seconds: int = _y("mineru_timeout_seconds", 20)
    mineru_poll_interval_seconds: int = _y("mineru_poll_interval_seconds", 3)
    mineru_poll_timeout_seconds: int = _y("mineru_poll_timeout_seconds", 180)

    # --- scheduler (from config.yaml) ---
    scheduler_interval_seconds: int = _y("scheduler_interval_seconds", 30)

    # --- derived ---
    data_dir: Path = _PROJECT_ROOT / "data"
    database_path: Path = _PROJECT_ROOT / "data" / "agent_os.db"

    @classmethod
    def from_env(
        cls,
        *,
        data_dir: str | Path = "",
        model: str | None = None,
    ) -> "Settings":
        data_path = Path(data_dir) if data_dir else cls.data_dir
        data_path.mkdir(parents=True, exist_ok=True)
        return cls(
            data_dir=data_path,
            database_path=data_path / "agent_os.db",
            model=model or _y("model", "deepseek-v4-flash"),
            disabled_tools=_y("disabled_tools", None),
        )
