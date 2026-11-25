"""Поисковый RAG-ретривер по базе СП 31.13330 в Qdrant для привязки норм к отчёту и правилам."""
from __future__ import annotations
from pathlib import Path
from typing import Any
from rag.ingest import COLLECTION_NAME, compute_dense_embedding, ingest_sp31_into_qdrant


def _cosine_sim(v1: list[float], v2: list[float]) -> float:
    return sum(a * b for a, b in zip(v1, v2))


class QdrantNormativeRetriever:
    """Гибридный ретривер СП 31.13330 на базе Qdrant (векторный поиск + фильтр Applies_to и diag_codes)."""

    def __init__(
        self,
        *,
        pdf_path: Path | str | None = None,
        qdrant_path: Path | str | None = None,
        qdrant_url: str | None = None,
        collection_name: str = COLLECTION_NAME,
    ) -> None:
        state = ingest_sp31_into_qdrant(
            pdf_path=pdf_path,
            qdrant_path=qdrant_path,
            qdrant_url=qdrant_url,
            collection_name=collection_name,
        )
        self.collection_name = collection_name
        self.qdrant_active: bool = state["qdrant_active"]
        self.client: Any = state["client"]
        self.points: list[dict[str, Any]] = state["points"]

    def search(
        self,
        query: str,
        *,
        applies_to: str | None = None,
        diag_code: str | None = None,
        top_k: int = 3,
    ) -> list[dict[str, Any]]:
        """Ищет релевантные пункты СП 31.13330 в Qdrant по тексту запроса, типу объекта и коду правила."""
        q_vec = compute_dense_embedding(query)
        q_lower = query.lower().replace("ё", "е")

        # Если запущен qdrant_client, выполняем векторный запрос через Qdrant API
        qdrant_scores: dict[int, float] = {}
        if self.qdrant_active and self.client is not None:
            try:
                if hasattr(self.client, "query_points"):
                    res = self.client.query_points(
                        collection_name=self.collection_name,
                        query=q_vec,
                        limit=max(top_k * 3, 10),
                    )
                    for hit in getattr(res, "points", []):
                        qdrant_scores[int(hit.id)] = float(hit.score)
                elif hasattr(self.client, "search"):
                    hits = self.client.search(
                        collection_name=self.collection_name,
                        query_vector=q_vec,
                        limit=max(top_k * 3, 10),
                    )
                    for hit in hits:
                        qdrant_scores[int(hit.id)] = float(hit.score)
            except Exception:
                qdrant_scores = {}

        scored: list[tuple[float, dict[str, Any]]] = []
        for pt in self.points:
            payload = pt["payload"]
            if applies_to and applies_to not in payload.get("Applies_to", []):
                continue
            vec_score = qdrant_scores.get(pt["id"], _cosine_sim(q_vec, pt["vector"]))
            bonus = 0.0
            if diag_code and diag_code in (payload.get("diag_codes") or []):
                bonus += 0.65
            for kw in payload.get("keywords") or []:
                if kw.lower().replace("ё", "е") in q_lower:
                    bonus += 0.15
            if payload.get("clause_id", "").lower() in q_lower:
                bonus += 0.45
            total_score = round(vec_score + bonus, 4)
            scored.append((total_score, {**payload, "score": total_score}))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [item for _s, item in scored[:top_k]]

    def get_by_diag_code(self, rule_id: str) -> list[dict[str, Any]]:
        """Возвращает пункты СП 31.13330, напрямую связанные с правилом онтологии (R01..R17)."""
        matched = []
        for pt in self.points:
            payload = pt["payload"]
            if rule_id in (payload.get("diag_codes") or []):
                matched.append(payload)
        return matched


_DEFAULT_RETRIEVER: QdrantNormativeRetriever | None = None


def get_default_normative_retriever(
    pdf_path: Path | str | None = None,
    qdrant_path: Path | str | None = None,
) -> QdrantNormativeRetriever:
    global _DEFAULT_RETRIEVER
    if _DEFAULT_RETRIEVER is None or pdf_path is not None or qdrant_path is not None:
        default_pdf = Path("public/assets/aistudio/___31.13330.2012_-______48cf50a4.pdf")
        resolved_pdf = Path(pdf_path) if pdf_path else (default_pdf if default_pdf.exists() else None)
        _DEFAULT_RETRIEVER = QdrantNormativeRetriever(
            pdf_path=resolved_pdf,
            qdrant_path=qdrant_path,
        )
    return _DEFAULT_RETRIEVER


def retrieve_norms_for_facts(
    facts: dict[str, Any],
    triggered_rules: list[dict[str, Any]] | None = None,
    retriever: QdrantNormativeRetriever | None = None,
    top_k: int | None = None,
) -> list[dict[str, Any]]:
    """Подбирает точные чанки СП 31.13330 из Qdrant под сработавшие диагностические правила и факты города."""
    ret = retriever or get_default_normative_retriever()
    selected_by_id: dict[str, dict[str, Any]] = {}

    # Обязательно включаем базовые нормы по свободному напору (п. 5.11, п. 5.13), резерву насосов (п. 10.3) и РЧВ/потерям (п. 12.1-12.5)
    pa = facts.get("pressure_analysis") or {}
    pma = facts.get("pumps_analysis") or {}
    ppa = facts.get("pipes_analysis") or {}
    queries: list[tuple[str, str]] = []
    if pa.get("real_required_pressure_deficit_count", 0) > 0:
        queries.append(("минимальный свободный напор по этажности 10 м + 4 м местные ПНС п. 5.11", "node"))
    if pa.get("excess_over_60m_count", 0) > 0:
        queries.append(("максимальный свободный напор 60 м регуляторы давления PRV п. 5.13", "node"))
    if pma.get("total_pumps", 0) > 0:
        queries.append(("резервные агрегаты Таблица 23 холодный резерв ВЫКЛ зона BEP п. 10.3", "pump"))
    if ppa.get("bottleneck_pipes_count", 0) > 0:
        queries.append(("удельные и полные потери напора Хейзен-Уильямс скорость потока п. 11.38", "pipe"))
    if ppa.get("low_velocity_under_01ms_count", 0) > 0 or ppa.get("closed_zero_flow_pipes_count", 0) > 0:
        queries.append(("кольцевые сети тупиковые линии минимальный диаметр 100 мм пожарные гидранты п. 11.5", "pipe"))
    queries.append(("емкости РЧВ суточный баланс потери воды физические и коммерческие п. 12.1", "tank"))

    for q_text, app_to in queries:
        for hit in ret.search(q_text, applies_to=app_to, top_k=1):
            cid = hit["clause_id"]
            if cid not in selected_by_id:
                selected_by_id[cid] = {
                    "clause_id": cid,
                    "document": hit.get("document", "СП 31.13330.2012"),
                    "section": hit.get("section", ""),
                    "title": hit.get("title", ""),
                    "norm_threshold": hit.get("norm_threshold", ""),
                    "text": hit.get("text", ""),
                    "excerpt": hit.get("text", ""),
                    "matched_rules": list(hit.get("diag_codes", [])),
                }

    for rule in (triggered_rules or []):
        rid = str(rule.get("rule_id") or "")
        for clause in ret.get_by_diag_code(rid):
            cid = clause["clause_id"]
            if cid not in selected_by_id:
                selected_by_id[cid] = {
                    "clause_id": cid,
                    "document": clause.get("document", "СП 31.13330.2012"),
                    "section": clause.get("section", ""),
                    "title": clause.get("title", ""),
                    "norm_threshold": clause.get("norm_threshold", ""),
                    "text": clause.get("text", ""),
                    "excerpt": clause.get("text", ""),
                    "matched_rules": [rid],
                }
            elif rid not in selected_by_id[cid]["matched_rules"]:
                selected_by_id[cid]["matched_rules"].append(rid)

    items = list(selected_by_id.values())
    return items[:top_k] if top_k and top_k > 0 else items
