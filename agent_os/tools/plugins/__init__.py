"""
Tool plugins — auto-discovered from .py files in this directory.

Each plugin file must expose a ``register(tool_registry)`` function that
calls ``tool_registry.register(name, toolset, schema, handler)`` for each
tool it provides.
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..registry import ToolRegistry

logger = logging.getLogger(__name__)


def discover_plugins(registry: "ToolRegistry") -> int:
    plugins_dir = Path(__file__).parent
    count = 0
    for entry in sorted(plugins_dir.iterdir()):
        if not entry.is_file():
            continue
        if entry.suffix != ".py":
            continue
        if entry.name.startswith("_") or entry.name.startswith("."):
            continue
        if entry.name.startswith(("sample_", "example_")):
            continue
        module_name = f"agent_os.tools.plugins.{entry.stem}"
        try:
            spec = importlib.util.spec_from_file_location(module_name, str(entry))
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        except Exception:
            logger.exception("Failed to load plugin: %s", entry.name)
            continue
        register_fn = getattr(module, "register", None)
        if register_fn is None or not callable(register_fn):
            logger.warning("Plugin %s has no register(tool_registry) function, skipping", entry.name)
            continue
        try:
            register_fn(registry)
            count += 1
        except Exception:
            logger.exception("Plugin %s registration failed", entry.name)
    return count
