"""Модули постобработки, валидации и схем ответов локальной LLM."""
from __future__ import annotations
from llm.post_validator import (
    DIAGNOSIS_JSON_SCHEMA,
    collect_known_model_ids,
    sanitize_ai_report_text,
    validate_ai_report_against_facts,
)

__all__ = [
    "DIAGNOSIS_JSON_SCHEMA",
    "collect_known_model_ids",
    "sanitize_ai_report_text",
    "validate_ai_report_against_facts",
]
