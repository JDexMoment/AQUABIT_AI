"""Векторизация и запись пунктов СП 31.13330 в Qdrant (локальный/серверный или in-memory)."""
from __future__ import annotations
import hashlib
import math
import re
from pathlib import Path
from typing import Any, Callable
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
    recreate: bool = False,
    embedding_fn: Callable[[str], list[float]] | None = None,
) -> dict[str, Any]:
    """Записывает чанки СП 31.13330 по пунктам в Qdrant (или переиспользует существующую коллекцию)."""
    embed = embedding_fn or compute_dense_embedding
    chunks = extract_sp31_pdf_chunks(pdf_path)
    points_data: list[dict[str, Any]] = []
    vec_dim = VECTOR_DIM
    for idx, ch in enumerate(chunks, 1):
        full_text = (
            f"{ch.get('clause_id', '')} {ch.get('title', '')}. "
            f"{ch.get('text', '')} {' '.join(ch.get('keywords', []))}"
        )
        vec = list(embed(full_text))
        if idx == 1 and vec:
            vec_dim = len(vec)
        points_data.append({
            "id": idx,
            "vector": vec,
            "payload": ch,
        })

    qdrant_used = False
    reused_existing = False
    active_client = client
    try:
        from qdrant_client import QdrantClient  # type: ignore[import-not-found]
        from qdrant_client.models import Distance, PayloadSchemaType, PointStruct, VectorParams  # type: ignore[import-not-found]

        if active_client is None:
            if qdrant_url:
                active_client = QdrantClient(url=qdrant_url)
            elif qdrant_path:
                q_dir = Path(qdrant_path)
                q_dir.mkdir(parents=True, exist_ok=True)
                active_client = QdrantClient(path=str(q_dir))
            else:
                active_client = QdrantClient(":memory:")

        exists = bool(active_client.collection_exists(collection_name))
        need_upsert = recreate or not exists
        if exists and not recreate:
            try:
                cnt_res = active_client.count(collection_name=collection_name, exact=True)
                existing_cnt = int(getattr(cnt_res, "count", 0))
                if existing_cnt == len(points_data) and existing_cnt > 0:
                    reused_existing = True
                    need_upsert = False
                else:
                    need_upsert = True
            except Exception:
                need_upsert = True

        if need_upsert:
            if exists:
                active_client.delete_collection(collection_name)
            active_client.create_collection(
                collection_name=collection_name,
                vectors_config=VectorParams(size=vec_dim, distance=Distance.COSINE),
            )
            q_points = [
                PointStruct(id=pt["id"], vector=pt["vector"], payload=pt["payload"])
                for pt in points_data
            ]
            # Записываем батчами для устойчивости при больших PDF
            batch_size = 128
            for start in range(0, len(q_points), batch_size):
                active_client.upsert(collection_name=collection_name, points=q_points[start:start + batch_size])
            for field_name in ("Applies_to", "diag_codes", "clause_id"):
                try:
                    active_client.create_payload_index(
                        collection_name=collection_name,
                        field_name=field_name,
                        field_schema=PayloadSchemaType.KEYWORD,
                    )
                except Exception:
                    pass
        qdrant_used = True
    except ImportError:
        pass

    return {
        "collection_name": collection_name,
        "qdrant_active": qdrant_used,
        "reused_existing": reused_existing,
        "client": active_client,
        "chunks_count": len(points_data),
        "points": points_data,
    }
