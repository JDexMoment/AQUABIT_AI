"""Векторизация и запись пунктов СП 31.13330 в Qdrant (локальный/серверный или in-memory)."""
from __future__ import annotations
import hashlib
import math
import re
from pathlib import Path
from typing import Any
from rag.chunker import extract_sp31_pdf_chunks

VECTOR_DIM = 384
COLLECTION_NAME = "sp31_13330_norms"

_TOKEN_RE = re.compile(r"[а-яёa-z0-9]+(?:\.[0-9]+)?", flags=re.IGNORECASE)


def compute_dense_embedding(text: str, dim: int = VECTOR_DIM) -> list[float]:
    """Строит детерминированный L2-нормированный плотный вектор размерности 384
    по символьным и словным n-граммам (feature hashing с весами ключевых терминов).
    Работает полностью автономно без скачивания внешних моделей и совместим с Qdrant Cosine.
    """
    vec = [0.0] * dim
    if not text:
        vec[0] = 1.0
        return vec
    norm_text = text.lower().replace("ё", "е")
    tokens = _TOKEN_RE.findall(norm_text)
    features: list[tuple[str, float]] = []
    for tok in tokens:
        # Повышенный вес для номеров пунктов (5.11, 10.3, 11.5) и технических терминов
        weight = 3.0 if ("." in tok or tok.isdigit()) else 1.5
        features.append((f"w:{tok}", weight))
        if len(tok) >= 4:
            features.append((f"p4:{tok[:4]}", 1.2))
            for i in range(len(tok) - 2):
                features.append((f"c3:{tok[i:i+3]}", 0.6))
    for i in range(len(tokens) - 1):
        features.append((f"bg:{tokens[i]}_{tokens[i+1]}", 1.8))

    for feat, w in features:
        digest = hashlib.md5(feat.encode("utf-8")).digest()
        idx = int.from_bytes(digest[:2], "little") % dim
        sign = 1.0 if (digest[2] & 1) == 0 else -1.0
        vec[idx] += sign * w

    norm = math.sqrt(sum(v * v for v in vec))
    if norm < 1e-12:
        vec[0] = 1.0
        return vec
    return [round(v / norm, 6) for v in vec]


def ingest_sp31_into_qdrant(
    *,
    pdf_path: Path | str | None = None,
    qdrant_path: Path | str | None = None,
    qdrant_url: str | None = None,
    collection_name: str = COLLECTION_NAME,
    client: Any = None,
) -> dict[str, Any]:
    """Записывает чанки СП 31.13330 по пунктам в Qdrant (или возвращает структурированный индекс)."""
    chunks = extract_sp31_pdf_chunks(pdf_path)
    points_data: list[dict[str, Any]] = []
    for idx, ch in enumerate(chunks, 1):
        full_text = (
            f"{ch.get('clause_id', '')} {ch.get('title', '')}. "
            f"{ch.get('text', '')} {' '.join(ch.get('keywords', []))}"
        )
        vec = compute_dense_embedding(full_text)
        points_data.append({
            "id": idx,
            "vector": vec,
            "payload": ch,
        })

    qdrant_used = False
    active_client = client
    try:
        from qdrant_client import QdrantClient  # type: ignore[import-not-found]
        from qdrant_client.models import Distance, PointStruct, VectorParams  # type: ignore[import-not-found]

        if active_client is None:
            if qdrant_url:
                active_client = QdrantClient(url=qdrant_url)
            elif qdrant_path:
                q_dir = Path(qdrant_path)
                q_dir.mkdir(parents=True, exist_ok=True)
                active_client = QdrantClient(path=str(q_dir))
            else:
                active_client = QdrantClient(":memory:")

        if active_client.collection_exists(collection_name):
            active_client.delete_collection(collection_name)
        active_client.create_collection(
            collection_name=collection_name,
            vectors_config=VectorParams(size=VECTOR_DIM, distance=Distance.COSINE),
        )
        q_points = [
            PointStruct(id=pt["id"], vector=pt["vector"], payload=pt["payload"])
            for pt in points_data
        ]
        active_client.upsert(collection_name=collection_name, points=q_points)
        qdrant_used = True
    except ImportError:
        pass

    return {
        "collection_name": collection_name,
        "qdrant_active": qdrant_used,
        "client": active_client,
        "chunks_count": len(points_data),
        "points": points_data,
    }
