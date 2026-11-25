"""Поисковый RAG-ретривер по базе СП 31.13330 в Qdrant для привязки норм к отчёту и правилам."""
from __future__ import annotations
import os
from pathlib import Path
from typing import Any, Callable
from rag.ingest import COLLECTION_NAME, compute_dense_embedding, ingest_sp31_into_qdrant


def _cosine_sim(v1: list[float], v2: list[float]) -> float:
    return sum(a * b for a, b in zip(v1, v2))


def find_sp31_document(search_roots: list[Path] | None = None) -> Path | None:
    """Автоматически ищет локальный файл СП 31.13330 (PDF/TXT/MD) через переменную окружения SP31_PDF_PATH,
    папки data/norms, data/ и корень проекта."""
    env_path = os.environ.get("SP31_PDF_PATH")
    if env_path:
        p = Path(env_path)
        if p.exists() and p.is_file():
            return p

    project_root = Path(__file__).resolve().parents[2]
    roots = search_roots or [
        project_root / "data" / "norms",
        project_root / "data",
        project_root,
    ]
    patterns = (
        "*31.13330*.pdf",
        "*СП*31*.pdf",
        "*sp31*.pdf",
        "*31.13330*.md",
        "*31.13330*.txt",
    )
    for root in roots:
        if not root or not root.exists() or not root.is_dir():
            continue
        for pat in patterns:
            hits = sorted(root.glob(pat))
            if hits:
                return hits[0]
    return None


class QdrantNormativeRetriever:
    """Гибридный ретривер СП 31.13330 на базе Qdrant (векторный поиск + фильтр Applies_to и diag_codes)."""

    def __init__(
        self,
        *,
        pdf_path: Path | str | None = None,
        qdrant_path: Path | str | None = None,
        qdrant_url: str | None = None,
        collection_name: str = COLLECTION_NAME,
        recreate: bool = False,
        client: Any = None,
        embedding_fn: Callable[[str], list[float]] | None = None,
    ) -> None:
        self._embed = embedding_fn or compute_dense_embedding
        state = ingest_sp31_into_qdrant(
            pdf_path=pdf_path,
            qdrant_path=qdrant_path,
            qdrant_url=qdrant_url,
            collection_name=collection_name,
            client=client,
            recreate=recreate,
            embedding_fn=self._embed,
        )
        self.collection_name = collection_name
        self.qdrant_active: bool = state["qdrant_active"]
        self.reused_existing: bool = state.get("reused_existing", False)
        self.client: Any = state["client"]
        self.points: list[dict[str, Any]] = state["points"]

    def close(self) -> None:
        """Освобождает файловую блокировку локального QdrantClient (актуально для повторных запусков в Jupyter)."""
        if self.client is not None and hasattr(self.client, "close"):
            try:
                self.client.close()
            except Exception:
                pass
            self.client = None

    def _build_qdrant_filter(self, applies_to: str | None) -> Any:
        if not applies_to:
            return None
        try:
            from qdrant_client.models import FieldCondition, Filter, MatchValue  # type: ignore[import-not-found]
            return Filter(
                must=[
                    FieldCondition(
                        key="Applies_to",
                        match=MatchValue(value=applies_to),
                    )
                ]
            )
        except Exception:
            return None

    def search(
        self,
        query: str,
        *,
        applies_to: str | None = None,
        diag_code: str | None = None,
        top_k: int = 3,
    ) -> list[dict[str, Any]]:
        """Ищет релевантные пункты СП 31.13330 в Qdrant по тексту запроса, типу объекта и коду правила."""
        q_vec = self._embed(query)
        q_lower = query.lower().replace("ё", "е")

        # Если активен qdrant_client, выполняем векторный запрос с нативным фильтром Applies_to
        qdrant_scores: dict[int, float] = {}
        qdrant_payloads: dict[int, dict[str, Any]] = {}
        if self.qdrant_active and self.client is not None:
            q_filter = self._build_qdrant_filter(applies_to)
            try:
                if hasattr(self.client, "query_points"):
                    res = self.client.query_points(
                        collection_name=self.collection_name,
                        query=q_vec,
                        query_filter=q_filter,
                        limit=max(top_k * 4, 12),
                        with_payload=True,
                    )
                    for hit in getattr(res, "points", []):
                        hid = int(hit.id)
                        qdrant_scores[hid] = float(hit.score)
                        if isinstance(getattr(hit, "payload", None), dict):
                            qdrant_payloads[hid] = hit.payload
                elif hasattr(self.client, "search"):
                    hits = self.client.search(
                        collection_name=self.collection_name,
                        query_vector=q_vec,
                        query_filter=q_filter,
                        limit=max(top_k * 4, 12),
                        with_payload=True,
                    )
                    for hit in hits:
                        hid = int(hit.id)
                        qdrant_scores[hid] = float(hit.score)
                        if isinstance(getattr(hit, "payload", None), dict):
                            qdrant_payloads[hid] = hit.payload
            except Exception:
                qdrant_scores = {}
                qdrant_payloads = {}

        candidates: list[tuple[int, dict[str, Any], list[float] | None]] = []
        if self.points:
            for pt in self.points:
                candidates.append((pt["id"], pt["payload"], pt.get("vector")))
        elif qdrant_payloads:
            for hid, pl in qdrant_payloads.items():
                candidates.append((hid, pl, None))

        scored: list[tuple[float, dict[str, Any]]] = []
        for pid, payload, vec in candidates:
            if applies_to and applies_to not in (payload.get("Applies_to") or []):
                continue
            if pid in qdrant_scores:
                vec_score = qdrant_scores[pid]
            elif vec is not None:
                vec_score = _cosine_sim(q_vec, vec)
            else:
                vec_score = 0.0
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


_RETRIEVER_CACHE: dict[tuple[str, str, str, str], QdrantNormativeRetriever] = {}


def get_default_normative_retriever(
    pdf_path: Path | str | None = None,
    qdrant_path: Path | str | None = None,
    qdrant_url: str | None = None,
    collection_name: str = COLLECTION_NAME,
    recreate: bool = False,
) -> QdrantNormativeRetriever:
    resolved_pdf = Path(pdf_path) if pdf_path else find_sp31_document()
    key = (
        str(resolved_pdf.resolve()) if (resolved_pdf and resolved_pdf.exists()) else "",
        str(Path(qdrant_path).resolve()) if qdrant_path else "",
        str(qdrant_url or ""),
        collection_name,
    )
    if recreate and key in _RETRIEVER_CACHE:
        _RETRIEVER_CACHE[key].close()
        del _RETRIEVER_CACHE[key]
    if key not in _RETRIEVER_CACHE:
        _RETRIEVER_CACHE[key] = QdrantNormativeRetriever(
            pdf_path=resolved_pdf,
            qdrant_path=qdrant_path,
            qdrant_url=qdrant_url,
            collection_name=collection_name,
            recreate=recreate,
        )
    return _RETRIEVER_CACHE[key]


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
