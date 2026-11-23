"""Загрузка и разбор TTL-онтологии."""
from __future__ import annotations
import logging
import re
from pathlib import Path
from typing import Any
logger = logging.getLogger(__name__)
# Сохраняем исходные пути поиска, как если бы загрузчик оставался в src/.
_LEGACY_MODULE_FILE = Path(__file__).resolve().parent.parent / "ontology_engine.py"

DEFAULT_ONTOLOGY_TTL = Path("data/aquabit_hydraulic_ontology_v3.ttl")

_DOMAIN_KEY_BY_TTL_ID: dict[str, str] = {
    "DomainPressureDeficit": "pressure_deficit",
    "DomainPumpImbalance": "pump_imbalance",
    "DomainHydraulicBottlenecks": "hydraulic_bottlenecks",
    "DomainWaterStagnation": "water_stagnation",
    "DomainValvesAndStorage": "valves_and_storage",
    "DomainDiurnalImbalance": "diurnal_imbalance",
}

def _split_turtle_statements(text: str) -> list[str]:
    """Разбивает Turtle-файл на логические утверждения, не разрывая строковые литералы."""
    statements: list[str] = []
    buf: list[str] = []
    in_quotes = False
    escape = False
    in_comment = False

    for ch in text:
        if in_comment:
            if ch == "\n":
                in_comment = False
                buf.append(ch)
            continue
        if escape:
            buf.append(ch)
            escape = False
            continue
        if ch == "\\" and in_quotes:
            buf.append(ch)
            escape = True
            continue
        if ch == '"':
            in_quotes = not in_quotes
            buf.append(ch)
            continue
        if ch == "#" and not in_quotes:
            in_comment = True
            continue
        if ch == "." and not in_quotes:
            stmt = "".join(buf).strip()
            if stmt:
                statements.append(stmt)
            buf = []
            continue
        buf.append(ch)

    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements

def _extract_literal(body: str, prop: str) -> str:
    m = re.search(rf':{prop}\s+"((?:[^"\\]|\\.)*)"(?:@[a-zA-Z\-]+)?', body)
    return m.group(1).replace('\\"', '"') if m else ""

def _extract_int(body: str, prop: str, default: int = 2) -> int:
    m = re.search(rf":{prop}\s+(\d+)", body)
    return int(m.group(1)) if m else default

def _extract_ref(body: str, prop: str) -> str:
    m = re.search(rf":{prop}\s+:(\w+)", body)
    return m.group(1) if m else ""

def parse_turtle_ontology(ttl_path: Path) -> dict[str, Any]:
    text = ttl_path.read_text(encoding="utf-8")
    statements = _split_turtle_statements(text)

    rules: list[dict[str, Any]] = []
    domains: dict[str, dict[str, Any]] = {}
    version_info = "3.2.0"

    for stmt in statements:
        if stmt.startswith("@prefix"):
            continue
        if "a owl:Ontology" in stmt:
            ver_m = re.search(r'owl:versionInfo\s+"([^"]+)"', stmt)
            if ver_m:
                version_info = ver_m.group(1)
            continue

        m_dom = re.match(r"^:(\w+)\s+a\s+:HydraulicProblemDomain\s*;([\s\S]+)$", stmt)
        if m_dom:
            ttl_id, body = m_dom.group(1), m_dom.group(2)
            lbl_m = re.search(r'rdfs:label\s+"([^"]+)"', body)
            dom_key = lbl_m.group(1) if lbl_m else _DOMAIN_KEY_BY_TTL_ID.get(ttl_id, ttl_id)
            domains[dom_key] = {
                "ttl_id": ttl_id,
                "domain": dom_key,
                "title": _extract_literal(body, "domainTitle") or dom_key,
                "recommended_dashboard": _extract_literal(body, "dashboardSection") or "pressureSection",
                "order": _extract_int(body, "domainOrder", default=99),
            }
            continue

        m_rule = re.match(r"^:(\w+)\s+a\s+:DiagnosticRule\s*;([\s\S]+)$", stmt)
        if m_rule:
            rid, body = m_rule.group(1), m_rule.group(2)
            dom_ref = _extract_ref(body, "belongsToDomain")
            dom_key = _DOMAIN_KEY_BY_TTL_ID.get(dom_ref, dom_ref)
            rules.append({
                "rule_id": rid,
                "priority": _extract_int(body, "rulePriority", default=2),
                "severity": _extract_literal(body, "severityLevel") or "medium",
                "problem_domain": dom_key,
                "domain_ref": dom_ref,
                "condition": _extract_literal(body, "conditionFormula"),
                "diagnosis": _extract_literal(body, "diagnosisText") or rid,
                "explanation": _extract_literal(body, "physicalExplanation"),
                "action_in_model": _extract_literal(body, "engineeringAction"),
            })

    return {
        "meta": {
            "system": "ПК «Аквабит»",
            "domain": "Гидравлика (RDF Turtle)",
            "version": version_info,
            "source_path": str(ttl_path),
        },
        "problem_domains": domains,
        "diagnostic_rules": rules,
        "format": "ttl",
    }

def load_hydraulic_ontology(ontology_path: str | Path | None = None) -> dict[str, Any]:
    candidates = [
        Path(ontology_path) if ontology_path else None,
        DEFAULT_ONTOLOGY_TTL,
        Path("data/aquabit_hydraulic_ontology_v3.ttl"),
        _LEGACY_MODULE_FILE.parent.parent / "data" / "aquabit_hydraulic_ontology_v3.ttl",
        _LEGACY_MODULE_FILE.parent.parent.parent / "data" / "aquabit_hydraulic_ontology_v3.ttl",
    ]
    for data_dir in (Path("data"), _LEGACY_MODULE_FILE.parent.parent / "data"):
        if data_dir.exists() and data_dir.is_dir():
            candidates.extend(sorted(data_dir.glob("*.ttl")))

    for p in candidates:
        if p and p.exists() and p.is_file():
            try:
                if p.suffix.lower() == ".ttl":
                    return parse_turtle_ontology(p)
                logger.warning("Загружен не-TTL файл онтологии %s; ожидается формат .ttl", p)
            except Exception as e:
                logger.warning("Ошибка чтения TTL-онтологии из %s: %s", p, e)
    return {"meta": {"system": "ПК «Аквабит»"}, "problem_domains": {}, "diagnostic_rules": [], "format": "ttl"}
