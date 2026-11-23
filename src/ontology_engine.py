"""Совместимый интерфейс онтологии. Реализация находится в ontology/."""
from ontology.loader import (DEFAULT_ONTOLOGY_TTL, _DOMAIN_KEY_BY_TTL_ID, _split_turtle_statements,
                             _extract_literal, _extract_int, _extract_ref, parse_turtle_ontology,
                             load_hydraulic_ontology)
from ontology.diagnostics import _SEVERITY_RANK, evaluate_ontology_rules, analyze_problem_domains
from ontology.context import (clean_object_id, clean_object_name, build_enriched_ai_context,
                              build_ontology_ai_system_prompt, extract_ai_dashboards_and_content)
