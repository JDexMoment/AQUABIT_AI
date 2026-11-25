"""Расчет доменных инженерных метрик качества ИИ-диагностики (вместо BLEU/ROUGE)."""
from __future__ import annotations
import re
from typing import Any
from llm.post_validator import validate_ai_report_against_facts


def evaluate_report_quality_metrics(ai_text: str, facts: dict[str, Any]) -> dict[str, Any]:
    """Оценивает отчёт по 5 доменным метрикам:
    1. Factual ID Precision (точность ссылок на реальные объекты сети);
    2. Physical Invariant Compliance (соблюдение законов гидравлики: ΔP vs вакуум, потери воды, BEP);
    3. Normative Grounding Score (ссылки на пункты СП 31.13330.2012);
    4. UI Link Integrity (отсутствие битых плейсхолдеров #ID и вложенных тегов);
    5. Overall Engineering Score (0..100).
    """
    val = validate_ai_report_against_facts(ai_text, facts)
    id_prec = float(val["id_precision"])

    # Проверка отсутствия битых ссылок и плейсхолдеров
    has_placeholder_leak = bool(re.search(r"#ID\b|\(table:ID\)|\(map:ID\)", ai_text))
    has_backtick_link = bool(re.search(r"`\[[^\]]+\]\([^)]+\)`", ai_text))
    ui_link_integrity = 1.0 if (not has_placeholder_leak and not has_backtick_link) else 0.5

    # Проверка нормативной привязки к СП 31.13330
    norm_cnt = val["normative_citations_count"]
    norm_score = min(1.0, norm_cnt / 3.0) if norm_cnt > 0 else 0.0

    # Проверка соблюдения физических инвариантов
    phys_penalties = len(val["issues"])
    phys_score = max(0.0, 1.0 - 0.25 * phys_penalties)

    overall = round((id_prec * 0.35 + phys_score * 0.35 + norm_score * 0.15 + ui_link_integrity * 0.15) * 100.0, 1)
    return {
        "overall_engineering_score": overall,
        "factual_id_precision": id_prec,
        "physical_invariant_compliance": round(phys_score, 3),
        "normative_grounding_score": round(norm_score, 3),
        "ui_link_integrity": ui_link_integrity,
        "validation_details": val,
    }
