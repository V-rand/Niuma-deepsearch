"""
Shared helpers for tool implementations.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Iterable

import requests

_PROXY_ENV_KEYS = (
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "ALL_PROXY",
    "https_proxy",
    "http_proxy",
    "all_proxy",
)


def _has_proxy_env() -> bool:
    return any((os.getenv(key) or "").strip() for key in _PROXY_ENV_KEYS)


def _skip_jina_without_proxy() -> bool:
    raw = (os.getenv("READER_SKIP_JINA_WITHOUT_PROXY", "true") or "").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _read_provider_order(
    env_name: str = "READER_PROVIDER_ORDER",
    default: Iterable[str] = ("jina", "firecrawl", "trafilatura"),
    allowed: Iterable[str] = ("jina", "firecrawl", "trafilatura"),
) -> list[str]:
    allowed_set = {item.strip().lower() for item in allowed if item.strip()}
    default_list = [item.strip().lower() for item in default if item.strip()]
    raw = os.getenv(env_name, ",".join(default_list))
    parsed = [item.strip().lower() for item in raw.split(",") if item.strip()]
    normalized = [item for item in parsed if item in allowed_set]
    return normalized or default_list


def _resolve_firecrawl_api_key() -> str | None:
    return (os.getenv("FIRECRAWL_API_KEY") or "").strip() or None


def _resolve_mineru_api_token() -> str | None:
    return (
        (os.getenv("MINERU_API_TOKEN") or "").strip()
        or (os.getenv("MINERU_API_KEY") or "").strip()
        or None
    )


def _resolve_serper_api_key() -> str | None:
    return (os.getenv("SERPER_API_KEY") or "").strip() or None


def _normalize_url(url: str) -> str:
    value = (url or "").strip()
    if value.startswith(("http://", "https://")):
        return value
    return f"https://{value}"


def _summarize_error(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"


def _coerce_markdown_output(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n\n".join(str(item) for item in value)
    if value is None:
        return ""
    return str(value)


def _extract_pdf_markdown_from_path(pdf_path: str | Path) -> str:
    import pymupdf4llm

    markdown = pymupdf4llm.to_markdown(str(pdf_path))
    return _coerce_markdown_output(markdown).strip()


def _extract_pdf_markdown_from_url(url: str, *, timeout_seconds: float = 30.0) -> str:
    response = requests.get(url, timeout=timeout_seconds)
    response.raise_for_status()

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=True) as handle:
        handle.write(response.content)
        handle.flush()
        return _extract_pdf_markdown_from_path(handle.name)
