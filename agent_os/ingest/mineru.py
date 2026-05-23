"""
MinerU parsing client with lightweight and premium fallback.
"""

from __future__ import annotations

import io
import time
import zipfile
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit

import requests
from requests.exceptions import RequestException


LOCAL_TEXT_SUFFIXES = {".md", ".txt"}
LOCAL_SPREADSHEET_SUFFIXES = {".csv", ".tsv"}
IMAGE_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".jp2",
    ".webp",
    ".gif",
    ".bmp",
}
MINERU_SUPPORTED_SUFFIXES = {
    ".pdf",
    ".doc",
    ".docx",
    ".pptx",
    ".xls",
    ".xlsx",
    ".html",
    ".htm",
    *IMAGE_SUFFIXES,
}
LEGACY_DOC_SUFFIXES = {".doc"}
# MinerU lightweight API error codes that indicate the file is too complex for
# the fast parser. When any of these codes is returned, we retry with the premium
# (v4) API that handles more formats and complex layouts.
LIGHTWEIGHT_FALLBACK_CODES = {-30001, -30002, -30003, -30004, -30007}


class MineruError(RuntimeError):
    def __init__(self, message: str, *, code: int | None = None):
        super().__init__(message)
        self.code = code


def _format_mineru_error(prefix: str, exc: Exception) -> str:
    if isinstance(exc, TimeoutError):
        return f"{prefix} timed out: {exc}"
    if isinstance(exc, MineruError):
        if exc.code is not None:
            return f"{prefix} failed [{exc.code}]: {exc}"
        return f"{prefix} failed: {exc}"
    return f"{prefix} failed: {exc}"


class MineruClient:
    def __init__(
        self,
        *,
        base_url: str,
        premium_base_url: str | None = None,
        api_token: str | None = None,
        premium_model_version: str = "vlm",
        timeout_seconds: int = 20,
        poll_interval_seconds: int = 3,
        poll_timeout_seconds: int = 180,
    ):
        self.base_url = base_url.rstrip("/")
        self.premium_base_url = (premium_base_url or "https://mineru.net/api/v4").rstrip("/")
        self.api_token = api_token
        self.premium_model_version = premium_model_version
        self.timeout_seconds = timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self.poll_timeout_seconds = poll_timeout_seconds

    def parse_local_file(
        self,
        file_path: str | Path,
        *,
        language: str = "ch",
        page_range: str | None = None,
        prefer_premium: bool = False,
    ) -> dict[str, Any]:
        source_path = Path(file_path)
        suffix = source_path.suffix.lower()
        if suffix in LOCAL_TEXT_SUFFIXES:
            return {
                "parser": "local-text",
                "content": source_path.read_text(encoding="utf-8"),
                "task_id": None,
                "markdown_url": None,
            }
        if suffix in LOCAL_SPREADSHEET_SUFFIXES:
            return {
                "parser": "local-tabular-text",
                "content": source_path.read_text(encoding="utf-8"),
                "task_id": None,
                "markdown_url": None,
            }
        if suffix in LEGACY_DOC_SUFFIXES:
            try:
                from doc2txt import extract_text
                content = extract_text(str(source_path), optimize_format=True)
                if content.strip():
                    return {"parser": "doc2txt", "content": content.strip(), "task_id": None, "markdown_url": None}
            except Exception:
                pass
            raise MineruError(".doc 文件解析失败。请手动在 Word 中另存为 .docx 格式。")
        if suffix not in MINERU_SUPPORTED_SUFFIXES:
            raise ValueError(f"Unsupported upload type: {suffix or source_path.name}")

        if self._is_html_target(source_path=source_path):
            return self._parse_local_file_premium(
                source_path,
                language=language,
                page_range=page_range,
            )

        if prefer_premium:
            return self._parse_local_file_premium(
                source_path,
                language=language,
                page_range=page_range,
            )

        try:
            return self._parse_local_file_lightweight(
                source_path,
                language=language,
                page_range=page_range,
            )
        except Exception as exc:
            if self._should_fallback_to_premium(exc):
                return self._parse_local_file_premium(
                    source_path,
                    language=language,
                    page_range=page_range,
                )
            raise

    def parse_local_files(
        self,
        file_paths: Iterable[str | Path],
        *,
        language: str = "ch",
        prefer_premium: bool = True,
    ) -> list[dict[str, Any]]:
        paths = [Path(path) for path in file_paths]
        if not paths:
            return []
        if not prefer_premium:
            return [
                self.parse_local_file(path, language=language, prefer_premium=False)
                for path in paths
            ]

        return self._parse_local_files_premium(paths, language=language)

    def parse_remote_url(
        self,
        url: str,
        *,
        file_name: str | None = None,
        language: str = "ch",
        page_range: str | None = None,
        prefer_premium: bool = False,
    ) -> dict[str, Any]:
        if self._is_html_target(url=url, file_name=file_name):
            if self.api_token:
                return self._parse_remote_url_premium(
                    url,
                    file_name=file_name,
                    language=language,
                    page_range=page_range,
                )
            raise MineruError("HTML documents require the standard MinerU API token")

        if prefer_premium:
            return self._parse_remote_url_premium(
                url,
                file_name=file_name,
                language=language,
                page_range=page_range,
            )

        try:
            return self._parse_remote_url_lightweight(
                url,
                file_name=file_name,
                language=language,
                page_range=page_range,
            )
        except Exception as exc:
            if self._should_fallback_to_premium(exc):
                return self._parse_remote_url_premium(
                    url,
                    file_name=file_name,
                    language=language,
                    page_range=page_range,
                )
            raise

    def _parse_local_file_lightweight(
        self,
        source_path: Path,
        *,
        language: str,
        page_range: str | None,
    ) -> dict[str, Any]:
        task = self._create_file_task(source_path.name, language=language, page_range=page_range)
        self._upload_file(task["file_url"], source_path)
        result = self._poll_lightweight_result(task["task_id"])
        markdown_url = result.get("markdown_url")
        if not markdown_url:
            raise MineruError("MinerU did not return markdown_url")
        markdown = self._download_text(markdown_url)
        return {
            "parser": "mineru-agent",
            "content": markdown,
            "task_id": task["task_id"],
            "markdown_url": markdown_url,
        }

    def _parse_local_file_premium(
        self,
        source_path: Path,
        *,
        language: str,
        page_range: str | None,
    ) -> dict[str, Any]:
        model_version = self._resolve_premium_model_version(source_path=source_path)
        result = self._parse_local_files_premium(
            [source_path],
            language=language,
            page_range=page_range,
            model_version=model_version,
        )[0]
        result["parser"] = "mineru-premium-upload"
        return result

    def _parse_local_files_premium(
        self,
        source_paths: list[Path],
        *,
        language: str,
        page_range: str | None = None,
        model_version: str | None = None,
    ) -> list[dict[str, Any]]:
        self._ensure_premium_available()
        resolved_model_version = model_version or self.premium_model_version
        file_entries = []
        for index, source_path in enumerate(source_paths, start=1):
            suffix = source_path.suffix.lower()
            if suffix not in MINERU_SUPPORTED_SUFFIXES:
                raise ValueError(f"Unsupported upload type: {suffix or source_path.name}")
            file_entry: dict[str, Any] = {
                "name": source_path.name,
                "data_id": self._build_data_id(source_path, index),
            }
            if page_range:
                file_entry["page_ranges"] = page_range
            if self._should_enable_ocr(source_path):
                file_entry["is_ocr"] = True
            file_entries.append(file_entry)

        response = requests.post(
            f"{self.premium_base_url}/file-urls/batch",
            headers=self._premium_headers(),
            json={
                "files": file_entries,
                "language": language,
                "model_version": resolved_model_version,
            },
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        data = self._unwrap_json(response)
        batch_id = data["data"]["batch_id"]
        file_urls = data["data"]["file_urls"]
        if len(file_urls) != len(source_paths):
            raise MineruError("MinerU returned mismatched upload URLs")

        for source_path, upload_url in zip(source_paths, file_urls, strict=True):
            self._upload_file(upload_url, source_path)

        results = self._poll_premium_batch_results(batch_id)
        by_data_id = {
            str(item.get("data_id") or ""): item
            for item in results
        }

        parsed_results: list[dict[str, Any]] = []
        for index, source_path in enumerate(source_paths, start=1):
            data_id = self._build_data_id(source_path, index)
            item = by_data_id.get(data_id)
            if item is None:
                raise MineruError(f"MinerU did not return batch result for {source_path.name}")
            full_zip_url = item.get("full_zip_url")
            if not full_zip_url:
                raise MineruError(f"MinerU batch result missing full_zip_url for {source_path.name}")
            markdown = self._download_markdown_from_zip(full_zip_url)
            parsed_results.append(
                {
                    "parser": "mineru-premium-batch",
                    "content": markdown,
                    "task_id": batch_id,
                    "markdown_url": full_zip_url,
                    "data_id": data_id,
                }
            )
        return parsed_results

    def _parse_remote_url_lightweight(
        self,
        url: str,
        *,
        file_name: str | None,
        language: str,
        page_range: str | None,
    ) -> dict[str, Any]:
        task = self._create_url_task(
            url=url,
            file_name=file_name,
            language=language,
            page_range=page_range,
        )
        result = self._poll_lightweight_result(task["task_id"])
        markdown_url = result.get("markdown_url")
        if not markdown_url:
            raise MineruError("MinerU did not return markdown_url")
        markdown = self._download_text(markdown_url)
        return {
            "parser": "mineru-url",
            "content": markdown,
            "task_id": task["task_id"],
            "markdown_url": markdown_url,
        }

    def _parse_remote_url_premium(
        self,
        url: str,
        *,
        file_name: str | None,
        language: str,
        page_range: str | None,
    ) -> dict[str, Any]:
        model_version = self._resolve_premium_model_version(url=url, file_name=file_name)
        self._ensure_premium_available()
        payload: dict[str, Any] = {
            "url": url,
            "model_version": model_version,
            "language": language,
        }
        if page_range:
            payload["page_ranges"] = page_range
        if file_name and self._should_enable_ocr(Path(file_name)):
            payload["is_ocr"] = True
        response = requests.post(
            f"{self.premium_base_url}/extract/task",
            headers=self._premium_headers(),
            json=payload,
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        data = self._unwrap_json(response)
        task_id = data["data"]["task_id"]
        result = self._poll_premium_task(task_id)
        full_zip_url = result.get("full_zip_url")
        if not full_zip_url:
            raise MineruError("MinerU premium result missing full_zip_url")
        markdown = self._download_markdown_from_zip(full_zip_url)
        return {
            "parser": "mineru-premium-url",
            "content": markdown,
            "task_id": task_id,
            "markdown_url": full_zip_url,
        }

    def _create_file_task(
        self,
        file_name: str,
        *,
        language: str,
        page_range: str | None,
    ) -> dict[str, str]:
        payload: dict[str, Any] = {"file_name": file_name, "language": language}
        if page_range:
            payload["page_range"] = page_range
        response = requests.post(
            f"{self.base_url}/parse/file",
            json=payload,
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        data = self._unwrap_json(response)
        return data["data"]

    def _create_url_task(
        self,
        *,
        url: str,
        file_name: str | None,
        language: str,
        page_range: str | None,
    ) -> dict[str, str]:
        payload: dict[str, Any] = {"url": url, "language": language}
        if file_name:
            payload["file_name"] = file_name
        if page_range:
            payload["page_range"] = page_range
        response = requests.post(
            f"{self.base_url}/parse/url",
            json=payload,
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        data = self._unwrap_json(response)
        return data["data"]

    def _upload_file(self, file_url: str, source_path: Path) -> None:
        with source_path.open("rb") as handle:
            response = requests.put(file_url, data=handle, timeout=self.timeout_seconds)
        response.raise_for_status()

    def _poll_lightweight_result(self, task_id: str) -> dict[str, Any]:
        deadline = time.time() + self.poll_timeout_seconds
        while time.time() < deadline:
            response = requests.get(
                f"{self.base_url}/parse/{task_id}",
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            data = self._unwrap_json(response)
            task_data = data.get("data", {}) or {}
            state = task_data.get("state")
            if state == "done":
                return task_data
            if state == "failed":
                raise MineruError(
                    task_data.get("err_msg") or "MinerU parsing failed",
                    code=task_data.get("err_code"),
                )
            time.sleep(self.poll_interval_seconds)
        raise TimeoutError(f"MinerU parse timed out after {self.poll_timeout_seconds} seconds")

    def _poll_premium_task(self, task_id: str) -> dict[str, Any]:
        deadline = time.time() + self.poll_timeout_seconds
        while time.time() < deadline:
            response = requests.get(
                f"{self.premium_base_url}/extract/task/{task_id}",
                headers=self._premium_headers(),
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            data = self._unwrap_json(response)
            task_data = data.get("data", {}) or {}
            state = task_data.get("state")
            if state == "done":
                return task_data
            if state == "failed":
                raise MineruError(task_data.get("err_msg") or "MinerU premium parse failed")
            time.sleep(self.poll_interval_seconds)
        raise TimeoutError(f"MinerU premium parse timed out after {self.poll_timeout_seconds} seconds")

    def _poll_premium_batch_results(self, batch_id: str) -> list[dict[str, Any]]:
        deadline = time.time() + self.poll_timeout_seconds
        while time.time() < deadline:
            response = requests.get(
                f"{self.premium_base_url}/extract-results/batch/{batch_id}",
                headers=self._premium_headers(),
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            data = self._unwrap_json(response)
            items = list(data.get("data", {}).get("extract_result", []) or [])
            if not items:
                time.sleep(self.poll_interval_seconds)
                continue
            states = {item.get("state") for item in items}
            if "failed" in states:
                failed = next(item for item in items if item.get("state") == "failed")
                raise MineruError(failed.get("err_msg") or "MinerU premium batch parse failed")
            if all(state == "done" for state in states):
                return items
            time.sleep(self.poll_interval_seconds)
        raise TimeoutError(f"MinerU premium batch timed out after {self.poll_timeout_seconds} seconds")

    def _download_text(self, markdown_url: str) -> str:
        response = requests.get(markdown_url, timeout=self.timeout_seconds)
        response.raise_for_status()
        return response.content.decode("utf-8", errors="replace")

    def _download_markdown_from_zip(self, full_zip_url: str) -> str:
        response = requests.get(full_zip_url, timeout=max(self.timeout_seconds, 60))
        response.raise_for_status()
        archive = zipfile.ZipFile(io.BytesIO(response.content))
        members = archive.namelist()
        preferred = next((name for name in members if name.endswith("full.md")), None)
        fallback = next((name for name in members if name.endswith(".md")), None)
        target = preferred or fallback
        if not target:
            raise MineruError("MinerU result zip does not contain markdown")
        with archive.open(target) as handle:
            return handle.read().decode("utf-8", errors="replace")

    def _premium_headers(self) -> dict[str, str]:
        self._ensure_premium_available()
        return {"Authorization": f"Bearer {self.api_token}", "Content-Type": "application/json"}

    def _ensure_premium_available(self) -> None:
        if not self.api_token:
            raise MineruError("MINERU_API_TOKEN not configured")

    def _should_fallback_to_premium(self, exc: Exception) -> bool:
        if not self.api_token:
            return False
        if isinstance(exc, RequestException):
            return True
        if isinstance(exc, MineruError) and exc.code in LIGHTWEIGHT_FALLBACK_CODES:
            return True
        message = str(exc).lower()
        return any(
            needle in message
            for needle in (
                "lightweight",
                "page count exceeds",
                "file size",
                "too many requests",
                "429",
                "temporarily unavailable",
                "service temporarily unavailable",
                "model service",
                "network is unreachable",
                "name or service not known",
                "failed to resolve",
            )
        )

    @staticmethod
    def _is_html_target(*, url: str | None = None, file_name: str | None = None, source_path: Path | None = None) -> bool:
        if file_name:
            suffix = Path(file_name).suffix.lower()
            return suffix in {".html", ".htm"}
        if url:
            suffix = Path(urlsplit(url).path).suffix.lower()
            return suffix in {".html", ".htm"}
        if source_path is not None:
            suffix = source_path.suffix.lower()
            return suffix in {".html", ".htm"}
        suffix = ""
        return suffix in {".html", ".htm"}

    def _resolve_premium_model_version(
        self,
        *,
        url: str | None = None,
        file_name: str | None = None,
        source_path: Path | None = None,
    ) -> str:
        if self._is_html_target(url=url, file_name=file_name, source_path=source_path):
            return "MinerU-HTML"
        return self.premium_model_version

    @staticmethod
    def _build_data_id(source_path: Path, index: int) -> str:
        stem = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in source_path.stem)
        return f"{stem}_{index}"

    @staticmethod
    def _should_enable_ocr(source_path: Path) -> bool:
        return source_path.suffix.lower() in IMAGE_SUFFIXES

    @staticmethod
    def _unwrap_json(response: requests.Response) -> dict[str, Any]:
        data = response.json()
        if data.get("code") != 0:
            raise MineruError(
                data.get("msg") or "MinerU request failed",
                code=data.get("code"),
            )
        return data
