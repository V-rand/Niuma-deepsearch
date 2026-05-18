"""
得理开放平台案例检索接口。
通过环境变量配置：DELI_APP_ID, DELI_APP_SECRET, DELI_BASE_URL
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

BASE_URL = os.getenv("DELI_BASE_URL", "https://openapi.delilegal.com")
APP_ID = os.getenv("DELI_APP_ID", "")
APP_SECRET = os.getenv("DELI_APP_SECRET", "")


def _token_path() -> str:
    return os.getenv("DELI_TOKEN_FILE", "token_cache.json")


def _load_token() -> dict[str, Any] | None:
    path = _token_path()
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        if "access_token" in data and "expires_at" in data:
            return data
    except Exception:
        logger.debug("Failed to read token cache", exc_info=True)
    return None


def _save_token(token_data: dict[str, Any]) -> None:
    try:
        with open(_token_path(), "w") as f:
            json.dump(token_data, f, indent=2)
    except Exception:
        logger.debug("Failed to save token cache", exc_info=True)


def _fetch_token() -> str:
    if not APP_ID or not APP_SECRET:
        raise RuntimeError("DELI_APP_ID and DELI_APP_SECRET must be set")
    resp = requests.get(
        f"{BASE_URL}/oauth/authorize",
        params={"appid": APP_ID, "secret": APP_SECRET, "grant_type": "client_credential"},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        raise RuntimeError(f"Token request failed: {data.get('msg')}")
    body = data.get("body", {})
    token = body.get("accessToken")
    if not token:
        raise RuntimeError("No accessToken in response")
    expires_at = time.time() + int(body.get("expiresIn", 7200)) - 300
    token_data = {"access_token": token, "expires_at": expires_at}
    _save_token(token_data)
    return token


def get_access_token() -> str:
    cached = _load_token()
    if cached and time.time() < cached["expires_at"]:
        return cached["access_token"]
    return _fetch_token()


def get_case_results(question: str, size: int = 5, debug: bool = False) -> list[dict[str, Any]]:
    token = get_access_token()
    resp = requests.get(
        f"{BASE_URL}/api/v1/rag/case",
        headers={"Content-Type": "application/json", "authorization": token},
        params={"question": question, "size": size},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        raise RuntimeError(f"API failed: {data.get('msg')}")
    return [
        {
            "title": item.get("title", ""),
            "content": item.get("content", ""),
            "source": item.get("source", ""),
            "caseNo": item.get("caseNo", ""),
            "court": item.get("court", ""),
        }
        for item in data.get("body", [])
    ]
