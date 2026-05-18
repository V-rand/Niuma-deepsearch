"""
Hybrid retriever: FTS + embedding with RRF fusion.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any

from ..storage import SQLiteStore
from .embedding import EmbeddingClient


@dataclass(slots=True)
class RetrievedItem:
    source: str
    path: str
    content: str
    score: float = 0.0
    metadata: dict[str, Any] | None = None
    artifact_type: str = ""
    title: str = ""
    summary: str = ""
    chunk_index: int | None = None


class SessionRetriever:
    def __init__(self, store: SQLiteStore, embedding_client: EmbeddingClient | None = None):
        self.store = store
        self.embedding = embedding_client

    async def search(self, session_id: str, query: str, limit: int = 8, work_dir: str = "") -> list[RetrievedItem]:
        fts_results = self._fts_search(session_id, query, limit=limit, work_dir=work_dir)
        embedding_results = await self._embedding_search(work_dir or session_id, query, limit=limit)
        combined = self._rrf_fuse(fts_results, embedding_results, limit=limit)
        return combined

    def _fts_search(self, session_id: str, query: str, limit: int, work_dir: str = "") -> list[dict]:
        """FTS keyword search. If work_dir is set, searches all sessions sharing that work_dir."""
        results: list[dict] = []
        if work_dir:
            for row in self.store.search_artifact_chunks_by_work_dir(work_dir, query, limit=limit):
                results.append({"source": "artifact", "path": row.get("path", ""),
                                "content": row.get("content", ""),
                                "metadata": row.get("metadata") or {},
                                "artifact_type": str(row.get("artifact_type", "")),
                                "title": str(row.get("title", "")),
                                "summary": str(row.get("summary", "")),
                                "chunk_index": row.get("chunk_index")})
        else:
            for row in self.store.search_artifact_chunks(session_id, query, limit=limit):
                results.append({"source": "artifact", "path": row.get("path", ""),
                                "content": row.get("content", ""),
                                "metadata": row.get("metadata") or {},
                                "artifact_type": str(row.get("artifact_type", "")),
                                "title": str(row.get("title", "")),
                                "summary": str(row.get("summary", "")),
                                "chunk_index": row.get("chunk_index")})
        for row in self.store.search_messages(session_id, query, limit=max(2, limit // 2)):
            results.append({"source": f"message:{row.get('kind', 'chat')}",
                            "path": "messages", "content": row.get("content", ""),
                            "metadata": {**(row.get("metadata") or {}), "created_at": row.get("created_at"), "kind": row.get("kind")},
                            "artifact_type": "", "title": "", "summary": "", "chunk_index": None})
        return results[:limit * 2]

    async def _embedding_search(self, work_dir_or_session: str, query: str, limit: int) -> list[dict]:
        """Embedding-based semantic search. work_dir_or_session can be a work_dir path or session_id."""
        if not self.embedding or not self.embedding.available:
            return []
        query_vec = await self.embedding.embed_query(query)
        if query_vec is None:
            return []
        if "/" in (work_dir_or_session or "") or "data" in (work_dir_or_session or ""):
            candidates = self.store.get_all_chunk_embeddings_by_work_dir(work_dir_or_session)
        else:
            candidates = self.store.get_all_chunk_embeddings(work_dir_or_session)
        if not candidates:
            return []
        scored = []
        for c in candidates:
            stored_vec = c.get("embedding")
            if not isinstance(stored_vec, list):
                continue
            sim = EmbeddingClient.cosine_similarity(query_vec, stored_vec)
            scored.append((sim, c))
        scored.sort(key=lambda x: x[0], reverse=True)
        results = []
        for sim, row in scored[:limit * 2]:
            results.append({"source": "artifact", "path": row.get("path", ""),
                            "content": row.get("content", ""),
                            "metadata": row.get("metadata") or {},
                            "artifact_type": str(row.get("artifact_type", "")),
                            "title": str(row.get("title", "")),
                            "summary": str(row.get("summary", "")),
                            "chunk_index": row.get("chunk_index")})
        return results

    @staticmethod
    def _rrf_fuse(fts: list[dict], emb: list[dict], limit: int, k: int = 60) -> list[RetrievedItem]:
        items: dict[tuple[str, str], dict] = {}
        for rank, hit in enumerate(fts):
            key = (hit["source"], hit["content"][:60])
            hit["_rrf"] = 1.0 / (k + rank + 1)
            items[key] = hit
        for rank, hit in enumerate(emb):
            key = (hit["source"], hit["content"][:60])
            if key in items:
                items[key]["_rrf"] += 1.0 / (k + rank + 1)
            else:
                hit["_rrf"] = 1.0 / (k + rank + 1)
                items[key] = hit
        sorted_items = sorted(items.values(), key=lambda x: x.get("_rrf", 0), reverse=True)
        return [RetrievedItem(source=h["source"], path=h["path"], content=h["content"],
                              score=h.get("_rrf", 0), metadata=h.get("metadata"),
                              artifact_type=h.get("artifact_type", ""), title=h.get("title", ""),
                              summary=h.get("summary", ""), chunk_index=h.get("chunk_index"))
                for h in sorted_items[:limit]]
