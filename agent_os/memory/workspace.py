"""
Workspace artifact management backed by files plus SQLite metadata.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from ..core.session import SessionManager
from ..storage import SQLiteStore

logger = logging.getLogger(__name__)


class WorkspaceMemory:
    def __init__(
        self,
        *,
        session_manager: SessionManager,
        store: SQLiteStore,
        embedding_client=None,
    ):
        self.session_manager = session_manager
        self.store = store
        self.embedding = embedding_client
        self._embedding_revisions: dict[tuple[str, str], object] = {}
        self._embedding_tasks: set[asyncio.Task] = set()

    async def upsert_artifact(
        self,
        session_id: str,
        *,
        path: str,
        content: str,
        artifact_type: str,
        title: str,
        summary: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        session = await self.session_manager.get(session_id)
        if session is None:
            raise ValueError(f"Session not found: {session_id}")
        path, artifact_path = self._resolve_artifact_path(session.work_dir, path, for_write=True)
        existing_artifact = self.store.row_to_json(self.store.get_artifact(session_id, path)) or {}
        merged_metadata = self._merge_metadata(existing_artifact.get("metadata"), metadata, path=path)

        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(content, encoding="utf-8")

        artifact_id = self.store.upsert_artifact(
            session_id=session_id,
            path=path,
            title=title,
            artifact_type=artifact_type,
            summary=summary,
            metadata=merged_metadata,
        )
        chunks = self._split_chunks(content)
        self.store.replace_artifact_chunks(
            artifact_id=artifact_id, session_id=session_id, path=path,
            chunks=[{"content": chunk, "embedding": None} for chunk in chunks],
        )
        if self.embedding and self.embedding.available:
            revision = object()
            self._embedding_revisions[(session_id, path)] = revision
            task = asyncio.create_task(self._embed_chunks(artifact_id, session_id, path, chunks, revision))
            self._embedding_tasks.add(task)
            task.add_done_callback(self._log_embedding_task_result)
            task.add_done_callback(self._embedding_tasks.discard)
        artifact = self.store.get_artifact(session_id, path)
        return self.store.row_to_json(artifact) or {}

    async def _embed_chunks(
        self,
        artifact_id: str,
        session_id: str,
        path: str,
        chunks: list[str],
        revision: object,
    ) -> None:
        texts = [c if isinstance(c, str) else c.get("content", "") for c in chunks]
        vectors = await self.embedding.embed(texts)
        if self._embedding_revisions.get((session_id, path)) is not revision:
            return
        if vectors:
            self.store.replace_artifact_chunks(
                artifact_id=artifact_id, session_id=session_id, path=path,
                chunks=[{"content": (c if isinstance(c, str) else c.get("content", "")), "embedding": v}
                        for c, v in zip(chunks, vectors)],
            )
        if self._embedding_revisions.get((session_id, path)) is revision:
            self._embedding_revisions.pop((session_id, path), None)

    @staticmethod
    def _log_embedding_task_result(task: asyncio.Task) -> None:
        try:
            task.result()
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("Background artifact embedding failed")

    async def drain_background_tasks(self, timeout_seconds: float = 5.0) -> None:
        pending = [task for task in self._embedding_tasks if not task.done()]
        if not pending:
            return
        _done, pending_set = await asyncio.wait(pending, timeout=timeout_seconds)
        for task in pending_set:
            task.cancel()
        if pending_set:
            await asyncio.gather(*pending_set, return_exceptions=True)

    async def read_artifact(self, session_id: str, path: str) -> dict[str, Any] | None:
        session = await self.session_manager.get(session_id)
        if session is None:
            return None
        try:
            path, artifact_path = self._resolve_artifact_path(session.work_dir, path, for_write=False)
        except ValueError:
            return None
        row = self.store.get_artifact(session_id, path)
        if row is None:
            row = self.store.get_artifact_by_work_dir(session.work_dir, path)
        if row is None:
            return None
        if not artifact_path.exists() or not artifact_path.is_file():
            return None
        data = self.store.row_to_json(row) or {}
        data["content"] = artifact_path.read_text(encoding="utf-8")
        return data

    async def sync_file_artifact(self, session_id: str, path: str) -> dict[str, Any] | None:
        if not path.endswith(".md"):
            return None
        session = await self.session_manager.get(session_id)
        if session is None:
            return None
        try:
            path, artifact_path = self._resolve_artifact_path(session.work_dir, path, for_write=True)
        except ValueError:
            return None
        if not artifact_path.exists() or not artifact_path.is_file():
            return None
        content = artifact_path.read_text(encoding="utf-8")
        return await self.upsert_artifact(
            session_id,
            path=path,
            content=content,
            artifact_type=self._infer_artifact_type(path),
            title=Path(path).stem.replace("_", " ").replace("-", " ").title(),
            summary=self._infer_summary(content, path),
        )

    async def remove_artifact(self, session_id: str, path: str) -> None:
        session = await self.session_manager.get(session_id)
        if session is None:
            return
        try:
            path, _ = self._resolve_artifact_path(session.work_dir, path, for_write=True)
        except ValueError:
            return
        self.store.delete_artifacts_by_work_dir(session.work_dir, path)

    def _resolve_artifact_path(self, work_dir: str, path: str, *, for_write: bool) -> tuple[str, Path]:
        normalized = self._normalize_artifact_path(path)
        if for_write and self._is_read_only_path(normalized):
            raise ValueError(f"Path is read-only for artifact writes: {normalized}")
        base = Path(work_dir).resolve()
        resolved = (base / normalized).resolve()
        try:
            rel = resolved.relative_to(base)
        except ValueError as exc:
            raise ValueError(f"Path traversal detected: {path}") from exc
        rel_path = rel.as_posix()
        if for_write and self._is_read_only_path(rel_path):
            raise ValueError(f"Path is read-only for artifact writes: {rel_path}")
        return rel_path, resolved

    @staticmethod
    def _normalize_artifact_path(path: str) -> str:
        if Path(str(path or "")).is_absolute():
            raise ValueError(f"Absolute artifact paths are not allowed: {path}")
        normalized = str(path or "").strip().replace("\\", "/").lstrip("/")
        if not normalized or normalized in {".", ".."}:
            raise ValueError("Artifact path is required")
        parts = [part for part in normalized.split("/") if part and part != "."]
        if any(part == ".." for part in parts):
            raise ValueError(f"Path traversal detected: {path}")
        normalized = "/".join(parts)
        if not normalized:
            raise ValueError("Artifact path is required")
        return normalized

    @staticmethod
    def _is_read_only_path(path: str) -> bool:
        normalized = path.strip().replace("\\", "/").lstrip("/")
        return normalized == "uploads" or normalized.startswith("uploads/")

    def _split_chunks(self, content: str) -> list[str]:
        paragraphs = [part.strip() for part in content.split("\n\n") if part.strip()]
        if not paragraphs:
            stripped = content.strip()
            return [stripped] if stripped else []

        chunks: list[str] = []
        current = ""
        for paragraph in paragraphs:
            candidate = paragraph if not current else f"{current}\n\n{paragraph}"
            if len(candidate) <= 1200:
                current = candidate
                continue
            if current:
                chunks.append(current)
            current = paragraph
        if current:
            chunks.append(current)
        return chunks

    def _infer_artifact_type(self, path: str) -> str:
        if path.startswith("drafts/"):
            return "draft"
        if path.startswith("research/"):
            return "research"
        if path.startswith("evidence/"):
            return "evidence"
        return "note"

    def _infer_summary(self, content: str, path: str) -> str:
        for line in content.splitlines():
            stripped = line.strip().lstrip("#").strip()
            if stripped:
                return stripped[:120]
        return path

    def _merge_metadata(
        self,
        existing: dict[str, Any] | None,
        update: dict[str, Any] | None,
        *,
        path: str,
    ) -> dict[str, Any]:
        merged = dict(existing or {})
        patch = dict(update or {})
        merged.update(patch)

        lineage = dict(merged.get("lineage") or {})
        existing_lineage = dict((existing or {}).get("lineage") or {})
        patch_lineage = dict((update or {}).get("lineage") or {})
        lineage.update(existing_lineage)
        lineage.update(patch_lineage)

        source_paths = self._unique_strings(
            [
                *(existing_lineage.get("source_paths") or []),
                *((existing or {}).get("source_paths") or []),
                *((patch_lineage.get("source_paths") or [])),
                *((patch or {}).get("source_paths") or []),
                (existing or {}).get("source_path"),
                patch.get("source_path"),
            ]
        )
        if source_paths:
            lineage["source_paths"] = source_paths

        derived_from = self._unique_strings(
            [
                *(existing_lineage.get("derived_from") or []),
                *(patch_lineage.get("derived_from") or []),
                *((patch or {}).get("derived_from") or []),
            ]
        )
        if derived_from:
            lineage["derived_from"] = derived_from

        generated_by = patch.get("generated_by") or patch_lineage.get("generated_by")
        if generated_by:
            lineage["generated_by"] = generated_by
        elif existing_lineage.get("generated_by"):
            lineage["generated_by"] = existing_lineage["generated_by"]

        parser = patch.get("parser") or patch_lineage.get("parser")
        if parser:
            lineage["parser"] = parser
        elif existing_lineage.get("parser"):
            lineage["parser"] = existing_lineage["parser"]

        if lineage:
            lineage["artifact_path"] = path
            merged["lineage"] = lineage
        return merged

    @staticmethod
    def _unique_strings(values: list[Any]) -> list[str]:
        items: list[str] = []
        for value in values:
            if not value:
                continue
            if not isinstance(value, str):
                continue
            if value not in items:
                items.append(value)
        return items
