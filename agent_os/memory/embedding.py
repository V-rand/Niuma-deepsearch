"""
Embedding client using DashScope/Bailian text-embedding-v4.
Gracefully degrades when API key lacks embedding permission.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any


class EmbeddingClient:
    def __init__(self, api_key: str | None = None, base_url: str | None = None, model: str = "text-embedding-v4", dimensions: int = 1024):
        self.api_key = api_key or os.getenv("EMBEDDING_API_KEY") or os.getenv("OPENAI_API_KEY") or os.getenv("DASHSCOPE_API_KEY")
        self.base_url = base_url or os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
        self.model = model
        self.dimensions = dimensions
        self._available = bool(self.api_key)

    @property
    def available(self) -> bool:
        return self._available

    async def embed(self, texts: list[str]) -> list[list[float]] | None:
        """Batch embed texts. Returns None on failure."""
        if not self._available:
            return None
        try:
            from openai import AsyncOpenAI
            client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url, timeout=30, max_retries=0)
            response = await asyncio.wait_for(
                client.embeddings.create(model=self.model, input=texts, dimensions=self.dimensions),
                timeout=30,
            )
            return [d.embedding for d in response.data]
        except Exception:
            self._available = False
            return None

    async def embed_query(self, text: str) -> list[float] | None:
        """Embed a single query text."""
        results = await self.embed([text])
        return results[0] if results else None

    @staticmethod
    def cosine_similarity(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(x * x for x in b) ** 0.5
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    def pack(self, embedding: list[float]) -> str:
        return json.dumps(embedding, ensure_ascii=False)

    def unpack(self, json_str: str | None) -> list[float] | None:
        if not json_str:
            return None
        try:
            return json.loads(json_str)
        except (json.JSONDecodeError, TypeError):
            return None
