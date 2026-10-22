"""Embedding providers — explicit configuration, no silent fallback.

Providers:
  - voyage      : Voyage AI REST API (semantic; requires AGENTOS_VOYAGE_API_KEY)
  - openai      : OpenAI embeddings REST API (semantic; requires AGENTOS_OPENAI_API_KEY)
  - local-hash  : deterministic feature-hash TF vectors. Lexical, offline, suitable
                  for demos/tests. Labeled non-semantic everywhere it appears.

Every stored document records which provider embedded it. Query embedding uses the
same configured provider; if its credentials are missing, embedding fails loudly
(callers may then use lexical ranking, stating so in results).
"""

import hashlib
import math
import re

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger("agentos.embeddings")

LOCAL_HASH_DIM = 512
_WORD = re.compile(r"[a-zA-Z0-9]{2,}")


class EmbeddingError(Exception):
    pass


def _local_hash_embed(texts: list[str]) -> list[list[float]]:
    """Deterministic feature hashing (term frequency, L2-normalized)."""
    vectors = []
    for text in texts:
        vec = [0.0] * LOCAL_HASH_DIM
        for token in _WORD.findall(text.lower()):
            digest = hashlib.md5(token.encode(), usedforsecurity=False).digest()
            index = int.from_bytes(digest[:4], "little") % LOCAL_HASH_DIM
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vec[index] += sign
        norm = math.sqrt(sum(v * v for v in vec))
        vectors.append([v / norm for v in vec] if norm > 0 else vec)
    return vectors


async def _voyage_embed(texts: list[str], input_type: str) -> list[list[float]]:
    settings = get_settings()
    if not settings.voyage_api_key:
        raise EmbeddingError("embeddings provider is 'voyage' but AGENTOS_VOYAGE_API_KEY is unset")
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://api.voyageai.com/v1/embeddings",
            headers={"Authorization": f"Bearer {settings.voyage_api_key}"},
            json={"model": "voyage-3.5", "input": texts, "input_type": input_type},
        )
    if resp.status_code != 200:
        raise EmbeddingError(f"voyage embeddings failed: HTTP {resp.status_code}")
    data = resp.json()["data"]
    return [item["embedding"] for item in sorted(data, key=lambda d: d["index"])]


async def _openai_embed(texts: list[str]) -> list[list[float]]:
    settings = get_settings()
    if not settings.openai_api_key:
        raise EmbeddingError("embeddings provider is 'openai' but AGENTOS_OPENAI_API_KEY is unset")
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://api.openai.com/v1/embeddings",
            headers={"Authorization": f"Bearer {settings.openai_api_key}"},
            json={"model": "text-embedding-3-small", "input": texts},
        )
    if resp.status_code != 200:
        raise EmbeddingError(f"openai embeddings failed: HTTP {resp.status_code}")
    data = resp.json()["data"]
    return [item["embedding"] for item in sorted(data, key=lambda d: d["index"])]


async def embed_texts(texts: list[str], *, input_type: str = "document") -> list[list[float]]:
    """Embed with the configured provider. Raises EmbeddingError on failure."""
    provider = get_settings().embeddings_provider
    if provider == "local-hash":
        return _local_hash_embed(texts)
    if provider == "voyage":
        return await _voyage_embed(texts, input_type)
    if provider == "openai":
        return await _openai_embed(texts)
    raise EmbeddingError(f"unknown embeddings provider: {provider}")


def provider_label() -> str:
    provider = get_settings().embeddings_provider
    if provider == "local-hash":
        return "local-hash (deterministic lexical vectors — not a semantic model)"
    return provider


async def embed_query_safe(query: str) -> tuple[list[float] | None, str]:
    """Embed a query; on failure return (None, reason) instead of raising."""
    try:
        vectors = await embed_texts([query], input_type="query")
        return vectors[0], f"Embeddings: {provider_label()}."
    except EmbeddingError as exc:
        logger.warning("query embedding unavailable", error=str(exc))
        return None, f"Semantic search unavailable ({exc})."
