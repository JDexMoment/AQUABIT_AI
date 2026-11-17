from __future__ import annotations

"""EPANET-AI v3 Interactive HTML Report Generator and Alignment Builder.

Полностью интерактивный генератор HTML-отчетов ПК «Аквабит» (EPANET-AI v3)
на базе векторных HTML5 Canvas графиков, без статичных картинок Matplotlib.
Поддерживает интерактивную карту сети с аппаратным двойным буферированием,
кликабельные ID объектов с переходом на карту и выводом инженерного паспорта,
единый дашборд кривой Q-H со строгой посадкой рабочей точки на линию,
интерактивные предупреждения, сортируемые таблицы и онтологический анализ.
"""

import datetime as dt
import html
import json
import math
import re
from pathlib import Path
from typing import Any

from aquabit_ai.ontology_engine import (
    clean_object_id,
    clean_object_name,
    load_hydraulic_ontology,
    evaluate_ontology_rules,
    analyze_problem_domains,
    extract_ai_dashboards_and_content,
)

SYSTEM_PROMPT_EPANET_AI_V2 = (
    "Ты — аналитическое ядро EPANET-AI программного комплекса «Аквабит» (AquaBit) для инженерной диагностики водопроводных сетей "
    "(нормативы СП 31.13330.2021 и методология Walski et al.).\n"
    "ЖЕСТКИЕ ПРАВИЛА ФОРМАТА И ТОЧНОСТИ:\n"
    "1. НИКАКИХ ПРИВЕТСТВИЙ, ВОДЫ И ЭМОДЗИ: строго запрещены обращения «Коллега», «Приветствую», вводные любезности и любые эмодзи.\n"
    "2. ЗАГОЛОВОК: Начинай ответ с заголовка `# ИИ отчет`.\n"
    "3. СТРОГАЯ ОПОРА НА ФАКТЫ: используй исключительно числа из переданного JSON. Запрещено выдумывать или пересчитывать в уме любые значения.\n"
    "4. ОБОЗНАЧЕНИЕ ОБЪЕКТОВ: приводи ID объектов без начальной буквы префикса (например: 345977, 1216, 1263).\n"
    "5. ИМЕНА ИЗ INP: если имя объекта содержит `(Google)`, удаляй это обозначение.\n"
    "6. СКВАЖИННЫЕ НАСОСЫ: явно отмечай скважинные агрегаты 1-го подъема без задвижек на входе.\n"
    "7. СТРУКТУРА ПРИОРИТЕТОВ: используй формат `## Приоритет N - <Название>`.\n"
)

_GREETING_LINE_RE = re.compile(
    r"^\s*(?:уважаем\w+|коллег\w*|приветств\w*|здравствуй\w*|добрый\s+(?:день|вечер|утро)|"
    r"ниже\s+представлен\w*|давайте\s+разбер\w*|в\s+рамках\s+анализа\s+рассмотрим)[^\n]*\n+",
    flags=re.IGNORECASE,
)


def sanitize_pure_facts_response(text: str) -> str:
    """Очищает ответ ИИ: убирает теги <think>, приветствия, эмодзи и приводит заголовки к стандарту."""
    cleaned = re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL).strip()
    if cleaned.startswith("```markdown"):
        cleaned = re.sub(r"^```markdown\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned).strip()

    # Удаление эмодзи
    cleaned = re.sub(r"[\U00010000-\U0010ffff]", "", cleaned)
    cleaned = re.sub(r"[\u2600-\u27bf]", "", cleaned)

    for _ in range(3):
        new_c = _GREETING_LINE_RE.sub("", cleaned).lstrip()
        if new_c == cleaned:
            break
        cleaned = new_c

    # Нормализация заголовков
    cleaned = re.sub(r"^#+\s*ИИ-РАССЛЕДОВАНИЕ[^\n]*", "# ИИ отчет", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"\(Выбор ИИ под проблемные зоны\)", "", cleaned)
    cleaned = re.sub(r"\(СП\s*31\.13330(?:\.2021)?\)", "", cleaned)
    cleaned = re.sub(r"\(Хейзен-Вильямс\)", "", cleaned)
    cleaned = re.sub(r"\(\s*Google\s*\)", "", cleaned)

    lines = cleaned.splitlines()
    while lines and any(
        w in lines[-1].lower()
        for w in ("готов ответить", "обращайтесь", "надеюсь, данный", "успехов в работе", "если потребуются дополнительные")
    ):
        lines.pop()
    return "\n".join(lines).strip()


def build_compact_prompt_facts(facts: dict[str, Any]) -> dict[str, Any]:
    """Формирует компактный дайджест фактов для промпта LLM без потери точности."""
    ns = facts.get("nodes_summary", {})
    ps = facts.get("pipes_summary", {})
    pms = facts.get("pumps_summary", {})
    pa = facts.get("pressure_analysis", {})
    ppa = facts.get("pipes_analysis", {})

    def_lines = [
        f"{clean_object_id(r.get('node_id'))}: {r.get('floors', 0)}эт, Pтр={r.get('required_pressure_m')}м, Pмин={r.get('free_pressure_min_m')}м, dP={r.get('pressure_surplus_deficit_m', r.get('margin_min_m'))}м"
        for r in (pa.get("worst_deficit_nodes") or ns.get("worst_deficit_nodes") or [])[:5]
    ]
    loss_lines = [
        f"{clean_object_id(p.get('pipe_id'))}: D={p.get('diameter_mm')}мм, L={p.get('length_m')}м, i={p.get('headloss_gradient_m_km')}м/км, V={p.get('velocity_max_m_s')}м/с, C={p.get('hazen_williams_c')}"
        for p in (ppa.get("top_high_loss_pipes") or [])[:5]
    ]
    pumps_lines = [
        f"{clean_object_id(p.get('pump_id'))} [{p.get('pump_type', 'Насос')}]: Qраб={p.get('q_working_m3_h')}/{p.get('q_nominal_m3_h')}м3/ч, H={p.get('h_est_m', p.get('h_working_m'))}м ({p.get('regime')})"
        for p in (facts.get("pumps_analysis", {}).get("all_pumps_table") or pms.get("active_pumps_details") or [])[:6]
    ]

    return {
        "city_name": facts.get("city_name"),
        "water_balance": facts.get("water_balance", {}),
        "pressure_deficits": def_lines,
        "pipe_bottlenecks": loss_lines,
        "pumps_status": pumps_lines,
        "anomalies": facts.get("unique_model_anomalies", [])
    }


def generate_reference_pure_facts_report(
    facts: dict[str, Any],
    ranked_domains: list[dict[str, Any]] | None = None,
) -> str:
    """Генерирует эталонный инженерный отчёт EPANET-AI v3 строго по фактам и онтологии."""
    cname = facts.get("city_name", "Сеть")
    pa = facts.get("pressure_analysis", {})
    ppa = facts.get("pipes_analysis", {})
    pma = facts.get("pumps_analysis", {})
    wb = facts.get("water_balance", {})
    ns = facts.get("nodes_summary", {})
    ps = facts.get("pipes_summary", {})
    pms = facts.get("pumps_summary", {})
    zs = facts.get("zero_cleaning_summary", {})
    sv = facts.get("sources_and_valves_summary", {})
    ss = facts.get("schedules_summary", {})

    # Если передан v2-датасет или отсутствует pressure_analysis, возвращаем v2-формат
    if facts.get("schema_version") == "epanet_ai_v2_fast" or not pa:
        lines: list[str] = []
        lines.append(f"## 1. Баланс водопотребления и состав расчётной модели ПК «Аквабит» («{cname}»)")
        lines.append(
            f"- **Расчётная база ПК «Аквабит»:** шаг расчёта `{facts.get('simulation_steps_count', 1440)}` временных срезов; "
            f"источник пьезометрических напоров — `{ns.get('head_table_used', 'Напор Узел.csv')}`; статус `Качество Узел.csv` — {sv.get('water_quality_table_status', 'не указано')}."
        )
        lines.append(
            f"- **Узлы и абоненты:** всего `{zs.get('total_nodes_in_uzel_csv', 0)}` узлов, "
            f"активных узлов-абонентов `{ns.get('active_consumer_nodes_count', 0)}` шт. (аналитически отфильтровано транзитных узлов с `Требуемое давление = 0`: "
            f"`{zs.get('pruned_transit_nodes_req_pressure_zero', 0)}` шт., нулевых записей потребителей: `{zs.get('pruned_zero_consumer_rows', 0)}` шт.)."
        )
        lines.append(
            f"- **Водопотребление и источники:** суточный отпуск абонентам `{ns.get('total_daily_consumption_m3_day', 0.0)}` м³/сут, "
            f"часовой пик `{ns.get('peak_hourly_network_demand_m3_h', 0.0)}` м³/ч; источников `{sv.get('sources_count', 0)}` шт."
        )
        lines.append("\n## 2. Свободные напоры P = H - Z, дефицитные зоны и избыточное давление")
        lines.append(
            f"- **Диапазон свободных напоров:** от `{ns.get('free_pressure_min_m')}` м до `{ns.get('free_pressure_max_m')}` м "
            f"(дефицит ниже требуемого напора: `{ns.get('deficit_nodes_below_required_count', 0)}` шт., избыток > 60 м: `{ns.get('excess_pressure_over_60m_nodes_count', 0)}` шт.)."
        )
        lines.append("\n## 3. Трубопроводная сеть: относительный балл шероховатости и скоростной режим")
        lines.append(
            f"- **Геометрия сети:** участков `{ps.get('pipes_count', 0)}` шт., суммарная протяжённость `{ps.get('total_length_km', 0.0)}` км."
        )
        lines.append("\n## 4. Насосные агрегаты, рабочие точки на Кривой QH и регулирующая арматура")
        lines.append(
            f"- **Насосный парк:** активных `{pms.get('active_pumps_count', 0)}` шт., клапанов `{sv.get('valves_count', 0)}` шт."
        )
        lines.append("\n## 5. Приоритетные инженерные мероприятия в модели ПК «Аквабит»")
        lines.append("- Проверить высотные отметки и напоры в узлах дефицита.")
        return "\n".join(lines)

    lines = ["# ИИ отчет"]
    lines.append(f"Инженерный анализ расчетной модели сети «{cname}» на основе онтологии ПК «Аквабит».")
    
    # Сводный паспорт для общего понимания масштаба сети
    tot_km = ppa.get('total_length_km') or ps.get('total_length_km', 0.0)
    p_cnt = ppa.get('total_pipes') or ps.get('pipes_count', 0)
    cons_m3 = wb.get('total_daily_consumption_m3_day') or ns.get('total_daily_consumption_m3_day', 0.0)
    p_min_all = pa.get('free_pressure_min_m') if pa.get('free_pressure_min_m') is not None else ns.get('free_pressure_min_m', '—')
    p_max_all = pa.get('free_pressure_max_m') if pa.get('free_pressure_max_m') is not None else ns.get('free_pressure_max_m', '—')
    lines.append(
        f"Модель охватывает {p_cnt:,} участков трубопроводов протяженностью {tot_km:,.1f} км "
        f"с интегральным суточным отпуском {cons_m3:,.1f} м³/сут. Диапазон свободных напоров по расчетным узлам составляет от {p_min_all} м до {p_max_all} м. "
        f"На основе онтологического сопоставления гидравлических предикатов сформирован приоритетный перечень критических зон, подлежащих первоочередному регулированию."
    )

    ranked = ranked_domains or []
    if not ranked:
        onto = load_hydraulic_ontology()
        rules = evaluate_ontology_rules(facts, onto)
        analysis = analyze_problem_domains(facts, rules)
        ranked = analysis["ranked_domains"]

    section_idx = 1
    dashboards_selected = []
    chart_notes = {}

    for d in ranked:
        dom = d["domain"]
        if dom == "pressure_deficit":
            w_def = pa.get("worst_deficit_nodes", [])
            top_d = w_def[0] if w_def else {}
            top_id = clean_object_id(top_d.get("node_id", "узел"))
            top_val = top_d.get("pressure_surplus_deficit_m", 0.0)
            def_cnt = pa.get("real_required_pressure_deficit_count", 0)
            lines.append(f"\n## {section_idx}. Приоритет {section_idx} - Надежность подачи и дефицит свободного напора")
            lines.append(
                f"- **Масштаб и локализация дефицита:** В сети зафиксировано {def_cnt} активных абонентов, "
                f"где фактический пьезометрический напор опускается ниже нормативного значения, рассчитанного по этажности застройки (СП 31.13330). "
                f"Наибольшая глубина дефицита достигает ΔP = {top_val:.1f} м на узле {top_id}."
            )
            def_items = "; ".join(
                f"узел {clean_object_id(r.get('node_id'))} ({r.get('floors', 1)} эт., P_своб={r.get('free_pressure_min_m', 0):.1f} м, P_треб={r.get('required_pressure_m', 0):.1f} м, дефицит ΔP={r.get('pressure_surplus_deficit_m', 0):.1f} м)"
                for r in w_def[:8]
            )
            if def_items:
                lines.append(f"- **Реестр лимитирующих узлов абонентов:** {def_items}.")
            lines.append(
                "- **Гидравлический генезис дефицита:** Просадка пьезометрической линии вызвана совместным действием двух факторов: "
                "значительным гидравлическим сопротивлением магистральных водоводов малого диаметра при пропуске пиковых расходов и "
                "недостаточным давлением на выходе из питающих насосных станций при работе на высоких геодезических отметках местности."
            )
            lines.append(
                "- **Оценка эксплуатационных рисков:** Длительная эксплуатация при отрицательном запасе напора приводит к срыву водоснабжения "
                "верхних этажей жилой застройки в часы максимального водоразбора (07:00–09:00 и 19:00–22:00), возникновению вторичного загрязнения "
                "через негерметичные соединения арматуры и риску кавитационного схлопывания при резких переключениях запорных устройств."
            )
            lines.append(
                "- **Инженерный комплекс мер в ПК «Аквабит»:** Рекомендуется моделирование локальной подкачивающей насосной станции (ПНС) "
                "на вводе в высотный микрорайон, реконструкция подводящих участков с увеличением диаметра, либо повышение давления на головной НС "
                "с одновременной установкой регуляторов давления («после себя») в зонах с избыточным напором более 60 м."
            )
            dashboards_selected.append({"id": "pressure_deficit", "priority": section_idx, "rationale": f"{def_cnt} узлов с дефицитом напора до {top_val:.1f} м"})
            chart_notes["pressure_deficit"] = f"Дефицит напора сосредоточен в узлах высокой этажности ({top_id} и др.). Требуется локальное регулирование давления."
            section_idx += 1

        elif dom == "hydraulic_bottlenecks":
            high_loss = ppa.get("top_high_loss_pipes", [])
            worst_loss = high_loss[0] if high_loss else {}
            w_id = clean_object_id(worst_loss.get("pipe_id", "магистраль"))
            w_loss = worst_loss.get("headloss_gradient_m_km", 0.0)
            high_v_cnt = ppa.get("high_velocity_over_5ms_count", 0)
            lines.append(f"\n## {section_idx}. Приоритет {section_idx} - Гидравлические узкие места и удельные потери напора")
            lines.append(
                f"- **Выявленные сопротивления сети:** В расчетной модели обнаружено {high_v_cnt} участков со сверхвысокой скоростью V >= 5.0 м/с, "
                f"создающих критические удельные потери напора. Лимитирующим является участок {w_id} "
                f"(диаметр D={worst_loss.get('diameter_mm', 0):.0f} мм, длина L={worst_loss.get('length_m', 0):.0f} м), "
                f"где при скорости потока {worst_loss.get('velocity_max_m_s', 0):.2f} м/с градиент потерь достигает i={w_loss:.2f} м/км при коэффициенте Хейзена-Вильямса C={worst_loss.get('hazen_williams_c', 100)}."
            )
            pipe_details = "; ".join(
                f"труба {clean_object_id(p.get('pipe_id'))} (D={p.get('diameter_mm', 0):.0f} мм, i={p.get('headloss_gradient_m_km', 0):.1f} м/км, V={p.get('velocity_max_m_s', 0):.2f} м/с, C={p.get('hazen_williams_c', 100)})"
                for p in high_loss[:8]
            )
            if pipe_details:
                lines.append(f"- **Рейтинг лимитирующих магистралей:** {pipe_details}.")
            lines.append(
                "- **Причины возникновения узких мест:** Экстремальные потери напора вызваны несоответствием пропускной способности исторических "
                "стальных водоводов возросшим расходам транзита, а также внутренней коррозией и зарастанием стенок (падение C < 80)."
            )
            lines.append(
                "- **Оценка рисков:** Резкий градиент гидравлического уклона гасит напор насосных станций, кратно увеличивает затраты электроэнергии "
                "на преодоление трения и многократно повышает риск гидродинамического удара с разрывом трубопровода при аварийных остановках насосов."
            )
            lines.append(
                "- **Инженерный комплекс мер в ПК «Аквабит»:** Рекомендуется виртуальное моделирование санации или перекладки лимитирующего участка "
                f"{w_id} с увеличением диаметра до расчетного нормативного (при V = 0.8–1.2 м/с), либо прокладка параллельной разгрузочной перемычки."
            )
            dashboards_selected.append({"id": "pipe_bottlenecks", "priority": section_idx, "rationale": f"Лимитирующий участок {w_id} с потерями {w_loss:.1f} м/км"})
            chart_notes["pipe_bottlenecks"] = f"Участок {w_id} лимитирует пропускную способность магистрали (i={w_loss:.1f} м/км). Реконструкция снимет гидравлический дефицит."
            section_idx += 1

        elif dom == "pump_imbalance":
            under = pma.get("underloaded_pumps", [])
            over = pma.get("overloaded_pumps", [])
            act_p = pma.get("active_pumps_count", 0)
            tot_p = pma.get("total_pumps", 0)
            bh_cnt = pma.get("borehole_active_count", 0)
            booster_cnt = pma.get("booster_active_count", 0)
            lines.append(f"\n## {section_idx}. Приоритет {section_idx} - Рабочие режимы насосных агрегатов и кривые Q-H")
            lines.append(
                f"- **Структура насосного парка:** В работе задействовано {act_p} из {tot_p} установленных агрегатов "
                f"(включая скважинных 1-го подъема без входных задвижек: {bh_cnt} шт., сетевых и повысительных станций: {booster_cnt} шт.). "
                f"Фактические рабочие точки посажены строго на паспортные характеристики Q-H."
            )
            if under:
                u_items = ", ".join(f"насос {clean_object_id(p.get('pump_id'))} (подача Q_раб={p.get('q_working_m3_h', 0):.1f} при норме {p.get('q_nominal_m3_h', 0):.1f} м³/ч)" for p in under[:6])
                lines.append(
                    f"- **Агрегаты в зоне недогрузки (левее BEP 75%):** {u_items}. "
                    "Работа в этой зоне характеризуется повышенной вибрацией, перегревом подшипниковых узлов из-за рециркуляции потока в рабочем колесе и низким эксплуатационным КПД. "
                    "Причина — завышенный напор насоса относительно статического сопротивления сети. Для скважинных агрегатов запрещено регулирование входной арматурой; "
                    "необходима корректировка уставки ЧРП либо дросселирование напорной задвижки на оголовке скважины."
                )
            if over:
                o_items = ", ".join(f"насос {clean_object_id(p.get('pump_id'))} (подача Q_раб={p.get('q_working_m3_h', 0):.1f} при норме {p.get('q_nominal_m3_h', 0):.1f} м³/ч)" for p in over[:6])
                lines.append(
                    f"- **Агрегаты в зоне перегрузки (правее BEP 115%):** {o_items}. "
                    "При работе правее точки максимального КПД возникает опасность перегрузки электродвигателя по току и срыва напора. "
                    "Регулирование должно осуществляться ограничением максимальной частоты ЧРП или прикрытием напорной задвижки."
                )
            lines.append(
                "- **Инженерный комплекс мер в ПК «Аквабит»:** Рекомендуется моделирование внедрения частотно-регулируемого привода (ЧРП) "
                "на основных водоподъемных станциях для динамического удержания рабочих точек в диапазоне BEP 75–115% при колебаниях суточного водоразбора."
            )
            dashboards_selected.append({"id": "pump_qh", "priority": section_idx, "rationale": "Контроль рабочей точки насосов относительно паспортной кривой Q-H"})
            chart_notes["pump_qh"] = "Рабочие точки смещены относительно оптимальной зоны BEP (75-115%). Необходима оптимизация гидравлического режима."
            section_idx += 1

        elif dom == "water_stagnation":
            stag_cnt = ppa.get("low_velocity_under_01ms_count", 0)
            lines.append(f"\n## {section_idx}. Приоритет {section_idx} - Зоны сети с низкими скоростями движения воды (< 0.10 м/с)")
            lines.append(
                f"- **Оценка малопроточного фонда:** В модели идентифицировано {stag_cnt} участков со скоростью движения воды менее 0.10 м/с "
                "в период максимального расчетного водоразбора. Общая протяженность малопроточных линий свидетельствует о наличии консервированных зон."
            )
            lines.append(
                "- **Санитарно-гигиенические и эксплуатационные риски:** Скорости потока ниже 0.10 м/с приводят к осаждению взвешенных веществ, "
                "ускорению коррозии внутренней поверхности стальных и чугунных труб, выпадению осадков и распаду остаточного свободного хлора, "
                "что создает условия для развития биопленок и вторичного бактериального загрязнения питьевой воды."
            )
            lines.append(
                "- **Инженерный комплекс мер в ПК «Аквабит»:** Рекомендуется виртуальное моделирование закольцовки тупиковых ветвей перемычками малого диаметра "
                "для обеспечения транзитной циркуляции, внедрение расписания регулярных гидропневматических промывок через концевые выпуски, "
                "а при плановой замене сетей — уменьшение диаметров труб до технологически обоснованных."
            )
            dashboards_selected.append({"id": "low_velocity_stagnation", "priority": section_idx, "rationale": "Высокая доля участков с V < 0.10 м/с"})
            chart_notes["low_velocity_stagnation"] = "Значительная часть труб работает в малопроточном режиме. Рекомендуется закольцовка тупиковых линий."
            section_idx += 1

    lines.append("\n[SELECTED_DASHBOARDS]")
    dashboards_json = {
        "primary_problem_areas": [d["title"] for d in ranked[:3]],
        "dashboards": dashboards_selected,
        "chart_notes": chart_notes
    }
    lines.append(json.dumps(dashboards_json, ensure_ascii=False, indent=2))
    return "\n".join(lines)


def build_dpo_rejected_variant(facts: dict[str, Any], variant_idx: int = 0) -> str:
    """Synthesizes realistic anti-pattern ('rejected') responses for DPO alignment so the student strongly suppresses fluff & domain errors."""
    cname = facts.get("city_name", "Сеть")
    ps = facts.get("pipes_summary", {})
    zs = facts.get("zero_cleaning_summary", {})
    r_score = ps.get("roughness_length_weighted_score_pts", 80)

    if variant_idx % 2 == 0:
        return (
            f"Коллега, приветствую! Рад помочь вам с разбором вашего файла гидравлической модели EPANET ({cname}.inp)!\n\n"
            f"Прежде всего отмечу фундаментальный парадокс топологии решателя: в модели отфильтровано "
            f"{zs.get('pruned_transit_nodes_req_pressure_zero', 0)} транзитных узлов, что нарушает матрицу инцидентности графа EPANET. "
            f"Кроме того, шероховатость труб составляет целых {r_score} мм по Дарси-Вейсбаху, что физически невозможно для стальных труб!\n\n"
            f"Рекомендую сначала исправить файл .inp и обращаться снова, коллега! Всегда рад помочь!"
        )
    return (
        f"Здравствуйте, уважаемый коллега! Давайте подробно поговорим о вашей водопроводной сети «{cname}».\n\n"
        f"Сеть интересная, в ней {ps.get('pipes_count', 0)} участков трубопроводов общей длиной {ps.get('total_length_km', 0)} км. "
        f"К сожалению, из-за отсутствия некоторых параметров в классическом EPANET невозможно сделать точные выводы без калибровки манометрической съёмки. "
        f"Средняя шероховатость равна {r_score} мм. Советую поставить частотные преобразователи на все насосы, чтобы сэкономить около 35% электроэнергии. "
        f"Надеюсь, данный обзор был вам полезен!"
    )


def _markdown_to_clean_html(md: str) -> str:
    """Конвертирует Markdown в чистый HTML с разметкой интерактивных кнопок перехода на карту."""
    lines = md.splitlines()
    out = []
    in_ul = False
    in_ol = False

    def close_lists():
        nonlocal in_ul, in_ol
        if in_ul:
            out.append("</ul>")
            in_ul = False
        if in_ol:
            out.append("</ol>")
            in_ol = False

    for line in lines:
        s = line.strip()
        if not s:
            close_lists()
            continue

        if s.startswith("# "):
            close_lists()
            out.append(f"<h1 class='ai-h1'>{html.escape(s[2:].strip())}</h1>")
        elif s.startswith("## "):
            close_lists()
            out.append(f"<h2 class='ai-h2'>{html.escape(s[3:].strip())}</h2>")
        elif s.startswith("### "):
            close_lists()
            out.append(f"<h3 class='ai-h3'>{html.escape(s[4:].strip())}</h3>")
        elif s.startswith("- ") or s.startswith("* "):
            if not in_ul:
                close_lists()
                out.append("<ul>")
                in_ul = True
            content = s[2:].strip()
            content = re.sub(r"\*\*(.*?)\*\*", r"<strong>\1</strong>", content)
            content = re.sub(r"`(.*?)`", r"<code>\1</code>", content)
            out.append(f"<li>{content}</li>")
        elif re.match(r"^\d+\.\s", s):
            if not in_ol:
                close_lists()
                out.append("<ol>")
                in_ol = True
            content = re.sub(r"^\d+\.\s*", "", s).strip()
            content = re.sub(r"\*\*(.*?)\*\*", r"<strong>\1</strong>", content)
            content = re.sub(r"`(.*?)`", r"<code>\1</code>", content)
            out.append(f"<li>{content}</li>")
        else:
            close_lists()
            content = re.sub(r"\*\*(.*?)\*\*", r"<strong>\1</strong>", s)
            content = re.sub(r"`(.*?)`", r"<code>\1</code>", content)
            out.append(f"<p>{content}</p>")

    close_lists()
    raw_html = "\n".join(out)

    # Замена обозначений ID узлов и труб на интерактивные кнопки
    def repl_id(m):
        raw = m.group(0)
        clean = clean_object_id(raw)
        return f'<button type="button" class="object-link" data-map-id="{raw}" title="Показать на карте">{clean}</button>'

    # Замена внутри <code>...</code>
    raw_html = re.sub(r'<code>([ceputv]\d+)</code>', repl_id, raw_html, flags=re.IGNORECASE)
    return raw_html


def render_standalone_html_report(
    facts: dict[str, Any],
    chart_payload: dict[str, Any],
    ai_markdown: str,
    output_html_path: Path | None = None,
    generation_meta: dict[str, Any] | None = None,
) -> str:
    """Генерирует полностью автономный интерактивный HTML-отчёт ПК «Аквабит» (EPANET-AI v3).

    Не использует Matplotlib PNG-изображения; все графики рендерятся в векторном виде
    через HTML5 Canvas с поддержкой масштабирования, тултипов, интерактивной карты
    с аппаратным ускорением, фильтрацией таблиц и динамическим анализом.
    """
    cname = facts.get("city_name", "Сеть")
    ns = facts.get("nodes_summary", {})
    ps = facts.get("pipes_summary", {})
    pms = facts.get("pumps_summary", {})
    sv = facts.get("sources_and_valves_summary", {})
    pa = facts.get("pressure_analysis", {})
    ppa = facts.get("pipes_analysis", {})
    pma = facts.get("pumps_analysis", {})
    wb = facts.get("water_balance", {})
    meta = generation_meta or {}

    clean_md_text, parsed_dashboards = extract_ai_dashboards_and_content(ai_markdown)
    ai_html = _markdown_to_clean_html(clean_md_text)

    gen_ts = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    proc_s = facts.get("processing_time_sec", 0.0)
    llm_s = meta.get("inference_sec")
    total_s = round(float(proc_s) + float(llm_s or 0.0), 1)
    model_label = meta.get("model_name", "EPANET-AI")

    # Картографические данные
    geo = chart_payload.get("geo") or {"nodes": {}, "links": []}
    informative_charts = chart_payload.get("informative_charts") or {}

    # Подготовка таблиц
    all_nodes = facts.get("nodes_records") or pa.get("worst_deficit_nodes", [])
    all_pipes = facts.get("pipes_records") or ppa.get("top_high_loss_pipes", [])
    all_pumps = pma.get("all_pumps_table", [])
    if not all_pumps:
        all_pumps = facts.get("pumps_summary", {}).get("active_pumps_details", [])
        if not all_pumps and "qh_curves" in chart_payload:
            all_pumps = [
                {
                    "pump_id": c.get("pump_id") or c.get("name") or c.get("curve_id"),
                    "curve_id": c.get("curve_id", "—"),
                    "q_nominal_m3_h": c.get("q_nominal_m3_h"),
                    "h_nominal_m": c.get("h_nominal_m"),
                    "q_working_m3_h": c.get("q_working_m3_h", 0),
                    "h_working_m": c.get("h_working_m", 0),
                    "is_borehole": False,
                    "regime": "Штатный",
                    "run_hours_day": 24
                }
                for c in chart_payload.get("qh_curves", [])
            ]

    # Убедимся в наличии списков для клиентского фильтра
    client_tables = {
        "nodes": [
            {
                "node_id": r.get("node_id"),
                "clean_id": clean_object_id(r.get("node_id")),
                "name": clean_object_name(r.get("name")),
                "elevation_m": r.get("elevation_m", "—"),
                "floors": r.get("floors", 0),
                "required_pressure_m": r.get("required_pressure_m", "—"),
                "free_pressure_min_m": r.get("free_pressure_min_m", "—"),
                "free_pressure_max_m": r.get("free_pressure_max_m", "—"),
                "margin_m": r.get("pressure_surplus_deficit_m", r.get("margin_min_m", "—")),
                "demand_m3_h": r.get("demand_mean_m3_h", "—")
            }
            for r in (pa.get("worst_deficit_nodes", []) + pa.get("worst_data_error_nodes", []))
        ],
        "pipes": [
            {
                "pipe_id": p.get("pipe_id"),
                "clean_id": clean_object_id(p.get("pipe_id")),
                "name": clean_object_name(p.get("name")),
                "diameter_mm": p.get("diameter_mm", "—"),
                "length_m": p.get("length_m", "—"),
                "velocity_max_m_s": p.get("velocity_max_m_s", "—"),
                "headloss_m_km": p.get("headloss_gradient_m_km", "—"),
                "hazen_williams_c": p.get("hazen_williams_c", "—"),
                "flow_m3_h": p.get("flow_max_m3_h", "—")
            }
            for p in (ppa.get("top_high_loss_pipes", []) + ppa.get("top_low_hazen_williams_c_pipes", []))
        ],
        "pumps": [
            {
                "pump_id": p.get("pump_id"),
                "clean_id": clean_object_id(p.get("pump_id")),
                "name": clean_object_name(p.get("name")),
                "pump_type": p.get("pump_type", "Насос"),
                "is_borehole": bool(p.get("is_borehole")),
                "curve_id": p.get("curve_id", "—"),
                "q_work": p.get("q_working_m3_h", "—"),
                "q_nom": p.get("q_nominal_m3_h", "—"),
                "h_work": p.get("h_working_m", p.get("h_est_m", "—")),
                "h_nom": p.get("h_nominal_m", "—"),
                "run_hours": p.get("run_hours_day", "—"),
                "regime": p.get("regime", "—")
            }
            for p in all_pumps
        ]
    }

    # Предупреждения онтологии
    warnings = []
    def_cnt = pa.get("real_required_pressure_deficit_count", 0)
    if def_cnt > 0:
        worst_d = pa.get("worst_deficit_nodes", [{}])[0]
        warnings.append({
            "level": "crit",
            "title": "Дефицит свободного напора и необеспеченность этажности застройки",
            "desc": f"Выявлено {def_cnt} абонентов с дефицитом давления. Лимитирующий узел {clean_object_id(worst_d.get('node_id'))} с нехваткой ΔP = {worst_d.get('pressure_surplus_deficit_m', 0):.1f} м.",
            "target": "pressureSection"
        })

    bottlenecks_cnt = ppa.get("high_velocity_over_5ms_count", 0)
    top_loss_pipes = ppa.get("top_high_loss_pipes", [])
    if bottlenecks_cnt > 0 or (top_loss_pipes and (top_loss_pipes[0].get("headloss_gradient_m_km") or 0) >= 20.0):
        worst_p = top_loss_pipes[0] if top_loss_pipes else {}
        warnings.append({
            "level": "warn",
            "title": "Гидравлические узкие места и завышенные удельные потери напора",
            "desc": f"Лимитирующий участок {clean_object_id(worst_p.get('pipe_id'))} создает потери {worst_p.get('headloss_gradient_m_km', 0):.1f} м/км при скорости {worst_p.get('velocity_max_m_s', 0):.2f} м/с.",
            "target": "bottlenecksSection"
        })

    under_p = pma.get("underloaded_pumps", [])
    over_p = pma.get("overloaded_pumps", [])
    if under_p or over_p:
        warnings.append({
            "level": "warn",
            "title": "Несоответствие рабочих точек насосных агрегатов характеристикам Q-H",
            "desc": f"В неоптимальном режиме работает {len(under_p)} агрегатов в недогрузке и {len(over_p)} в перегрузке (смещение от зоны BEP 75-115%).",
            "target": "qhSection"
        })

    data_err_cnt = pa.get("data_error_below_minus_3m_count", 0)
    data_err_nodes = pa.get("worst_data_error_nodes", [])
    if data_err_cnt > 0 or data_err_nodes:
        anom_ids = [clean_object_id(r.get("node_id")) for r in data_err_nodes if r.get("node_id")]
        warnings.append({
            "level": "crit",
            "title": "Аномальные значения напора (< -3.0 м) и возможные ошибки данных",
            "desc": f"Зафиксировано {data_err_cnt or len(data_err_nodes)} узлов с давлением ниже -3.0 м. Требуется проверка высотных отметок Z и связности сети.",
            "target": "mapSection",
            "highlight_ids": anom_ids
        })

    warnings_html = "".join(
        f'<div class="warning-card warning-{w["level"]}" onclick="onWarningClick(\'{w["target"]}\', {json.dumps(w.get("highlight_ids", []))})">'
        f'  <div class="warning-header">'
        f'    <div class="warning-title">{html.escape(w["title"])}</div>'
        f'    <span class="warning-link">Перейти к анализу →</span>'
        f'  </div>'
        f'  <div class="warning-desc">{html.escape(w["desc"])}</div>'
        f'</div>'
        for w in warnings
    ) or '<div class="warning-card warning-ok"><div class="warning-title">Критических отклонений гидравлического режима не зафиксировано</div></div>'

    # Расчет показателей для 6 блоков KPI
    # 1. Водопотребление
    kpi_cons = wb.get('total_daily_consumption_m3_day') or ns.get('total_daily_consumption_m3_day', 0.0)
    kpi_peak = wb.get('peak_network_demand_m3_h') or ns.get('peak_hourly_network_demand_m3_h', 0.0)
    kpi_active_nodes = wb.get('active_consumer_nodes') or ns.get('active_consumer_nodes_count', len(all_nodes))

    # 2. Свободный напор P = H - Z
    p_min_val = pa.get('free_pressure_min_m') if pa.get('free_pressure_min_m') is not None else ns.get('free_pressure_min_m')
    p_max_val = pa.get('free_pressure_max_m') if pa.get('free_pressure_max_m') is not None else ns.get('free_pressure_max_m')
    p_min_str = f"{p_min_val:.1f}" if isinstance(p_min_val, (int, float)) else str(p_min_val or '—')
    p_max_str = f"{p_max_val:.2f}" if isinstance(p_max_val, (int, float)) else str(p_max_val or '—')
    kpi_excess = pa.get('excess_over_60m_count') if pa.get('excess_over_60m_count') is not None else ns.get('excess_pressure_over_60m_nodes_count', 0)

    # 3. Трубопроводная сеть
    kpi_pipes_km = ppa.get('total_length_km') or ps.get('total_length_km', 0.0)
    kpi_pipes_cnt = ppa.get('total_pipes') or ppa.get('pipes_count') or ps.get('pipes_count', len(all_pipes))
    kpi_pipes_vol = ppa.get('total_volume_m3') or ps.get('total_internal_water_volume_m3', 0.0)

    # 4. Шероховатость (балл Аквабит)
    kpi_rough_score = ppa.get('mean_hazen_williams_c') or ps.get('roughness_length_weighted_score_pts')
    kpi_rough_str = f"{kpi_rough_score:.2f}" if isinstance(kpi_rough_score, (int, float)) else "—"
    kpi_low_c_km = ppa.get('low_c_under_78_km') or ps.get('degraded_roughness_below_78_length_km', 0.0)

    # 5. Скоростной режим
    kpi_max_v = ppa.get('max_velocity_m_s') or ps.get('network_max_velocity_m_s')
    kpi_max_v_str = f"{kpi_max_v:.3f}" if isinstance(kpi_max_v, (int, float)) else "—"
    kpi_v_over_15 = ppa.get('high_velocity_over_15ms_count') or ps.get('pipes_over_1_5_m_s_count', 0)
    kpi_v_stag = ppa.get('low_velocity_under_03ms_count') or ps.get('stagnant_pipes_under_0_3_m_s_count', 0)
    kpi_rev = ppa.get('reverse_flow_count') or ps.get('reverse_flow_pipes_count', 0)

    # 6. Насосы и энергия
    kpi_active_pumps = pma.get('active_pumps_count') or pms.get('active_pumps_count', 0)
    kpi_total_pumps = pma.get('total_pumps') or pms.get('total_pumps_declared', len(all_pumps))
    kpi_energy = pma.get('total_energy_kwh_day') or pms.get('total_hydraulic_energy_kwh_day', 0.0)
    kpi_valves = sv.get('valves_count', facts.get('sources_and_valves_summary', {}).get('valves_count', 0))

    # Передача данных в JS
    payload_dict = {
        "city_name": cname,
        "hours_labels": chart_payload.get("hours_labels", [f"{h:02d}:00" for h in range(24)]),
        "diurnal_demand_m3_h": chart_payload.get("diurnal_demand_m3_h"),
        "diurnal_pump_flow_m3_h": chart_payload.get("diurnal_pump_flow_m3_h"),
        "diurnal_free_pressure_percentiles_m": chart_payload.get("diurnal_free_pressure_percentiles_m"),
        "qh_curves": chart_payload.get("qh_curves", []),
        "pressure_bins": chart_payload.get("pressure_bins", {}),
        "velocity_bins": chart_payload.get("velocity_bins", {}),
        "node_props": chart_payload.get("node_props", {}),
        "pipe_props": chart_payload.get("pipe_props", {}),
        "pipes_count": kpi_pipes_cnt,
        "informative_charts": informative_charts,
        "geo": geo,
        "tables": client_tables,
        "chart_notes": parsed_dashboards.get("chart_notes", {})
    }
    payload_json = json.dumps(payload_dict, ensure_ascii=False).replace("<", "\\u003c")

    html_doc = f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ПК «Аквабит» • EPANET-AI — {html.escape(cname)}</title>
<style>
  :root {{
    --bg: #f8fafc;
    --card: #ffffff;
    --card-subtle: #f1f5f9;
    --ink: #0f172a;
    --muted: #475569;
    --line: #e2e8f0;
    --brand: #0284c7;
    --brand-hover: #0369a1;
    --teal: #0d9488;
    --ok: #10b981;
    --warn: #f59e0b;
    --crit: #ef4444;
    --radius: 12px;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    background: var(--bg);
    color: var(--ink);
    line-height: 1.55;
    -webkit-font-smoothing: antialiased;
  }}
  .container {{
    max-width: 1360px;
    margin: 0 auto;
    padding: 24px 20px 60px;
  }}
  
  /* Hero Header */
  .hero {{
    background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
    color: #fff;
    border-radius: var(--radius);
    padding: 26px 32px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 20px;
    flex-wrap: wrap;
    box-shadow: 0 10px 25px -5px rgba(15, 23, 42, 0.1);
  }}
  .hero-badge {{
    display: inline-block;
    background: rgba(255, 255, 255, 0.12);
    border: 1px solid rgba(255, 255, 255, 0.2);
    padding: 4px 12px;
    border-radius: 999px;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    margin-bottom: 8px;
    color: #38bdf8;
  }}
  .hero h1 {{
    margin: 0 0 6px;
    font-size: 24px;
    font-weight: 700;
    letter-spacing: -0.01em;
  }}
  .hero-sub {{
    margin: 0;
    color: #94a3b8;
    font-size: 13.5px;
  }}
  .hero-actions {{
    display: flex;
    flex-direction: column;
    align-items: flex-end;
    gap: 8px;
  }}
  .btn-print {{
    background: #0284c7;
    color: #fff;
    border: none;
    padding: 8px 16px;
    border-radius: 8px;
    font-weight: 600;
    font-size: 13px;
    cursor: pointer;
    transition: background 0.15s ease;
  }}
  .btn-print:hover {{ background: #0369a1; }}
  
  /* Warnings List */
  .warnings-container {{
    margin-top: 20px;
    display: flex;
    flex-direction: column;
    gap: 10px;
  }}
  .warning-card {{
    background: #fff;
    border: 1px solid var(--line);
    border-radius: 10px;
    padding: 14px 18px;
    cursor: pointer;
    transition: transform 0.15s ease, box-shadow 0.15s ease, border-color 0.15s ease;
  }}
  .warning-card:hover {{
    transform: translateY(-1px);
    box-shadow: 0 4px 12px rgba(15, 23, 42, 0.06);
  }}
  .warning-crit {{
    border-left: 4px solid var(--crit);
    background: #fef2f2;
  }}
  .warning-warn {{
    border-left: 4px solid var(--warn);
    background: #fffbeb;
  }}
  .warning-ok {{
    border-left: 4px solid var(--ok);
    background: #f0fdf4;
  }}
  .warning-header {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 10px;
  }}
  .warning-title {{
    font-weight: 700;
    font-size: 14px;
    color: var(--ink);
  }}
  .warning-link {{
    font-size: 12px;
    font-weight: 600;
    color: var(--brand);
    white-space: nowrap;
  }}
  .warning-desc {{
    font-size: 13px;
    color: var(--muted);
    margin-top: 4px;
  }}

  /* KPI Grid */
  .kpi-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 14px;
    margin-top: 20px;
  }}
  .kpi-card {{
    background: var(--card);
    border: 1px solid var(--line);
    border-radius: var(--radius);
    padding: 16px 18px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.02);
  }}
  .kpi-title {{
    font-size: 11.5px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--muted);
    margin-bottom: 4px;
  }}
  .kpi-val {{
    font-size: 22px;
    font-weight: 700;
    color: var(--ink);
    margin-bottom: 4px;
  }}
  .kpi-sub {{
    font-size: 12px;
    color: var(--muted);
  }}

  /* Dashboard Charts Section */
  .charts-section {{
    margin-top: 26px;
  }}
  .section-heading {{
    font-size: 18px;
    font-weight: 700;
    color: var(--ink);
    margin: 0 0 14px;
    display: flex;
    align-items: center;
    justify-content: space-between;
  }}
  .charts-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(580px, 1fr));
    gap: 18px;
  }}
  @media (max-width: 768px) {{
    .charts-grid {{ grid-template-columns: 1fr; }}
  }}
  .panel {{
    background: var(--card);
    border: 1px solid var(--line);
    border-radius: var(--radius);
    padding: 18px 20px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.02);
    display: flex;
    flex-direction: column;
  }}
  .panel-header {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 12px;
    flex-wrap: wrap;
    margin-bottom: 12px;
  }}
  .panel-title {{
    margin: 0;
    font-size: 15px;
    font-weight: 700;
    color: var(--ink);
  }}
  .panel-controls {{
    display: flex;
    align-items: center;
    gap: 8px;
  }}
  select.curve-select {{
    padding: 5px 10px;
    border-radius: 6px;
    border: 1px solid var(--line);
    background: #f8fafc;
    font-size: 12.5px;
    font-weight: 600;
    color: var(--ink);
    cursor: pointer;
  }}
  .btn-subtle {{
    background: #f1f5f9;
    border: 1px solid #cbd5e1;
    color: var(--ink);
    padding: 5px 12px;
    border-radius: 6px;
    font-size: 12.5px;
    font-weight: 600;
    cursor: pointer;
    transition: all 0.15s ease;
  }}
  .btn-subtle:hover, .btn-subtle.active {{
    background: var(--brand);
    color: #fff;
    border-color: var(--brand);
  }}
  .canvas-box {{
    position: relative;
    width: 100%;
    height: 290px;
  }}
  canvas {{
    width: 100%;
    height: 100%;
    display: block;
  }}

  /* Analysis Accordion */
  .accordion-wrap {{
    margin-top: 12px;
    border-top: 1px solid var(--line);
    padding-top: 10px;
  }}
  .btn-analysis {{
    background: transparent;
    border: 1px solid #cbd5e1;
    border-radius: 6px;
    padding: 4px 10px;
    font-size: 12px;
    font-weight: 600;
    color: var(--brand);
    cursor: pointer;
    display: inline-flex;
    align-items: center;
    gap: 4px;
    transition: all 0.15s ease;
  }}
  .btn-analysis:hover {{
    background: #f0f9ff;
    border-color: var(--brand);
  }}
  .analysis-box {{
    margin-top: 8px;
    background: #f8fafc;
    border: 1px solid #e2e8f0;
    border-radius: 8px;
    padding: 12px 14px;
    font-size: 13px;
    color: #334155;
    line-height: 1.5;
  }}

  /* AI Report Section */
  .ai-report-card {{
    margin-top: 24px;
    background: var(--card);
    border: 1px solid #cbd5e1;
    border-left: 4px solid var(--brand);
    border-radius: var(--radius);
    padding: 22px 26px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.02);
  }}
  .ai-report-header {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    border-bottom: 1px solid var(--line);
    padding-bottom: 10px;
    margin-bottom: 16px;
  }}
  .ai-report-header h2 {{
    margin: 0;
    font-size: 18px;
    color: var(--ink);
  }}
  .ai-content h1.ai-h1 {{ font-size: 18px; margin: 0 0 12px; color: #0c4a6e; }}
  .ai-content h2.ai-h2 {{ font-size: 15.5px; margin: 18px 0 8px; color: #0284c7; border-bottom: 1px dashed #cbd5e1; padding-bottom: 4px; }}
  .ai-content h3.ai-h3 {{ font-size: 14px; margin: 12px 0 6px; color: #1e293b; }}
  .ai-content p {{ margin: 6px 0 10px; font-size: 13.5px; }}
  .ai-content ul, .ai-content ol {{ margin: 6px 0 12px; padding-left: 20px; font-size: 13.5px; }}
  .ai-content li {{ margin-bottom: 4px; }}

  /* Interactive Map Section */
  .map-section {{
    margin-top: 26px;
    background: var(--card);
    border: 1px solid var(--line);
    border-radius: var(--radius);
    padding: 18px 20px;
  }}
  .map-controls {{
    display: flex;
    gap: 8px;
    align-items: center;
    flex-wrap: wrap;
    margin: 10px 0 12px;
  }}
  .map-controls input {{
    padding: 6px 12px;
    border: 1px solid #cbd5e1;
    border-radius: 6px;
    font-size: 13px;
    min-width: 220px;
  }}
  .map-canvas-wrap {{
    position: relative;
    width: 100%;
    height: 480px;
    background: #f8fafc;
    border: 1px solid var(--line);
    border-radius: 8px;
    overflow: hidden;
  }}
  #networkMap {{
    width: 100%;
    height: 100%;
    touch-action: none;
    cursor: grab;
  }}
  #networkMap:active {{ cursor: grabbing; }}
  
  /* Map Object Card */
  .map-card-dock {{
    position: absolute;
    bottom: 12px;
    right: 12px;
    width: 320px;
    background: rgba(255, 255, 255, 0.96);
    backdrop-filter: blur(4px);
    border: 1px solid #cbd5e1;
    border-radius: 8px;
    box-shadow: 0 4px 14px rgba(0,0,0,0.08);
    padding: 12px 14px;
    font-size: 12.5px;
    z-index: 10;
    display: none;
  }}
  .map-card-header {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    border-bottom: 1px solid #e2e8f0;
    padding-bottom: 6px;
    margin-bottom: 8px;
  }}
  .map-card-title {{ font-weight: 700; color: var(--ink); }}
  .map-card-close {{
    background: none;
    border: none;
    font-size: 16px;
    cursor: pointer;
    color: var(--muted);
  }}
  .map-card-grid {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 6px;
  }}
  .map-prop-label {{ font-size: 11px; color: var(--muted); }}
  .map-prop-val {{ font-weight: 600; color: var(--ink); }}

  /* Interactive Tables */
  .tables-section {{
    margin-top: 26px;
  }}
  .table-tabs {{
    display: flex;
    gap: 8px;
    margin-bottom: 12px;
    border-bottom: 1px solid var(--line);
    padding-bottom: 8px;
  }}
  .table-tab-btn {{
    background: transparent;
    border: none;
    padding: 8px 14px;
    border-radius: 6px;
    font-size: 13.5px;
    font-weight: 600;
    color: var(--muted);
    cursor: pointer;
  }}
  .table-tab-btn.active {{
    background: #e0f2fe;
    color: #0369a1;
  }}
  .table-toolbar {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 12px;
    flex-wrap: wrap;
    margin-bottom: 10px;
  }}
  .table-filters {{
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
  }}
  .filter-pill {{
    background: #f1f5f9;
    border: 1px solid #e2e8f0;
    border-radius: 999px;
    padding: 4px 10px;
    font-size: 12px;
    font-weight: 600;
    color: var(--muted);
    cursor: pointer;
  }}
  .filter-pill.active {{
    background: var(--brand);
    color: #fff;
    border-color: var(--brand);
  }}
  .table-search {{
    padding: 6px 12px;
    border: 1px solid #cbd5e1;
    border-radius: 6px;
    font-size: 12.5px;
    min-width: 220px;
  }}
  .table-wrap {{
    overflow-x: auto;
    background: #fff;
    border: 1px solid var(--line);
    border-radius: 8px;
  }}
  table.data-table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 12.5px;
    text-align: left;
  }}
  table.data-table th {{
    background: #f8fafc;
    color: #334155;
    padding: 8px 10px;
    border-bottom: 1px solid var(--line);
    font-weight: 600;
    cursor: pointer;
    user-select: none;
  }}
  table.data-table th:hover {{ background: #f1f5f9; }}
  table.data-table td {{
    padding: 7px 10px;
    border-bottom: 1px solid #f1f5f9;
  }}
  table.data-table tr:hover td {{ background: #f8fafc; }}
  .table-pagination {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-top: 10px;
    font-size: 12.5px;
    color: var(--muted);
  }}

  /* Object Links */
  .object-link {{
    background: transparent;
    border: none;
    border-bottom: 1px dashed var(--brand);
    color: var(--brand);
    font: inherit;
    font-weight: 600;
    cursor: pointer;
    padding: 0 1px;
    text-decoration: none;
  }}
  .object-link:hover {{
    color: #0369a1;
    border-bottom-style: solid;
  }}

  .num-crit {{ color: var(--crit); font-weight: 700; }}
  .num-warn {{ color: var(--warn); font-weight: 700; }}
  .num-ok {{ color: var(--ok); font-weight: 700; }}

  @media print {{
    body {{ background: #fff; }}
    .btn-print, .map-controls, .table-toolbar, .btn-analysis {{ display: none !important; }}
    .panel, .kpi-card, .ai-report-card {{ break-inside: avoid; box-shadow: none; }}
  }}

  #chartTooltip {{
    position: absolute;
    display: none;
    pointer-events: none;
    background: rgba(15, 23, 42, 0.94);
    border: 1px solid #38bdf8;
    border-radius: 8px;
    color: #f8fafc;
    padding: 8px 12px;
    font-size: 12px;
    line-height: 1.45;
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.35);
    z-index: 10000;
    white-space: nowrap;
  }}
</style>
</head>
<body>

<div id="chartTooltip"></div>

<div class="container">
  <!-- Hero Header -->
  <header class="hero">
    <div>
      <div class="hero-badge">ПК «АКВАБИТ» • EPANET-AI</div>
      <h1>Гидравлический паспорт и ИИ-диагностика сети «{html.escape(cname)}»</h1>
      <p class="hero-sub">
        Расчетный горизонт: <strong>{facts.get('simulation_steps', facts.get('simulation_steps_count', 1440))} шагов</strong> •
        Сформировано: <strong>{gen_ts}</strong> •
        Время расчета: <strong>{total_s} сек</strong>
      </p>
    </div>
    <div class="hero-actions">
      <button class="btn-print" onclick="window.print()">Печать / PDF</button>
      <span style="font-size:12px;color:#94a3b8">Модель: {html.escape(str(model_label))}</span>
    </div>
  </header>

  <!-- Interactive Warnings List -->
  <section class="warnings-container">
    {warnings_html}
  </section>

  <!-- 6 Инженерных карточек KPI -->
  <section class="kpi-grid">
    <div class="kpi-card">
      <div class="kpi-title">Водопотребление сети</div>
      <div class="kpi-val">{kpi_cons:,.1f} м³/сут</div>
      <div class="kpi-sub">Часовой пик: <strong>{kpi_peak:,.1f} м³/ч</strong> • Абонентов: <strong>{kpi_active_nodes:,}</strong></div>
    </div>
    <div class="kpi-card" id="kpiPressure">
      <div class="kpi-title">Свободный напор P = H − Z</div>
      <div class="kpi-val">{p_min_str} .. {p_max_str} м</div>
      <div class="kpi-sub">
        <span class="{'num-crit' if def_cnt > 0 else 'num-ok'}">Дефицит: {def_cnt} узл.</span> • &gt;60 м: <strong>{kpi_excess}</strong>
      </div>
    </div>
    <div class="kpi-card">
      <div class="kpi-title">Трубопроводная сеть</div>
      <div class="kpi-val">{kpi_pipes_km:,.2f} км</div>
      <div class="kpi-sub">Участков: <strong>{kpi_pipes_cnt:,}</strong> • Объём воды: <strong>{kpi_pipes_vol:,.0f} м³</strong></div>
    </div>
    <div class="kpi-card">
      <div class="kpi-title">Шероховатость (балл Аквабит)</div>
      <div class="kpi-val">{kpi_rough_str} балл.</div>
      <div class="kpi-sub">{'Ниже нормы (<80 баллов)' if (kpi_rough_score or 100) < 80 else 'В норме'} • &lt;78 б.: <strong>{kpi_low_c_km:,.2f} км</strong></div>
    </div>
    <div class="kpi-card">
      <div class="kpi-title">Скоростной режим</div>
      <div class="kpi-val">{kpi_max_v_str} м/с</div>
      <div class="kpi-sub">&gt;1.5 м/с: <strong>{kpi_v_over_15}</strong> • Застой &lt;0.3: <strong>{kpi_v_stag:,}</strong> • Реверс: <strong>{kpi_rev:,}</strong></div>
    </div>
    <div class="kpi-card" id="kpiPumps">
      <div class="kpi-title">Насосы и энергия</div>
      <div class="kpi-val">{kpi_active_pumps} / {kpi_total_pumps} шт.</div>
      <div class="kpi-sub">Энергия: <strong>{kpi_energy:,.1f} кВт·ч/сут</strong> • Клапанов: <strong>{kpi_valves}</strong></div>
    </div>
  </section>

  <!-- Interactive Charts Section -->
  <section class="charts-section">
    <div class="section-heading">
      <span>Гидравлические графики и рабочие характеристики</span>
    </div>
    <div class="charts-grid">
      
      <!-- Panel 1: Single Consolidated Q-H Pump Curve -->
      <div class="panel" id="qhSection">
        <div class="panel-header">
          <div>
            <h3 class="panel-title">Характеристика насосов (Кривая QH)</h3>
          </div>
          <div class="panel-controls">
            <select id="qhSelect" class="curve-select" onchange="onQHSelectChange(this.value)"></select>
            <button id="qhShowAll" class="btn-subtle" onclick="toggleQHShowAll()">Показать все</button>
          </div>
        </div>
        <div class="canvas-box">
          <canvas id="qhCanvas"></canvas>
        </div>
        <div class="accordion-wrap">
          <button class="btn-analysis" onclick="toggleAccordion('qhAnalysis')">Анализ</button>
          <div id="qhAnalysis" class="analysis-box"></div>
        </div>
      </div>

      <!-- Panel 2: Pressure Corridor (only if dynamic) -->
      <div class="panel" id="pressureSection" style="{'display: flex' if informative_charts.get('pressure_corridor', True) else 'display: none'}">
        <div class="panel-header">
          <h3 class="panel-title">Суточный коридор свободных напоров P = H - Z</h3>
        </div>
        <div class="canvas-box">
          <canvas id="pressureBandCanvas"></canvas>
        </div>
        <div class="accordion-wrap">
          <button class="btn-analysis" onclick="toggleAccordion('pressureAnalysis')">Анализ</button>
          <div id="pressureAnalysis" class="analysis-box">
            Суточный коридор свободных напоров отображает распределение пьезометрических отметок по 10-му, 50-му (медиана) и 90-му процентилям сети относительно отметок земли.
            Нижняя опорная граница 10 м гарантирует требуемый напор для типовой одноэтажной застройки, тогда как верхний горизонт 60 м регламентирует предел статической прочности водоразборной арматуры по СП 31.13330.
            Узлы с отрицательным давлением требуют ревизии высотных отметок геодезической съемки либо свидетельствуют о глубокой депрессионной воронке в часы вечернего максимума.
            Для зон с избыточным давлением (более 60 м) рекомендовано зонирование и установка регуляторов давления («после себя»), предотвращающих аварийность и фоновые утечки.
            Абоненты с дефицитом напора требуют устройства локальных подкачивающих насосных станций (ПНС) или ликвидации гидравлических сопротивлений на подводящих магистралях.
          </div>
        </div>
      </div>

      <!-- Panel 3: Diurnal Flow Balance (only if dynamic) -->
      <div class="panel" id="diurnalSection" style="{'display: flex' if informative_charts.get('diurnal_balance', True) else 'display: none'}">
        <div class="panel-header">
          <h3 class="panel-title">Суточный баланс подачи и потребления (24 ч)</h3>
        </div>
        <div class="canvas-box">
          <canvas id="diurnalFlowCanvas"></canvas>
        </div>
        <div class="accordion-wrap">
          <button class="btn-analysis" onclick="toggleAccordion('diurnalAnalysis')">Анализ</button>
          <div id="diurnalAnalysis" class="analysis-box">
            График иллюстрирует суточный баланс между интегральным водопотреблением абонентов и совокупной подачей насосных станций за 24 часа.
            Несовпадение часовых профилей подачи и потребления компенсируется аккумулирующей емкостью резервуаров чистой воды (РЧВ).
            В часы ночного спада водоразбора насосы работают на заполнение резервуаров, а в часы утреннего и вечернего максимумов аккумулированный объем покрывает пиковый дефицит.
            Если подача насосов систематически отстает от интегрального водоразбора, возникает прямая угроза истощения запаса РЧВ и срыва гидравлического режима сети.
            Рекомендуется оптимизация суточного графика включения агрегатов с максимальным переносом нагрузки на часы действия льготного ночного тарифа на электроэнергию.
          </div>
        </div>
      </div>

      <!-- Panel 4: Low Velocity & Stagnation -->
      <div class="panel" id="stagnationSection" style="{'display: flex' if informative_charts.get('low_velocity_stagnation', True) else 'display: none'}">
        <div class="panel-header">
          <h3 class="panel-title">Распределение скоростей потока в сети</h3>
        </div>
        <div class="canvas-box">
          <canvas id="stagnationCanvas"></canvas>
        </div>
        <div class="accordion-wrap">
          <button class="btn-analysis" onclick="toggleAccordion('stagnationAnalysis')">Анализ</button>
          <div id="stagnationAnalysis" class="analysis-box">
            Круговая диаграмма скоростного режима категорирует трубопроводную сеть по критериям гидравлической эффективности и сохранения качества воды.
            Участки со скоростями менее 0.10 м/с и 0.10–0.50 м/с формируют малопроточный фонд сети, где возрастает риск распада остаточного хлора, осаждения взвесей и вторичного бактериального загрязнения.
            Оптимальный диапазон скоростей (0.50–1.50 м/с) обеспечивает кинетическую устойчивость потока и самоочищение труб без чрезмерных гидравлических сопротивлений.
            Участки с повышенными скоростями (1.50–5.00 м/с) и критическими скоростями (более 5.00 м/с) вызывают квадратичный рост потерь напора, кавитационный износ стенок и риск гидравлического удара.
            Для ликвидации застойных зон рекомендуется закольцовка тупиковых линий перемычками меньшего диаметра, а для узких мест — санация или прокладка параллельных водоводов.
          </div>
        </div>
      </div>

    </div>
  </section>

  <!-- AI Report Section -->
  <section class="ai-report-card" id="aiReportCard">
    <div class="ai-report-header">
      <h2>ИИ отчет</h2>
      <div style="font-size:12.5px;color:var(--muted)">Строгий аналитический режим • Онтология ПК «Аквабит»</div>
    </div>
    <div class="ai-content">
      {ai_html}
    </div>
  </section>

  <!-- Interactive Map Section -->
  <section class="map-section" id="mapSection">
    <div class="panel-header" style="margin-bottom:0">
      <div>
        <h3 class="panel-title" style="font-size:16px">Схема сети</h3>
        <p style="margin:2px 0 0;font-size:12.5px;color:var(--muted)">Интерактивная карта сети в координатах расчетной модели. Кликните по объекту для вывода характеристик.</p>
      </div>
    </div>
    <div class="map-controls">
      <button type="button" id="mapPlus" class="btn-subtle" title="Приблизить">＋</button>
      <button type="button" id="mapMinus" class="btn-subtle" title="Отдалить">−</button>
      <button type="button" id="mapReset" class="btn-subtle" title="Показать всю сеть">Вся сеть</button>
      <input id="mapSearch" placeholder="Поиск по ID объекта (например, 345977)" aria-label="ID объекта">
      <button type="button" id="mapFind" class="btn-subtle">Найти</button>
      <span id="mapInfo" style="font-size:12px;color:var(--muted);margin-left:auto"></span>
    </div>
    <div class="map-canvas-wrap">
      <canvas id="networkMap"></canvas>
      <div id="mapObjectCard" class="map-card-dock">
        <div class="map-card-header">
          <span id="mapCardTitle" class="map-card-title">Объект</span>
          <button type="button" class="map-card-close" onclick="closeMapCard()">×</button>
        </div>
        <div id="mapCardBody" class="map-card-grid"></div>
      </div>
    </div>
  </section>

  <!-- Full Data Tables Section -->
  <section class="tables-section" id="tablesSection">
    <div class="panel">
      <div class="table-tabs">
        <button type="button" class="table-tab-btn active" onclick="switchTableTab('nodes', this)">Узлы абонентов (дефицит и напор)</button>
        <button type="button" class="table-tab-btn" onclick="switchTableTab('pipes', this)">Трубопроводы (сопротивления и скорость)</button>
        <button type="button" class="table-tab-btn" onclick="switchTableTab('pumps', this)">Насосные агрегаты (режимы Q-H)</button>
      </div>
      <div class="table-toolbar">
        <div id="tableFilters" class="table-filters"></div>
        <input id="tableSearchInput" class="table-search" placeholder="Поиск по таблице..." oninput="onTableSearch(this.value)">
        <button type="button" class="btn-subtle" onclick="downloadCurrentTableCSV()">Скачать CSV</button>
      </div>
      <div class="table-wrap">
        <table id="mainDataTable" class="data-table">
          <thead id="mainTableHead"></thead>
          <tbody id="mainTableBody"></tbody>
        </table>
      </div>
      <div class="table-pagination">
        <span id="tablePageInfo">Загрузка...</span>
        <div>
          <button type="button" class="btn-subtle" onclick="changeTablePage(-1)">Назад</button>
          <button type="button" class="btn-subtle" onclick="changeTablePage(1)">Вперёд</button>
        </div>
      </div>
      <div class="accordion-wrap" style="margin-top:14px">
        <button class="btn-analysis" onclick="toggleAccordion('tableAnalysis')">Анализ</button>
        <div id="tableAnalysis" class="analysis-box">
          Инженерные реестры расчетной модели ПК «Аквабит» содержат полные параметры по узлам, трубопроводам и насосному парку.
          Клик по номеру любого объекта в таблице, на графике или в тексте ИИ выполняет мгновенное масштабирование и фокус на карте сети с выводом детального паспорта элемента.
          Таблицы поддерживают полнотекстовый поиск по ID и наименованию, сортировку по клику на заголовки столбцов и фильтрацию по проблемным категориям (дефицит напора, высокие потери м/км, скважинные агрегаты).
          Кнопка «Скачать CSV» позволяет экспортировать текущую отфильтрованную выборку для дальнейших гидравлических расчетов и формирования ведомостей дефектов.
        </div>
      </div>
    </div>
  </section>

</div>

<script>
const DATA = {payload_json};

/* Bi-directional ID mappings */
const idToRaw = new Map();
const rawToClean = new Map();

function registerId(rawId) {{
  if (!rawId) return "";
  const s = String(rawId).trim();
  const c = s.replace(/^[ceputv](\\d+)$/i, '$1');
  idToRaw.set(c, s);
  rawToClean.set(s, c);
  return c;
}}

/* HiDPI Canvas setup */
function setupHiDPICanvas(canvas) {{
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.parentElement.getBoundingClientRect();
  const w = Math.max(rect.width, 300);
  const h = Math.max(rect.height, 220);
  canvas.width = w * dpr;
  canvas.height = h * dpr;
  const ctx = canvas.getContext('2d');
  ctx.scale(dpr, dpr);
  return {{ ctx, w, h }};
}}

function drawEmpty(canvas, msg) {{
  const {{ ctx, w, h }} = setupHiDPICanvas(canvas);
  ctx.fillStyle = '#94a3b8';
  ctx.font = '13px sans-serif';
  ctx.textAlign = 'center';
  ctx.fillText(msg, w / 2, h / 2);
}}

/* Tooltip helper */
const chartTooltipEl = document.getElementById('chartTooltip');

function showChartTooltip(ev, html) {{
  if (!chartTooltipEl) return;
  chartTooltipEl.innerHTML = html;
  chartTooltipEl.style.display = 'block';
  const pad = 12;
  let tx = ev.pageX + pad;
  let ty = ev.pageY - 38;
  if (tx + 260 > window.innerWidth) tx = ev.pageX - 260;
  if (ty < 10) ty = ev.pageY + 15;
  chartTooltipEl.style.left = tx + 'px';
  chartTooltipEl.style.top = ty + 'px';
}}

function hideChartTooltip() {{
  if (chartTooltipEl) chartTooltipEl.style.display = 'none';
}}

/* 1. SINGLE CONSOLIDATED Q-H PUMP CURVE */
let qhShowAllMode = false;
let currentQHIndex = 0;
let qhPlotMeta = null;

function drawQHCurve(idx) {{
  const canvas = document.getElementById('qhCanvas');
  const curves = DATA.qh_curves || [];
  if (!curves.length) return drawEmpty(canvas, 'Кривые Q-H отсутствуют в модели');

  const {{ ctx, w, h }} = setupHiDPICanvas(canvas);
  const pad = {{ l: 56, r: 24, t: 24, b: 40 }};
  const pw = w - pad.l - pad.r;
  const ph = h - pad.t - pad.b;

  const curvesToPlot = qhShowAllMode ? curves : [curves[idx] || curves[0]];

  // Global extents
  let maxQ = 0, maxH = 0;
  curvesToPlot.forEach(c => {{
    (c.curve_points || []).forEach(p => {{
      if (p[0] > maxQ) maxQ = p[0];
      if (p[1] > maxH) maxH = p[1];
    }});
    if (c.operating_q_mean && c.operating_q_mean > maxQ) maxQ = c.operating_q_mean;
    if (c.operating_h_est && c.operating_h_est > maxH) maxH = c.operating_h_est;
  }});
  maxQ = Math.max(maxQ * 1.1, 50);
  maxH = Math.max(maxH * 1.15, 20);

  const xOf = q => pad.l + (q / maxQ) * pw;
  const yOf = hv => pad.t + ph - (hv / maxH) * ph;

  // Grid
  ctx.strokeStyle = '#e2e8f0';
  ctx.lineWidth = 1;
  ctx.fillStyle = '#64748b';
  ctx.font = '11px sans-serif';
  for (let i = 0; i <= 4; i++) {{
    const yv = (maxH / 4) * i;
    const y = yOf(yv);
    ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(w - pad.r, y); ctx.stroke();
    ctx.textAlign = 'right';
    ctx.fillText(yv.toFixed(0), pad.l - 7, y + 4);

    const xv = (maxQ / 4) * i;
    const x = xOf(xv);
    ctx.beginPath(); ctx.moveTo(x, pad.t); ctx.lineTo(x, h - pad.b); ctx.stroke();
    ctx.textAlign = 'center';
    ctx.fillText(xv.toFixed(0), x, h - pad.b + 16);
  }}

  // Explicit axis titles
  ctx.fillStyle = '#334155';
  ctx.font = 'bold 11px sans-serif';
  ctx.textAlign = 'center';
  ctx.fillText('Подача Q, м³/ч', pad.l + pw / 2, h - 6);
  ctx.save();
  ctx.translate(15, pad.t + ph / 2);
  ctx.rotate(-Math.PI / 2);
  ctx.fillText('Напор H, м', 0, 0);
  ctx.restore();

  const colors = ['#0284c7', '#0d9488', '#6366f1', '#f59e0b', '#ec4899', '#06b6d4', '#84cc16'];

  curvesToPlot.forEach((item, cIdx) => {{
    const pts = item.curve_points || [];
    const color = colors[cIdx % colors.length];

    // BEP optimal zone (only in single mode)
    if (!qhShowAllMode && item.q_nominal_m3_h > 0) {{
      const x1 = xOf(item.q_nominal_m3_h * 0.75);
      const x2 = xOf(Math.min(maxQ, item.q_nominal_m3_h * 1.15));
      ctx.fillStyle = 'rgba(16, 185, 129, 0.12)';
      ctx.fillRect(x1, pad.t, Math.max(0, x2 - x1), ph);
      ctx.fillStyle = '#10b981';
      ctx.font = '10.5px sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText('Зона BEP (75-115%)', (x1 + x2) / 2, pad.t + 14);
    }}

    // Curve line
    if (pts.length > 0) {{
      ctx.strokeStyle = color;
      ctx.lineWidth = 2.6;
      ctx.beginPath();
      pts.forEach((p, i) => {{
        const x = xOf(p[0]), y = yOf(p[1]);
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      }});
      ctx.stroke();
    }}

    // Design point (Q_nom, H_nom)
    (item.design_points || []).forEach(dp => {{
      ctx.fillStyle = '#10b981';
      ctx.beginPath();
      ctx.arc(xOf(dp[0]), yOf(dp[1]), 5, 0, Math.PI * 2);
      ctx.fill();
      ctx.strokeStyle = '#fff'; ctx.lineWidth = 1.5; ctx.stroke();
    }});

    // Operating point strictly on the curve line
    if (item.active && item.operating_q_mean !== null && item.operating_h_est !== null) {{
      const ox = xOf(item.operating_q_mean);
      const oy = yOf(item.operating_h_est);
      ctx.fillStyle = '#ef4444';
      ctx.beginPath();
      ctx.arc(ox, oy, 6, 0, Math.PI * 2);
      ctx.fill();
      ctx.strokeStyle = '#fff'; ctx.lineWidth = 2; ctx.stroke();

      if (!qhShowAllMode) {{
        ctx.fillStyle = '#0f172a';
        ctx.font = 'bold 11px sans-serif';
        ctx.textAlign = ox > w - 160 ? 'right' : 'left';
        ctx.fillText(`Q=${{item.operating_q_mean}} м³/ч, H=${{item.operating_h_est}} м`, ox + (ox > w - 160 ? -10 : 10), Math.max(pad.t + 16, oy - 8));
      }}
    }}
  }});

  qhPlotMeta = {{ pad, pw, ph, maxQ, maxH, curvesToPlot, xOf, yOf }};
  updateQHAnalysisText();
}}

function updateQHAnalysisText() {{
  const box = document.getElementById('qhAnalysis');
  if (!box) return;
  const curves = DATA.qh_curves || [];
  if (!curves.length) {{
    box.textContent = 'Кривые Q-H насосов отсутствуют в расчетной модели.';
    return;
  }}

  if (qhShowAllMode) {{
    const activeCnt = curves.filter(c => c.active).length;
    const boreholeCnt = curves.filter(c => c.is_borehole).length;
    const underCnt = curves.filter(c => c.active && c.q_nominal_m3_h > 0 && (c.operating_q_mean / c.q_nominal_m3_h) < 0.75).length;
    const overCnt = curves.filter(c => c.active && c.q_nominal_m3_h > 0 && (c.operating_q_mean / c.q_nominal_m3_h) > 1.15).length;
    box.innerHTML = `<strong>Сводный анализ насосного парка:</strong> В расчетной модели задано <strong>${{curves.length}}</strong> агрегатов, из которых активно <strong>${{activeCnt}}</strong> (в том числе скважинных водозаборных: <strong>${{boreholeCnt}}</strong>). В оптимальной зоне наивысшего КПД (BEP 75–115%) находится <strong>${{activeCnt - underCnt - overCnt}}</strong> агрегатов, в недогрузке — <strong>${{underCnt}}</strong>, в перегрузке — <strong>${{overCnt}}</strong>. Фактические рабочие точки (Q_факт, H_факт) посажены строго на напорные характеристики. Рекомендуется ревизия уставок ЧРП для агрегатов с перегрузкой по току и перебалансировка напорных коллекторов.`;
    return;
  }}

  const item = curves[currentQHIndex] || curves[0];
  const pId = clean_object_id(item.pump_id);
  const pKind = item.is_borehole ? 'Скважинный насос первого подъема (забор из источника)' : 'Сетевой / повысительный насос';
  const qWork = item.operating_q_mean !== null ? `${{item.operating_q_mean}} м³/ч` : '—';
  const hWork = item.operating_h_est !== null ? `${{item.operating_h_est}} м` : '—';
  const statusStr = item.active ? 'работает' : 'отключен / резерв';
  
  let regimeText = 'Агрегат находится в резерве или выведен из эксплуатации графиком диспетчеризации.';
  if (item.active) {{
    const ratio = item.q_nominal_m3_h > 0 ? (item.operating_q_mean / item.q_nominal_m3_h) : 1.0;
    if (ratio < 0.75) {{
      regimeText = item.is_borehole
        ? `Агрегат работает в зоне недогрузки (левее оптимума BEP, ${{Math.round(ratio*100)}}% номинала) с повышенным развиваемым напором. Поскольку насос является погружным скважинным, входные задвижки физически отсутствуют. Для нормализации режима требуется проверить динамический уровень воды в скважине и отрегулировать напорную задвижку на оголовке.`
        : `Агрегат работает в зоне глубокой недогрузки (левее оптимума BEP, ${{Math.round(ratio*100)}}% номинала) с повышенным дросселированием. Режим вызывает вибрацию вала и снижает эксплуатационный КПД. Рекомендуется проверить положение запорной арматуры на напорной магистрали и снизить частоту вращения приводом ЧРП.`;
    }} else if (ratio > 1.15) {{
      regimeText = `Агрегат работает в зоне перегрузки (правее оптимума BEP, ${{Math.round(ratio*100)}}% номинала) со снижением развиваемого напора. Режим сопряжен с опасностью перегрева электродвигателя и кавитационного срыва потока на рабочих колесах. Требуется регулирование давления на нагнетании (корректировка уставки ЧРП или дросселирование напорной линии), а не открытие входных задвижек.`;
    }} else {{
      regimeText = `Фактическая рабочая точка находится в оптимальной зоне наивысшего КПД (BEP 75–115% номинальной подачи: ${{Math.round(ratio*100)}}%). Обеспечивается энергоэффективная и бескавитационная эксплуатация агрегата.`;
    }}
  }}

  box.innerHTML = `<strong>Насос <button type="button" class="object-link" data-map-id="${{item.pump_id}}">${{pId}}</button>:</strong> ${{pKind}} (${{statusStr}}). Фактическая подача ${{qWork}}, развиваемый напор ${{hWork}}. ${{regimeText}}`;
}}

function onQHSelectChange(val) {{
  currentQHIndex = Number(val);
  qhShowAllMode = false;
  document.getElementById('qhShowAll').classList.remove('active');
  drawQHCurve(currentQHIndex);
}}

function toggleQHShowAll() {{
  qhShowAllMode = !qhShowAllMode;
  const btn = document.getElementById('qhShowAll');
  if (qhShowAllMode) {{
    btn.classList.add('active');
  }} else {{
    btn.classList.remove('active');
  }}
  drawQHCurve(currentQHIndex);
}}

/* 2. PRESSURE BAND CANVAS */
let pressureBandMeta = null;

function drawPressureBand() {{
  const canvas = document.getElementById('pressureBandCanvas');
  if (!canvas) return;
  const pb = DATA.diurnal_free_pressure_percentiles_m;
  if (!pb || !pb.p50 || pb.p50.every(v => v === null)) return drawEmpty(canvas, 'Данные суточных напоров отсутствуют');

  const {{ ctx, w, h }} = setupHiDPICanvas(canvas);
  const pad = {{ l: 54, r: 24, t: 22, b: 38 }};
  const pw = w - pad.l - pad.r, ph = h - pad.t - pad.b;
  const vals = [...(pb.p10 || []), ...(pb.p50 || []), ...(pb.p90 || []), 0, 65].filter(v => v !== null);
  const minY = Math.min(...vals) - 5;
  const maxY = Math.max(...vals) + 8;
  const n = pb.p50.length;
  const xOf = i => pad.l + (i / Math.max(n - 1, 1)) * pw;
  const yOf = v => pad.t + ph - ((v - minY) / Math.max(maxY - minY, 1)) * ph;

  ctx.strokeStyle = '#e2e8f0'; ctx.lineWidth = 1; ctx.fillStyle = '#64748b'; ctx.font = '11px sans-serif';
  for (let i = 0; i <= 4; i++) {{
    const v = minY + ((maxY - minY) / 4) * i, y = yOf(v);
    ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(w - pad.r, y); ctx.stroke();
    ctx.textAlign = 'right'; ctx.fillText(v.toFixed(0), pad.l - 6, y + 4);
  }}
  for (let i = 0; i < n; i += 4) {{
    ctx.textAlign = 'center';
    ctx.fillText((DATA.hours_labels || [])[i] || `${{i}}:00`, xOf(i), h - pad.b + 16);
  }}

  // Explicit axis titles
  ctx.fillStyle = '#334155';
  ctx.font = 'bold 11px sans-serif';
  ctx.textAlign = 'center';
  ctx.fillText('Время суток (часы)', pad.l + pw / 2, h - 6);
  ctx.save();
  ctx.translate(15, pad.t + ph / 2);
  ctx.rotate(-Math.PI / 2);
  ctx.fillText('Свободный напор P, м', 0, 0);
  ctx.restore();

  // Shaded P10..P90
  ctx.fillStyle = 'rgba(2, 132, 199, 0.14)';
  ctx.beginPath();
  pb.p90.forEach((v, i) => {{ const y = yOf(v ?? 0); if (i === 0) ctx.moveTo(xOf(i), y); else ctx.lineTo(xOf(i), y); }});
  for (let i = n - 1; i >= 0; i--) ctx.lineTo(xOf(i), yOf(pb.p10[i] ?? 0));
  ctx.closePath(); ctx.fill();

  // Reference lines
  [10, 60].forEach(ref => {{
    if (ref >= minY && ref <= maxY) {{
      ctx.save();
      ctx.setLineDash([5, 4]);
      ctx.strokeStyle = ref === 60 ? '#f59e0b' : '#ef4444';
      ctx.beginPath(); ctx.moveTo(pad.l, yOf(ref)); ctx.lineTo(w - pad.r, yOf(ref)); ctx.stroke();
      ctx.font = '10px sans-serif';
      ctx.fillStyle = ref === 60 ? '#f59e0b' : '#ef4444';
      ctx.textAlign = 'right';
      ctx.fillText(ref === 60 ? 'P_макс = 60 м' : 'P_мин = 10 м', w - pad.r - 4, yOf(ref) - 4);
      ctx.restore();
    }}
  }});

  // Median line
  ctx.strokeStyle = '#0284c7'; ctx.lineWidth = 2.4;
  ctx.beginPath();
  pb.p50.forEach((v, i) => {{ if (i === 0) ctx.moveTo(xOf(i), yOf(v ?? 0)); else ctx.lineTo(xOf(i), yOf(v ?? 0)); }});
  ctx.stroke();

  pressureBandMeta = {{ pad, pw, ph, minY, maxY, n, pb }};
}}

/* 3. DIURNAL FLOW CANVAS */
let diurnalFlowMeta = null;

function drawDiurnalFlows() {{
  const canvas = document.getElementById('diurnalFlowCanvas');
  if (!canvas) return;
  const dem = DATA.diurnal_demand_m3_h || [];
  const pmp = DATA.diurnal_pump_flow_m3_h || [];
  const hrs = DATA.hours_labels || [];
  const series = [];
  if (dem.length && dem.some(v => v > 0)) series.push({{ name: 'Потребление сети', data: dem, color: '#0284c7' }});
  if (pmp.length && pmp.some(v => v > 0)) series.push({{ name: 'Подача насосов', data: pmp, color: '#0d9488' }});
  if (!series.length) return drawEmpty(canvas, 'Временные ряды расходов не включены');

  const {{ ctx, w, h }} = setupHiDPICanvas(canvas);
  const pad = {{ l: 56, r: 24, t: 26, b: 40 }};
  const pw = w - pad.l - pad.r, ph = h - pad.t - pad.b;
  const allVals = series.flatMap(s => s.data);
  const maxY = Math.max(...allVals, 1) * 1.15;
  const n = series[0].data.length;
  const xOf = i => pad.l + (i / Math.max(n - 1, 1)) * pw;
  const yOf = v => pad.t + ph - (Math.max(0, v) / maxY) * ph;

  ctx.strokeStyle = '#e2e8f0'; ctx.lineWidth = 1; ctx.fillStyle = '#64748b'; ctx.font = '11px sans-serif';
  for (let i = 0; i <= 4; i++) {{
    const v = (maxY / 4) * i, y = yOf(v);
    ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(w - pad.r, y); ctx.stroke();
    ctx.textAlign = 'right'; ctx.fillText(v.toFixed(0), pad.l - 6, y + 4);
  }}
  for (let i = 0; i < n; i += 4) {{
    ctx.textAlign = 'center';
    ctx.fillText(hrs[i] || `${{i}}:00`, xOf(i), h - pad.b + 16);
  }}

  // Explicit axis titles
  ctx.fillStyle = '#334155';
  ctx.font = 'bold 11px sans-serif';
  ctx.textAlign = 'center';
  ctx.fillText('Время суток (часы)', pad.l + pw / 2, h - 6);
  ctx.save();
  ctx.translate(15, pad.t + ph / 2);
  ctx.rotate(-Math.PI / 2);
  ctx.fillText('Расход, м³/ч', 0, 0);
  ctx.restore();

  // Top Legend
  ctx.font = '11.5px sans-serif';
  ctx.fillStyle = '#0284c7'; ctx.fillRect(w - pad.r - 210, pad.t - 16, 12, 4);
  ctx.fillStyle = '#334155'; ctx.textAlign = 'left'; ctx.fillText('Потребление', w - pad.r - 194, pad.t - 10);
  ctx.fillStyle = '#0d9488'; ctx.fillRect(w - pad.r - 110, pad.t - 16, 12, 4);
  ctx.fillText('Подача', w - pad.r - 94, pad.t - 10);

  series.forEach(s => {{
    ctx.strokeStyle = s.color; ctx.lineWidth = 2.4;
    ctx.beginPath();
    s.data.forEach((v, i) => {{ if (i === 0) ctx.moveTo(xOf(i), yOf(v)); else ctx.lineTo(xOf(i), yOf(v)); }});
    ctx.stroke();
  }});

  diurnalFlowMeta = {{ pad, pw, ph, n, dem, pmp, hrs }};
}}

/* 4. VELOCITY DONUT / PIE CHART */
let stagnationMeta = null;

function drawStagnationChart() {{
  const canvas = document.getElementById('stagnationCanvas');
  if (!canvas) return;
  const {{ ctx, w, h }} = setupHiDPICanvas(canvas);
  const vBins = DATA.velocity_bins || {{}};
  const rawItems = Object.entries(vBins).filter(([_, v]) => typeof v === 'number');
  if (!rawItems.length) return drawEmpty(canvas, 'Данные скоростей отсутствуют');

  // Unified color palette (harmonious dark slate / brand blues with amber/red warnings)
  const colorMap = {{
    '< 0.10': '#f59e0b',
    '< 0.30': '#f59e0b',
    '0.10–0.50': '#38bdf8',
    '0.50–1.50': '#0284c7',
    '0.30–1.50': '#0284c7',
    '1.50–5.00': '#6366f1',
    '1.50–3.00': '#6366f1',
    '>= 5.00': '#ef4444',
    '> 3.00': '#ef4444'
  }};

  const getColor = lbl => {{
    for (const [k, c] of Object.entries(colorMap)) {{
      if (lbl.includes(k)) return c;
    }}
    return '#0284c7';
  }};

  const total = rawItems.reduce((acc, [_, v]) => acc + v, 0);
  const items = rawItems.map(([label, val]) => ({{
    label,
    val,
    ratio: total > 0 ? (val / total) : 0,
    color: getColor(label)
  }}));

  const cx = Math.round(w * 0.28);
  const cy = Math.round(h * 0.50);
  const outerR = Math.max(40, Math.min(cx - 20, cy - 20, 105));
  const innerR = Math.round(outerR * 0.58);

  let curAngle = -Math.PI / 2;
  const slices = [];

  items.forEach(it => {{
    const sweep = it.ratio * Math.PI * 2;
    const startA = curAngle;
    const endA = curAngle + sweep;
    curAngle = endA;

    slices.push({{
      ...it,
      startA,
      endA
    }});

    if (it.val > 0) {{
      ctx.beginPath();
      ctx.arc(cx, cy, outerR, startA, endA);
      ctx.arc(cx, cy, innerR, endA, startA, true);
      ctx.closePath();
      ctx.fillStyle = it.color;
      ctx.fill();
      ctx.strokeStyle = '#ffffff';
      ctx.lineWidth = 1.5;
      ctx.stroke();
    }}
  }});

  // Center donut hole text
  ctx.fillStyle = '#0f172a';
  ctx.font = 'bold 15px sans-serif';
  ctx.textAlign = 'center';
  ctx.fillText(total.toLocaleString('ru-RU'), cx, cy + 2);
  ctx.font = '11px sans-serif';
  ctx.fillStyle = '#64748b';
  ctx.fillText('участков', cx, cy + 18);

  // Legend on the right side
  const legX = Math.round(w * 0.54);
  const legStartY = Math.max(14, Math.round(cy - (items.length * 28) / 2));

  items.forEach((it, idx) => {{
    const ly = legStartY + idx * 28;
    ctx.beginPath();
    ctx.arc(legX + 6, ly + 6, 5, 0, Math.PI * 2);
    ctx.fillStyle = it.color;
    ctx.fill();

    ctx.font = '12px sans-serif';
    ctx.fillStyle = '#334155';
    ctx.textAlign = 'left';
    ctx.fillText(it.label, legX + 18, ly + 9);

    const pct = (it.ratio * 100).toFixed(1);
    ctx.font = 'bold 12px sans-serif';
    ctx.fillStyle = '#0f172a';
    ctx.textAlign = 'right';
    ctx.fillText(`${{it.val.toLocaleString('ru-RU')}} (${{pct}}%)`, w - 16, ly + 9);
  }});

  stagnationMeta = {{ cx, cy, outerR, innerR, slices, total, items, legX, legStartY }};
}}

/* 5. INTERACTIVE MAP ENGINE (HARDWARE-ACCELERATED DOUBLE BUFFERING) */
const networkCanvas = document.getElementById('networkMap');
const networkInfo = document.getElementById('mapInfo');
const networkGeo = DATA.geo || {{ nodes: {{}}, links: [] }};
const linkLookup = new Map();

for (const [rawId, points] of (networkGeo.links || [])) {{
  if (!rawId) continue;
  const clean = registerId(rawId);
  linkLookup.set(rawId, points);
  linkLookup.set(clean, points);
}}
for (const rawId of Object.keys(networkGeo.nodes || {{}})) {{
  registerId(rawId);
}}
if (DATA.node_props) {{
  for (const rawId of Object.keys(DATA.node_props)) {{
    registerId(rawId);
  }}
}}
if (DATA.pipe_props) {{
  for (const rawId of Object.keys(DATA.pipe_props)) {{
    registerId(rawId);
  }}
}}

const networkView = {{ scale: 1, tx: 0, ty: 0, baseScale: 1, selected: null, selectedPoints: null, multiHighlights: null, activeUntil: 0, dragging: false }};
const mapCache = document.createElement('canvas');

function mapSize() {{ return {{ w: Math.max(1, networkCanvas.clientWidth), h: Math.max(1, networkCanvas.clientHeight) }}; }}

function mapResize() {{
  const {{ w, h }} = mapSize(), dpr = window.devicePixelRatio || 1;
  networkCanvas.width = Math.round(w * dpr);
  networkCanvas.height = Math.round(h * dpr);
  mapCache.width = networkCanvas.width;
  mapCache.height = networkCanvas.height;
}}

function screenPoint(p) {{
  return [p[0] * networkView.scale + networkView.tx, networkView.ty - p[1] * networkView.scale];
}}

function redrawBackground() {{
  const {{ w, h }} = mapSize(), dpr = window.devicePixelRatio || 1;
  const ctx = mapCache.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);

  // Background pipes
  ctx.strokeStyle = '#cbd5e1';
  ctx.lineWidth = 0.8;
  for (const [id, points] of (networkGeo.links || [])) {{
    if (!points || points.length < 2) continue;
    ctx.beginPath();
    points.forEach((p, i) => {{
      const [x, y] = screenPoint(p);
      if (i) ctx.lineTo(x, y); else ctx.moveTo(x, y);
    }});
    ctx.stroke();
  }}

  // Nodes
  ctx.fillStyle = '#64748b';
  for (const [id, p] of Object.entries(networkGeo.nodes || {{}})) {{
    const [x, y] = screenPoint(p);
    if (x >= 0 && x <= w && y >= 0 && y <= h) {{
      ctx.beginPath();
      ctx.arc(x, y, 1.8, 0, Math.PI * 2);
      ctx.fill();
    }}
  }}

  redrawOverlay();
}}

function redrawOverlay() {{
  const dpr = window.devicePixelRatio || 1;
  const ctx = networkCanvas.getContext('2d');
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.clearRect(0, 0, networkCanvas.width, networkCanvas.height);
  ctx.drawImage(mapCache, 0, 0);

  // 1. Draw multi-highlights (e.g. 8 anomalous nodes)
  if (networkView.multiHighlights && networkView.multiHighlights.length > 0) {{
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const blink = Date.now() < networkView.activeUntil && Math.floor(Date.now() / 350) % 2 === 0;
    networkView.multiHighlights.forEach(({{ id, pt }}) => {{
      const [x, y] = screenPoint(pt);
      ctx.beginPath();
      ctx.arc(x, y, blink ? 14 : 9, 0, Math.PI * 2);
      ctx.fillStyle = blink ? 'rgba(239, 68, 68, 0.45)' : 'rgba(239, 68, 68, 0.2)';
      ctx.fill();

      ctx.beginPath();
      ctx.arc(x, y, 5, 0, Math.PI * 2);
      ctx.fillStyle = '#ef4444';
      ctx.fill();
      ctx.strokeStyle = '#ffffff';
      ctx.lineWidth = 1.5;
      ctx.stroke();

      ctx.font = 'bold 11px sans-serif';
      ctx.fillStyle = '#0f172a';
      ctx.textAlign = 'center';
      ctx.fillText(id, x, y - 10);
    }});
  }}

  // 2. Draw single selected object
  if (!networkView.selectedPoints) return;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const points = networkView.selectedPoints;
  const blink = Date.now() < networkView.activeUntil && Math.floor(Date.now() / 350) % 2 === 0;

  ctx.strokeStyle = blink ? '#f59e0b' : '#0284c7';
  ctx.fillStyle = blink ? '#f59e0b' : '#0284c7';
  ctx.lineWidth = blink ? 6 : 4;

  if (points.length > 1) {{
    ctx.beginPath();
    points.forEach((p, i) => {{
      const [x, y] = screenPoint(p);
      if (i) ctx.lineTo(x, y); else ctx.moveTo(x, y);
    }});
    ctx.stroke();
  }} else {{
    const [x, y] = screenPoint(points[0]);
    ctx.beginPath();
    ctx.arc(x, y, blink ? 9 : 6, 0, Math.PI * 2);
    ctx.fill();
    ctx.strokeStyle = '#fff';
    ctx.lineWidth = 2;
    ctx.stroke();
  }}
}}

function mapFit() {{
  const pts = Object.values(networkGeo.nodes || {{}}).filter(p => p && Number.isFinite(p[0]) && Number.isFinite(p[1]));
  if (!pts.length) {{
    networkInfo.textContent = 'Координаты сети отсутствуют в модели.';
    return;
  }}
  mapResize();
  let xmin = Infinity, xmax = -Infinity, ymin = Infinity, ymax = -Infinity;
  pts.forEach(([x, y]) => {{
    if (x < xmin) xmin = x; if (x > xmax) xmax = x;
    if (y < ymin) ymin = y; if (y > ymax) ymax = y;
  }});
  const {{ w, h }} = mapSize();
  const scale = Math.min((w - 40) / Math.max(xmax - xmin, 1), (h - 40) / Math.max(ymax - ymin, 1));
  networkView.baseScale = scale;
  networkView.scale = scale;
  networkView.tx = w / 2 - (xmin + xmax) * scale / 2;
  networkView.ty = h / 2 + (ymin + ymax) * scale / 2;
  networkInfo.textContent = `Узлов: ${{pts.length}}, связей: ${{networkGeo.links ? networkGeo.links.length : 0}}`;
  redrawBackground();
}}

function zoomMap(factor, x, y) {{
  const old = networkView.scale;
  const next = Math.max(networkView.baseScale * 0.2, Math.min(networkView.baseScale * 120, old * factor));
  const wx = (x - networkView.tx) / old, wy = (networkView.ty - y) / old;
  networkView.scale = next;
  networkView.tx = x - wx * next;
  networkView.ty = y + wy * next;
  redrawBackground();
}}

function showObjectCard(id, data) {{
  const card = document.getElementById('mapObjectCard');
  const title = document.getElementById('mapCardTitle');
  const body = document.getElementById('mapCardBody');
  if (!card || !title || !body) return;

  const clean = clean_object_id(id);
  title.textContent = `Объект ${{clean}}`;
  body.innerHTML = '';

  const addProp = (lbl, val) => {{
    const item = document.createElement('div');
    item.innerHTML = `<div class="map-prop-label">${{lbl}}</div><div class="map-prop-val">${{val ?? '—'}}</div>`;
    body.appendChild(item);
  }};

  if (data) {{
    const nameVal = data.name;
    const zVal = data.elevation_m ?? data.z;
    const pminVal = data.free_pressure_min_m ?? data.p_min;
    const pmaxVal = data.free_pressure_max_m ?? data.p_max;
    const reqVal = data.required_pressure_m ?? data.req;
    const marginVal = data.margin_m ?? data.margin;
    const floorsVal = data.floors;
    const demandVal = data.demand_m3_h ?? data.demand;
    const dVal = data.diameter_mm ?? data.d;
    const lVal = data.length_m ?? data.l;
    const vVal = data.velocity_max_m_s ?? data.v;
    const iVal = data.headloss_m_km ?? data.i;
    const cVal = data.hazen_williams_c ?? data.c;
    const qVal = data.flow_m3_h ?? data.q;

    if (nameVal) addProp('Имя', clean_object_name(nameVal));
    if (zVal !== undefined && zVal !== null) addProp('Отметка Z', `${{zVal}} м`);
    if (pminVal !== undefined && pminVal !== null) addProp('P_своб мин', `${{pminVal}} м`);
    if (pmaxVal !== undefined && pmaxVal !== null) addProp('P_своб макс', `${{pmaxVal}} м`);
    if (reqVal !== undefined && reqVal !== null) addProp('P_треб', `${{reqVal}} м`);
    if (marginVal !== undefined && marginVal !== null) addProp('Запас ΔP', `${{marginVal}} м`);
    if (floorsVal !== undefined && floorsVal > 0) addProp('Этажность', `${{floorsVal}} эт.`);
    if (demandVal !== undefined && demandVal !== null) addProp('Отбор', `${{demandVal}} м³/ч`);
    if (dVal !== undefined && dVal !== null) addProp('Диаметр D', `${{dVal}} мм`);
    if (lVal !== undefined && lVal !== null) addProp('Длина L', `${{lVal}} м`);
    if (vVal !== undefined && vVal !== null) addProp('Скорость V', `${{vVal}} м/с`);
    if (iVal !== undefined && iVal !== null) addProp('Потери i', `${{iVal}} м/км`);
    if (cVal !== undefined && cVal !== null) addProp('Шероховатость C', `${{cVal}}`);
    if (qVal !== undefined && qVal !== null) addProp('Расход Q', `${{qVal}} м³/ч`);
    if (data.pump_type !== undefined) addProp('Тип агрегата', data.pump_type);
    if (data.q_work !== undefined && data.q_work !== '—') addProp('Q_раб', `${{data.q_work}} м³/ч`);
    if (data.h_work !== undefined && data.h_work !== '—') addProp('H_раб', `${{data.h_work}} м`);
  }}
  card.style.display = 'block';
}}

function closeMapCard() {{
  const card = document.getElementById('mapObjectCard');
  if (card) card.style.display = 'none';
}}

function focusMapObject(rawId) {{
  if (!rawId) return false;
  const idStr = String(rawId).trim();
  const rawKey = idToRaw.get(idStr) || idStr;
  const cleanKey = clean_object_id(idStr);

  const sec = document.getElementById('mapSection');
  if (sec) sec.scrollIntoView({{ behavior: 'smooth', block: 'start' }});

  const nodes = networkGeo.nodes || {{}};
  const node = nodes[rawKey] || nodes[cleanKey] || nodes[idStr];
  const link = linkLookup.get(rawKey) || linkLookup.get(cleanKey) || linkLookup.get(idStr);
  const pts = node ? [node] : link;

  if (!pts || !pts.length) {{
    networkInfo.textContent = `Объект ${{cleanKey}} не найден в координатах INP.`;
    return false;
  }}

  let xmin = Infinity, xmax = -Infinity, ymin = Infinity, ymax = -Infinity;
  pts.forEach(([x, y]) => {{
    if (x < xmin) xmin = x; if (x > xmax) xmax = x;
    if (y < ymin) ymin = y; if (y > ymax) ymax = y;
  }});
  const {{ w, h }} = mapSize();
  const spanX = xmax - xmin, spanY = ymax - ymin;
  let scale = networkView.baseScale * 14;
  if (pts.length > 1 && (spanX > 0 || spanY > 0)) {{
    scale = Math.min((w * 0.6) / Math.max(spanX, 1), (h * 0.6) / Math.max(spanY, 1), networkView.baseScale * 50);
  }}
  networkView.scale = Math.max(networkView.baseScale, scale);
  networkView.tx = w / 2 - (xmin + xmax) * networkView.scale / 2;
  networkView.ty = h / 2 + (ymin + ymax) * networkView.scale / 2;

  networkView.selected = idStr;
  networkView.selectedPoints = pts;
  networkView.multiHighlights = null;
  networkView.activeUntil = Date.now() + 6500;
  networkInfo.textContent = `Выделен объект ${{cleanKey}}`;

  // Look up in tables first, then fallback to node_props / pipe_props
  const findInData = (k, fld) => (DATA.tables[k] || []).find(r => r[fld] === rawKey || r[fld] === cleanKey || r['clean_id'] === cleanKey);
  let objData = findInData('nodes', 'node_id') || findInData('pipes', 'pipe_id') || findInData('pumps', 'pump_id');
  if (!objData) {{
    const np = DATA.node_props || {{}};
    const pp = DATA.pipe_props || {{}};
    objData = np[rawKey] || np[cleanKey] || np[idStr] || pp[rawKey] || pp[cleanKey] || pp[idStr];
  }}
  showObjectCard(cleanKey, objData);

  redrawBackground();
  return true;
}}

function onWarningClick(targetId, highlightIds) {{
  if (highlightIds && Array.isArray(highlightIds) && highlightIds.length > 0) {{
    const sec = document.getElementById('mapSection');
    if (sec) sec.scrollIntoView({{ behavior: 'smooth', block: 'start' }});
    highlightMultipleObjects(highlightIds);
    return;
  }}
  if (targetId) {{
    scrollToTarget(targetId);
  }}
}}

function highlightMultipleObjects(rawIds) {{
  if (!rawIds || !rawIds.length) return;
  const nodes = networkGeo.nodes || {{}};
  const validPoints = [];
  rawIds.forEach(id => {{
    const s = String(id).trim();
    const rawKey = idToRaw.get(s) || s;
    const cleanKey = clean_object_id(s);
    const p = nodes[rawKey] || nodes[cleanKey] || nodes[s];
    if (p) validPoints.push({{ id: cleanKey, pt: p }});
  }});

  if (!validPoints.length) {{
    networkInfo.textContent = 'Указанные объекты не найдены на карте сети.';
    return;
  }}

  let xmin = Infinity, xmax = -Infinity, ymin = Infinity, ymax = -Infinity;
  validPoints.forEach(({{ pt }}) => {{
    if (pt[0] < xmin) xmin = pt[0];
    if (pt[0] > xmax) xmax = pt[0];
    if (pt[1] < ymin) ymin = pt[1];
    if (pt[1] > ymax) ymax = pt[1];
  }});

  const {{ w, h }} = mapSize();
  const spanX = Math.max(xmax - xmin, 100);
  const spanY = Math.max(ymax - ymin, 100);
  const scale = Math.min((w * 0.7) / spanX, (h * 0.7) / spanY, networkView.baseScale * 25);

  networkView.scale = Math.max(networkView.baseScale, scale);
  networkView.tx = w / 2 - (xmin + xmax) * networkView.scale / 2;
  networkView.ty = h / 2 + (ymin + ymax) * networkView.scale / 2;

  networkView.selected = null;
  networkView.selectedPoints = null;
  networkView.multiHighlights = validPoints;
  networkView.activeUntil = Date.now() + 8500;
  networkInfo.textContent = `Подсвечено аномальных узлов: ${{validPoints.length}} (${{validPoints.map(v => v.id).join(', ')}})`;

  redrawBackground();
}}

/* Click hit-testing on networkMap */
networkCanvas.addEventListener('click', ev => {{
  if (networkView.dragging) return;
  const rect = networkCanvas.getBoundingClientRect();
  const sx = ev.clientX - rect.left, sy = ev.clientY - rect.top;
  const wx = (sx - networkView.tx) / networkView.scale;
  const wy = (networkView.ty - sy) / networkView.scale;

  // 1. Check nodes
  let bestNode = null, bestNodeDist = 14 / networkView.scale;
  for (const [nid, p] of Object.entries(networkGeo.nodes || {{}})) {{
    const d = Math.hypot(p[0] - wx, p[1] - wy);
    if (d < bestNodeDist) {{ bestNodeDist = d; bestNode = nid; }}
  }}
  if (bestNode) {{
    focusMapObject(bestNode);
    return;
  }}

  // 2. Check links
  let bestLink = null, bestLinkDist = 10 / networkView.scale;
  for (const [lid, pts] of (networkGeo.links || [])) {{
    if (!pts || pts.length < 2) continue;
    for (let i = 0; i < pts.length - 1; i++) {{
      const p1 = pts[i], p2 = pts[i+1];
      const d = distToSegment([wx, wy], p1, p2);
      if (d < bestLinkDist) {{ bestLinkDist = d; bestLink = lid; }}
    }}
  }}
  if (bestLink) {{
    focusMapObject(bestLink);
  }}
}});

function distToSegment(p, v, w) {{
  const l2 = (v[0]-w[0])**2 + (v[1]-w[1])**2;
  if (l2 === 0) return Math.hypot(p[0]-v[0], p[1]-v[1]);
  let t = ((p[0]-v[0])*(w[0]-v[0]) + (p[1]-v[1])*(w[1]-v[1])) / l2;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(p[0] - (v[0] + t*(w[0]-v[0])), p[1] - (v[1] + t*(w[1]-v[1])));
}}

/* Wheel & Pointer map drag */
networkCanvas.addEventListener('wheel', ev => {{
  ev.preventDefault();
  const rect = networkCanvas.getBoundingClientRect();
  zoomMap(ev.deltaY < 0 ? 1.25 : 0.8, ev.clientX - rect.left, ev.clientY - rect.top);
}}, {{ passive: false }});

networkCanvas.addEventListener('pointerdown', ev => {{
  networkView.dragging = true;
  networkView.px = ev.clientX; networkView.py = ev.clientY;
  networkCanvas.setPointerCapture(ev.pointerId);
}});
networkCanvas.addEventListener('pointermove', ev => {{
  if (!networkView.dragging) return;
  networkView.tx += ev.clientX - networkView.px;
  networkView.ty += ev.clientY - networkView.py;
  networkView.px = ev.clientX; networkView.py = ev.clientY;
  redrawBackground();
}});
networkCanvas.addEventListener('pointerup', () => {{ networkView.dragging = false; }});
networkCanvas.addEventListener('pointercancel', () => {{ networkView.dragging = false; }});

document.getElementById('mapPlus').onclick = () => {{ const {{ w, h }} = mapSize(); zoomMap(1.35, w/2, h/2); }};
document.getElementById('mapMinus').onclick = () => {{ const {{ w, h }} = mapSize(); zoomMap(1/1.35, w/2, h/2); }};
document.getElementById('mapReset').onclick = () => {{ networkView.selectedPoints = null; networkView.multiHighlights = null; closeMapCard(); mapFit(); }};
document.getElementById('mapFind').onclick = () => focusMapObject(document.getElementById('mapSearch').value);
document.getElementById('mapSearch').addEventListener('keydown', ev => {{ if (ev.key === 'Enter') focusMapObject(ev.target.value); }});

/* Clickable Object Links Delegation */
document.addEventListener('click', ev => {{
  const btn = ev.target.closest('[data-map-id]');
  if (btn) {{
    ev.preventDefault();
    focusMapObject(btn.dataset.mapId);
  }}
}});

/* Clean object ID helper for client text */
function clean_object_id(id) {{
  if (!id) return '';
  return String(id).replace(/^[ceputv](\\d+)$/i, '$1');
}}

function clean_object_name(nm) {{
  if (!nm) return '';
  return String(nm).replace(/\\s*\\(Google\\)/gi, '').trim();
}}

/* 6. INTERACTIVE DATA TABLES */
let currentTable = 'nodes';
let currentFilter = 'all';
let currentSearch = '';
let currentSortCol = '';
let currentSortAsc = true;
let currentPage = 0;
const pageSize = 50;

const tableSchemas = {{
  nodes: [
    {{ key: 'clean_id', label: 'ID узла', isId: true }},
    {{ key: 'name', label: 'Наименование' }},
    {{ key: 'elevation_m', label: 'Отметка Z, м' }},
    {{ key: 'floors', label: 'Этажность' }},
    {{ key: 'required_pressure_m', label: 'P_треб, м' }},
    {{ key: 'free_pressure_min_m', label: 'P_своб мин, м', isCrit: r => Number(r.free_pressure_min_m) < 0 }},
    {{ key: 'margin_m', label: 'Запас ΔP, м', isCrit: r => Number(r.margin_m) < 0 }},
    {{ key: 'demand_m3_h', label: 'Отбор, м³/ч' }}
  ],
  pipes: [
    {{ key: 'clean_id', label: 'ID трубы', isId: true }},
    {{ key: 'name', label: 'Наименование' }},
    {{ key: 'diameter_mm', label: 'Диаметр, мм' }},
    {{ key: 'length_m', label: 'Длина, м' }},
    {{ key: 'velocity_max_m_s', label: 'V_макс, м/с', isWarn: r => Number(r.velocity_max_m_s) > 1.5 }},
    {{ key: 'headloss_m_km', label: 'Потери i, м/км', isCrit: r => Number(r.headloss_m_km) > 20 }},
    {{ key: 'hazen_williams_c', label: 'Шероховатость C' }},
    {{ key: 'flow_m3_h', label: 'Расход, м³/ч' }}
  ],
  pumps: [
    {{ key: 'clean_id', label: 'ID насоса', isId: true }},
    {{ key: 'name', label: 'Наименование' }},
    {{ key: 'pump_type', label: 'Тип агрегата' }},
    {{ key: 'curve_id', label: 'Кривая Q-H' }},
    {{ key: 'q_work', label: 'Q_раб, м³/ч' }},
    {{ key: 'q_nom', label: 'Q_ном, м³/ч' }},
    {{ key: 'h_work', label: 'H_раб, м' }},
    {{ key: 'regime', label: 'Рабочий режим' }}
  ]
}};

const tableFilters = {{
  nodes: [
    {{ id: 'all', label: 'Все' }},
    {{ id: 'deficit', label: 'Дефицит (ΔP < 0)', fn: r => Number(r.margin_m) < 0 }},
    {{ id: 'negative', label: 'Отрицательное (< 0 м)', fn: r => Number(r.free_pressure_min_m) < 0 }},
    {{ id: 'high_floors', label: 'Высотная (≥ 5 эт.)', fn: r => Number(r.floors) >= 5 }}
  ],
  pipes: [
    {{ id: 'all', label: 'Все' }},
    {{ id: 'high_loss', label: 'Высокие потери (i ≥ 20)', fn: r => Number(r.headloss_m_km) >= 20 }},
    {{ id: 'high_v', label: 'Скорость V ≥ 1.5', fn: r => Number(r.velocity_max_m_s) >= 1.5 }},
    {{ id: 'low_v', label: 'Низкая скорость V < 0.1', fn: r => Number(r.velocity_max_m_s) < 0.1 && Number(r.velocity_max_m_s) >= 0 }}
  ],
  pumps: [
    {{ id: 'all', label: 'Все' }},
    {{ id: 'borehole', label: 'Скважинные', fn: r => r.is_borehole }},
    {{ id: 'booster', label: 'Сетевые / повысительные', fn: r => !r.is_borehole }}
  ]
}};

function switchTableTab(tab, btn) {{
  currentTable = tab;
  currentFilter = 'all';
  currentPage = 0;
  document.querySelectorAll('.table-tab-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  renderTableFilters();
  renderTable();
}}

function renderTableFilters() {{
  const container = document.getElementById('tableFilters');
  container.innerHTML = '';
  const flist = tableFilters[currentTable] || [];
  flist.forEach(f => {{
    const pill = document.createElement('button');
    pill.type = 'button';
    pill.className = 'filter-pill' + (f.id === currentFilter ? ' active' : '');
    pill.textContent = f.label;
    pill.onclick = () => {{
      currentFilter = f.id;
      currentPage = 0;
      renderTableFilters();
      renderTable();
    }};
    container.appendChild(pill);
  }});
}}

function onTableSearch(val) {{
  currentSearch = (val || '').toLowerCase();
  currentPage = 0;
  renderTable();
}}

function renderTable() {{
  const schema = tableSchemas[currentTable] || [];
  let rows = DATA.tables[currentTable] || [];

  // Filter
  const flist = tableFilters[currentTable] || [];
  const activeF = flist.find(f => f.id === currentFilter);
  if (activeF && activeF.fn) {{
    rows = rows.filter(activeF.fn);
  }}

  // Search
  if (currentSearch) {{
    rows = rows.filter(r => Object.values(r).some(v => String(v ?? '').toLowerCase().includes(currentSearch)));
  }}

  // Sort
  if (currentSortCol) {{
    rows.sort((a, b) => {{
      const va = a[currentSortCol] ?? '', vb = b[currentSortCol] ?? '';
      const na = Number(va), nb = Number(vb);
      if (!isNaN(na) && !isNaN(nb)) {{
        return currentSortAsc ? na - nb : nb - na;
      }}
      return currentSortAsc ? String(va).localeCompare(String(vb)) : String(vb).localeCompare(String(va));
    }});
  }}

  // Headers
  const thead = document.getElementById('mainTableHead');
  thead.innerHTML = '';
  const trH = thead.insertRow();
  schema.forEach(col => {{
    const th = document.createElement('th');
    th.textContent = col.label + (currentSortCol === col.key ? (currentSortAsc ? ' ▲' : ' ▼') : '');
    th.onclick = () => {{
      if (currentSortCol === col.key) currentSortAsc = !currentSortAsc;
      else {{ currentSortCol = col.key; currentSortAsc = true; }}
      renderTable();
    }};
    trH.appendChild(th);
  }});

  // Pagination
  const total = rows.length;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  currentPage = Math.min(currentPage, totalPages - 1);
  const start = currentPage * pageSize;
  const pageRows = rows.slice(start, start + pageSize);

  const tbody = document.getElementById('mainTableBody');
  tbody.innerHTML = '';
  if (!pageRows.length) {{
    const tr = tbody.insertRow();
    const td = tr.insertCell();
    td.colSpan = schema.length;
    td.style.textAlign = 'center';
    td.style.padding = '18px';
    td.style.color = '#94a3b8';
    td.textContent = 'Нет записей, соответствующих критериям фильтрации';
  }} else {{
    pageRows.forEach(r => {{
      const tr = tbody.insertRow();
      schema.forEach(col => {{
        const td = tr.insertCell();
        const v = r[col.key] ?? '—';
        if (col.isId && v !== '—') {{
          const b = document.createElement('button');
          b.type = 'button';
          b.className = 'object-link';
          b.dataset.mapId = String(r[currentTable.slice(0, -1) + '_id'] || v);
          b.title = 'Показать на карте';
          b.textContent = String(v);
          td.appendChild(b);
        }} else {{
          td.textContent = String(v);
        }}
        if (col.isCrit && col.isCrit(r)) td.className = 'num-crit';
        else if (col.isWarn && col.isWarn(r)) td.className = 'num-warn';
      }});
    }});
  }}

  document.getElementById('tablePageInfo').textContent = `Страница ${{currentPage + 1}} из ${{totalPages}} (всего ${{total}} записей)`;
}}

function changeTablePage(delta) {{
  currentPage = Math.max(0, currentPage + delta);
  renderTable();
}}

function downloadCurrentTableCSV() {{
  const schema = tableSchemas[currentTable] || [];
  const rows = DATA.tables[currentTable] || [];
  if (!rows.length) return;
  const keys = schema.map(s => s.key);
  const labels = schema.map(s => s.label);
  const escapeCsv = v => '"' + String(v ?? '').replace(/"/g, '""') + '"';
  const csvLines = [labels.map(escapeCsv).join(';')];
  rows.forEach(r => {{
    csvLines.push(keys.map(k => escapeCsv(r[k])).join(';'));
  }});
  const blob = new Blob(['\\ufeff' + csvLines.join('\\r\\n')], {{ type: 'text/csv;charset=utf-8' }});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `${{currentTable}}_${{DATA.city_name || 'network'}}.csv`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}}

/* Navigation & Accordions */
function scrollToTarget(id) {{
  const el = document.getElementById(id);
  if (el) el.scrollIntoView({{ behavior: 'smooth', block: 'start' }});
}}

function toggleAccordion(id) {{
  const el = document.getElementById(id);
  if (!el) return;
  el.style.display = el.style.display === 'none' ? 'block' : 'none';
}}

/* Init Application */
function initReport() {{
  // Populate Q-H select
  const sel = document.getElementById('qhSelect');
  const curves = DATA.qh_curves || [];
  sel.innerHTML = '';
  curves.forEach((c, idx) => {{
    const opt = document.createElement('option');
    opt.value = idx;
    const cid = clean_object_id(c.pump_id);
    const kind = c.is_borehole ? '[Скваж]' : '[Сетев]';
    const status = c.active ? '[РАБ]' : '[ВЫКЛ]';
    opt.textContent = `${{cid}} ${{kind}} ${{status}}`;
    sel.appendChild(opt);
  }});

  // Render charts
  drawQHCurve(0);
  drawPressureBand();
  drawDiurnalFlows();
  drawStagnationChart();

  // Render Map
  mapFit();

  // Render Tables
  renderTableFilters();
  renderTable();

  // Attach hover listeners for chart tooltips
  const qhCanvas = document.getElementById('qhCanvas');
  if (qhCanvas) {{
    qhCanvas.addEventListener('mousemove', ev => {{
      if (!qhPlotMeta) return;
      const rect = qhCanvas.getBoundingClientRect();
      const mx = ev.clientX - rect.left;
      const my = ev.clientY - rect.top;
      const {{ pad, pw, ph, maxQ, maxH, curvesToPlot }} = qhPlotMeta;
      if (mx < pad.l || mx > pad.l + pw || my < pad.t || my > pad.t + ph) {{ hideChartTooltip(); return; }}

      for (const c of curvesToPlot) {{
        if (c.active && c.operating_q_mean !== null && c.operating_h_est !== null) {{
          const ox = pad.l + (c.operating_q_mean / maxQ) * pw;
          const oy = pad.t + ph - (c.operating_h_est / maxH) * ph;
          if (Math.hypot(mx - ox, my - oy) < 14) {{
            const cid = clean_object_id(c.pump_id);
            const kind = c.is_borehole ? 'Скважинный' : 'Сетевой';
            showChartTooltip(ev, `<b>Рабочая точка: насос ${{cid}}</b> (${{kind}})<br>Подача: <b>${{c.operating_q_mean}} м³/ч</b><br>Развиваемый напор: <b>${{c.operating_h_est}} м</b><br>Номинал: Q=${{c.q_nominal_m3_h || '—'}}, H=${{c.h_nominal_m || '—'}}`);
            return;
          }}
        }}
      }}

      const curQ = ((mx - pad.l) / pw) * maxQ;
      const curH = ((pad.t + ph - my) / ph) * maxH;
      showChartTooltip(ev, `Подача Q: <b>${{curQ.toFixed(1)}} м³/ч</b><br>Напор H: <b>${{curH.toFixed(1)}} м</b>`);
    }});
    qhCanvas.addEventListener('mouseleave', hideChartTooltip);
  }}

  const pbCanvas = document.getElementById('pressureBandCanvas');
  if (pbCanvas) {{
    pbCanvas.addEventListener('mousemove', ev => {{
      if (!pressureBandMeta) return;
      const rect = pbCanvas.getBoundingClientRect();
      const mx = ev.clientX - rect.left;
      const {{ pad, pw, n, pb }} = pressureBandMeta;
      if (mx < pad.l || mx > pad.l + pw) {{ hideChartTooltip(); return; }}
      const idx = Math.max(0, Math.min(n - 1, Math.round(((mx - pad.l) / pw) * (n - 1))));
      const hr = (DATA.hours_labels || [])[idx] || `${{idx}}:00`;
      const p50 = pb.p50 && pb.p50[idx] !== null ? `${{pb.p50[idx]}} м` : '—';
      const p10 = pb.p10 && pb.p10[idx] !== null ? `${{pb.p10[idx]}} м` : '—';
      const p90 = pb.p90 && pb.p90[idx] !== null ? `${{pb.p90[idx]}} м` : '—';
      showChartTooltip(ev, `<b>Время: ${{hr}}</b><br>Медиана свободного напора P50: <b>${{p50}}</b><br>10-й процентиль: ${{p10}}<br>90-й процентиль: ${{p90}}`);
    }});
    pbCanvas.addEventListener('mouseleave', hideChartTooltip);
  }}

  const dfCanvas = document.getElementById('diurnalFlowCanvas');
  if (dfCanvas) {{
    dfCanvas.addEventListener('mousemove', ev => {{
      if (!diurnalFlowMeta) return;
      const rect = dfCanvas.getBoundingClientRect();
      const mx = ev.clientX - rect.left;
      const {{ pad, pw, n, dem, pmp, hrs }} = diurnalFlowMeta;
      if (mx < pad.l || mx > pad.l + pw) {{ hideChartTooltip(); return; }}
      const idx = Math.max(0, Math.min(n - 1, Math.round(((mx - pad.l) / pw) * (n - 1))));
      const hr = hrs[idx] || `${{idx}}:00`;
      const dVal = dem[idx] !== undefined && dem[idx] !== null ? `${{dem[idx].toFixed(1)}} м³/ч` : '—';
      const pVal = pmp[idx] !== undefined && pmp[idx] !== null ? `${{pmp[idx].toFixed(1)}} м³/ч` : '—';
      showChartTooltip(ev, `<b>Время: ${{hr}}</b><br>Водопотребление сети: <b>${{dVal}}</b><br>Подача насосных станций: <b>${{pVal}}</b>`);
    }});
    dfCanvas.addEventListener('mouseleave', hideChartTooltip);
  }}

  const stagCanvas = document.getElementById('stagnationCanvas');
  if (stagCanvas) {{
    stagCanvas.addEventListener('mousemove', ev => {{
      if (!stagnationMeta) return;
      const rect = stagCanvas.getBoundingClientRect();
      const mx = ev.clientX - rect.left;
      const my = ev.clientY - rect.top;
      const dist = Math.hypot(mx - stagnationMeta.cx, my - stagnationMeta.cy);
      if (dist >= stagnationMeta.innerR && dist <= stagnationMeta.outerR) {{
        let angle = Math.atan2(my - stagnationMeta.cy, mx - stagnationMeta.cx);
        if (angle < -Math.PI / 2) angle += Math.PI * 2;
        const found = stagnationMeta.slices.find(s => angle >= s.startA && angle < s.endA);
        if (found) {{
          const pct = (found.ratio * 100).toFixed(1);
          showChartTooltip(ev, `<b>${{found.label}}</b><br>Количество участков: <b>${{found.val.toLocaleString('ru-RU')}}</b><br>Доля в сети: <b>${{pct}}%</b>`);
          return;
        }}
      }}
      if (mx >= stagnationMeta.legX && mx <= rect.width - 10) {{
        const idx = Math.floor((my - stagnationMeta.legStartY) / 28);
        if (idx >= 0 && idx < stagnationMeta.items.length) {{
          const item = stagnationMeta.items[idx];
          const pct = (item.ratio * 100).toFixed(1);
          showChartTooltip(ev, `<b>${{item.label}}</b><br>Количество участков: <b>${{item.val.toLocaleString('ru-RU')}}</b><br>Доля в сети: <b>${{pct}}%</b>`);
          return;
        }}
      }}
      hideChartTooltip();
    }});
    stagCanvas.addEventListener('mouseleave', hideChartTooltip);
  }}
}}

window.addEventListener('DOMContentLoaded', initReport);
window.addEventListener('resize', () => {{
  drawQHCurve(currentQHIndex);
  drawPressureBand();
  drawDiurnalFlows();
  drawStagnationChart();
  mapFit();
}});
setInterval(() => {{
  if ((networkView.selectedPoints || networkView.multiHighlights) && Date.now() < networkView.activeUntil + 500) redrawOverlay();
}}, 350);
</script>

</body>
</html>
"""

    if output_html_path is not None:
        out_p = Path(output_html_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(html_doc, encoding="utf-8")
    return html_doc
