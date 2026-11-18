"""Модуль интеграции онтологии ПК «Аквабит» (EPANET-AI v3.2, строго RDF Turtle + графовый контекст для ИИ)."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_ONTOLOGY_TTL = Path("data/aquabit_hydraulic_ontology_v3.ttl")

_DOMAIN_KEY_BY_TTL_ID: dict[str, str] = {
    "DomainPressureDeficit": "pressure_deficit",
    "DomainPumpImbalance": "pump_imbalance",
    "DomainHydraulicBottlenecks": "hydraulic_bottlenecks",
    "DomainWaterStagnation": "water_stagnation",
    "DomainValvesAndStorage": "valves_and_storage",
    "DomainDiurnalImbalance": "diurnal_imbalance",
}

_SEVERITY_RANK: dict[str, int] = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
    "info": 4,
}


def clean_object_id(raw_id: str | None) -> str:
    if not raw_id:
        return ""
    s = str(raw_id).strip()
    return re.sub(r"^[ceputvk](\d+)$", r"\1", s, flags=re.IGNORECASE)


def clean_object_name(raw_name: str | None) -> str:
    if not raw_name:
        return ""
    return re.sub(r"\s*\(\s*Google\s*\)\s*", "", str(raw_name), flags=re.IGNORECASE).strip()


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
        Path(__file__).resolve().parent.parent / "data" / "aquabit_hydraulic_ontology_v3.ttl",
        Path(__file__).resolve().parent.parent.parent / "data" / "aquabit_hydraulic_ontology_v3.ttl",
    ]
    for data_dir in (Path("data"), Path(__file__).resolve().parent.parent / "data"):
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


def evaluate_ontology_rules(facts: dict[str, Any], ontology: dict[str, Any]) -> list[dict[str, Any]]:
    triggered: list[dict[str, Any]] = []
    pa = facts.get("pressure_analysis", {})
    ppa = facts.get("pipes_analysis", {})
    pma = facts.get("pumps_analysis", {})
    vra = facts.get("valves_and_reservoirs_analysis", {})
    wb = facts.get("water_balance", {})
    chart_payload = facts.get("chart_payload") or {}
    inf_map = chart_payload.get("informative_charts") or {}
    rules_by_id = {r["rule_id"]: r for r in ontology.get("diagnostic_rules", [])}

    def add_rule(rule_id: str, matched_objects: str, fallback_diag: str, fallback_domain: str, fallback_sev: str = "medium", fallback_prio: int = 2) -> None:
        base = rules_by_id.get(rule_id, {})
        triggered.append({
            "rule_id": rule_id,
            "priority": base.get("priority", fallback_prio),
            "severity": base.get("severity", fallback_sev),
            "problem_domain": base.get("problem_domain") or fallback_domain,
            "diagnosis": base.get("diagnosis") or fallback_diag,
            "explanation": base.get("explanation", ""),
            "action_in_model": base.get("action_in_model", ""),
            "condition": base.get("condition", ""),
            "matched_objects": matched_objects,
        })

    # R01: Отключенные скважины / тупики с P < 0 и Q = 0
    if pa.get("shutoff_wells_negative_count", 0) > 0:
        wells = pa.get("shutoff_wells_nodes", [])[:3]
        objs = ", ".join(f"{clean_object_id(r.get('node_id'))} (P={r.get('free_pressure_min_m')}м)" for r in wells)
        add_rule(
            "R01_SHUTOFF_WELL_NEGATIVE_PRESSURE",
            f"Всего {pa.get('shutoff_wells_negative_count', 0)} узлов" + (f": {objs}" if objs else ""),
            "Отрицательное давление в отключенной скважине или тупике без отбора",
            "pump_imbalance",
            fallback_sev="info",
            fallback_prio=1,
        )

    # R02: Критический вакуум < -3.0 м у активных потребителей
    if pa.get("data_error_below_minus_3m_count", 0) > 0:
        err_nodes = pa.get("worst_data_error_nodes", [])[:3]
        objs = ", ".join(f"{clean_object_id(r.get('node_id'))} (P={r.get('free_pressure_min_m')}м)" for r in err_nodes)
        add_rule(
            "R02_EXTREME_DATA_ERROR_PRESSURE",
            f"Всего {pa.get('data_error_below_minus_3m_count', 0)} активных узлов" + (f": {objs}" if objs else ""),
            "Критическое отрицательное давление (< -3.0 м) — ошибка отметки Z или отсечение участка",
            "pressure_deficit",
            fallback_sev="critical",
            fallback_prio=1,
        )

    # R03: Дефицит требуемого напора по этажности
    if pa.get("real_required_pressure_deficit_count", 0) > 0:
        worst_def = pa.get("worst_deficit_nodes", [])[:3]
        objs = ", ".join(f"{clean_object_id(r.get('node_id'))} (ΔP={r.get('pressure_surplus_deficit_m')}м)" for r in worst_def)
        add_rule(
            "R03_REQUIRED_PRESSURE_DEFICIT",
            objs or f"{pa.get('real_required_pressure_deficit_count', 0)} абонентов",
            "Дефицит свободного напора у абонентов",
            "pressure_deficit",
            fallback_sev="high",
            fallback_prio=2,
        )

    # R04: Гидравлические узкие места (V >= 5 м/с или i >= 15 м/км)
    high_loss = [p for p in ppa.get("top_high_loss_pipes", []) if (p.get("velocity_max_m_s") or 0) >= 5.0 or (p.get("headloss_gradient_m_km") or 0) >= 15.0]
    if high_loss:
        bneck_total = ppa.get("bottleneck_pipes_count") or len(high_loss)
        objs = f"Всего {bneck_total} участков: " + ", ".join(
            f"{clean_object_id(p.get('pipe_id'))} (узлы {p.get('start_node')}→{p.get('end_node')}, i={p.get('headloss_gradient_m_km')} м/км, V={p.get('velocity_max_m_s')} м/с)"
            for p in high_loss[:3]
        )
        add_rule(
            "R04_HIGH_VELOCITY_BOTTLENECK",
            objs,
            "Гидравлические узкие места и завышенные удельные потери напора",
            "hydraulic_bottlenecks",
            fallback_sev="high",
            fallback_prio=2,
        )

    # R05: Активные малопроточные участки (0 < V < 0.1 м/с)
    if ppa.get("low_velocity_under_01ms_count", 0) > 0:
        add_rule(
            "R05_LOW_VELOCITY_PIPES",
            f"Участков с 0 < V < 0.1 м/с: {ppa.get('low_velocity_under_01ms_count', 0)} шт.",
            "Малопроточные зоны (< 0.1 м/с) и риск застоя воды",
            "water_stagnation",
            fallback_sev="medium",
            fallback_prio=4,
        )

    # R06: Недогрузка сетевых/повысительных насосов
    booster_under = [p for p in pma.get("underloaded_pumps", []) if not p.get("is_borehole")]
    if booster_under:
        objs = ", ".join(
            f"{clean_object_id(p.get('pump_id'))} (коллектор #{p.get('end_node')}, Pвых={p.get('collector_pressure_m')}м)"
            for p in booster_under[:4]
        )
        add_rule(
            "R06_BOOSTER_PUMP_UNDERLOAD_VALVE_CHECK",
            objs,
            "Недогрузка сетевого насоса (повышенное сопротивление за напорным узлом)",
            "pump_imbalance",
            fallback_sev="medium",
            fallback_prio=2,
        )

    # R07: Недогрузка скважинных насосов
    borehole_under = [p for p in pma.get("underloaded_pumps", []) if p.get("is_borehole")]
    if borehole_under:
        objs = ", ".join(
            f"{clean_object_id(p.get('pump_id'))} (скважина #{p.get('start_node')} → коллектор #{p.get('end_node')}, Pвых={p.get('collector_pressure_m')}м)"
            for p in borehole_under[:4]
        )
        add_rule(
            "R07_BOREHOLE_PUMP_UNDERLOAD",
            objs,
            "Недогрузка скважинного насоса (перепад высот со скважиной или противодавление коллектора)",
            "pump_imbalance",
            fallback_sev="medium",
            fallback_prio=2,
        )

    # R08: Перегрузка насосов (> 115% Q_ном)
    overloaded = pma.get("overloaded_pumps", [])
    if overloaded:
        objs = ", ".join(
            f"{clean_object_id(p.get('pump_id'))} [{p.get('pump_type')}, узел #{p.get('end_node')} P={p.get('collector_pressure_m')}м]"
            for p in overloaded[:4]
        )
        add_rule(
            "R08_PUMP_OVERLOAD_OUTLET_REGULATION",
            objs,
            "Работа насоса правее зоны BEP (регулирование давления на нагнетании)",
            "pump_imbalance",
            fallback_sev="high",
            fallback_prio=2,
        )

    # R09: Индивидуальные аномалии модели
    unique_anom = facts.get("unique_model_anomalies", [])
    if unique_anom:
        add_rule(
            "R09_ANOMALY_DETECTOR_CONTRAST",
            "; ".join(unique_anom[:2]),
            "Индивидуальная аномалия модели",
            "hydraulic_bottlenecks",
            fallback_sev="high",
            fallback_prio=1,
        )

    # R10: Работающие скважинные насосы 1-го подъема
    if pma.get("borehole_active_count", 0) > 0:
        add_rule(
            "R10_BOREHOLE_PUMP_WELLHEAD",
            f"Активных скважинных агрегатов: {pma.get('borehole_active_count', 0)} из {pma.get('borehole_total_count', 0)}",
            "Скважинные насосы 1-го подъема (подпор определяется уровнем источника)",
            "pump_imbalance",
            fallback_sev="info",
            fallback_prio=3,
        )

    # R11: Деградация шероховатости C < 80 с высокими потерями >= 20 м/км
    degraded_pipes = ppa.get("degraded_high_loss_pipes", [])
    if degraded_pipes:
        objs = ", ".join(
            f"{clean_object_id(p.get('pipe_id'))} (C={p.get('hazen_williams_c')}, i={p.get('headloss_gradient_m_km')} м/км)"
            for p in degraded_pipes[:3]
        )
        add_rule(
            "R11_HAZEN_WILLIAMS_DEGRADATION",
            objs,
            "Деградация пропускной способности (C < 80) с высокими потерями (>= 20 м/км)",
            "hydraulic_bottlenecks",
            fallback_sev="high",
            fallback_prio=2,
        )

    # R12: Суточная неравномерность и разрыв установленной мощности и расхода сети
    if abs(wb.get("installed_vs_demand_flow_gap_m3_h", 0)) > 10 or wb.get("peak_factor_k_hour", 1.0) > 1.3:
        add_rule(
            "R12_DIURNAL_HYDROGRAPH_IMBALANCE",
            f"Разрыв мощности ({wb.get('pumps_installed_capacity_m3_h', 0):.1f} м³/ч) и расхода сети ({wb.get('peak_network_demand_m3_h', 0):.1f} м³/ч), K_час={wb.get('peak_factor_k_hour', 1.0)}",
            "Суточная неравномерность и разрыв мощности с расходом сети",
            "diurnal_imbalance",
            fallback_sev="low",
            fallback_prio=3,
        )

    # R13: Фильтрация статических/пустых графиков
    hidden_charts = [k for k, v in inf_map.items() if v is False]
    if hidden_charts:
        add_rule(
            "R13_STATIC_UNINFORMATIVE_CHART_FILTER",
            f"Исключены неинформативные графики: {', '.join(hidden_charts)}",
            "Фильтрация статических и неинформативных графиков",
            "diurnal_imbalance",
            fallback_sev="info",
            fallback_prio=5,
        )

    # R14: Клапаны PRV/PSV/FCV и емкости РЧВ
    if vra.get("valves_count", 0) > 0 or vra.get("tanks_count", 0) > 0:
        add_rule(
            "R14_VALVES_AND_TANKS_REGULATION",
            f"Клапанов: {vra.get('valves_count', 0)}, РЧВ: {vra.get('tanks_count', 0)}, Источников: {vra.get('sources_count', 0)}",
            "Режим работы регулирующих клапанов (PRV/PSV/FCV) и емкостей РЧВ",
            "valves_and_storage",
            fallback_sev="medium",
            fallback_prio=2,
        )

    # R15: Закрытые задвижками участки (Q = 0)
    if ppa.get("closed_zero_flow_pipes_count", 0) > 0:
        add_rule(
            "R15_CLOSED_GATE_VALVE_PIPE",
            f"Перекрытых труб (Q = 0): {ppa.get('closed_zero_flow_pipes_count', 0)} шт.",
            "Участки с нулевым потоком (Q = 0), перекрытые задвижками",
            "water_stagnation",
            fallback_sev="medium",
            fallback_prio=3,
        )

    # R16: Избыточный напор > 60 м
    if pa.get("excess_over_60m_count", 0) > 0:
        top_exc = pa.get("top_excess_nodes", [])[:3]
        objs = ", ".join(f"{clean_object_id(r.get('node_id'))} (Pmax={r.get('free_pressure_max_m')}м)" for r in top_exc)
        add_rule(
            "R16_EXCESS_PRESSURE_OVER_60M",
            f"Всего {pa.get('excess_over_60m_count', 0)} узлов" + (f": {objs}" if objs else ""),
            "Избыточный свободный напор (> 60 м по СП 31.13330.2021)",
            "pressure_deficit",
            fallback_sev="medium",
            fallback_prio=3,
        )

    # R17: Реверс направления потока
    if ppa.get("reverse_flow_count", 0) > 0:
        add_rule(
            "R17_REVERSE_FLOW_INSTABILITY",
            f"Участков со сменой направления потока: {ppa.get('reverse_flow_count', 0)} шт.",
            "Знакопеременный реверс потока по трубопроводам",
            "hydraulic_bottlenecks",
            fallback_sev="low",
            fallback_prio=4,
        )

    triggered.sort(key=lambda r: (r.get("priority", 9), _SEVERITY_RANK.get(r.get("severity", "medium"), 5)))
    return triggered


def analyze_problem_domains(
    facts: dict[str, Any],
    triggered_rules: list[dict[str, Any]],
    ontology: dict[str, Any] | None = None,
) -> dict[str, Any]:
    pa = facts.get("pressure_analysis", {})
    ppa = facts.get("pipes_analysis", {})
    pma = facts.get("pumps_analysis", {})
    vra = facts.get("valves_and_reservoirs_analysis", {})
    wb = facts.get("water_balance", {})
    chart_payload = facts.get("chart_payload") or {}
    inf_map = chart_payload.get("informative_charts") or {}
    ttl_domains = (ontology or {}).get("problem_domains", {})

    def dom_meta(key: str, default_title: str, default_dash: str) -> tuple[str, str]:
        d = ttl_domains.get(key) or {}
        return d.get("title") or default_title, d.get("recommended_dashboard") or default_dash

    t_pd, dash_pd = dom_meta("pressure_deficit", "Дефицит свободного напора и обеспеченность этажности застройки", "pressureSection")
    t_pm, dash_pm = dom_meta("pump_imbalance", "Режимы работы скважинных и сетевых насосов и баланс установленной мощности", "qhSection")
    t_hb, dash_hb = dom_meta("hydraulic_bottlenecks", "Гидравлические узкие места и завышенные удельные потери напора", "bottlenecksSection")
    t_ws, dash_ws = dom_meta("water_stagnation", "Скоростной режим сети, закрытые задвижками трубы и зоны низких скоростей", "stagnationSection")
    t_vs, dash_vs = dom_meta("valves_and_storage", "Работа регулирующих клапанов (PRV/PSV), источников и резервуаров РЧВ", "valvesSection")
    t_di, dash_di = dom_meta("diurnal_imbalance", "Суточный баланс подачи насосов и реального водопотребления сети", "diurnalSection")

    bneck_cnt = ppa.get("bottleneck_pipes_count") or len(ppa.get("top_high_loss_pipes", []))
    domains: dict[str, dict[str, Any]] = {
        "pressure_deficit": {
            "score": 0,
            "title": t_pd,
            "details": (
                f"{pa.get('real_required_pressure_deficit_count', 0)} абон. с дефицитом, "
                f"{pa.get('data_error_below_minus_3m_count', 0)} с P<-3м, "
                f"{pa.get('excess_over_60m_count', 0)} с P>60м"
            ),
            "worst_items": pa.get("worst_deficit_nodes", [])[:5],
            "recommended_dashboard": dash_pd,
        },
        "pump_imbalance": {
            "score": 0,
            "title": t_pm,
            "details": f"Скважинных: {pma.get('borehole_total_count', 0)}, недогрузка: {len(pma.get('underloaded_pumps', []))}, перегрузка: {len(pma.get('overloaded_pumps', []))}",
            "worst_items": pma.get("overloaded_pumps", [])[:3] + pma.get("underloaded_pumps", [])[:3],
            "recommended_dashboard": dash_pm,
        },
        "hydraulic_bottlenecks": {
            "score": 0,
            "title": t_hb,
            "details": f"Участков с потерями: {bneck_cnt} шт., макс. потери {max((p.get('headloss_gradient_m_km') or 0 for p in ppa.get('top_high_loss_pipes', [])), default=0):.1f} м/км",
            "worst_items": ppa.get("top_high_loss_pipes", [])[:5],
            "recommended_dashboard": dash_hb,
        },
        "water_stagnation": {
            "score": 0,
            "title": t_ws,
            "details": f"{ppa.get('low_velocity_under_01ms_count', 0)} с V<0.10 м/с, {ppa.get('closed_zero_flow_pipes_count', 0)} перекрыто (Q=0)",
            "worst_items": [],
            "recommended_dashboard": dash_ws,
        },
        "valves_and_storage": {
            "score": 0,
            "title": t_vs,
            "details": f"Клапанов: {vra.get('valves_count', 0)}, РЧВ: {vra.get('tanks_count', 0)}, Источников: {vra.get('sources_count', 0)}",
            "worst_items": vra.get("valves_table", [])[:5],
            "recommended_dashboard": dash_vs,
        },
        "diurnal_imbalance": {
            "score": 0,
            "title": t_di,
            "details": f"Разрыв мощности и расхода сети: {wb.get('installed_vs_demand_flow_gap_m3_h', 0):.1f} м³/ч",
            "worst_items": [],
            "recommended_dashboard": dash_di,
        },
    }

    if pa.get("real_required_pressure_deficit_count", 0) > 0 or pa.get("data_error_below_minus_3m_count", 0) > 0 or pa.get("excess_over_60m_count", 0) > 0:
        worst_def_m = abs(min((r.get("pressure_surplus_deficit_m", 0.0) for r in pa.get("worst_deficit_nodes", [])), default=0.0))
        base_pd = 50 + min(50, worst_def_m * 1.8) if pa.get("real_required_pressure_deficit_count", 0) > 0 else 0
        err_bonus = 25 if pa.get("data_error_below_minus_3m_count", 0) > 0 else 0
        exc_bonus = 15 if pa.get("excess_over_60m_count", 0) > 0 else 0
        domains["pressure_deficit"]["score"] = min(100.0, base_pd + err_bonus + exc_bonus)

    if pma.get("overloaded_pumps") or pma.get("underloaded_pumps") or pma.get("borehole_total_count", 0) > 0:
        domains["pump_imbalance"]["score"] = (
            len(pma.get("overloaded_pumps", [])) * 20
            + len(pma.get("underloaded_pumps", [])) * 15
            + (20 if pma.get("borehole_total_count", 0) > 0 else 10)
        )

    if ppa.get("top_high_loss_pipes"):
        top_loss = max((p.get("headloss_gradient_m_km") or 0.0 for p in ppa.get("top_high_loss_pipes", [])), default=0.0)
        if top_loss >= 10.0 or ppa.get("degraded_high_loss_pipes"):
            domains["hydraulic_bottlenecks"]["score"] = 35 + min(60, top_loss * 0.2) + (10 if ppa.get("degraded_high_loss_pipes") else 0)

    if ppa.get("low_velocity_under_01ms_count", 0) > 5 or ppa.get("closed_zero_flow_pipes_count", 0) > 0:
        domains["water_stagnation"]["score"] = 30 + min(30, ppa.get("low_velocity_under_01ms_count", 0) / 20)

    if vra.get("valves_count", 0) > 0 or vra.get("tanks_count", 0) > 0:
        domains["valves_and_storage"]["score"] = 42 if vra.get("valves_count", 0) > 0 else 25

    if inf_map.get("diurnal_balance"):
        domains["diurnal_imbalance"]["score"] = 28

    ranked_domains = sorted(
        [{"domain": k, **v} for k, v in domains.items() if v["score"] > 0],
        key=lambda x: x["score"],
        reverse=True,
    )
    return {
        "ranked_domains": ranked_domains,
        "primary_domain": ranked_domains[0]["domain"] if ranked_domains else "pump_imbalance",
        "has_spatial_geo": bool(chart_payload.get("geo", {}).get("nodes")),
    }


def build_enriched_ai_context(
    facts: dict[str, Any],
    arg2: Any = None,
    arg3: Any = None,
) -> dict[str, Any]:
    """Формирует обогащенный графовый контекст для LLM (с цепочками Скважина -> Насос -> Коллектор -> Труба),
    позволяющий модели делать однозначные инженерные выводы без гадания.
    Поддерживает вызов как (facts, triggered_rules, problem_domains), так и (facts, ranked_domains, triggered_rules)."""
    pa = facts.get("pressure_analysis", {})
    ppa = facts.get("pipes_analysis", {})
    pma = facts.get("pumps_analysis", {})
    under = pma.get("underloaded_pumps", [])
    over = pma.get("overloaded_pumps", [])

    ranked_domains: list[dict[str, Any]] = []
    triggered_rules: list[dict[str, Any]] = []

    for candidate in (arg2, arg3):
        if isinstance(candidate, dict) and "ranked_domains" in candidate:
            ranked_domains = candidate.get("ranked_domains") or []
        elif isinstance(candidate, list) and candidate:
            first = candidate[0]
            if isinstance(first, dict) and ("domain" in first or "score" in first):
                ranked_domains = candidate
            elif isinstance(first, dict) and ("rule_id" in first or "diagnosis" in first):
                triggered_rules = candidate

    def compact_pump(p: dict[str, Any]) -> dict[str, Any]:
        return {
            "pump_id": p.get("clean_id"),
            "type": p.get("pump_type"),
            "source_or_start_node": p.get("start_node"),
            "source_head_m": p.get("source_head_m"),
            "collector_end_node": p.get("end_node"),
            "collector_pressure_m": p.get("collector_pressure_m"),
            "collector_elevation_m": p.get("collector_elevation_m"),
            "connected_out_pipe_id": p.get("connected_out_pipe_id"),
            "connected_out_pipe_loss_m_km": p.get("connected_out_pipe_loss"),
            "q_work_m3_h": p.get("q_working_m3_h"),
            "q_nom_m3_h": p.get("q_nominal_m3_h"),
            "h_work_m": p.get("h_working_m"),
            "exact_diagnosis": p.get("diagnostic_advice"),
        }

    return {
        "city": facts.get("city_name"),
        "ranked_problem_domains": [
            {
                "domain": d.get("domain", ""),
                "score": d.get("score", 0),
                "title": d.get("title", ""),
                "details": d.get("details", ""),
            }
            for d in ranked_domains
            if isinstance(d, dict)
        ],
        "activated_ontology_rules": triggered_rules,
        "unique_anomalies": facts.get("unique_model_anomalies", []),
        "water_balance": facts.get("water_balance"),
        "pressure_kpi": pa.get("pressure_bins"),
        "worst_deficit_nodes_with_pipes": [
            {
                "node_id": r.get("clean_id"),
                "z_m": r.get("elevation_m"),
                "floors": r.get("floors"),
                "p_min_m": r.get("free_pressure_min_m"),
                "dp_deficit_m": r.get("pressure_surplus_deficit_m"),
                "connected_pipes": r.get("connected_pipe_ids"),
                "diagnosis": r.get("diagnostic_advice"),
            }
            for r in pa.get("worst_deficit_nodes", [])[:4]
        ],
        "worst_data_error_nodes": [
            {
                "node_id": r.get("clean_id"),
                "z_m": r.get("elevation_m"),
                "p_min_m": r.get("free_pressure_min_m"),
                "connected_pipes": r.get("connected_pipe_ids"),
                "diagnosis": r.get("diagnostic_advice"),
            }
            for r in pa.get("worst_data_error_nodes", [])[:4]
        ],
        "bottleneck_pipes_kpi": {
            "total_bottleneck_pipes_count": ppa.get("bottleneck_pipes_count", 0),
            "closed_pipes_count": ppa.get("closed_zero_flow_pipes_count", 0),
            "stagnant_pipes_count": ppa.get("low_velocity_under_01ms_count", 0),
            "top_bottlenecks": [
                {
                    "pipe_id": p.get("clean_id"),
                    "start_node": p.get("start_node"),
                    "end_node": p.get("end_node"),
                    "d_mm": p.get("diameter_mm"),
                    "c_hw": p.get("hazen_williams_c"),
                    "v_m_s": p.get("velocity_max_m_s"),
                    "i_m_km": p.get("headloss_gradient_m_km"),
                    "diagnosis": p.get("diagnostic_advice"),
                }
                for p in ppa.get("top_high_loss_pipes", [])[:4]
            ],
        },
        "pumps_causal_chains": {
            "total_pumps": pma.get("total_pumps"),
            "active_pumps": pma.get("active_pumps_count"),
            "borehole_pumps": pma.get("borehole_total_count"),
            "underloaded_count": len(under),
            "overloaded_count": len(over),
            "sample_underloaded_borehole": [compact_pump(p) for p in under if p.get("is_borehole")][:3],
            "sample_underloaded_booster": [compact_pump(p) for p in under if not p.get("is_borehole")][:3],
            "sample_overloaded": [compact_pump(p) for p in over[:3]],
        },
    }


def build_ontology_ai_system_prompt(ontology: dict[str, Any]) -> str:
    rules = ontology.get("diagnostic_rules", [])
    rules_digest = "\n".join(
        f"   - [{r.get('rule_id')}, приор. {r.get('priority')}] {r.get('diagnosis')} -> {r.get('action_in_model')}"
        for r in sorted(rules, key=lambda x: x.get("priority", 9))[:17]
    )
    onto_block = f"\nАКТИВНАЯ БАЗА ПРАВИЛ ОНТОЛОГИИ (RDF Turtle):\n{rules_digest}\n" if rules_digest else ""

    return (
        "Ты — главный инженер-аналитик систем водоснабжения (EPANET-AI v3.2).\n"
        "Твоя задача — составить точный, однозначный технический аудит гидравлической модели города, "
        "опираясь исключительно на предвычисленные цепочки фактов (Источник -> Насос -> Приемный коллектор -> Отходящий трубопровод).\n\n"
        "СТРОГИЕ ПРАВИЛА ИНЖЕНЕРНОЙ ТОЧНОСТИ (БЕЗ ШАБЛОНОВ И ВОДЫ):\n"
        "1. НИКАКИХ ШАБЛОННЫХ ОГОВОРОК И ДВОЙНЫХ ОТРИЦАНИЙ: Категорически запрещено писать фразы вида "
        "«проверять задвижки на входе некорректно (у скважины их нет)» или «проблема ТОЧНО НЕ в задвижке». "
        "Пиши сразу конкретную физическую причину, подтвержденную числами из поля `exact_diagnosis` и параметрами смежных узлов.\n"
        "2. ОДНОЗНАЧНЫЕ ВЫВОДЫ ВМЕСТО «ЛИБО / ЛИБО»: Не пиши «снижение уровня в скважине либо высокое противодавление в коллекторе». "
        "Смотри на давление `collector_pressure_m` в конечном узле насоса (`collector_end_node`) и потери `connected_out_pipe_loss_m_km` в отходящей трубе (`connected_out_pipe_id`): "
        "указывай точный ID скважины (`source_or_start_node`), точный ID узла коллектора (`collector_end_node`) с его давлением P и отметкой Z, и точный ID лимитирующей трубы!\n"
        "3. КОНКРЕТНЫЕ МЕРОПРИЯТИЯ В СЕТИ И В ПК «АКВАБИТ»: Для каждой проблемы разделяй: "
        "(а) инженерное мероприятие на сети (перекладка, закольцовка, настройка ЧРП/PRV) и "
        "(б) действие в модели ПК «Аквабит» (проверка отметки Z в Узел.csv, корректировка коэффициента C или статуса трубы, проверка кривой Q-H).\n"
        "4. ИНТЕРАКТИВНЫЕ ССЫЛКИ НА ОБЪЕКТЫ И ФИЛЬТРЫ ТАБЛИЦ:\n"
        "   - Любой ID узла, скважины, коллектора, трубы, насоса или клапана пиши в обратных кавычках (например: узел `3459771`, коллектор `3466812`, водовод `3466703`, насос `3466811`).\n"
        "   - Добавляй прямые ссылки для фильтрации и сортировки таблиц в формате `[Текст ссылки](filter:ТАБЛИЦА:ФИЛЬТР)`, где доступные коды:\n"
        "     * `filter:nodes:data_error` (узлы с P < -3.0 м)\n"
        "     * `filter:nodes:deficit` (абоненты с дефицитом ΔP < 0)\n"
        "     * `filter:nodes:excess` (узлы с избытком P > 60 м)\n"
        "     * `filter:pipes:bottlenecks` (узкие места с высокими потерями i)\n"
        "     * `filter:pipes:closed` (перекрытые участки Q = 0)\n"
        "     * `filter:pipes:stagnant` (малопроточные участки V < 0.10 м/с)\n"
        "     * `filter:pumps:underload` (насосы в недогрузке)\n"
        "     * `filter:pumps:overload` (насосы в перегрузке)\n"
        f"{onto_block}"
        "5. В конце ответа выведи JSON-блок `[SELECTED_DASHBOARDS]` с ключами `section_order` (массив ID из `['qhSection', 'pressureSection', 'bottlenecksSection', 'stagnationSection', 'valvesSection', 'diurnalSection']`) и `dashboards` (`[{\"id\": \"...\"}]`).\n"
    )


def extract_ai_dashboards_and_content(raw_ai_text: str, default_dashboards: list[str] | None = None) -> tuple[str, dict[str, Any]]:
    text = (raw_ai_text or "").strip()
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    text = re.sub(r"[\U00010000-\U0010ffff]", "", text)
    text = re.sub(r"[\u2600-\u27bf]", "", text)

    parsed_dashboards: dict[str, Any] = {
        "primary_problem_areas": [],
        "section_order": list(default_dashboards or []),
        "dashboards": [{"id": d} for d in (default_dashboards or [])],
        "chart_notes": {},
    }
    main_text = text

    if "[SELECTED_DASHBOARDS]" in text:
        parts = text.split("[SELECTED_DASHBOARDS]", 1)
        main_text = parts[0].strip()
        json_part = parts[1].strip()
        m = re.search(r"\{.*\}", json_part, flags=re.DOTALL)
        if m:
            clean_json_str = re.sub(r",\s*([}\]])", r"\1", m.group(0).strip())
            try:
                loaded = json.loads(clean_json_str)
                if isinstance(loaded, dict):
                    parsed_dashboards.update(loaded)
            except Exception:
                pass

    sec_order = parsed_dashboards.get("section_order") or []
    dashes = parsed_dashboards.get("dashboards") or []
    if sec_order and not dashes:
        parsed_dashboards["dashboards"] = [{"id": s} for s in sec_order]
    elif dashes and not sec_order:
        parsed_dashboards["section_order"] = [d["id"] for d in dashes if isinstance(d, dict) and d.get("id")]

    main_text = re.sub(r"\(Выбор ИИ под проблемные зоны\)", "", main_text)
    main_text = re.sub(r"\(\s*Google\s*\)", "", main_text)
    return main_text, parsed_dashboards
