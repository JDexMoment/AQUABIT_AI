"""Модуль интеграции онтологии ПК «Аквабит» (EPANET-AI v3).

Содержит детерминированный движок применения правил онтологии
(aquabit_hydraulic_ontology_v3.json / .ttl), классификацию проблемных зон сети,
динамическую сборку системных промптов и парсинг решений ИИ по структуре отчёта и дашбордам.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_ONTOLOGY_JSON = Path("data/aquabit_hydraulic_ontology_v3.json")
DEFAULT_ONTOLOGY_TTL = Path("data/aquabit_hydraulic_ontology_v3.ttl")


def clean_object_id(raw_id: str | None) -> str:
    """Strips leading prefix letter [ceputv] from object ID: c345977 -> 345977, e1216 -> 1216."""
    if not raw_id:
        return ""
    s = str(raw_id).strip()
    return re.sub(r"^[ceputv](\d+)$", r"\1", s, flags=re.IGNORECASE)


def clean_object_name(raw_name: str | None) -> str:
    """Strips '(Google)' or similar artifact from object name."""
    if not raw_name:
        return ""
    return re.sub(r"\s*\(\s*Google\s*\)\s*", "", str(raw_name), flags=re.IGNORECASE).strip()


def parse_turtle_ontology(ttl_path: Path) -> dict[str, Any]:
    """Легковесный парсер правил и структуры из W3C RDF Turtle (.ttl) онтологии."""
    text = ttl_path.read_text(encoding="utf-8")
    rules = []
    rule_blocks = re.findall(r":(\w+)\s+a\s+:DiagnosticRule\s*;([\s\S]*?)(?=\n:\w+\s+a|\n###|\Z)", text)
    for rid, body in rule_blocks:
        prio_m = re.search(r":rulePriority\s+(\d+)", body)
        cond_m = re.search(r':conditionFormula\s+"([^"]+)"', body)
        diag_m = re.search(r':diagnosisText\s+"([^"]+)"@ru', body)
        expl_m = re.search(r':physicalExplanation\s+"([^"]+)"@ru', body)
        act_m = re.search(r':engineeringAction\s+"([^"]+)"@ru', body)
        rules.append({
            "rule_id": rid,
            "priority": int(prio_m.group(1)) if prio_m else 2,
            "condition": cond_m.group(1) if cond_m else "",
            "diagnosis": diag_m.group(1) if diag_m else rid,
            "explanation": expl_m.group(1) if expl_m else "",
            "action_in_model": act_m.group(1) if act_m else "",
        })
    return {
        "meta": {"system": "ПК «Аквабит»", "domain": "Гидравлика (RDF Turtle)"},
        "diagnostic_rules": rules,
        "format": "ttl"
    }


def load_hydraulic_ontology(ontology_path: str | Path | None = None) -> dict[str, Any]:
    """Загружает файл гидравлической онтологии ПК «Аквабит» (JSON или TTL)."""
    candidates = [
        Path(ontology_path) if ontology_path else None,
        DEFAULT_ONTOLOGY_JSON,
        DEFAULT_ONTOLOGY_TTL,
        Path(__file__).resolve().parent.parent.parent / "data" / "aquabit_hydraulic_ontology_v3.json",
        Path(__file__).resolve().parent.parent.parent / "data" / "aquabit_hydraulic_ontology_v3.ttl",
        Path("aquabit_hydraulic_ontology_v3.json"),
    ]
    for p in candidates:
        if p and p.exists() and p.is_file():
            try:
                if p.suffix.lower() == ".ttl":
                    data = parse_turtle_ontology(p)
                    logger.info("Загружена RDF Turtle онтология из %s", p)
                    return data
                data = json.loads(p.read_text(encoding="utf-8"))
                logger.info("Загружена JSON онтология из %s", p)
                return data
            except Exception as e:
                logger.warning("Ошибка чтения онтологии из %s: %s", p, e)
    logger.warning("Онтология не найдена по путям, используется встроенная базовая онтология.")
    return _build_builtin_fallback_ontology()


def _build_builtin_fallback_ontology() -> dict[str, Any]:
    return {
        "meta": {"system": "ПК «Аквабит»", "domain": "Гидравлика"},
        "terminology_and_style_constraints": {
            "forbidden_terms_and_phrases": [
                {"forbidden": "вакуум", "replacement": "отрицательное давление / дефицит напора"},
                {"forbidden": "застой / застойные трубы", "replacement": "участки с низкими скоростями (< 0.1 м/с)"},
                {"forbidden": "средняя шероховатость по сети", "replacement": "оценивать только конкретные участки с отклонением шероховатости"},
            ]
        },
        "diagnostic_rules": []
    }


def evaluate_ontology_rules(facts: dict[str, Any], ontology: dict[str, Any]) -> list[dict[str, Any]]:
    """Детерминированно вычисляет сработавшие правила онтологии для конкретной модели сети."""
    triggered: list[dict[str, Any]] = []
    pa = facts.get("pressure_analysis", {})
    ppa = facts.get("pipes_analysis", {})
    pma = facts.get("pumps_analysis", {})
    wb = facts.get("water_balance", {})
    rules_by_id = {r["rule_id"]: r for r in ontology.get("diagnostic_rules", [])}

    # R01: Отрицательное давление в отключенной скважине
    if pa.get("shutoff_wells_negative_count", 0) > 0 and "R01_SHUTOFF_WELL_NEGATIVE_PRESSURE" in rules_by_id:
        triggered.append({
            **rules_by_id["R01_SHUTOFF_WELL_NEGATIVE_PRESSURE"],
            "matched_objects": f"{pa['shutoff_wells_negative_count']} отключенных скважин / узлов без водоразбора",
            "severity": "low",
            "problem_domain": "data_integrity"
        })

    # R02: Критическое отрицательное давление (< -3.0 м) у потребителей
    if pa.get("data_error_below_minus_3m_count", 0) > 0 and "R02_EXTREME_DATA_ERROR_PRESSURE" in rules_by_id:
        worst = pa.get("worst_data_error_nodes", [])[:5]
        objs = ", ".join(f"{clean_object_id(r.get('node_id'))} ({r.get('free_pressure_min_m')} м)" for r in worst)
        triggered.append({
            **rules_by_id["R02_EXTREME_DATA_ERROR_PRESSURE"],
            "matched_objects": objs or f"{pa['data_error_below_minus_3m_count']} узлов",
            "severity": "critical",
            "problem_domain": "data_integrity"
        })

    # R03: Реальный дефицит требуемого напора относительно этажности
    if pa.get("real_required_pressure_deficit_count", 0) > 0 and "R03_REQUIRED_PRESSURE_DEFICIT" in rules_by_id:
        worst_def = pa.get("worst_deficit_nodes", [])[:6]
        objs = ", ".join(
            f"{clean_object_id(r.get('node_id'))} (P_своб={r.get('free_pressure_min_m')}м, P_треб={r.get('required_pressure_m')}м, ΔP={r.get('pressure_surplus_deficit_m')}м, {r.get('floors', 1)} эт.)"
            for r in worst_def
        )
        triggered.append({
            **rules_by_id["R03_REQUIRED_PRESSURE_DEFICIT"],
            "matched_objects": objs or f"{pa['real_required_pressure_deficit_count']} абонентов с дефицитом напора",
            "severity": "high",
            "problem_domain": "pressure_deficit"
        })

    # R04: Критическая скорость потока и узкие места (м/км)
    if ppa.get("high_velocity_over_5ms_count", 0) > 0 and "R04_HIGH_VELOCITY_BOTTLENECK" in rules_by_id:
        worst_pipes = ppa.get("top_high_loss_pipes", [])[:5]
        objs = ", ".join(
            f"{clean_object_id(p.get('pipe_id'))} (V={p.get('velocity_max_m_s')} м/с, i={p.get('headloss_gradient_m_km')} м/км, C={p.get('hazen_williams_c')})"
            for p in worst_pipes
        )
        triggered.append({
            **rules_by_id["R04_HIGH_VELOCITY_BOTTLENECK"],
            "matched_objects": objs,
            "severity": "high",
            "problem_domain": "hydraulic_bottlenecks"
        })

    # R05: Участки с низкими скоростями (< 0.1 м/с)
    if ppa.get("low_velocity_under_01ms_count", 0) > 0 and "R05_LOW_VELOCITY_PIPES" in rules_by_id:
        triggered.append({
            **rules_by_id["R05_LOW_VELOCITY_PIPES"],
            "matched_objects": f"{ppa['low_velocity_under_01ms_count']} участков с V < 0.10 м/с",
            "severity": "medium",
            "problem_domain": "water_stagnation"
        })

    # R06: Недогрузка сетевого/повысительного насоса
    booster_under = [p for p in pma.get("underloaded_pumps", []) if not p.get("is_borehole")]
    if booster_under and "R06_BOOSTER_PUMP_UNDERLOAD_VALVE_CHECK" in rules_by_id:
        objs = ", ".join(f"{clean_object_id(p.get('pump_id'))} (Q_раб={p.get('q_working_m3_h')}/{p.get('q_nominal_m3_h')} м³/ч)" for p in booster_under[:5])
        triggered.append({
            **rules_by_id["R06_BOOSTER_PUMP_UNDERLOAD_VALVE_CHECK"],
            "matched_objects": objs,
            "severity": "medium",
            "problem_domain": "pump_imbalance"
        })

    # R07: Недогрузка скважинного насоса
    borehole_under = [p for p in pma.get("underloaded_pumps", []) if p.get("is_borehole")]
    if borehole_under and "R07_BOREHOLE_PUMP_UNDERLOAD" in rules_by_id:
        objs = ", ".join(f"{clean_object_id(p.get('pump_id'))} (Q_раб={p.get('q_working_m3_h')}/{p.get('q_nominal_m3_h')} м³/ч)" for p in borehole_under[:5])
        triggered.append({
            **rules_by_id["R07_BOREHOLE_PUMP_UNDERLOAD"],
            "matched_objects": objs,
            "severity": "medium",
            "problem_domain": "pump_imbalance"
        })

    # R08: Перегрузка насоса (> 115% номинала)
    overloaded = pma.get("overloaded_pumps", [])
    if overloaded and "R08_PUMP_OVERLOAD_OUTLET_REGULATION" in rules_by_id:
        objs = ", ".join(f"{clean_object_id(p.get('pump_id'))} [{p.get('pump_type')}] (Q_раб={p.get('q_working_m3_h')}/{p.get('q_nominal_m3_h')} м³/ч)" for p in overloaded[:5])
        triggered.append({
            **rules_by_id["R08_PUMP_OVERLOAD_OUTLET_REGULATION"],
            "matched_objects": objs,
            "severity": "high",
            "problem_domain": "pump_imbalance"
        })

    # R09: Индивидуальная аномалия модели
    anomalies = facts.get("unique_model_anomalies", [])
    if anomalies and "R09_ANOMALY_DETECTOR_CONTRAST" in rules_by_id:
        triggered.append({
            **rules_by_id["R09_ANOMALY_DETECTOR_CONTRAST"],
            "matched_objects": " | ".join(anomalies[:3]),
            "severity": "high",
            "problem_domain": "anomalies"
        })

    # R10: Скважинные насосы первого подъема
    if pma.get("borehole_active_count", 0) > 0 and "R10_BOREHOLE_PUMP_WELLHEAD" in rules_by_id:
        triggered.append({
            **rules_by_id["R10_BOREHOLE_PUMP_WELLHEAD"],
            "matched_objects": f"{pma['borehole_active_count']} скважинных агрегатов 1-го подъема",
            "severity": "medium",
            "problem_domain": "pump_imbalance"
        })

    # R11: Деградация пропускной способности (C < 80)
    low_c = ppa.get("top_low_hazen_williams_c_pipes", [])
    if low_c and "R11_HAZEN_WILLIAMS_DEGRADATION" in rules_by_id:
        objs = ", ".join(f"{clean_object_id(p.get('pipe_id'))} (C={p.get('hazen_williams_c')}, i={p.get('headloss_gradient_m_km')} м/км)" for p in low_c[:4])
        triggered.append({
            **rules_by_id["R11_HAZEN_WILLIAMS_DEGRADATION"],
            "matched_objects": objs,
            "severity": "medium",
            "problem_domain": "hydraulic_bottlenecks"
        })

    # R12: Суточный баланс и разрыв установленной мощности
    if wb.get("capacity_vs_demand_gap_m3_day", 0) > 0 and "R12_DIURNAL_HYDROGRAPH_IMBALANCE" in rules_by_id:
        triggered.append({
            **rules_by_id["R12_DIURNAL_HYDROGRAPH_IMBALANCE"],
            "matched_objects": f"Разрыв установленной мощности: {wb.get('capacity_vs_demand_gap_m3_day', 0):.0f} м³/сут",
            "severity": "low",
            "problem_domain": "diurnal_imbalance"
        })

    return triggered


def analyze_problem_domains(facts: dict[str, Any], triggered_rules: list[dict[str, Any]]) -> dict[str, Any]:
    """Анализирует и ранжирует проблемные области сети водоснабжения для адаптации отчета и дашбордов."""
    pa = facts.get("pressure_analysis", {})
    ppa = facts.get("pipes_analysis", {})
    pma = facts.get("pumps_analysis", {})
    wb = facts.get("water_balance", {})

    chart_payload = facts.get("chart_payload") or {}
    inf_map = chart_payload.get("informative_charts") or {}

    domains: dict[str, dict[str, Any]] = {
        "pressure_deficit": {
            "score": 0,
            "title": "Дефицит свободного напора и необеспеченность этажности застройки",
            "details": f"{pa.get('real_required_pressure_deficit_count', 0)} абонентов с дефицитом напора",
            "worst_items": pa.get("worst_deficit_nodes", [])[:5],
            "recommended_dashboard": "pressure_deficit"
        },
        "pump_imbalance": {
            "score": 0,
            "title": "Несоответствие рабочих точек насосных агрегатов и характеристик Q-H",
            "details": f"{len(pma.get('underloaded_pumps', []))} недогружено, {len(pma.get('overloaded_pumps', []))} перегружено",
            "worst_items": pma.get("overloaded_pumps", []) + pma.get("underloaded_pumps", []),
            "recommended_dashboard": "pump_qh"
        },
        "hydraulic_bottlenecks": {
            "score": 0,
            "title": "Гидравлические узкие места и завышенные удельные потери напора (м/км)",
            "details": f"{ppa.get('high_velocity_over_5ms_count', 0)} труб с V >= 5 м/с",
            "worst_items": ppa.get("top_high_loss_pipes", [])[:5],
            "recommended_dashboard": "pipe_bottlenecks"
        },
        "water_stagnation": {
            "score": 0,
            "title": "Зоны пониженных скоростей (< 0.1 м/с) и риск ухудшения качества воды",
            "details": f"{ppa.get('low_velocity_under_01ms_count', 0)} участков с V < 0.10 м/с",
            "worst_items": [],
            "recommended_dashboard": "low_velocity_stagnation"
        },
        "data_integrity": {
            "score": 0,
            "title": "Аномалии исходных данных и отсеченные участки сети (P < -3 м)",
            "details": f"{pa.get('data_error_below_minus_3m_count', 0)} узлов с P < -3 м, {pa.get('shutoff_wells_negative_count', 0)} выключенных скважин",
            "worst_items": pa.get("worst_data_error_nodes", [])[:5],
            "recommended_dashboard": "pressure_corridor"
        },
        "diurnal_imbalance": {
            "score": 0,
            "title": "Суточная неравномерность водопотребления и резервирование подачи",
            "details": f"Пиковый фактор {wb.get('peak_factor_k_hour', 1.0):.2f}, разрыв {wb.get('installed_vs_peak_gap_m3_day', 0):.0f} м³/сут",
            "worst_items": [],
            "recommended_dashboard": "diurnal_balance"
        }
    }

    # Вычисляем скоры серьезности
    if pa.get("real_required_pressure_deficit_count", 0) > 0:
        worst_def_m = abs(min((r.get("pressure_surplus_deficit_m", 0.0) for r in pa.get("worst_deficit_nodes", [])), default=0.0))
        domains["pressure_deficit"]["score"] = 50 + min(50, worst_def_m * 2)

    if pma.get("overloaded_pumps", []):
        domains["pump_imbalance"]["score"] += 45
    if pma.get("underloaded_pumps", []):
        domains["pump_imbalance"]["score"] += 30

    if ppa.get("high_velocity_over_5ms_count", 0) > 0 or ppa.get("top_high_loss_pipes", []):
        top_loss = max((p.get("headloss_gradient_m_km", 0.0) for p in ppa.get("top_high_loss_pipes", [])), default=0.0)
        domains["hydraulic_bottlenecks"]["score"] = 40 + min(60, top_loss)

    if pa.get("data_error_below_minus_3m_count", 0) > 0:
        domains["data_integrity"]["score"] = 70

    if ppa.get("low_velocity_under_01ms_count", 0) > 10:
        domains["water_stagnation"]["score"] = 25 + min(25, ppa.get("low_velocity_under_01ms_count", 0) / 10)

    if wb.get("peak_factor_k_hour", 1.0) > 1.5 or abs(wb.get("balance_discrepancy_percent", 0.0)) > 10:
        domains["diurnal_imbalance"]["score"] = 35

    # Фильтрация неинформативных графиков: если график пустой или плоский, понижаем приоритет дашборда
    for dom_k, dom_v in domains.items():
        dash_id = dom_v.get("recommended_dashboard")
        if dash_id and inf_map.get(dash_id) is False:
            dom_v["score"] = 0

    # Сортируем проблемные области по убыванию серьезности
    ranked_domains = sorted(
        [{"domain": k, **v} for k, v in domains.items() if v["score"] > 0],
        key=lambda x: x["score"],
        reverse=True
    )
    return {
        "ranked_domains": ranked_domains,
        "primary_domain": ranked_domains[0]["domain"] if ranked_domains else "diurnal_imbalance",
        "has_spatial_geo": bool(facts.get("inp_metadata", {}).get("nodes")),
    }


def build_ontology_ai_system_prompt(ontology: dict[str, Any]) -> str:
    """Генерирует динамический системный промпт для ИИ на основе онтологии ПК «Аквабит»."""
    forbidden = "\n".join(
        f"- НЕ пиши «{x['forbidden']}» -> пиши «{x['replacement']}»."
        for x in ontology.get("terminology_and_style_constraints", {}).get("forbidden_terms_and_phrases", [])
    )
    return (
        "Ты — ведущий инженер-гидравлик систем водоснабжения ПК «Аквабит» (EPANET-AI v3).\n"
        "Твоя задача — составить строгий, глубокий инженерный отчет по расчетной схеме сети,\n"
        "опираясь ТОЛЬКО на переданные расчетные факты и активированные правила онтологии.\n\n"
        "ЖЕСТКИЕ ПРАВИЛА ФОРМАТИРОВАНИЯ И СТИЛЯ:\n"
        "1. ЗАГОЛОВОК ОТЧЕТА: Начинай отчет с заголовка `# ИИ отчет` (без дополнительных слов в скобках).\n"
        "2. НИКАКИХ ЭМОДЗИ И СМАЙЛИКОВ: категорически запрещено использовать любые значки и эмодзи (никаких значков вроде ⚠️, 🚀, 🧠, 📊, 📈). Формат текста — строгий научно-инженерный.\n"
        "3. ФОРМАТ ПРИОРИТЕТОВ: Заголовки разделов пиши строго в формате `## Приоритет N - <Название>` (например: `## Приоритет 1 - Надежность подачи`). Не добавляй фразы в скобках вроде `(Выбор ИИ под проблемные зоны)`.\n"
        "4. ОБОЗНАЧЕНИЕ ОБЪЕКТОВ: Указывай ID объектов БЕЗ начальных букв префикса (например: узел `345977`, труба `1216`, насос `1263`, задвижка `10`).\n"
        "5. ИМЕНА ИЗ INP: Если узел или объект имеет имя `(Google)` или имя в скобках содержит `(Google)`, удали `(Google)` и не показывай его.\n"
        "6. НАЗВАНИЯ ГРАФИКОВ: Не показывай ссылки на нормативы в скобках в заголовках графиков, например не пиши `(СП 31.13330)` и `(Хейзен-Вильямс)`.\n"
        "7. СКВАЖИННЫЕ НАСОСЫ: Явно указывай, какие насосы являются «скважинными» (1-го подъема без задвижек на входе), а какие — «сетевыми / повысительными».\n"
        "8. ОБЪЕКТЫ В ТЕКСТЕ: Приводи конкретные ID объектов, чтобы инженер мог легко сопоставить их со схемой сети.\n\n"
        "ПРАВИЛА ДИНАМИЧЕСКОЙ СТРУКТУРЫ ОТЧЕТА:\n"
        "На основе `ranked_problem_domains` и `activated_ontology_rules` выбери 3–4 самые острые проблемы именно этого города и сформируй под них предметные приоритетные разделы `## Приоритет N - <Название>` с указанием конкретных ID узлов/труб/насосов.\n"
        "В каждом проблемном разделе связывай три элемента: [Точный факт и локализация] -> [Физическая причина по онтологии] -> [Конкретное действие в ПК «Аквабит»].\n\n"
        "ВЫБОР ДАШБОРДОВ (НЕ ВЫБИРАЙ ПУСТЫЕ ГРАФИКИ!):\n"
        "Выбирай только те дашборды, которые содержат реальные непустые данные:\n"
        "- `pump_qh`: Кривая Q-H насосов с рабочими точками на кривой;\n"
        "- `pressure_deficit`: Дефицит требуемого давления по этажам;\n"
        "- `pipe_bottlenecks`: Удельные потери напора (м/км) и лимитирующие участки;\n"
        "- `pressure_corridor`: Суточный коридор свободных напоров (только при наличии суточных колебаний);\n"
        "- `diurnal_balance`: Суточный баланс подачи и потребления (только при динамических графиках);\n"
        "- `low_velocity_stagnation`: Анализ участков с пониженной скоростью (< 0.10 м/с).\n\n"
        "ФОРМАТ ВЫВОДА РЕШЕНИЯ ПО ДАШБОРДАМ:\n"
        "В самом конце ответа после основного текста обязательно размести блок:\n"
        "[SELECTED_DASHBOARDS]\n"
        "{\n"
        '  "primary_problem_areas": ["список ключевых проблем"],\n'
        '  "dashboards": [\n'
        '    {"id": "<id_дашборда>", "priority": 1, "rationale": "причина выбора"}\n'
        "  ],\n"
        '  "chart_notes": {\n'
        '    "<id_дашборда>": "Развернутый инженерный анализ (3-5 предложений) с выводами по конкретным объектам"\n'
        "  }\n"
        "}\n\n"
        "ЖЕСТКИЕ ОНТОЛОГИЧЕСКИЕ ОГРАНИЧЕНИЯ:\n"
        f"{forbidden}\n"
        "- Нулевой напор (0 м) — это манометрическое давление на уровне земли. Значения < -3.0 м — ошибка исходных данных отметки Z или отключенный участок, а не вакуум.\n"
        "- Коэффициент шероховатости — Хейзен-Вильямс (чем выше C, тем глаже труба). Сопротивление оценивай в удельных потерях напора i (м/км).\n"
        "- Скважинные насосы НЕ имеют задвижек на входе. Запрещено предлагать проверить открытие задвижек на входе скважинного насоса.\n"
        "- При перегрузке насосов предлагай регулирование давления на нагнетании (уставка ЧРП, дросселирование на выходе), а не открытие задвижек на входе.\n"
    )


def extract_ai_dashboards_and_content(raw_ai_text: str, default_dashboards: list[str] | None = None) -> tuple[str, dict[str, Any]]:
    """Извлекает из ответа ИИ аналитический Markdown и блок [SELECTED_DASHBOARDS] с очисткой эмодзи и служебных тегов."""
    text = (raw_ai_text or "").strip()
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    # Удаляем эмодзи
    text = re.sub(r"[\U00010000-\U0010ffff]", "", text)
    text = re.sub(r"[\u2600-\u27bf]", "", text)

    parsed_dashboards: dict[str, Any] = {
        "primary_problem_areas": [],
        "dashboards": [],
        "chart_notes": {}
    }
    main_text = text

    if "[SELECTED_DASHBOARDS]" in text:
        parts = text.split("[SELECTED_DASHBOARDS]", 1)
        main_text = parts[0].strip()
        json_part = parts[1].strip()
        m = re.search(r"\{.*\}", json_part, flags=re.DOTALL)
        if m:
            clean_json_str = m.group(0).strip()
            clean_json_str = re.sub(r"^```(?:json)?\s*", "", clean_json_str)
            clean_json_str = re.sub(r"\s*```$", "", clean_json_str)
            clean_json_str = re.sub(r",\s*([}\]])", r"\1", clean_json_str)
            try:
                parsed_dashboards = json.loads(clean_json_str)
            except Exception as e:
                dids = re.findall(r'"id"\s*:\s*"([a-zA-Z0-9_]+)"', clean_json_str)
                if dids:
                    parsed_dashboards["dashboards"] = [
                        {"id": did, "priority": idx + 1, "rationale": "Выбрано моделью"}
                        for idx, did in enumerate(dids)
                    ]
                else:
                    logger.warning("Ошибка разбора JSON [SELECTED_DASHBOARDS]: %s", e)
    elif "[CHART_NOTES]" in text:
        parts = text.split("[CHART_NOTES]", 1)
        main_text = parts[0].strip()
        m = re.search(r"\{.*\}", parts[1], flags=re.DOTALL)
        if m:
            try:
                notes = json.loads(m.group(0))
                parsed_dashboards["chart_notes"] = notes
            except Exception:
                pass

    if not parsed_dashboards.get("dashboards"):
        fallback_ids = default_dashboards or ["pressure_deficit", "pump_qh", "pressure_corridor", "diurnal_balance", "pipe_bottlenecks"]
        parsed_dashboards["dashboards"] = [
            {"id": did, "priority": idx + 1, "rationale": "Базовый мониторинг ключевых параметров"}
            for idx, did in enumerate(fallback_ids)
        ]

    # Очищаем заголовки от эмодзи и приводим к чистому виду
    main_text = re.sub(r"##\s*ИИ-РАССЛЕДОВАНИЕ[^\n]*", "## ИИ отчет", main_text)
    main_text = re.sub(r"#\s*ИИ-РАССЛЕДОВАНИЕ[^\n]*", "# ИИ отчет", main_text)
    main_text = re.sub(r"\(Выбор ИИ под проблемные зоны\)", "", main_text)
    main_text = re.sub(r"\(СП\s*31\.13330(?:\.2021)?\)", "", main_text)
    main_text = re.sub(r"\(Хейзен-Вильямс\)", "", main_text)
    main_text = re.sub(r"\(\s*Google\s*\)", "", main_text)

    return main_text, parsed_dashboards
