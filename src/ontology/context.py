"""Формирование контекста модели и извлечение настроек дашборда."""
from __future__ import annotations
import json
import re
from typing import Any

def clean_object_id(raw_id: str | None) -> str:
    if not raw_id:
        return ""
    s = str(raw_id).strip()
    return re.sub(r"^[ceputvk](\d+)$", r"\1", s, flags=re.IGNORECASE)

def clean_object_name(raw_name: str | None) -> str:
    if not raw_name:
        return ""
    return re.sub(r"\s*\(\s*Google\s*\)\s*", "", str(raw_name), flags=re.IGNORECASE).strip()

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

    def _clip(val: Any, limit: int = 220) -> Any:
        if not isinstance(val, str):
            return val
        s = re.sub(r"\s+", " ", val).strip()
        return s if len(s) <= limit else (s[: limit - 1].rstrip() + "…")

    def compact_pump(p: dict[str, Any]) -> dict[str, Any]:
        raw = {
            "id": p.get("clean_id"),
            "type": p.get("pump_type"),
            "src_node": p.get("start_node"),
            "src_head_m": p.get("source_head_m"),
            "end_node": p.get("end_node"),
            "end_p_m": p.get("collector_pressure_m"),
            "end_z_m": p.get("collector_elevation_m"),
            "out_pipe": p.get("connected_out_pipe_id"),
            "out_i_m_km": p.get("connected_out_pipe_loss"),
            "q_work": p.get("q_working_m3_h"),
            "q_nom": p.get("q_nominal_m3_h"),
            "h_work": p.get("h_working_m"),
            "diag": _clip(p.get("diagnostic_advice"), 200),
        }
        return {k: v for k, v in raw.items() if v is not None and v != ""}

    compact_rules = [
        {
            "id": r.get("rule_id"),
            "sev": r.get("severity"),
            "dom": r.get("problem_domain"),
            "diag": _clip(r.get("diagnosis"), 140),
            "objs": _clip(r.get("matched_objects"), 180),
        }
        for r in triggered_rules[:10]
        if isinstance(r, dict)
    ]

    wb_raw = facts.get("water_balance") or {}
    compact_wb = {
        k: wb_raw[k]
        for k in (
            "total_nodes_in_model",
            "active_consumer_nodes",
            "total_daily_consumption_m3_day",
            "peak_network_demand_m3_h",
            "peak_factor_k_hour",
            "pumps_installed_capacity_m3_h",
            "pumps_peak_actual_flow_m3_h",
            "installed_vs_demand_flow_gap_m3_h",
        )
        if k in wb_raw and wb_raw[k] is not None
    }

    return {
        "city": facts.get("city_name"),
        "ranked_domains": [
            {
                "domain": d.get("domain", ""),
                "score": round(float(d.get("score", 0)), 1),
                "title": _clip(d.get("title", ""), 90),
                "details": _clip(d.get("details", ""), 140),
            }
            for d in ranked_domains[:6]
            if isinstance(d, dict)
        ],
        "triggered_rules": compact_rules,
        "anomalies": [_clip(a, 180) for a in (facts.get("unique_model_anomalies") or [])[:4]],
        "water_balance": compact_wb,
        "pressure_kpi": pa.get("pressure_bins"),
        "worst_deficit_nodes": [
            {
                "id": r.get("clean_id"),
                "z": r.get("elevation_m"),
                "floors": r.get("floors"),
                "p_min": r.get("free_pressure_min_m"),
                "dp": r.get("pressure_surplus_deficit_m"),
                "pipes": (r.get("connected_pipe_ids") or [])[:4],
                "diag": _clip(r.get("diagnostic_advice"), 160),
            }
            for r in pa.get("worst_deficit_nodes", [])[:4]
        ],
        "worst_data_error_nodes": [
            {
                "id": r.get("clean_id"),
                "z": r.get("elevation_m"),
                "p_min": r.get("free_pressure_min_m"),
                "pipes": (r.get("connected_pipe_ids") or [])[:4],
                "diag": _clip(r.get("diagnostic_advice"), 160),
            }
            for r in pa.get("worst_data_error_nodes", [])[:4]
        ],
        "bottlenecks": {
            "total_count": ppa.get("bottleneck_pipes_count", 0),
            "closed_count": ppa.get("closed_zero_flow_pipes_count", 0),
            "stagnant_count": ppa.get("low_velocity_under_01ms_count", 0),
            "top_pipes": [
                {
                    "id": p.get("clean_id"),
                    "from": p.get("start_node"),
                    "to": p.get("end_node"),
                    "d_mm": p.get("diameter_mm"),
                    "c": p.get("hazen_williams_c"),
                    "v": p.get("velocity_max_m_s"),
                    "i": p.get("headloss_gradient_m_km"),
                    "diag": _clip(p.get("diagnostic_advice"), 150),
                }
                for p in ppa.get("top_high_loss_pipes", [])[:4]
            ],
        },
        "pumps_chains": {
            "total": pma.get("total_pumps"),
            "active": pma.get("active_pumps_count"),
            "borehole": pma.get("borehole_total_count"),
            "underloaded": len(under),
            "overloaded": len(over),
            "under_borehole": [compact_pump(p) for p in under if p.get("is_borehole")][:3],
            "under_booster": [compact_pump(p) for p in under if not p.get("is_borehole")][:3],
            "overloaded_sample": [compact_pump(p) for p in over[:3]],
        },
    }

def build_ontology_ai_system_prompt(ontology: dict[str, Any]) -> str:
    rules = ontology.get("diagnostic_rules", [])

    def _short(s: Any, lim: int = 120) -> str:
        t = re.sub(r"\s+", " ", str(s or "")).strip()
        return t if len(t) <= lim else (t[: lim - 1].rstrip() + "…")

    rules_digest = "\n".join(
        f"- [{r.get('rule_id')}] {_short(r.get('diagnosis'), 95)} -> {_short(r.get('action_in_model'), 115)}"
        for r in sorted(rules, key=lambda x: x.get("priority", 9))[:12]
    )
    onto_block = f"\nКРАТКАЯ ОНТОЛОГИЯ (RDF Turtle):\n{rules_digest}\n" if rules_digest else ""

    return (
        "Ты — главный инженер-аналитик систем водоснабжения (EPANET-AI v3.2).\n"
        "Составь точный технический аудит модели города по цепочкам фактов (Источник -> Насос -> Коллектор -> Отходящая труба).\n\n"
        "ПРАВИЛА ИНЖЕНЕРНОЙ ТОЧНОСТИ:\n"
        "1. Без шаблонных оговорок и двойных отрицаний: не пиши фразы «у скважины нет задвижки» или «проблема ТОЧНО НЕ в задвижке». Сразу называй физическую причину по полям `diag`, `end_p_m`, `out_i_m_km`.\n"
        "2. Однозначные выводы вместо «либо/либо»: указывай точный ID скважины/узла (`src_node`), ID коллектора (`end_node`, его P и Z) и ID лимитирующей трубы (`out_pipe`).\n"
        "3. Для каждой проблемы разделяй: (а) инженерное мероприятие на сети и (б) действие в ПК «Аквабит».\n"
        "4. Интерактивные ссылки (выбирай цель по контексту):\n"
        "   - Если нужно сравнить параметры объекта с другими в таблице (давление, потери, Q-H, уставки) — указывай ID как `#ID` или `[узел #ID](table:ID)` (ведет прямо на строку в таблице).\n"
        "   - Если важно именно пространственное расположение, район, ветка или соседство на схеме — пиши `[узел #ID на карте](map:ID)` (ведет сразу на карту).\n"
        "   - Для проблемных групп обязательно вставляй фильтр-ссылки на таблицу вида `[Показать узлы с вакуумом](filter:nodes:data_error)`. Доступные фильтры: "
        "`filter:nodes:data_error`, `filter:nodes:deficit`, `filter:nodes:excess`, "
        "`filter:pipes:bottlenecks`, `filter:pipes:closed`, `filter:pipes:stagnant`, "
        "`filter:pumps:underload`, `filter:pumps:overload`.\n"
        f"{onto_block}"
        "5. В конце ответа выведи JSON-блок `[SELECTED_DASHBOARDS]` с ключами `section_order` (из `['qhSection', 'pressureSection', 'bottlenecksSection', 'stagnationSection', 'valvesSection', 'diurnalSection']`) и `dashboards` (`[{\"id\": \"...\"}]`).\n"
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
