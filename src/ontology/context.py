"""Формирование контекста модели (с RAG-слоем СП 31.13330 в Qdrant) и извлечение настроек дашборда."""
from __future__ import annotations
import json
import re
from typing import Any

from processing.identifiers import clean_object_id, clean_object_name

try:
    from rag.retriever import retrieve_norms_for_facts
except Exception:
    retrieve_norms_for_facts = None  # type: ignore[assignment]

try:
    from llm.post_validator import sanitize_ai_report_text
except Exception:
    sanitize_ai_report_text = None  # type: ignore[assignment]


def build_enriched_ai_context(
    facts: dict[str, Any],
    arg2: Any = None,
    arg3: Any = None,
) -> dict[str, Any]:
    """Формирует обогащенный графовый контекст для LLM (с цепочками Скважина -> Насос -> Коллектор -> Труба
    и нормативными пунктами СП 31.13330 из Qdrant RAG), позволяющий модели делать однозначные инженерные выводы.
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

    def _clip(val: Any, limit: int = 240) -> Any:
        if not isinstance(val, str):
            return val
        s = re.sub(r"\s+", " ", val).strip()
        return s if len(s) <= limit else (s[: limit - 1].rstrip() + "…")

    def compact_pump(p: dict[str, Any]) -> dict[str, Any]:
        raw = {
            "id": p.get("clean_id"),
            "name": p.get("name"),
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
            "diag": _clip(p.get("diagnostic_advice"), 220),
        }
        return {k: v for k, v in raw.items() if v is not None and v != ""}

    compact_rules = [
        {
            "id": r.get("rule_id"),
            "sev": r.get("severity"),
            "dom": r.get("problem_domain"),
            "diag": _clip(r.get("diagnosis"), 150),
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
            "pumps_total_actual_m3_day",
            "first_lift_daily_supply_m3_day",
            "first_lift_peak_flow_m3_h",
            "booster_daily_repumping_m3_day",
            "booster_peak_flow_m3_h",
            "estimated_water_losses_and_unbilled_m3_day",
            "estimated_water_losses_pct",
            "installed_vs_demand_flow_gap_m3_h",
        )
        if k in wb_raw and wb_raw[k] is not None
    }

    normative_clauses: list[dict[str, Any]] = []
    if retrieve_norms_for_facts is not None:
        try:
            normative_clauses = retrieve_norms_for_facts(facts, triggered_rules, top_k=7)
        except Exception:
            normative_clauses = []

    return {
        "city": facts.get("city_name"),
        "data_source": facts.get("data_source_type", "json_digwater"),
        "normative_sp31_rag_clauses": [
            {
                "clause_id": c.get("clause_id"),
                "title": c.get("title"),
                "section": c.get("section"),
                "norm_text": _clip(c.get("text"), 260),
            }
            for c in normative_clauses
        ],
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
        "anomalies": [_clip(a, 190) for a in (facts.get("unique_model_anomalies") or [])[:4]],
        "water_balance": compact_wb,
        "pressure_kpi": pa.get("pressure_bins"),
        "worst_deficit_nodes": [
            {
                "id": r.get("clean_id"),
                "name": r.get("name"),
                "z": r.get("elevation_m"),
                "floors": r.get("floors"),
                "req_p_m": r.get("required_pressure_m"),
                "p_min": r.get("free_pressure_min_m"),
                "dp": r.get("pressure_surplus_deficit_m"),
                "dp_abs_m": r.get("pressure_deficit_abs_m"),
                "is_vacuum": r.get("is_vacuum", False),
                "pipes": (r.get("connected_pipe_ids") or [])[:4],
                "diag": _clip(r.get("diagnostic_advice"), 190),
            }
            for r in pa.get("worst_deficit_nodes", [])[:4]
        ],
        "worst_data_error_nodes": [
            {
                "id": r.get("clean_id"),
                "name": r.get("name"),
                "z": r.get("elevation_m"),
                "p_min": r.get("free_pressure_min_m"),
                "pipes": (r.get("connected_pipe_ids") or [])[:4],
                "diag": _clip(r.get("diagnostic_advice"), 190),
            }
            for r in pa.get("worst_data_error_nodes", [])[:4]
        ],
        "bottlenecks": {
            "total_count": ppa.get("bottleneck_pipes_count", 0),
            "closed_by_valve_count": ppa.get("closed_by_valve_pipes_count", ppa.get("closed_zero_flow_pipes_count", 0)),
            "zero_flow_open_count": ppa.get("zero_flow_open_pipes_count", 0),
            "closed_count": ppa.get("closed_zero_flow_pipes_count", 0),
            "stagnant_count": ppa.get("low_velocity_under_01ms_count", 0),
            "top_pipes": [
                {
                    "id": p.get("clean_id"),
                    "name": p.get("name"),
                    "from": p.get("start_node"),
                    "to": p.get("end_node"),
                    "d_mm": p.get("diameter_mm"),
                    "l_m": p.get("length_m"),
                    "c": p.get("hazen_williams_c"),
                    "v": p.get("velocity_max_m_s"),
                    "i_m_km": p.get("headloss_gradient_m_km"),
                    "total_loss_m": p.get("total_headloss_m"),
                    "diag": _clip(p.get("diagnostic_advice"), 190),
                }
                for p in ppa.get("top_high_loss_pipes", [])[:4]
            ],
        },
        "pumps_chains": {
            "total": pma.get("total_pumps"),
            "active": pma.get("active_pumps_count"),
            "reserve_inactive": pma.get("reserve_inactive_pumps_count"),
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
        "Ты — главный инженер-аналитик систем водоснабжения (ПК «Аквабит» / EPANET-AI v3.5).\n"
        "Составь точный технический аудит гидравлической модели города по цепочкам фактов (Источник -> Насос -> Коллектор -> Отходящая труба) "
        "с обязательной опорой на нормативные пункты СП 31.13330.2012 / СП 31.13330.2021 из блока `normative_sp31_rag_clauses`.\n\n"
        "ЖЕСТКИЕ ПРАВИЛА ИНЖЕНЕРНОЙ И ФИЗИЧЕСКОЙ ТОЧНОСТИ (АНТИ-ГАЛЛЮЦИНАЦИИ):\n"
        "1. ЕДИНИЦЫ ИЗМЕРЕНИЯ: Все напоры и давления даны строго в метрах водяного столба (`м` или `м вод. ст.`). КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО переводить их в кПа, МПа или бары.\n"
        "2. ДЕФИЦИТ НАПОРА ≠ ВАКУУМ:\n"
        "   - Если в узле свободный напор `p_min > 0` (например, `p_min = 4.65 м` при `req_p_m = 22.0 м`, `dp = -17.35 м`, `is_vacuum = false`), в уличной магистрали давление ПОЛОЖИТЕЛЬНОЕ. ЗАПРЕЩЕНО называть это «вакуумом» или «кавитацией»! Пиши: «дефицит свободного напора 17.35 м для верхних этажей 4-этажной застройки по СП 31.13330 п. 5.11».\n"
        "   - Если `p_min < -3.0 м` (особенно ниже -10 м, например -62 м) — физический вакуум в воде не может превышать -10 м вод. ст. Объясняй это как математический артефакт DDA-расчета EPANET при заданном отборе из отсеченного задвижкой узла или ошибке геодезической отметки Z.\n"
        "3. УДЕЛЬНЫЙ ГРАДИЕНТ `i_m_km` И ФАКТИЧЕСКАЯ ПОТЕРЯ `total_loss_m` (Δh):\n"
        "   - Всегда различай удельный гидравлический уклон `i` (м/км) и фактическую потерю напора на участке `Δh = i * L / 1000` (`total_loss_m`, м).\n"
        "   - Если у короткой трубы (`l_m < 30 м`) коэффициент Хейзена-Уильямса `c < 30` (например, `C = 2.0`), указывай, что это опечатка ввода данных или искусственная имитация прикрытой задвижки, а не физическое зарастание трубы.\n"
        "   - Различай трубы, реально перекрытые задвижкой в модели (`closed_by_valve_count`, статус CLOSED), и открытые тупиковые/резервные участки с нулевым расходом (`zero_flow_open_count`, Q = 0).\n"
        "4. НАСОСЫ И ТЕРМИНОЛОГИЯ:\n"
        "   - BEP — это Best Efficiency Point (точка оптимального КПД насоса, 75–115% от Qном). Никогда не расшифровывай BEP иначе.\n"
        "   - Отключенные насосы (`Q = 0`, `[ВЫКЛ]`) при нормальном давлении в коллекторе — это штатный холодный резерв по СП 31.13330.2012 п. 10.3 (Таблица 23), а не авария.\n"
        "   - При перегрузке насоса (`Q > 1.15 * Qном`, работа правее BEP из-за низкого противодавления в коллекторе) рекомендуй частотное регулирование (ЧРП) для снижения оборотов либо дросселирование напорной задвижки ПОСЛЕ насоса по СП 31.13330.2012 п. 10.7 (ЗАПРЕЩЕНО предлагать дросселирование или PRV на всасывающей линии до насоса).\n"
        "5. СУТОЧНЫЙ БАЛАНС И ПОТЕРИ ВОДЫ:\n"
        "   - Основная разница между суточной подачей в сеть и полезным водопотреблением абонентов — это потери воды в сети (физические утечки + коммерческие потери / неучтенное и несанкционированное потребление), а также учет многоступенчатой перекачки (`first_lift_daily_supply_m3_day` vs `booster_daily_repumping_m3_day`). Резервуары чистой воды (РЧВ, СП 31.13330.2012 п. 12.1–12.5) лишь сглаживают внутрисуточную неравномерность по часам.\n"
        "6. БЕЗ ВЫДУМАННЫХ ФАЙЛОВ, КНОПОК И СЫРОГО HTML: Никогда не упоминай исходники `.cs` (`Node.cs`, `Pipe.cs`), несуществующие кнопки интерфейса и НЕ вставляй сырые HTML-теги `<button>`, `<br>` в текст отчета.\n"
        "7. НОРМАТИВНЫЕ ССЫЛКИ (RAG СП 31.13330): В каждом разделе явно ссылайся на пункты из `normative_sp31_rag_clauses` (например, «СП 31.13330.2012 п. 5.11», «п. 5.13» для 60 м, «п. 10.3 Таблица 23», «п. 10.7», «п. 11.5», «п. 11.38», «п. 12.1–12.5»).\n"
        "8. Интерактивные ссылки (выбирай цель по контексту, строго чистый Markdown БЕЗ обратных кавычек и HTML):\n"
        "   - Для перехода к строке объекта в таблице пиши `[узел #ID](table:ID)` или `#ID`.\n"
        "   - Для показа объекта на карте пиши `[узел #ID на карте](map:ID)`.\n"
        "   - Для фильтрации таблицы вставляй ссылки: `[Показать узлы с ошибками данных](filter:nodes:data_error)`, `[Показать узлы с дефицитом напора](filter:nodes:deficit)`, `[Показать узлы с избытком напора](filter:nodes:excess)`, `[Показать узкие места труб](filter:pipes:bottlenecks)`, `[Показать перекрытые участки](filter:pipes:closed)`, `[Показать застойные зоны](filter:pipes:stagnant)`, `[Показать недогруженные насосы](filter:pumps:underload)`, `[Показать перегруженные насосы](filter:pumps:overload)`.\n"
        f"{onto_block}"
        "9. В конце ответа выведи JSON-блок `[SELECTED_DASHBOARDS]` с ключами `section_order` (из `['qhSection', 'pressureSection', 'bottlenecksSection', 'stagnationSection', 'valvesSection', 'diurnalSection']`) и `dashboards` (`[{\"id\": \"...\"}]`).\n"
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
    if sanitize_ai_report_text is not None:
        main_text = sanitize_ai_report_text(main_text)
    return main_text, parsed_dashboards
