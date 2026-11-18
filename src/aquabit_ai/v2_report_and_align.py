from __future__ import annotations

"""EPANET-AI v3 Interactive HTML Report Generator with Crosshair Projections,
Verifiable Anomaly Highlighting, Dynamic AI Layout, Valve Dashboards & Map Timeline."""

import datetime as dt
import html
import json
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


def generate_reference_pure_facts_report(
    facts: dict[str, Any],
    ranked_domains: list[dict[str, Any]] | None = None,
    ontology: dict[str, Any] | None = None,
    ontology_path: str | Path | None = None,
) -> str:
    """Формирует концептуальный ИИ-отчет: меньше сырых чисел, больше общей картины города,
    описания графиков/таблиц, разбора ярких аномалий и инженерных мероприятий."""
    cname = facts.get("city_name", "Сеть")
    pa = facts.get("pressure_analysis", {})
    ppa = facts.get("pipes_analysis", {})
    pma = facts.get("pumps_analysis", {})
    vra = facts.get("valves_and_reservoirs_analysis", {})
    wb = facts.get("water_balance", {})

    onto = ontology if ontology is not None else load_hydraulic_ontology(ontology_path)
    rules = evaluate_ontology_rules(facts, onto)
    analysis = analyze_problem_domains(facts, rules, ontology=onto)
    ranked = ranked_domains or analysis["ranked_domains"]

    tot_km = ppa.get("total_length_km", 0.0)
    p_cnt = ppa.get("total_pipes", 0)
    cons_m3 = wb.get("total_daily_consumption_m3_day", 0.0)
    peak_q = wb.get("peak_network_demand_m3_h", 0.0)
    inst_q = wb.get("pumps_installed_capacity_m3_h", 0.0)
    bh_tot = pma.get("borehole_total_count", 0)
    tot_pumps = pma.get("total_pumps", 0)
    valves_cnt = vra.get("valves_count", 0)
    tanks_cnt = vra.get("tanks_count", 0)
    src_cnt = vra.get("sources_count", 0)

    lines = ["# ИИ отчет"]
    lines.append(
        f"Комплексная диагностика расчетной схемы водоснабжения города «{cname}» выявила выраженную структурную неоднородность гидравлического режима. "
        f"Транспортировка воды по сети протяженностью {tot_km:,.1f} км ({p_cnt:,} участков) при суточном водопотреблении {cons_m3:,.0f} м³/сут "
        f"сопровождается локальными перепадами пьезометрического напора между зоной генерации (источниками: {src_cnt} шт., РЧВ: {tanks_cnt} шт., "
        f"насосными агрегатами: {tot_pumps} шт., из которых скважинных 1-го подъема — {bh_tot} шт.) и периферийными кварталами абонентской застройки. "
        f"Ниже структура отчета и порядок дашбордов автоматически ранжированы ИИ по правилам TTL-онтологии для города «{cname}»."
    )

    section_order = []
    for idx, d in enumerate(ranked, 1):
        dom = d["domain"]
        if dom == "pressure_deficit":
            section_order.append("pressureSection")
            w_def = pa.get("worst_deficit_nodes", [])
            top_d = w_def[0] if w_def else {}
            top_id = clean_object_id(top_d.get("node_id", ""))
            top_val = top_d.get("pressure_surplus_deficit_m", 0.0)
            def_cnt = pa.get("real_required_pressure_deficit_count", 0)
            err_cnt = pa.get("data_error_below_minus_3m_count", 0)
            exc_cnt = pa.get("excess_over_60m_count", 0)
            lines.append(f"\n## Приоритет {idx} - Обеспеченность этажности застройки свободным напором и анализ зоны депрессии")
            if def_cnt > 0 and top_id:
                lines.append(
                    f"- **Общая картина по городу и связь с графиком дефицитов:** На графике распределения свободного напора и в реестре абонентов выделяется кластер из {def_cnt} абонентских узлов, "
                    f"в которых фактический свободный напор проседает ниже нормативной потребности здания. Самая яркая аномалия зафиксирована в лимитирующем узле `{top_id}`, "
                    f"где нехватка давления составляет ΔP = {top_val:.1f} м (при требуемом напоре {top_d.get('required_pressure_m', 10):.1f} м для {top_d.get('floors', 1)}-этажной застройки)."
                )
            if err_cnt > 0:
                w_err = pa.get("worst_data_error_nodes", [{}])[0]
                err_id = clean_object_id(w_err.get("node_id", ""))
                lines.append(
                    f"- **Критическое отрицательное давление (< -3.0 м, правило R02):** Зафиксировано {err_cnt} активных узлов с вакуумом ниже -3.0 м (наиболее глубокая просадка в узле `{err_id}`: {w_err.get('free_pressure_min_m', 0):.1f} м). "
                    "Необходимо проверить отметку земли Z и связность ветки (отсутствие отсечения закрытой задвижкой)."
                )
            if exc_cnt > 0:
                w_exc = pa.get("top_excess_nodes", [{}])[0]
                exc_id = clean_object_id(w_exc.get("node_id", ""))
                lines.append(
                    f"- **Зона избыточного напора (> 60 м по СП 31.13330.2021, правило R16):** Выявлено {exc_cnt} узлов с давлением выше 60 м (пик в узле `{exc_id}`: {w_exc.get('free_pressure_max_m', 0):.1f} м), что требует зонирования редукционными клапанами PRV."
                )
            if top_id:
                lines.append(
                    f"- **Причина возникновения и инженерное решение:** Просадка пьезометра вокруг узла `{top_id}` обусловлена наложением высокого геодезического рельефа и потерь напора в распределительных линиях. "
                    "Для устранения дефицита необходимо перераспределить напор через закольцовку подводящих линий либо предусмотреть локальное бустерное повышение давления (ПНС) на вводе в квартал."
                )

        elif dom == "pump_imbalance":
            section_order.append("qhSection")
            under = pma.get("underloaded_pumps", [])
            over = pma.get("overloaded_pumps", [])
            bh_under = [p for p in under if p.get("is_borehole")]
            boost_under = [p for p in under if not p.get("is_borehole")]
            lines.append(f"\n## Приоритет {idx} - Диагностика скважинных и сетевых насосов и разрыв мощности с расходом сети")
            lines.append(
                f"- **Баланс установленной мощности и расхода сети:** Сопоставление установленной мощности насосного парка ({inst_q:,.1f} м³/ч) с реальным пиковым расходом сети ({peak_q:,.1f} м³/ч) "
                f"показывает разрыв в {abs(inst_q - peak_q):,.1f} м³/ч. На графике кривых Q-H насосы разделены на **скважинные агрегаты 1-го подъема** ({bh_tot} шт., начальный узел является Источником) и **сетевые насосы НС**."
            )
            if bh_under:
                p0 = clean_object_id(bh_under[0].get("pump_id"))
                lines.append(
                    f"- **Аномалия недогрузки скважинных насосов (на примере насоса `{p0}`):** Насосы в недогрузке не могут выдать свой расчетный поток в сеть. "
                    f"Поскольку агрегат `{p0}` является **скважинным** (его начальный узел — Источник), **у него физически нет задвижек на входе**, и проверять входную арматуру некорректно. "
                    "Ограничение подачи вызвано снижением динамического уровня воды в скважине либо завышенным противодавлением в сборном напорном коллекторе."
                )
            if boost_under:
                p_b = clean_object_id(boost_under[0].get("pump_id"))
                lines.append(
                    f"- **Аномалия недогрузки сетевых насосов (на примере насоса `{p_b}`):** Сетевой агрегат `{p_b}` работает левее оптимальной зоны BEP и не выдает свой поток — "
                    "здесь проблема вероятнее всего кроется в прикрытой задвижке на линии насоса или гидравлическом зажатии напорного узла."
                )
            if over:
                p_o = clean_object_id(over[0].get("pump_id"))
                lines.append(
                    f"- **Аномалия перегрузки насосов (на примере насоса `{p_o}`):** Агрегат `{p_o}` смещен правее зоны BEP (> 115% номинала). "
                    "При перегрузке проблема **точно не в задвижке**. Для нормализации режима необходимо проверить возможность уменьшения подачи насоса за счет **уменьшения давления на выходе** "
                    "(корректировка уставки ЧРП или регулирование давления на нагнетании), а не за счет манипуляций с задвижками на входе или отключения насоса в час пик."
                )

        elif dom == "hydraulic_bottlenecks":
            section_order.append("bottlenecksSection")
            high_loss = ppa.get("top_high_loss_pipes", [])
            w_p = high_loss[0] if high_loss else {}
            w_id = clean_object_id(w_p.get("pipe_id", ""))
            lines.append(f"\n## Приоритет {idx} - Гидравлические узкие места и аномалии удельных потерь напора")
            lines.append(
                f"- **Интерпретация графика узких мест:** На диаграмме удельных потерь напора выделяется лимитирующий участок `{w_id}` "
                f"(диаметр D={w_p.get('diameter_mm', 0):.0f} мм, C={w_p.get('hazen_williams_c', 100)}), где потери достигают {w_p.get('headloss_gradient_m_km', 0):.1f} м/км при скорости {w_p.get('velocity_max_m_s', 0):.2f} м/с. "
                "Именно такие локальные дроссели поглощают полезный напор насосных станций на пути к жилым кварталам."
            )
            lines.append(
                f"- **Инженерные мероприятия:** Выполнить перекладку или санацию участка `{w_id}` с увеличением внутреннего диаметра либо перераспределить поток по параллельной закольцованной ветке."
            )

        elif dom == "water_stagnation":
            section_order.append("stagnationSection")
            stag_cnt = ppa.get("low_velocity_under_01ms_count", 0)
            closed_cnt = ppa.get("closed_zero_flow_pipes_count", 0)
            lines.append(f"\n## Приоритет {idx} - Структура скоростного режима, закрытые задвижками участки и застойные зоны")
            lines.append(
                f"- **Анализ круговой диаграммы скоростей:** Круговая диаграмма показывает, что помимо оптимального транзитного ядра в сети присутствуют {closed_cnt} участков с полностью нулевым потоком (Q = 0) "
                f"и {stag_cnt} малопроточных участков со скоростью 0 < V < 0.10 м/с. Если в трубе поток не идет (Q = 0), значит участок перекрыт секущей задвижкой."
            )
            lines.append(
                "- **Инженерные мероприятия:** Провести ревизию запорной арматуры на перекрытых участках для восстановления кольцевого движения воды и устранить тупиковые зоны с риском вторичного ухудшения качества воды."
            )

        elif dom == "valves_and_storage":
            section_order.append("valvesSection")
            v_tbl = vra.get("valves_table", [])
            top_v = max(v_tbl, key=lambda x: x.get("flow_swing_m3_h", 0)) if v_tbl else {}
            v_id = top_v.get("clean_id", "")
            lines.append(f"\n## Приоритет {idx} - Режим работы регулирующих клапанов (PRV/PSV), источников и РЧВ")
            lines.append(
                f"- **Динамика регулирующей арматуры и емкостей:** В расчетной схеме задействовано {valves_cnt} клапанов, {src_cnt} источников и {tanks_cnt} резервуаров РЧВ. "
                + (f"На графике потоков через клапаны наиболее интенсивный режим регулирования демонстрирует клапан `{v_id}` (тип {top_v.get('type_code')}, настройка {top_v.get('setting')}), пропускающий от {top_v.get('flow_min_m3_h')} до {top_v.get('flow_max_m3_h')} м³/ч. " if v_id else "")
                + "Согласованная настройка уставок клапанов PRV и уровней РЧВ позволяет стабилизировать давление в диктующих точках без перегрузки насосных станций."
            )

        elif dom == "diurnal_imbalance":
            section_order.append("diurnalSection")
            lines.append(f"\n## Приоритет {idx} - Суточная динамика подачи и аккумулирующая способность сети")
            lines.append(
                "- **Согласование графиков подачи и потребления:** График суточного баланса отражает соотношение между подачей насосных станций и фактическим отбором абонентов по часам суток. "
                "Сглаживание пиковых нагрузок должно обеспечиваться регулирующим объемом РЧВ и плавным частотным регулированием напорных агрегатов."
            )

    lines.append("\n[SELECTED_DASHBOARDS]")
    lines.append(json.dumps({
        "section_order": section_order,
        "dashboards": [{"id": sec, "priority": i} for i, sec in enumerate(section_order, 1)],
    }, ensure_ascii=False))
    return "\n".join(lines)


def _markdown_to_clean_html(md: str) -> str:
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
            content = re.sub(r"\*\*(.*?)\*\*", r"<strong>\1</strong>", s[2:].strip())
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

    def repl_id(m):
        raw = m.group(1)
        clean = clean_object_id(raw)
        return f'<button type="button" class="object-link" data-map-id="{raw}" title="Показать на графике и карте">{clean}</button>'

    raw_html = re.sub(r"<code>([ceputvk]?\d+)</code>", repl_id, raw_html, flags=re.IGNORECASE)
    return raw_html


def render_standalone_html_report(
    facts: dict[str, Any],
    chart_payload: dict[str, Any],
    ai_markdown: str,
    output_html_path: Path | None = None,
    generation_meta: dict[str, Any] | None = None,
    selected_dashboards_spec: dict[str, Any] | None = None,
) -> str:
    cname = facts.get("city_name", "Сеть")
    ns = facts.get("nodes_summary", {})
    pa = facts.get("pressure_analysis", {})
    ppa = facts.get("pipes_analysis", {})
    pma = facts.get("pumps_analysis", {})
    vra = facts.get("valves_and_reservoirs_analysis", {})
    wb = facts.get("water_balance", {})
    meta = generation_meta or {}

    clean_md_text, parsed_dashboards = extract_ai_dashboards_and_content(ai_markdown)
    if selected_dashboards_spec and selected_dashboards_spec.get("section_order"):
        parsed_dashboards["section_order"] = selected_dashboards_spec["section_order"]
    ai_html = _markdown_to_clean_html(clean_md_text)

    gen_ts = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    proc_s = facts.get("processing_time_sec", 0.0)
    llm_s = meta.get("inference_sec")
    total_s = round(float(proc_s) + float(llm_s or 0.0), 1)

    geo = chart_payload.get("geo") or {"nodes": {}, "links": []}
    informative_charts = chart_payload.get("informative_charts") or {}

    all_pumps = pma.get("all_pumps_table", [])
    valves_table = vra.get("valves_table", [])
    sources_table = vra.get("sources_table", [])
    tanks_table = vra.get("tanks_table", [])

    raw_table_nodes = (
        (pa.get("all_deficit_nodes") or pa.get("worst_deficit_nodes", []))
        + pa.get("worst_data_error_nodes", [])
        + pa.get("top_excess_nodes", [])
    )
    seen_node_ids: set[str] = set()
    dedup_nodes = []
    for r in raw_table_nodes:
        nid = str(r.get("node_id") or "")
        if nid and nid not in seen_node_ids:
            seen_node_ids.add(nid)
            dedup_nodes.append(r)

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
                "demand_m3_h": r.get("demand_mean_m3_h", "—"),
            }
            for r in dedup_nodes
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
                "flow_m3_h": p.get("flow_max_m3_h", "—"),
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
                "start_node": p.get("start_node", "—"),
                "curve_id": p.get("curve_id", "—"),
                "q_work": p.get("q_working_m3_h", "—"),
                "q_nom": p.get("q_nominal_m3_h", "—"),
                "h_work": p.get("h_working_m", p.get("h_est_m", "—")),
                "regime": p.get("regime", "—"),
                "advice": p.get("diagnostic_advice", "—"),
            }
            for p in all_pumps
        ],
        "valves": [
            {
                "valve_id": v.get("valve_id"),
                "clean_id": v.get("clean_id"),
                "type_code": f"{v.get('type_code')} ({v.get('type_id')})",
                "type_desc": v.get("type_desc"),
                "setting": v.get("setting", "—"),
                "flow_min": v.get("flow_min_m3_h", 0),
                "flow_mean": v.get("flow_mean_m3_h", 0),
                "flow_max": v.get("flow_max_m3_h", 0),
                "flow_swing": v.get("flow_swing_m3_h", 0),
                "status": v.get("status", "—"),
            }
            for v in valves_table
        ],
    }

    warnings = []
    err_cnt = pa.get("data_error_below_minus_3m_count", 0)
    if err_cnt > 0 and pa.get("worst_data_error_nodes"):
        worst_e = pa["worst_data_error_nodes"][0]
        we_id = clean_object_id(worst_e.get("node_id"))
        warnings.append({
            "kind": "data_error",
            "level": "crit",
            "title": "Критическое отрицательное давление (< -3.0 м) — ошибка данных или отсечение участка",
            "desc": f"Обнаружено {err_cnt} активных узлов с P < -3.0 м. Минимальный напор в узле {we_id} ({worst_e.get('free_pressure_min_m', 0):.1f} м). Нажмите для перехода к строке в таблице узлов.",
            "target": "tablesSection",
            "focus_id": we_id,
            "highlight_ids": [clean_object_id(r.get("node_id")) for r in pa["worst_data_error_nodes"]],
        })

    def_cnt = pa.get("real_required_pressure_deficit_count", 0)
    def_pts = chart_payload.get("deficit_nodes_points") or []
    if def_cnt > 0 and def_pts:
        worst_d = pa.get("worst_deficit_nodes", [{}])[0]
        worst_id = clean_object_id(worst_d.get("node_id"))
        all_def_ids = [clean_object_id(r.get("node_id")) for r in def_pts]
        warnings.append({
            "kind": "pressure_deficit",
            "level": "crit",
            "title": "Дефицит свободного напора и необеспеченность этажности застройки",
            "desc": f"Выявлено {def_cnt} абонентов с дефицитом давления. Лимитирующий узел {worst_id} с нехваткой ΔP = {worst_d.get('pressure_surplus_deficit_m', 0):.1f} м (нажмите для подсветки всех {len(all_def_ids)} точек на графике и в таблице).",
            "target": "pressureSection",
            "focus_id": worst_id,
            "highlight_ids": all_def_ids,
        })

    bneck_pts = chart_payload.get("bottleneck_pipes_points") or []
    if bneck_pts and ((bneck_pts[0].get("i") or 0) >= 15.0 or (bneck_pts[0].get("v") or 0) >= 5.0):
        worst_p = bneck_pts[0]
        wp_id = worst_p["clean_id"]
        warnings.append({
            "kind": "pipe_bottlenecks",
            "level": "warn",
            "title": "Гидравлические узкие места и завышенные удельные потери напора",
            "desc": f"Лимитирующий участок {wp_id} (D={worst_p.get('d')} мм) создает потери {worst_p.get('i', 0):.1f} м/км при скорости {worst_p.get('v', 0):.2f} м/с (нажмите для подсветки на графике потерь и в таблице).",
            "target": "bottlenecksSection",
            "focus_id": wp_id,
            "highlight_ids": [p["clean_id"] for p in bneck_pts[:30]],
        })

    under_p = pma.get("underloaded_pumps", [])
    over_p = pma.get("overloaded_pumps", [])
    if (under_p or over_p) and informative_charts.get("pump_qh"):
        focus_p = clean_object_id((over_p + under_p)[0].get("pump_id"))
        bh_cnt = pma.get("borehole_total_count", 0)
        warnings.append({
            "kind": "pump_qh",
            "level": "warn",
            "title": "Отклонение рабочих точек насосов от зоны BEP и режим скважинных агрегатов",
            "desc": f"В недогрузке: {len(under_p)} шт., в перегрузке: {len(over_p)} шт. (скважинных 1-го подъема: {bh_cnt} шт.). Нажмите для подсветки агрегата {focus_p} на кривой Q-H.",
            "target": "qhSection",
            "focus_id": focus_p,
            "highlight_ids": [clean_object_id(p.get("pump_id")) for p in (over_p + under_p)],
        })

    if valves_table and chart_payload.get("valves_hourly_series"):
        top_v = max(valves_table, key=lambda x: x.get("flow_swing_m3_h", 0))
        warnings.append({
            "kind": "valves",
            "level": "warn" if top_v.get("flow_swing_m3_h", 0) > 50 else "ok",
            "title": "Режим работы регулирующих клапанов (PRV/PSV), источников и РЧВ",
            "desc": f"В схеме {len(valves_table)} клапанов, {len(sources_table)} источников, {len(tanks_table)} РЧВ. Клапан {top_v['clean_id']} ({top_v['type_code']}, уст. {top_v['setting']}): поток от {top_v['flow_min_m3_h']} до {top_v['flow_max_m3_h']} м³/ч.",
            "target": "valvesSection",
            "focus_id": top_v["clean_id"],
            "highlight_ids": [v["clean_id"] for v in valves_table],
        })

    kpi_closed_pipes = ppa.get("closed_zero_flow_pipes_count", 0)
    kpi_v_stag = ppa.get("low_velocity_under_01ms_count", 0)
    if (kpi_closed_pipes > 0 or kpi_v_stag > 10) and informative_charts.get("low_velocity_stagnation"):
        warnings.append({
            "kind": "stagnation",
            "level": "warn",
            "title": "Закрытые задвижками участки (Q = 0) и зоны пониженных скоростей (< 0.1 м/с)",
            "desc": f"Перекрыто задвижкой: {kpi_closed_pipes} участков, малопроточных участков: {kpi_v_stag} шт. Нажмите для перехода к диаграмме скоростей.",
            "target": "stagnationSection",
            "focus_id": None,
            "highlight_ids": [],
        })

    kpi_excess = pa.get("excess_over_60m_count", 0)
    if kpi_excess > 0 and pa.get("top_excess_nodes"):
        top_ex = pa["top_excess_nodes"][0]
        ex_id = clean_object_id(top_ex.get("node_id"))
        warnings.append({
            "kind": "excess_pressure",
            "level": "warn",
            "title": "Избыточный свободный напор (> 60 м по СП 31.13330.2021)",
            "desc": f"Выявлено {kpi_excess} узлов с давлением выше 60 м (максимум в узле {ex_id}: {top_ex.get('free_pressure_max_m', 0):.1f} м). Нажмите для перехода к таблице узлов.",
            "target": "tablesSection",
            "focus_id": ex_id,
            "highlight_ids": [clean_object_id(r.get("node_id")) for r in pa["top_excess_nodes"]],
        })

    warnings_html = "".join(
        f'<div class="warning-card warning-{w["level"]}" onclick=\'onWarningClick({json.dumps(w, ensure_ascii=False)})\'>'
        f'  <div class="warning-header">'
        f'    <div class="warning-title">{html.escape(w["title"])}</div>'
        f'    <span class="warning-link">Подсветить на графике и в таблице →</span>'
        f'  </div>'
        f'  <div class="warning-desc">{html.escape(w["desc"])}</div>'
        f'</div>'
        for w in warnings
    )

    kpi_peak = wb.get("peak_network_demand_m3_h") or ns.get("peak_hourly_network_demand_m3_h", 0.0)
    kpi_inst_q = wb.get("pumps_installed_capacity_m3_h", 0.0)
    kpi_active_nodes = wb.get("active_consumer_nodes") or ns.get("active_consumer_nodes_count", 0)
    kpi_total_nodes = wb.get("total_nodes_in_model", kpi_active_nodes)

    p_min_val = pa.get("free_pressure_min_m")
    p_max_val = pa.get("free_pressure_max_m")
    p_min_str = f"{p_min_val:.1f}" if isinstance(p_min_val, (int, float)) else "—"
    p_max_str = f"{p_max_val:.1f}" if isinstance(p_max_val, (int, float)) else "—"

    kpi_pipes_km = ppa.get("total_length_km", 0.0)
    kpi_pipes_cnt = ppa.get("total_pipes", 0)

    kpi_max_v = ppa.get("max_velocity_m_s")
    kpi_max_v_str = f"{kpi_max_v:.2f}" if isinstance(kpi_max_v, (int, float)) else "—"

    kpi_active_pumps = pma.get("active_pumps_count", 0)
    kpi_total_pumps = pma.get("total_pumps", 0)
    kpi_bh_pumps = pma.get("borehole_total_count", 0)

    payload_dict = {
        "city_name": cname,
        "hours_labels": chart_payload.get("hours_labels", [f"{h:02d}:00" for h in range(24)]),
        "step_labels": chart_payload.get("step_labels", [f"R{h}" for h in range(24)]),
        "diurnal_demand_m3_h": chart_payload.get("diurnal_demand_m3_h"),
        "diurnal_pump_flow_m3_h": chart_payload.get("diurnal_pump_flow_m3_h"),
        "valves_hourly_series": chart_payload.get("valves_hourly_series", []),
        "diurnal_free_pressure_percentiles_m": chart_payload.get("diurnal_free_pressure_percentiles_m"),
        "node_hourly_pressures": chart_payload.get("node_hourly_pressures", {}),
        "deficit_nodes_points": def_pts,
        "bottleneck_pipes_points": bneck_pts,
        "qh_curves": chart_payload.get("qh_curves", []),
        "pressure_bins": chart_payload.get("pressure_bins", {}),
        "velocity_bins": chart_payload.get("velocity_bins", {}),
        "node_props": chart_payload.get("node_props", {}),
        "pipe_props": chart_payload.get("pipe_props", {}),
        "pipes_count": kpi_pipes_cnt,
        "informative_charts": informative_charts,
        "section_order": parsed_dashboards.get("section_order", []),
        "geo": geo,
        "tables": client_tables,
        "sources_table": sources_table,
        "tanks_table": tanks_table,
        "data_export_warnings": facts.get("data_export_warnings", []),
    }
    payload_json = json.dumps(payload_dict, ensure_ascii=False).replace("<", "\\u003c")

    html_doc = f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Гидравлический паспорт и ИИ-диагностика сети — {html.escape(cname)}</title>
<style>
  :root {{
    --bg: #f8fafc; --card: #ffffff; --ink: #0f172a; --muted: #475569;
    --line: #e2e8f0; --brand: #0284c7; --ok: #10b981; --warn: #f59e0b; --crit: #ef4444; --radius: 12px;
  }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: var(--bg); color: var(--ink); line-height: 1.55; }}
  .container {{ max-width: 1400px; margin: 0 auto; padding: 22px 20px 60px; }}
  .hero {{ background: linear-gradient(135deg, #0f172a 0%, #1e3a8a 100%); color: #fff; border-radius: var(--radius); padding: 24px 30px; display: flex; justify-content: space-between; align-items: center; gap: 20px; flex-wrap: wrap; }}
  .hero h1 {{ margin: 0 0 6px; font-size: 24px; font-weight: 700; }}
  .hero-sub {{ margin: 0; color: #cbd5e1; font-size: 13.5px; }}
  .btn-print {{ background: #0284c7; color: #fff; border: none; padding: 8px 16px; border-radius: 8px; font-weight: 600; font-size: 13px; cursor: pointer; }}
  .warnings-container {{ margin-top: 18px; display: flex; flex-direction: column; gap: 10px; }}
  .warning-card {{ background: #fff; border: 1px solid var(--line); border-radius: 10px; padding: 14px 18px; cursor: pointer; transition: all 0.15s ease; }}
  .warning-card:hover {{ transform: translateY(-2px); box-shadow: 0 6px 16px rgba(15, 23, 42, 0.08); }}
  .warning-crit {{ border-left: 5px solid var(--crit); background: #fef2f2; }}
  .warning-warn {{ border-left: 5px solid var(--warn); background: #fffbeb; }}
  .warning-ok {{ border-left: 5px solid var(--ok); background: #f0fdf4; }}
  .warning-header {{ display: flex; justify-content: space-between; align-items: center; gap: 10px; }}
  .warning-title {{ font-weight: 700; font-size: 14.5px; }}
  .warning-link {{ font-size: 12.5px; font-weight: 700; color: var(--brand); }}
  .warning-desc {{ font-size: 13px; color: #334155; margin-top: 4px; }}
  .kpi-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 14px; margin-top: 18px; }}
  .kpi-card {{ background: var(--card); border: 1px solid var(--line); border-radius: var(--radius); padding: 15px 18px; }}
  .kpi-title {{ font-size: 11px; font-weight: 700; text-transform: uppercase; color: var(--muted); margin-bottom: 4px; }}
  .kpi-val {{ font-size: 21px; font-weight: 800; color: var(--ink); margin-bottom: 4px; }}
  .kpi-sub {{ font-size: 12px; color: var(--muted); }}
  .charts-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(600px, 1fr)); gap: 18px; margin-top: 22px; }}
  @media (max-width: 768px) {{ .charts-grid {{ grid-template-columns: 1fr; }} }}
  .panel {{ background: var(--card); border: 1px solid var(--line); border-radius: var(--radius); padding: 18px 20px; display: flex; flex-direction: column; transition: box-shadow 0.25s ease; }}
  .panel.active-highlight {{ box-shadow: 0 0 0 3px #f59e0b, 0 8px 24px rgba(245, 158, 11, 0.2); }}
  .panel-header {{ display: flex; justify-content: space-between; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 10px; }}
  .panel-title {{ margin: 0; font-size: 15.5px; font-weight: 700; }}
  .panel-controls {{ display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }}
  select.curve-select {{ padding: 5px 10px; border-radius: 6px; border: 1px solid var(--line); background: #f8fafc; font-size: 12.5px; font-weight: 600; }}
  .btn-subtle {{ background: #f1f5f9; border: 1px solid #cbd5e1; color: var(--ink); padding: 5px 12px; border-radius: 6px; font-size: 12.5px; font-weight: 600; cursor: pointer; }}
  .btn-subtle:hover, .btn-subtle.active {{ background: var(--brand); color: #fff; border-color: var(--brand); }}
  .canvas-box {{ position: relative; width: 100%; height: 310px; }}
  canvas {{ width: 100%; height: 100%; display: block; }}
  .accordion-wrap {{ margin-top: 12px; border-top: 1px solid var(--line); padding-top: 10px; }}
  .btn-analysis {{ background: linear-gradient(135deg, #0284c7, #0369a1); color: #fff; border: none; border-radius: 6px; padding: 6px 14px; font-size: 12.5px; font-weight: 600; cursor: pointer; }}
  .analysis-box {{ display: none; margin-top: 10px; background: #f8fafc; border-left: 4px solid var(--brand); border-radius: 8px; padding: 12px 16px; font-size: 13px; color: #1e293b; line-height: 1.55; }}
  .ai-report-card {{ margin-top: 24px; background: var(--card); border: 1px solid #cbd5e1; border-left: 5px solid var(--brand); border-radius: var(--radius); padding: 22px 26px; }}
  .ai-content h1.ai-h1 {{ font-size: 19px; margin: 0 0 12px; color: #0c4a6e; }}
  .ai-content h2.ai-h2 {{ font-size: 15.5px; margin: 18px 0 8px; color: #0284c7; border-bottom: 1px dashed #cbd5e1; padding-bottom: 4px; }}
  .ai-content p, .ai-content li {{ font-size: 13.5px; }}
  .map-section {{ margin-top: 26px; background: var(--card); border: 1px solid var(--line); border-radius: var(--radius); padding: 18px 20px; }}
  .map-controls {{ display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin: 10px 0 12px; }}
  .map-controls input[type=text] {{ padding: 6px 12px; border: 1px solid #cbd5e1; border-radius: 6px; font-size: 13px; min-width: 220px; }}
  .timeline-bar {{ display: flex; align-items: center; gap: 12px; background: #f1f5f9; padding: 8px 14px; border-radius: 8px; margin-bottom: 10px; font-size: 13px; }}
  .timeline-bar input[type=range] {{ flex: 1; accent-color: var(--brand); cursor: pointer; }}
  .map-canvas-wrap {{ position: relative; width: 100%; height: 500px; background: #f8fafc; border: 1px solid var(--line); border-radius: 8px; overflow: hidden; }}
  .map-card-dock {{ position: absolute; bottom: 12px; right: 12px; width: 350px; background: rgba(255,255,255,0.97); border: 1px solid #cbd5e1; border-radius: 10px; box-shadow: 0 6px 20px rgba(0,0,0,0.12); padding: 14px 16px; font-size: 12.5px; z-index: 10; display: none; }}
  .map-card-header {{ display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #e2e8f0; padding-bottom: 6px; margin-bottom: 8px; font-weight: 700; }}
  .map-card-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 6px; }}
  .map-prop-label {{ font-size: 11px; color: var(--muted); }}
  .map-prop-val {{ font-weight: 600; color: var(--ink); }}
  .tables-section {{ margin-top: 26px; }}
  .table-tabs {{ display: flex; gap: 8px; margin-bottom: 12px; border-bottom: 1px solid var(--line); padding-bottom: 8px; flex-wrap: wrap; }}
  .table-tab-btn {{ background: transparent; border: none; padding: 8px 14px; border-radius: 6px; font-size: 13.5px; font-weight: 600; color: var(--muted); cursor: pointer; }}
  .table-tab-btn.active {{ background: #e0f2fe; color: #0369a1; }}
  .table-toolbar {{ display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 10px; }}
  .table-wrap {{ overflow-x: auto; background: #fff; border: 1px solid var(--line); border-radius: 8px; max-height: 420px; }}
  table.data-table {{ width: 100%; border-collapse: collapse; font-size: 12.5px; text-align: left; }}
  table.data-table th {{ position: sticky; top: 0; background: #f8fafc; padding: 8px 10px; border-bottom: 1px solid var(--line); font-weight: 600; cursor: pointer; }}
  table.data-table td {{ padding: 7px 10px; border-bottom: 1px solid #f1f5f9; }}
  table.data-table tr.row-highlighted td {{ background: #fef2f2 !important; font-weight: 700; }}
  .object-link {{ background: transparent; border: none; border-bottom: 1px dashed var(--brand); color: var(--brand); font: inherit; font-weight: 700; cursor: pointer; padding: 0 2px; }}
  .badge-bh {{ background: #dcfce7; color: #166534; padding: 2px 6px; border-radius: 4px; font-size: 11px; font-weight: 700; }}
  .badge-net {{ background: #e0f2fe; color: #075985; padding: 2px 6px; border-radius: 4px; font-size: 11px; font-weight: 700; }}
  .num-crit {{ color: var(--crit); font-weight: 700; }}
  .num-warn {{ color: var(--warn); font-weight: 700; }}
  .num-ok {{ color: var(--ok); font-weight: 700; }}
  #chartTooltip {{ position: absolute; display: none; pointer-events: none; background: rgba(15, 23, 42, 0.95); border: 1px solid #38bdf8; border-radius: 8px; color: #f8fafc; padding: 8px 12px; font-size: 12px; line-height: 1.45; z-index: 10000; white-space: nowrap; }}
</style>
</head>
<body>
<div id="chartTooltip"></div>
<div class="container">
  <header class="hero">
    <div>
      <h1>Гидравлический паспорт и ИИ-диагностика сети «{html.escape(cname)}»</h1>
      <p class="hero-sub">
        Расчетный горизонт: <strong>{facts.get('simulation_steps', 1440)} шагов</strong> •
        Сформировано: <strong>{gen_ts}</strong> •
        Время расчета: <strong>{total_s} сек</strong>
      </p>
    </div>
    <div><button class="btn-print" onclick="window.print()">Печать / PDF</button></div>
  </header>

  <section class="warnings-container">{warnings_html}</section>

  <section class="kpi-grid">
    <div class="kpi-card">
      <div class="kpi-title">Баланс мощности и расхода сети</div>
      <div class="kpi-val">{kpi_peak:,.1f} м³/ч</div>
      <div class="kpi-sub">Уст. мощность насосов: <strong>{kpi_inst_q:,.1f} м³/ч</strong> • Узлов всего: <strong>{kpi_total_nodes:,}</strong> (абон.: {kpi_active_nodes:,})</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-title">Свободный напор P = H − Z</div>
      <div class="kpi-val">{p_min_str} .. {p_max_str} м</div>
      <div class="kpi-sub"><span class="{'num-crit' if def_cnt > 0 else 'num-ok'}">Дефицит: {def_cnt} абон.</span> • Избыток &gt;60 м: <strong>{kpi_excess}</strong></div>
    </div>
    <div class="kpi-card">
      <div class="kpi-title">Протяженность и задвижки</div>
      <div class="kpi-val">{kpi_pipes_km:,.1f} км</div>
      <div class="kpi-sub">Труб: <strong>{kpi_pipes_cnt:,}</strong> • Перекрыто задвижкой (Q=0): <strong>{kpi_closed_pipes}</strong></div>
    </div>
    <div class="kpi-card">
      <div class="kpi-title">Скоростной режим</div>
      <div class="kpi-val">{kpi_max_v_str} м/с</div>
      <div class="kpi-sub">Малопроточных (&lt;0.1 м/с): <strong>{kpi_v_stag:,}</strong></div>
    </div>
    <div class="kpi-card">
      <div class="kpi-title">Насосы и Скважины 1-го подъема</div>
      <div class="kpi-val">{kpi_active_pumps} / {kpi_total_pumps} шт.</div>
      <div class="kpi-sub">Скважинных (от источника): <strong>{kpi_bh_pumps} шт.</strong></div>
    </div>
    <div class="kpi-card">
      <div class="kpi-title">Клапаны, Источники и РЧВ</div>
      <div class="kpi-val">{len(valves_table)} / {len(sources_table)} / {len(tanks_table)}</div>
      <div class="kpi-sub">Клапанов PRV/PSV: <strong>{len(valves_table)}</strong> • Источников: <strong>{len(sources_table)}</strong> • РЧВ: <strong>{len(tanks_table)}</strong></div>
    </div>
  </section>

  <section class="ai-report-card" id="aiReportCard">
    <div class="ai-content">{ai_html}</div>
  </section>

  <section class="charts-grid" id="dynamicChartsGrid">
    <div class="panel" id="qhSection">
      <div class="panel-header">
        <h3 class="panel-title">Характеристика насосов (Кривая Q-H • Скважинные vs Сетевые)</h3>
        <div class="panel-controls">
          <select id="qhSelect" class="curve-select" onchange="onQHSelectChange(this.value)"></select>
          <button id="qhShowAll" class="btn-subtle" onclick="toggleQHShowAll()">Показать все</button>
        </div>
      </div>
      <div class="canvas-box"><canvas id="qhCanvas"></canvas></div>
      <div class="accordion-wrap">
        <button class="btn-analysis" onclick="generateLiveAnalysis('qh')">Сгенерировать развернутый анализ графика</button>
        <div id="qhAnalysis" class="analysis-box"></div>
      </div>
    </div>

    <div class="panel" id="pressureSection">
      <div class="panel-header">
        <h3 class="panel-title" id="pressurePanelTitle">Верификация дефицита напора по абонентам и суточный коридор P = H − Z</h3>
        <div class="panel-controls">
          <button id="btnModeDeficitPts" class="btn-subtle active" onclick="switchPressureMode('points')">Точки дефицита ({def_cnt})</button>
          <button id="btnModeCorridor" class="btn-subtle" onclick="switchPressureMode('corridor')">Суточный коридор 24ч</button>
        </div>
      </div>
      <div class="canvas-box"><canvas id="pressureBandCanvas"></canvas></div>
      <div class="accordion-wrap">
        <button class="btn-analysis" onclick="generateLiveAnalysis('pressure')">Сгенерировать развернутый анализ графика</button>
        <div id="pressureAnalysis" class="analysis-box"></div>
      </div>
    </div>

    <div class="panel" id="bottlenecksSection">
      <div class="panel-header">
        <h3 class="panel-title">Гидравлические узкие места: скорость потока V (м/с) и потери i (м/км)</h3>
      </div>
      <div class="canvas-box"><canvas id="bottlenecksCanvas"></canvas></div>
      <div class="accordion-wrap">
        <button class="btn-analysis" onclick="generateLiveAnalysis('bottlenecks')">Сгенерировать развернутый анализ графика</button>
        <div id="bottlenecksAnalysis" class="analysis-box"></div>
      </div>
    </div>

    <div class="panel" id="stagnationSection">
      <div class="panel-header">
        <h3 class="panel-title">Круговая диаграмма распределения скоростей потока в сети</h3>
      </div>
      <div class="canvas-box"><canvas id="stagnationCanvas"></canvas></div>
      <div class="accordion-wrap">
        <button class="btn-analysis" onclick="generateLiveAnalysis('stagnation')">Сгенерировать развернутый анализ графика</button>
        <div id="stagnationAnalysis" class="analysis-box"></div>
      </div>
    </div>

    <div class="panel" id="valvesSection" style="{'display:flex' if len(valves_table) > 0 else 'display:none'}">
      <div class="panel-header">
        <h3 class="panel-title">Режим работы регулирующих клапанов (PRV / PSV / FCV) во времени</h3>
      </div>
      <div class="canvas-box"><canvas id="valvesCanvas"></canvas></div>
      <div class="accordion-wrap">
        <button class="btn-analysis" onclick="generateLiveAnalysis('valves')">Сгенерировать развернутый анализ графика</button>
        <div id="valvesAnalysis" class="analysis-box"></div>
      </div>
    </div>

    <div class="panel" id="diurnalSection" style="{'display:flex' if informative_charts.get('diurnal_balance', True) else 'display:none'}">
      <div class="panel-header">
        <h3 class="panel-title">Суточный баланс подачи насосов и потребления сети (24 ч)</h3>
      </div>
      <div class="canvas-box"><canvas id="diurnalFlowCanvas"></canvas></div>
      <div class="accordion-wrap">
        <button class="btn-analysis" onclick="generateLiveAnalysis('diurnal')">Сгенерировать развернутый анализ графика</button>
        <div id="diurnalAnalysis" class="analysis-box"></div>
      </div>
    </div>
  </section>

  <section class="map-section" id="mapSection">
    <div class="panel-header">
      <div>
        <h3 class="panel-title" style="font-size:16px">Интерактивная карта сети с временной шкалой (Скважины, РЧВ, Клапаны и все узлы)</h3>
        <p style="margin:2px 0 0;font-size:12.5px;color:var(--muted)">Кликните по любому узлу (включая транзитные), трубе, скважинному насосу или источнику для просмотра полного паспорта.</p>
      </div>
    </div>
    <div class="timeline-bar">
      <strong>Временная шкала (шаг расчета):</strong>
      <input type="range" id="mapTimelineRange" min="0" max="23" value="0" oninput="onMapTimelineChange(this.value)">
      <span id="mapTimelineLabel" style="font-weight:700;color:var(--brand);min-width:140px;">00:00 (R0)</span>
    </div>
    <div class="map-controls">
      <button type="button" id="mapPlus" class="btn-subtle">＋</button>
      <button type="button" id="mapMinus" class="btn-subtle">−</button>
      <button type="button" id="mapReset" class="btn-subtle">Вся сеть</button>
      <input type="text" id="mapSearch" placeholder="Поиск по ID (например, 3459771)">
      <button type="button" id="mapFind" class="btn-subtle">Найти на карте</button>
      <span id="mapInfo" style="font-size:12px;color:var(--muted);margin-left:auto"></span>
    </div>
    <div class="map-canvas-wrap">
      <canvas id="networkMap"></canvas>
      <div id="mapObjectCard" class="map-card-dock">
        <div class="map-card-header">
          <span id="mapCardTitle">Паспорт объекта</span>
          <button type="button" style="background:none;border:none;font-size:16px;cursor:pointer;" onclick="closeMapCard()">×</button>
        </div>
        <div id="mapCardBody" class="map-card-grid"></div>
      </div>
    </div>
  </section>

  <section class="tables-section" id="tablesSection">
    <div class="panel">
      <div class="table-tabs">
        <button type="button" class="table-tab-btn active" id="tabBtn_nodes" onclick="switchTableTab('nodes', this)">Узлы абонентов ({len(client_tables['nodes'])})</button>
        <button type="button" class="table-tab-btn" id="tabBtn_pipes" onclick="switchTableTab('pipes', this)">Трубопроводы и потери ({len(client_tables['pipes'])})</button>
        <button type="button" class="table-tab-btn" id="tabBtn_pumps" onclick="switchTableTab('pumps', this)">Насосы и Скважины ({len(client_tables['pumps'])})</button>
        <button type="button" class="table-tab-btn" id="tabBtn_valves" onclick="switchTableTab('valves', this)">Клапаны PRV/PSV ({len(client_tables['valves'])})</button>
      </div>
      <div class="table-toolbar">
        <div id="tableFilters" class="table-filters"></div>
        <input id="tableSearchInput" style="padding:6px 12px;border:1px solid #cbd5e1;border-radius:6px;" placeholder="Поиск по ID или параметру..." oninput="onTableSearch(this.value)">
      </div>
      <div class="table-wrap">
        <table id="mainDataTable" class="data-table">
          <thead id="mainTableHead"></thead>
          <tbody id="mainTableBody"></tbody>
        </table>
      </div>
    </div>
  </section>
</div>

<script>
const DATA = {payload_json};
let currentTimelineHour = 0;
let highlightedObjectId = null;
let pressureChartMode = (DATA.deficit_nodes_points && DATA.deficit_nodes_points.length > 0) ? 'points' : 'corridor';

(function applyAISectionOrder() {{
  const grid = document.getElementById('dynamicChartsGrid');
  (DATA.section_order || []).forEach(secId => {{
    const el = document.getElementById(secId);
    if (el && grid) grid.appendChild(el);
  }});
}})();

function clean_object_id(id) {{
  if (!id) return '';
  return String(id).trim().replace(/^[ceputvk](\\d+)$/i, '$1');
}}

function setupHiDPICanvas(canvas) {{
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.parentElement.getBoundingClientRect();
  const w = Math.max(rect.width, 320), h = Math.max(rect.height, 240);
  canvas.width = w * dpr; canvas.height = h * dpr;
  const ctx = canvas.getContext('2d');
  ctx.scale(dpr, dpr);
  return {{ ctx, w, h }};
}}

function drawCrosshairProjections(ctx, pad, pw, ph, px, py, xLabel, yLabel) {{
  if (px < pad.l || px > pad.l + pw || py < pad.t || py > pad.t + ph) return;
  ctx.save();
  ctx.setLineDash([4, 4]);
  ctx.strokeStyle = '#0f172a';
  ctx.lineWidth = 1.3;
  ctx.beginPath(); ctx.moveTo(px, py); ctx.lineTo(px, pad.t + ph); ctx.stroke();
  ctx.beginPath(); ctx.moveTo(pad.l, py); ctx.lineTo(px, py); ctx.stroke();
  ctx.setLineDash([]);

  ctx.font = 'bold 10.5px sans-serif';
  const xw = ctx.measureText(xLabel).width + 10;
  ctx.fillStyle = '#0f172a';
  ctx.fillRect(px - xw / 2, pad.t + ph + 2, xw, 16);
  ctx.fillStyle = '#ffffff';
  ctx.textAlign = 'center';
  ctx.fillText(xLabel, px, pad.t + ph + 13);

  const yw = ctx.measureText(yLabel).width + 8;
  ctx.fillStyle = '#0f172a';
  ctx.fillRect(Math.max(2, pad.l - yw - 2), py - 8, yw, 16);
  ctx.fillStyle = '#ffffff';
  ctx.textAlign = 'right';
  ctx.fillText(yLabel, pad.l - 5, py + 4);

  ctx.beginPath(); ctx.arc(px, py, 4.5, 0, Math.PI * 2);
  ctx.fillStyle = '#ef4444'; ctx.fill();
  ctx.strokeStyle = '#fff'; ctx.lineWidth = 1.5; ctx.stroke();
  ctx.restore();
}}

const chartTooltipEl = document.getElementById('chartTooltip');
function showChartTooltip(ev, htmlStr) {{
  chartTooltipEl.innerHTML = htmlStr;
  chartTooltipEl.style.display = 'block';
  let tx = ev.pageX + 14, ty = ev.pageY - 36;
  if (tx + 280 > window.innerWidth) tx = ev.pageX - 280;
  chartTooltipEl.style.left = tx + 'px';
  chartTooltipEl.style.top = ty + 'px';
}}
function hideChartTooltip() {{ chartTooltipEl.style.display = 'none'; }}

let qhShowAllMode = false, currentQHIndex = 0;
function drawQHCurve(idx, hoverPt = null) {{
  const canvas = document.getElementById('qhCanvas');
  const curves = DATA.qh_curves || [];
  if (!curves.length) return;
  const {{ ctx, w, h }} = setupHiDPICanvas(canvas);
  const pad = {{ l: 58, r: 24, t: 24, b: 42 }};
  const pw = w - pad.l - pad.r, ph = h - pad.t - pad.b;
  const curvesToPlot = qhShowAllMode ? curves : [curves[idx] || curves[0]];

  let maxQ = 0, maxH = 0;
  curvesToPlot.forEach(c => {{
    (c.curve_points || []).forEach(p => {{ if (p[0] > maxQ) maxQ = p[0]; if (p[1] > maxH) maxH = p[1]; }});
    if (c.operating_q_mean > maxQ) maxQ = c.operating_q_mean;
    if (c.operating_h_est > maxH) maxH = c.operating_h_est;
  }});
  maxQ = Math.max(maxQ * 1.12, 20); maxH = Math.max(maxH * 1.15, 20);
  const xOf = q => pad.l + (q / maxQ) * pw;
  const yOf = hv => pad.t + ph - (hv / maxH) * ph;

  ctx.strokeStyle = '#e2e8f0'; ctx.fillStyle = '#64748b'; ctx.font = '11px sans-serif';
  for (let i = 0; i <= 4; i++) {{
    const yv = (maxH / 4) * i, y = yOf(yv);
    ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(w - pad.r, y); ctx.stroke();
    ctx.textAlign = 'right'; ctx.fillText(yv.toFixed(0), pad.l - 7, y + 4);
    const xv = (maxQ / 4) * i, x = xOf(xv);
    ctx.beginPath(); ctx.moveTo(x, pad.t); ctx.lineTo(x, h - pad.b); ctx.stroke();
    ctx.textAlign = 'center'; ctx.fillText(xv.toFixed(0), x, h - pad.b + 16);
  }}
  ctx.fillStyle = '#0f172a'; ctx.font = 'bold 11.5px sans-serif'; ctx.textAlign = 'center';
  ctx.fillText('Подача насоса Q, м³/ч', pad.l + pw / 2, h - 6);
  ctx.save(); ctx.translate(15, pad.t + ph / 2); ctx.rotate(-Math.PI / 2);
  ctx.fillText('Развиваемый напор H, м', 0, 0); ctx.restore();

  curvesToPlot.forEach(item => {{
    const pts = item.curve_points || [];
    if (!qhShowAllMode && item.q_nominal_m3_h > 0) {{
      const x1 = xOf(item.q_nominal_m3_h * 0.75), x2 = xOf(Math.min(maxQ, item.q_nominal_m3_h * 1.15));
      ctx.fillStyle = 'rgba(16, 185, 129, 0.13)';
      ctx.fillRect(x1, pad.t, Math.max(0, x2 - x1), ph);
      ctx.fillStyle = '#059669'; ctx.font = 'bold 10.5px sans-serif';
      ctx.fillText('Оптимум BEP (75–115%)', (x1 + x2) / 2, pad.t + 14);
    }}
    ctx.strokeStyle = item.is_borehole ? '#059669' : '#0284c7';
    ctx.lineWidth = 2.6;
    ctx.beginPath();
    pts.forEach((p, i) => {{ if (i === 0) ctx.moveTo(xOf(p[0]), yOf(p[1])); else ctx.lineTo(xOf(p[0]), yOf(p[1])); }});
    ctx.stroke();

    if (item.active && item.operating_q_mean !== null && item.operating_h_est !== null) {{
      const ox = xOf(item.operating_q_mean), oy = yOf(item.operating_h_est);
      ctx.fillStyle = '#ef4444'; ctx.beginPath(); ctx.arc(ox, oy, 6.5, 0, Math.PI * 2); ctx.fill();
      ctx.strokeStyle = '#fff'; ctx.lineWidth = 2; ctx.stroke();
      if (!qhShowAllMode) {{
        const tag = item.is_borehole ? 'Скважинный' : 'Сетевой';
        ctx.fillStyle = '#0f172a'; ctx.font = 'bold 11px sans-serif'; ctx.textAlign = 'left';
        ctx.fillText(`${{tag}} #${{item.clean_id}}: Q=${{item.operating_q_mean}} м³/ч, H=${{item.operating_h_est}} м`, Math.min(ox + 10, w - 260), Math.max(pad.t + 26, oy - 8));
      }}
    }}
  }});

  if (hoverPt) {{
    drawCrosshairProjections(ctx, pad, pw, ph, hoverPt.px, hoverPt.py, `Q=${{hoverPt.q.toFixed(1)}}`, `H=${{hoverPt.h.toFixed(1)}}`);
  }}
  canvas._meta = {{ pad, pw, ph, maxQ, maxH, curvesToPlot }};
}}

function switchPressureMode(mode) {{
  pressureChartMode = mode;
  document.getElementById('btnModeDeficitPts').classList.toggle('active', mode === 'points');
  document.getElementById('btnModeCorridor').classList.toggle('active', mode === 'corridor');
  drawPressureBand();
}}

function drawPressureBand(hoverPt = null) {{
  const canvas = document.getElementById('pressureBandCanvas');
  const {{ ctx, w, h }} = setupHiDPICanvas(canvas);
  const pad = {{ l: 58, r: 24, t: 26, b: 42 }};
  const pw = w - pad.l - pad.r, ph = h - pad.t - pad.b;

  if (pressureChartMode === 'points' && DATA.deficit_nodes_points && DATA.deficit_nodes_points.length > 0) {{
    const pts = DATA.deficit_nodes_points;
    const zs = pts.map(p => p.z || 0), dps = pts.map(p => p.dp || 0);
    const minX = Math.min(...zs) - 2, maxX = Math.max(...zs) + 2;
    const minY = Math.min(...dps) - 4, maxY = Math.max(0, ...dps) + 4;
    const xOf = v => pad.l + ((v - minX) / Math.max(maxX - minX, 1)) * pw;
    const yOf = v => pad.t + ph - ((v - minY) / Math.max(maxY - minY, 1)) * ph;

    ctx.strokeStyle = '#e2e8f0'; ctx.fillStyle = '#64748b'; ctx.font = '11px sans-serif';
    for (let i = 0; i <= 4; i++) {{
      const yv = minY + ((maxY - minY) / 4) * i, y = yOf(yv);
      ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(w - pad.r, y); ctx.stroke();
      ctx.textAlign = 'right'; ctx.fillText(yv.toFixed(1), pad.l - 6, y + 4);
      const xv = minX + ((maxX - minX) / 4) * i, x = xOf(xv);
      ctx.beginPath(); ctx.moveTo(x, pad.t); ctx.lineTo(x, h - pad.b); ctx.stroke();
      ctx.textAlign = 'center'; ctx.fillText(xv.toFixed(0), x, h - pad.b + 16);
    }}
    ctx.fillStyle = '#0f172a'; ctx.font = 'bold 11.5px sans-serif'; ctx.textAlign = 'center';
    ctx.fillText('Геодезическая отметка абонента Z, м', pad.l + pw / 2, h - 6);
    ctx.save(); ctx.translate(15, pad.t + ph / 2); ctx.rotate(-Math.PI / 2);
    ctx.fillText('Дефицит напора ΔP = Pсвоб − Pтреб, м', 0, 0); ctx.restore();

    if (0 >= minY && 0 <= maxY) {{
      ctx.save(); ctx.setLineDash([5, 4]); ctx.strokeStyle = '#10b981'; ctx.lineWidth = 2;
      ctx.beginPath(); ctx.moveTo(pad.l, yOf(0)); ctx.lineTo(w - pad.r, yOf(0)); ctx.stroke();
      ctx.restore();
    }}

    pts.forEach((p, idx) => {{
      const px = xOf(p.z || 0), py = yOf(p.dp || 0);
      const isWorst = idx === 0 || p.clean_id === highlightedObjectId;
      ctx.beginPath();
      ctx.arc(px, py, isWorst ? 8 : 5, 0, Math.PI * 2);
      ctx.fillStyle = isWorst ? '#991b1b' : 'rgba(239, 68, 68, 0.8)';
      ctx.fill();
      ctx.strokeStyle = '#ffffff'; ctx.lineWidth = isWorst ? 2.2 : 1; ctx.stroke();

      if (isWorst) {{
        ctx.fillStyle = '#7f1d1d'; ctx.font = 'bold 11.5px sans-serif'; ctx.textAlign = 'left';
        ctx.fillText(`Лимитирующий узел #${{p.clean_id}} (ΔP = ${{p.dp}} м)`, Math.min(px + 10, w - 240), Math.max(pad.t + 16, py - 8));
      }}
    }});

    if (hoverPt) {{
      drawCrosshairProjections(ctx, pad, pw, ph, hoverPt.px, hoverPt.py, `Z=${{hoverPt.xVal.toFixed(1)}}м`, `ΔP=${{hoverPt.yVal.toFixed(1)}}м`);
    }}
    canvas._meta = {{ mode: 'points', pad, pw, ph, minX, maxX, minY, maxY, pts, xOf, yOf }};
    return;
  }}

  const pb = DATA.diurnal_free_pressure_percentiles_m;
  if (!pb || !pb.p50) return;
  const vals = [...(pb.p10 || []), ...(pb.p50 || []), ...(pb.p90 || []), 0, 60].filter(v => v !== null);
  const minY = Math.min(...vals) - 5, maxY = Math.max(...vals) + 6;
  const n = pb.p50.length;
  const xOf = i => pad.l + (i / Math.max(n - 1, 1)) * pw;
  const yOf = v => pad.t + ph - ((v - minY) / Math.max(maxY - minY, 1)) * ph;

  ctx.strokeStyle = '#e2e8f0'; ctx.fillStyle = '#64748b'; ctx.font = '11px sans-serif';
  for (let i = 0; i <= 4; i++) {{
    const v = minY + ((maxY - minY) / 4) * i, y = yOf(v);
    ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(w - pad.r, y); ctx.stroke();
    ctx.textAlign = 'right'; ctx.fillText(v.toFixed(0), pad.l - 6, y + 4);
  }}
  for (let i = 0; i < n; i += 4) {{
    ctx.textAlign = 'center'; ctx.fillText(`${{i}}:00`, xOf(i), h - pad.b + 16);
  }}
  ctx.fillStyle = '#0f172a'; ctx.font = 'bold 11.5px sans-serif'; ctx.textAlign = 'center';
  ctx.fillText('Время суток, ч', pad.l + pw / 2, h - 6);
  ctx.save(); ctx.translate(15, pad.t + ph / 2); ctx.rotate(-Math.PI / 2);
  ctx.fillText('Свободный напор P = H − Z, м', 0, 0); ctx.restore();

  ctx.fillStyle = 'rgba(2, 132, 199, 0.15)';
  ctx.beginPath();
  pb.p90.forEach((v, i) => {{ if (i === 0) ctx.moveTo(xOf(i), yOf(v ?? 0)); else ctx.lineTo(xOf(i), yOf(v ?? 0)); }});
  for (let i = n - 1; i >= 0; i--) ctx.lineTo(xOf(i), yOf(pb.p10[i] ?? 0));
  ctx.closePath(); ctx.fill();

  ctx.strokeStyle = '#0284c7'; ctx.lineWidth = 2.4; ctx.beginPath();
  pb.p50.forEach((v, i) => {{ if (i === 0) ctx.moveTo(xOf(i), yOf(v ?? 0)); else ctx.lineTo(xOf(i), yOf(v ?? 0)); }});
  ctx.stroke();

  if (hoverPt) {{
    drawCrosshairProjections(ctx, pad, pw, ph, hoverPt.px, hoverPt.py, hoverPt.xLabel, `${{hoverPt.yVal.toFixed(1)}}м`);
  }}
  canvas._meta = {{ mode: 'corridor', pad, pw, ph, minY, maxY, n, pb, xOf, yOf }};
}}

function drawBottlenecksChart(hoverPt = null) {{
  const canvas = document.getElementById('bottlenecksCanvas');
  const pts = DATA.bottleneck_pipes_points || [];
  if (!pts.length) return;
  const {{ ctx, w, h }} = setupHiDPICanvas(canvas);
  const pad = {{ l: 60, r: 24, t: 26, b: 42 }};
  const pw = w - pad.l - pad.r, ph = h - pad.t - pad.b;

  const vs = pts.map(p => p.v || 0), is_arr = pts.map(p => p.i || 0);
  const maxX = Math.max(...vs, 1.0) * 1.12;
  const maxY = Math.max(...is_arr, 20.0) * 1.12;
  const xOf = v => pad.l + (v / maxX) * pw;
  const yOf = v => pad.t + ph - (v / maxY) * ph;

  ctx.strokeStyle = '#e2e8f0'; ctx.fillStyle = '#64748b'; ctx.font = '11px sans-serif';
  for (let i = 0; i <= 4; i++) {{
    const yv = (maxY / 4) * i, y = yOf(yv);
    ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(w - pad.r, y); ctx.stroke();
    ctx.textAlign = 'right'; ctx.fillText(yv.toFixed(0), pad.l - 6, y + 4);
    const xv = (maxX / 4) * i, x = xOf(xv);
    ctx.beginPath(); ctx.moveTo(x, pad.t); ctx.lineTo(x, h - pad.b); ctx.stroke();
    ctx.textAlign = 'center'; ctx.fillText(xv.toFixed(2), x, h - pad.b + 16);
  }}
  ctx.fillStyle = '#0f172a'; ctx.font = 'bold 11.5px sans-serif'; ctx.textAlign = 'center';
  ctx.fillText('Скорость потока в трубе V, м/с', pad.l + pw / 2, h - 6);
  ctx.save(); ctx.translate(15, pad.t + ph / 2); ctx.rotate(-Math.PI / 2);
  ctx.fillText('Удельные потери напора i, м/км', 0, 0); ctx.restore();

  pts.forEach((p, idx) => {{
    const px = xOf(p.v || 0), py = yOf(p.i || 0);
    const isTop = idx === 0 || p.clean_id === highlightedObjectId;
    ctx.beginPath(); ctx.arc(px, py, isTop ? 8 : 5.5, 0, Math.PI * 2);
    ctx.fillStyle = isTop ? '#dc2626' : '#f59e0b'; ctx.fill();
    ctx.strokeStyle = '#fff'; ctx.lineWidth = isTop ? 2 : 1; ctx.stroke();
    if (isTop) {{
      ctx.fillStyle = '#991b1b'; ctx.font = 'bold 11px sans-serif'; ctx.textAlign = 'left';
      ctx.fillText(`Лидер потерь #${{p.clean_id}}: i=${{p.i}} м/км (V=${{p.v}} м/с)`, Math.min(px + 10, w - 250), Math.max(pad.t + 16, py - 8));
    }}
  }});

  if (hoverPt) {{
    drawCrosshairProjections(ctx, pad, pw, ph, hoverPt.px, hoverPt.py, `V=${{hoverPt.xVal.toFixed(2)}}`, `i=${{hoverPt.yVal.toFixed(1)}}`);
  }}
  canvas._meta = {{ pad, pw, ph, maxX, maxY, pts, xOf, yOf }};
}}

function drawStagnationChart() {{
  const canvas = document.getElementById('stagnationCanvas');
  const {{ ctx, w, h }} = setupHiDPICanvas(canvas);
  const vBins = DATA.velocity_bins || {{}};
  const rawItems = Object.entries(vBins).filter(([_, v]) => typeof v === 'number');
  if (!rawItems.length) return;

  const colors = ['#94a3b8', '#f59e0b', '#38bdf8', '#10b981', '#6366f1', '#ef4444'];
  const total = rawItems.reduce((acc, [_, v]) => acc + v, 0);
  const items = rawItems.map(([label, val], i) => ({{ label, val, ratio: total > 0 ? val / total : 0, color: colors[i % colors.length] }}));

  const cx = Math.round(w * 0.27), cy = Math.round(h * 0.50);
  const outerR = Math.max(45, Math.min(cx - 20, cy - 20, 105)), innerR = Math.round(outerR * 0.56);
  let curAngle = -Math.PI / 2;
  const slices = [];

  items.forEach(it => {{
    const sweep = it.ratio * Math.PI * 2;
    slices.push({{ ...it, startA: curAngle, endA: curAngle + sweep }});
    if (it.val > 0) {{
      ctx.beginPath(); ctx.arc(cx, cy, outerR, curAngle, curAngle + sweep);
      ctx.arc(cx, cy, innerR, curAngle + sweep, curAngle, true);
      ctx.closePath(); ctx.fillStyle = it.color; ctx.fill();
      ctx.strokeStyle = '#fff'; ctx.lineWidth = 1.5; ctx.stroke();
    }}
    curAngle += sweep;
  }});

  ctx.fillStyle = '#0f172a'; ctx.font = 'bold 15px sans-serif'; ctx.textAlign = 'center';
  ctx.fillText(total.toLocaleString('ru-RU'), cx, cy + 2);
  ctx.font = '11px sans-serif'; ctx.fillStyle = '#64748b'; ctx.fillText('труб всего', cx, cy + 18);

  const legX = Math.round(w * 0.52), legStartY = Math.max(16, Math.round(cy - (items.length * 26) / 2));
  items.forEach((it, idx) => {{
    const ly = legStartY + idx * 26;
    ctx.beginPath(); ctx.arc(legX + 6, ly + 6, 5, 0, Math.PI * 2); ctx.fillStyle = it.color; ctx.fill();
    ctx.font = '12px sans-serif'; ctx.fillStyle = '#334155'; ctx.textAlign = 'left';
    ctx.fillText(it.label, legX + 16, ly + 10);
    ctx.font = 'bold 12px sans-serif'; ctx.fillStyle = '#0f172a'; ctx.textAlign = 'right';
    ctx.fillText(`${{it.val}} (${{(it.ratio * 100).toFixed(1)}}%)`, w - 12, ly + 10);
  }});
  canvas._meta = {{ cx, cy, outerR, innerR, slices }};
}}

function drawValvesChart(hoverPt = null) {{
  const canvas = document.getElementById('valvesCanvas');
  if (!canvas) return;
  const series = DATA.valves_hourly_series || [];
  if (!series.length) return;
  const {{ ctx, w, h }} = setupHiDPICanvas(canvas);
  const pad = {{ l: 60, r: 24, t: 26, b: 42 }};
  const pw = w - pad.l - pad.r, ph = h - pad.t - pad.b;

  const allVals = series.flatMap(s => s.data.filter(v => v !== null));
  const maxY = Math.max(...allVals, 10) * 1.12;
  const n = series[0].data.length;
  const xOf = i => pad.l + (i / Math.max(n - 1, 1)) * pw;
  const yOf = v => pad.t + ph - (Math.max(0, v) / maxY) * ph;

  ctx.strokeStyle = '#e2e8f0'; ctx.fillStyle = '#64748b'; ctx.font = '11px sans-serif';
  for (let i = 0; i <= 4; i++) {{
    const v = (maxY / 4) * i, y = yOf(v);
    ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(w - pad.r, y); ctx.stroke();
    ctx.textAlign = 'right'; ctx.fillText(v.toFixed(0), pad.l - 6, y + 4);
  }}
  for (let i = 0; i < n; i += 4) {{
    ctx.textAlign = 'center'; ctx.fillText((DATA.step_labels || [])[i] || `R${{i}}`, xOf(i), h - pad.b + 16);
  }}
  ctx.fillStyle = '#0f172a'; ctx.font = 'bold 11.5px sans-serif'; ctx.textAlign = 'center';
  ctx.fillText('Расчетный временной шаг (R0 ... RN)', pad.l + pw / 2, h - 6);
  ctx.save(); ctx.translate(15, pad.t + ph / 2); ctx.rotate(-Math.PI / 2);
  ctx.fillText('Поток через клапан Q, м³/ч', 0, 0); ctx.restore();

  const colors = ['#0284c7', '#dc2626', '#059669', '#d97706', '#7c3aed'];
  series.forEach((s, idx) => {{
    ctx.strokeStyle = colors[idx % colors.length]; ctx.lineWidth = 2.4; ctx.beginPath();
    s.data.forEach((v, i) => {{ if (i === 0) ctx.moveTo(xOf(i), yOf(v || 0)); else ctx.lineTo(xOf(i), yOf(v || 0)); }});
    ctx.stroke();
  }});

  if (hoverPt) {{
    drawCrosshairProjections(ctx, pad, pw, ph, hoverPt.px, hoverPt.py, hoverPt.xLabel, `${{hoverPt.yVal.toFixed(1)}}`);
  }}
  canvas._meta = {{ pad, pw, ph, maxY, n, series, xOf, yOf }};
}}

function drawDiurnalFlows(hoverPt = null) {{
  const canvas = document.getElementById('diurnalFlowCanvas');
  if (!canvas) return;
  const dem = DATA.diurnal_demand_m3_h || [], pmp = DATA.diurnal_pump_flow_m3_h || [];
  const series = [];
  if (dem.some(v => v > 0)) series.push({{ name: 'Потребление сети', data: dem, color: '#0284c7' }});
  if (pmp.some(v => v > 0)) series.push({{ name: 'Подача насосов', data: pmp, color: '#0d9488' }});
  if (!series.length) return;

  const {{ ctx, w, h }} = setupHiDPICanvas(canvas);
  const pad = {{ l: 60, r: 24, t: 26, b: 42 }};
  const pw = w - pad.l - pad.r, ph = h - pad.t - pad.b;
  const maxY = Math.max(...series.flatMap(s => s.data), 1) * 1.15;
  const n = series[0].data.length;
  const xOf = i => pad.l + (i / Math.max(n - 1, 1)) * pw;
  const yOf = v => pad.t + ph - (Math.max(0, v) / maxY) * ph;

  ctx.strokeStyle = '#e2e8f0'; ctx.fillStyle = '#64748b'; ctx.font = '11px sans-serif';
  for (let i = 0; i <= 4; i++) {{
    const v = (maxY / 4) * i, y = yOf(v);
    ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(w - pad.r, y); ctx.stroke();
    ctx.textAlign = 'right'; ctx.fillText(v.toFixed(0), pad.l - 6, y + 4);
  }}
  for (let i = 0; i < n; i += 4) {{
    ctx.textAlign = 'center'; ctx.fillText(`${{i}}:00`, xOf(i), h - pad.b + 16);
  }}
  ctx.fillStyle = '#0f172a'; ctx.font = 'bold 11.5px sans-serif'; ctx.textAlign = 'center';
  ctx.fillText('Время суток, ч', pad.l + pw / 2, h - 6);
  ctx.save(); ctx.translate(15, pad.t + ph / 2); ctx.rotate(-Math.PI / 2);
  ctx.fillText('Объемный расход Q, м³/ч', 0, 0); ctx.restore();

  series.forEach(s => {{
    ctx.strokeStyle = s.color; ctx.lineWidth = 2.5; ctx.beginPath();
    s.data.forEach((v, i) => {{ if (i === 0) ctx.moveTo(xOf(i), yOf(v)); else ctx.lineTo(xOf(i), yOf(v)); }});
    ctx.stroke();
  }});
  if (hoverPt) {{
    drawCrosshairProjections(ctx, pad, pw, ph, hoverPt.px, hoverPt.py, hoverPt.xLabel, `${{hoverPt.yVal.toFixed(1)}}`);
  }}
  canvas._meta = {{ pad, pw, ph, maxY, n, dem, pmp, xOf, yOf }};
}}

function generateLiveAnalysis(kind) {{
  const mapIds = {{
    qh: 'qhAnalysis', pressure: 'pressureAnalysis', bottlenecks: 'bottlenecksAnalysis',
    stagnation: 'stagnationAnalysis', valves: 'valvesAnalysis', diurnal: 'diurnalAnalysis'
  }};
  const box = document.getElementById(mapIds[kind]);
  if (!box) return;
  box.style.display = 'block';

  let text = '';
  if (kind === 'qh') {{
    const curves = DATA.qh_curves || [];
    const cur = curves[currentQHIndex] || curves[0] || {{}};
    const bhCnt = curves.filter(c => c.is_borehole).length;
    text = `<strong>Полная интерпретация характеристик Q-H и режимов насосов:</strong><br>
    • <b>Что хорошо:</b> В модели идентифицировано <b>${{bhCnt}}</b> скважинных насосов 1-го подъема (начальный узел — Источник) и <b>${{curves.length - bhCnt}}</b> сетевых агрегатов. Зеленый коридор (75–115% Qном) обозначает зону максимального КПД (BEP).<br>
    • <b>Выбранный агрегат #${{cur.clean_id || '—'}} (${{cur.pump_kind || 'Насос'}}):</b> Фактическая подача Q = <b>${{cur.operating_q_mean ?? 0}} м³/ч</b>, напор H = <b>${{cur.operating_h_est ?? 0}} м</b>.<br>
    • <b>Диагностика по правилам гидравлики:</b> ${{cur.diagnostic_advice || 'Штатный режим.'}}<br>
    • <b>Инженерный вывод:</b> У скважинных насосов нет входных задвижек (проверяется уровень воды в скважине и напорный оголовок), а при перегрузке любого насоса подачу снижают уменьшением давления на выходе.`;
  }} else if (kind === 'pressure') {{
    const pts = DATA.deficit_nodes_points || [];
    const worst = pts[0] || {{}};
    text = `<strong>Полная интерпретация графика свободного напора и дефицитов:</strong><br>
    • <b>Что показывает график:</b> Все <b>${{pts.length}}</b> абонентских узлов с нехваткой давления (ΔP &lt; 0) нанесены красными точками. Лимитирующий узел <button class="object-link" data-map-id="${{worst.node_id}}">${{worst.clean_id || '—'}}</button> имеет отклонение <b>ΔP = ${{worst.dp ?? 0}} м</b> при отметке Z = ${{worst.z ?? 0}} м.<br>
    • <b>Что хорошо:</b> Узлы выше линии ΔP = 0 м полностью обеспечены нормативным напором для своей этажности.<br>
    • <b>Чем грозит дефицит:</b> В часы пикового водоразбора верхние этажи зданий в красной зоне останутся без воды.<br>
    • <b>Как исправить:</b> Разгрузить подводящие лимитирующие водоводы либо повысить напор бустерной группой с установкой клапанов PRV в низинных зонах.`;
  }} else if (kind === 'bottlenecks') {{
    const pts = DATA.bottleneck_pipes_points || [];
    const top = pts[0] || {{}};
    text = `<strong>Полная интерпретация гидравлических узких мест:</strong><br>
    • <b>Подтверждение аномалии:</b> На графике подсвечен лимитирующий участок <button class="object-link" data-map-id="${{top.pipe_id}}">${{top.clean_id || '—'}}</button> (диаметр D = ${{top.d ?? 0}} мм, C = ${{top.c ?? 100}}), на котором удельные потери достигают <b>${{top.i ?? 0}} м/км</b> при скорости <b>${{top.v ?? 0}} м/с</b>.<br>
    • <b>Влияние на сеть:</b> Участки с градиентом потерь i ≥ 15–20 м/км работают как дроссельные шайбы, «съедая» напор насосных станций.<br>
    • <b>Мероприятия:</b> Перекладка или параллельное дублирование участка #${{top.clean_id || '—'}} большим диаметром.`;
  }} else if (kind === 'stagnation') {{
    text = `<strong>Полная интерпретация круговой диаграммы скоростей:</strong><br>
    • <b>Оптимальное ядро (0.50–1.50 м/с):</b> Гарантирует самоочищение труб без избыточных потерь на трение.<br>
    • <b>Перекрытые участки (Q = 0) и малые скорости (&lt; 0.10 м/с):</b> Нулевой поток означает, что труба закрыта задвижкой. Длительное пребывание воды при скорости менее 0.10 м/с ведет к потере остаточного хлора и вторичному загрязнению.<br>
    • <b>Мероприятия:</b> Открытие секущих задвижек для восстановления кольцевания и промывка тупиковых веток.`;
  }} else if (kind === 'valves') {{
    const vs = DATA.tables.valves || [];
    const topV = vs[0] || {{}};
    text = `<strong>Полная интерпретация работы регулирующих клапанов (PRV/PSV/FCV):</strong><br>
    • <b>Назначение в схеме:</b> Клапан #${{topV.clean_id || '—'}} (${{topV.type_code || 'PRV'}}, уставка ${{topV.setting ?? '—'}}) гасит избыточный напор и стабилизирует расход между зонами (диапазон потока от ${{topV.flow_min ?? 0}} до ${{topV.flow_max ?? 0}} м³/ч).<br>
    • <b>На что обратить внимание:</b> Резкие ступенчатые скачки потока на графике соответствуют моментам переключения насосных групп или заполнения РЧВ.`;
  }} else if (kind === 'diurnal') {{
    text = `<strong>Полная интерпретация суточного баланса подачи и потребления:</strong><br>
    • <b>Соотношение мощностей:</b> Разрыв между установленной мощностью насосов и реальным расходом сети покрывается работой регулирующих емкостей РЧВ и графиком включения агрегатов.`;
  }}

  box.innerHTML = text;
}}

function onWarningClick(w) {{
  highlightedObjectId = w.focus_id || null;
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active-highlight'));
  const targetEl = document.getElementById(w.target);
  if (targetEl) {{
    targetEl.classList.add('active-highlight');
    targetEl.scrollIntoView({{ behavior: 'smooth', block: 'center' }});
  }}

  if (w.kind === 'pressure_deficit') {{
    switchPressureMode('points');
    generateLiveAnalysis('pressure');
    switchTableTab('nodes', document.getElementById('tabBtn_nodes'), w.focus_id);
  }} else if (w.kind === 'data_error' || w.kind === 'excess_pressure') {{
    switchTableTab('nodes', document.getElementById('tabBtn_nodes'), w.focus_id);
  }} else if (w.kind === 'pipe_bottlenecks') {{
    drawBottlenecksChart();
    generateLiveAnalysis('bottlenecks');
    switchTableTab('pipes', document.getElementById('tabBtn_pipes'), w.focus_id);
  }} else if (w.kind === 'stagnation') {{
    drawStagnationChart();
    generateLiveAnalysis('stagnation');
    switchTableTab('pipes', document.getElementById('tabBtn_pipes'), w.focus_id);
  }} else if (w.kind === 'pump_qh') {{
    const pIdx = (DATA.qh_curves || []).findIndex(c => c.clean_id === w.focus_id);
    if (pIdx >= 0) {{
      currentQHIndex = pIdx;
      document.getElementById('qhSelect').value = pIdx;
      qhShowAllMode = false;
      drawQHCurve(pIdx);
    }}
    generateLiveAnalysis('qh');
    switchTableTab('pumps', document.getElementById('tabBtn_pumps'), w.focus_id);
  }} else if (w.kind === 'valves') {{
    drawValvesChart();
    generateLiveAnalysis('valves');
    switchTableTab('valves', document.getElementById('tabBtn_valves'), w.focus_id);
  }}
}}

const networkCanvas = document.getElementById('networkMap');
const networkGeo = DATA.geo || {{ nodes: {{}}, links: [] }};
const networkView = {{ scale: 1, tx: 0, ty: 0, baseScale: 1, selectedPts: null, dragging: false }};

function mapSize() {{ return {{ w: Math.max(1, networkCanvas.clientWidth), h: Math.max(1, networkCanvas.clientHeight) }}; }}
function screenPoint(p) {{ return [p[0] * networkView.scale + networkView.tx, networkView.ty - p[1] * networkView.scale]; }}

function redrawMap() {{
  const {{ w, h }} = mapSize(), dpr = window.devicePixelRatio || 1;
  networkCanvas.width = Math.round(w * dpr); networkCanvas.height = Math.round(h * dpr);
  const ctx = networkCanvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);

  ctx.strokeStyle = '#cbd5e1'; ctx.lineWidth = 0.9;
  for (const [id, pts] of (networkGeo.links || [])) {{
    if (!pts || pts.length < 2) continue;
    ctx.beginPath();
    pts.forEach((p, i) => {{ const [x, y] = screenPoint(p); if (i) ctx.lineTo(x, y); else ctx.moveTo(x, y); }});
    ctx.stroke();
  }}

  const hrPressures = DATA.node_hourly_pressures || {{}};
  const badSet = new Set((networkGeo.bad_nodes || []).map(clean_object_id));
  const srcSet = new Set(networkGeo.sources || []);
  const tankSet = new Set(networkGeo.tanks || []);

  for (const [rawId, p] of Object.entries(networkGeo.nodes || {{}})) {{
    const [x, y] = screenPoint(p);
    if (x < -10 || x > w + 10 || y < -10 || y > h + 10) continue;
    const cid = clean_object_id(rawId);
    const hrArr = hrPressures[rawId] || hrPressures['c' + cid];
    const pNow = (hrArr && hrArr[currentTimelineHour] !== undefined) ? hrArr[currentTimelineHour] : null;

    ctx.beginPath();
    if (srcSet.has(cid)) {{
      ctx.fillStyle = '#059669'; ctx.arc(x, y, 6, 0, Math.PI * 2);
    }} else if (tankSet.has(cid)) {{
      ctx.fillStyle = '#0284c7'; ctx.rect(x - 5, y - 5, 10, 10);
    }} else if (badSet.has(cid) || (pNow !== null && pNow < 0)) {{
      ctx.fillStyle = '#ef4444'; ctx.arc(x, y, 3.5, 0, Math.PI * 2);
    }} else {{
      ctx.fillStyle = '#64748b'; ctx.arc(x, y, 2.0, 0, Math.PI * 2);
    }}
    ctx.fill();
  }}

  if (networkView.selectedPts) {{
    ctx.strokeStyle = '#f59e0b'; ctx.fillStyle = '#f59e0b'; ctx.lineWidth = 4;
    if (networkView.selectedPts.length > 1) {{
      ctx.beginPath();
      networkView.selectedPts.forEach((p, i) => {{ const [x, y] = screenPoint(p); if (i) ctx.lineTo(x, y); else ctx.moveTo(x, y); }});
      ctx.stroke();
    }} else {{
      const [x, y] = screenPoint(networkView.selectedPts[0]);
      ctx.beginPath(); ctx.arc(x, y, 8, 0, Math.PI * 2); ctx.fill();
    }}
  }}
}}

function mapFit() {{
  const pts = Object.values(networkGeo.nodes || {{}}).filter(p => p && Number.isFinite(p[0]));
  if (!pts.length) return;
  let xmin = Infinity, xmax = -Infinity, ymin = Infinity, ymax = -Infinity;
  pts.forEach(([x, y]) => {{ if (x < xmin) xmin = x; if (x > xmax) xmax = x; if (y < ymin) ymin = y; if (y > ymax) ymax = y; }});
  const {{ w, h }} = mapSize();
  const scale = Math.min((w - 40) / Math.max(xmax - xmin, 1), (h - 40) / Math.max(ymax - ymin, 1));
  networkView.baseScale = scale; networkView.scale = scale;
  networkView.tx = w / 2 - (xmin + xmax) * scale / 2;
  networkView.ty = h / 2 + (ymin + ymax) * scale / 2;
  document.getElementById('mapInfo').textContent = `Объектов с координатами: ${{pts.length}}`;
  redrawMap();
}}

function onMapTimelineChange(val) {{
  currentTimelineHour = Number(val);
  document.getElementById('mapTimelineLabel').textContent = (DATA.hours_labels || [])[currentTimelineHour] || `${{currentTimelineHour}}:00`;
  redrawMap();
}}

function focusMapObject(rawId) {{
  if (!rawId) return;
  const cid = clean_object_id(rawId);
  const nodes = networkGeo.nodes || {{}};
  const pt = nodes[rawId] || nodes['c' + cid] || nodes['e' + cid] || nodes['k' + cid] || nodes[cid];
  const link = (networkGeo.links || []).find(([lid]) => clean_object_id(lid) === cid);
  const pts = pt ? [pt] : (link ? link[1] : null);

  if (pts && pts.length) {{
    const {{ w, h }} = mapSize();
    networkView.scale = networkView.baseScale * 10;
    networkView.tx = w / 2 - pts[0][0] * networkView.scale;
    networkView.ty = h / 2 + pts[0][1] * networkView.scale;
    networkView.selectedPts = pts;
    redrawMap();
  }}

  const np = DATA.node_props[rawId] || DATA.node_props['c' + cid] || DATA.node_props['e' + cid] || DATA.node_props['k' + cid] || DATA.node_props[cid];
  const pp = DATA.pipe_props[rawId] || DATA.pipe_props['e' + cid] || DATA.pipe_props[cid];
  const hrArr = (DATA.node_hourly_pressures || {{}})['c' + cid] || (DATA.node_hourly_pressures || {{}})[rawId];
  const pAtStep = hrArr ? hrArr[currentTimelineHour] : null;

  const card = document.getElementById('mapObjectCard');
  const title = document.getElementById('mapCardTitle');
  const body = document.getElementById('mapCardBody');
  title.textContent = `Объект #${{cid}}`;
  body.innerHTML = '';
  const addProp = (k, v) => {{ body.innerHTML += `<div><div class="map-prop-label">${{k}}</div><div class="map-prop-val">${{v ?? '—'}}</div></div>`; }};

  if (np) {{
    addProp('Роль в схеме', np.role);
    addProp('Отметка земли Z', `${{np.z ?? '—'}} м`);
    addProp(`Напор на шаге (${{currentTimelineHour}}:00)`, `${{pAtStep ?? np.p_min ?? '—'}} м`);
    addProp('Мин / Макс Pсвоб', `${{np.p_min ?? '—'}} / ${{np.p_max ?? '—'}} м`);
    addProp('Требуемый напор', `${{np.req}}`);
    addProp('Отбор абонента', `${{np.demand ?? 0}} м³/ч`);
    addProp('Транзитный поток', `${{np.transit_q ?? 0}} м³/ч`);
    addProp('Подключено труб', `${{np.connected_pipes ?? 1}} шт.`);
  }} else if (pp) {{
    addProp('Тип элемента', pp.closed ? 'Труба (Перекрыта задвижкой, Q=0)' : 'Трубопровод');
    addProp('Диаметр / Длина', `${{pp.d}} мм / ${{pp.l}} м`);
    addProp('Скорость / Потери', `${{pp.v ?? 0}} м/с / ${{pp.i ?? 0}} м/км`);
    addProp('Шероховатость C', `${{pp.c ?? '—'}}`);
  }} else {{
    addProp('Статус', 'Координатный узел модели');
  }}
  card.style.display = 'block';
}}

function closeMapCard() {{ document.getElementById('mapObjectCard').style.display = 'none'; }}

networkCanvas.addEventListener('click', ev => {{
  const rect = networkCanvas.getBoundingClientRect();
  const wx = (ev.clientX - rect.left - networkView.tx) / networkView.scale;
  const wy = (networkView.ty - (ev.clientY - rect.top)) / networkView.scale;
  let bestId = null, bestD = 16 / networkView.scale;
  for (const [nid, p] of Object.entries(networkGeo.nodes || {{}})) {{
    const d = Math.hypot(p[0] - wx, p[1] - wy);
    if (d < bestD) {{ bestD = d; bestId = nid; }}
  }}
  if (bestId) focusMapObject(bestId);
}});

networkCanvas.addEventListener('wheel', ev => {{
  ev.preventDefault();
  const f = ev.deltaY < 0 ? 1.25 : 0.8;
  networkView.scale = Math.max(networkView.baseScale * 0.4, Math.min(networkView.baseScale * 80, networkView.scale * f));
  redrawMap();
}}, {{ passive: false }});

document.getElementById('mapPlus').onclick = () => {{ networkView.scale *= 1.3; redrawMap(); }};
document.getElementById('mapMinus').onclick = () => {{ networkView.scale /= 1.3; redrawMap(); }};
document.getElementById('mapReset').onclick = () => {{ networkView.selectedPts = null; closeMapCard(); mapFit(); }};
document.getElementById('mapFind').onclick = () => focusMapObject(document.getElementById('mapSearch').value);

document.addEventListener('click', ev => {{
  const btn = ev.target.closest('[data-map-id]');
  if (btn) {{
    ev.preventDefault();
    document.getElementById('mapSection').scrollIntoView({{ behavior: 'smooth' }});
    focusMapObject(btn.dataset.mapId);
  }}
}});

let currentTable = 'nodes', currentSearch = '', highlightRowId = null;
const tableSchemas = {{
  nodes: [
    {{ key: 'clean_id', label: 'ID узла', isId: true }},
    {{ key: 'elevation_m', label: 'Отметка Z, м' }},
    {{ key: 'floors', label: 'Этажность' }},
    {{ key: 'required_pressure_m', label: 'P_треб, м' }},
    {{ key: 'free_pressure_min_m', label: 'P_своб мин, м', isCrit: r => Number(r.free_pressure_min_m) < 0 }},
    {{ key: 'free_pressure_max_m', label: 'P_своб макс, м', isCrit: r => Number(r.free_pressure_max_m) > 60 }},
    {{ key: 'margin_m', label: 'Дефицит ΔP, м', isCrit: r => Number(r.margin_m) < 0 }},
    {{ key: 'demand_m3_h', label: 'Отбор, м³/ч' }}
  ],
  pipes: [
    {{ key: 'clean_id', label: 'ID участка', isId: true }},
    {{ key: 'diameter_mm', label: 'Диаметр, мм' }},
    {{ key: 'length_m', label: 'Длина, м' }},
    {{ key: 'velocity_max_m_s', label: 'Скорость V, м/с' }},
    {{ key: 'headloss_m_km', label: 'Потери i, м/км', isCrit: r => Number(r.headloss_m_km) >= 20 }},
    {{ key: 'hazen_williams_c', label: 'Коэфф. C' }},
    {{ key: 'flow_m3_h', label: 'Поток Q, м³/ч' }}
  ],
  pumps: [
    {{ key: 'clean_id', label: 'ID насоса', isId: true }},
    {{ key: 'pump_type', label: 'Тип (Скважинный / Сетевой)', isBadge: true }},
    {{ key: 'start_node', label: 'Нач. узел (Источник)' }},
    {{ key: 'q_work', label: 'Q_раб, м³/ч' }},
    {{ key: 'q_nom', label: 'Q_ном, м³/ч' }},
    {{ key: 'h_work', label: 'H_раб, м' }},
    {{ key: 'regime', label: 'Режим' }},
    {{ key: 'advice', label: 'Диагностика по правилам' }}
  ],
  valves: [
    {{ key: 'clean_id', label: 'ID клапана', isId: true }},
    {{ key: 'type_code', label: 'Тип клапана' }},
    {{ key: 'type_desc', label: 'Назначение' }},
    {{ key: 'setting', label: 'Настройка' }},
    {{ key: 'flow_min', label: 'Q_мин, м³/ч' }},
    {{ key: 'flow_max', label: 'Q_макс, м³/ч' }},
    {{ key: 'flow_swing', label: 'Перепад ΔQ, м³/ч' }},
    {{ key: 'status', label: 'Статус' }}
  ]
}};

function switchTableTab(tab, btn, focusId = null) {{
  currentTable = tab; highlightRowId = focusId;
  document.querySelectorAll('.table-tab-btn').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');
  renderTable();
}}

function onTableSearch(val) {{ currentSearch = (val || '').toLowerCase(); renderTable(); }}

function renderTable() {{
  const schema = tableSchemas[currentTable] || [];
  let rows = DATA.tables[currentTable] || [];
  if (currentSearch) {{
    rows = rows.filter(r => Object.values(r).some(v => String(v ?? '').toLowerCase().includes(currentSearch)));
  }}
  const thead = document.getElementById('mainTableHead');
  thead.innerHTML = '<tr>' + schema.map(c => `<th>${{c.label}}</th>`).join('') + '</tr>';
  const tbody = document.getElementById('mainTableBody');
  tbody.innerHTML = '';
  rows.slice(0, 120).forEach(r => {{
    const tr = tbody.insertRow();
    if (highlightRowId && String(r.clean_id) === String(highlightRowId)) tr.className = 'row-highlighted';
    schema.forEach(col => {{
      const td = tr.insertCell();
      const v = r[col.key] ?? '—';
      if (col.isId) {{
        td.innerHTML = `<button type="button" class="object-link" data-map-id="${{v}}">${{v}}</button>`;
      }} else if (col.isBadge) {{
        td.innerHTML = `<span class="${{r.is_borehole ? 'badge-bh' : 'badge-net'}}">${{v}}</span>`;
      }} else {{
        td.textContent = String(v);
      }}
      if (col.isCrit && col.isCrit(r)) td.className = 'num-crit';
    }});
  }});
}}

function onQHSelectChange(v) {{ currentQHIndex = Number(v); qhShowAllMode = false; drawQHCurve(currentQHIndex); }}
function toggleQHShowAll() {{ qhShowAllMode = !qhShowAllMode; drawQHCurve(currentQHIndex); }}

function attachInteractiveCrosshairs() {{
  const qhC = document.getElementById('qhCanvas');
  qhC.addEventListener('mousemove', ev => {{
    const m = qhC._meta; if (!m) return;
    const rect = qhC.getBoundingClientRect();
    const px = ev.clientX - rect.left, py = ev.clientY - rect.top;
    if (px < m.pad.l || px > m.pad.l + m.pw || py < m.pad.t || py > m.pad.t + m.ph) {{ hideChartTooltip(); drawQHCurve(currentQHIndex); return; }}
    const q = ((px - m.pad.l) / m.pw) * m.maxQ;
    const h = ((m.pad.t + m.ph - py) / m.ph) * m.maxH;
    drawQHCurve(currentQHIndex, {{ px, py, q, h }});
    showChartTooltip(ev, `Подача Q: <b>${{q.toFixed(1)}} м³/ч</b><br>Напор H: <b>${{h.toFixed(1)}} м</b>`);
  }});
  qhC.addEventListener('mouseleave', () => {{ hideChartTooltip(); drawQHCurve(currentQHIndex); }});

  const pbC = document.getElementById('pressureBandCanvas');
  pbC.addEventListener('mousemove', ev => {{
    const m = pbC._meta; if (!m) return;
    const rect = pbC.getBoundingClientRect();
    const mx = ev.clientX - rect.left, my = ev.clientY - rect.top;
    if (mx < m.pad.l || mx > m.pad.l + m.pw || my < m.pad.t || my > m.pad.t + m.ph) {{ hideChartTooltip(); drawPressureBand(); return; }}
    if (m.mode === 'points') {{
      let best = null, bestD = 18;
      m.pts.forEach(p => {{
        const d = Math.hypot(m.xOf(p.z || 0) - mx, m.yOf(p.dp || 0) - my);
        if (d < bestD) {{ bestD = d; best = p; }}
      }});
      if (best) {{
        const px = m.xOf(best.z || 0), py = m.yOf(best.dp || 0);
        drawPressureBand({{ px, py, xVal: best.z || 0, yVal: best.dp || 0 }});
        showChartTooltip(ev, `<b>Узел #${{best.clean_id}}</b> (${{best.floors}} эт.)<br>Отметка Z: <b>${{best.z}} м</b><br>Pсвоб: <b>${{best.p_free}} м</b> (треб. ${{best.p_req}} м)<br>Дефицит ΔP: <b>${{best.dp}} м</b>`);
        return;
      }}
      const xVal = m.minX + ((mx - m.pad.l) / m.pw) * (m.maxX - m.minX);
      const yVal = m.minY + ((m.pad.t + m.ph - my) / m.ph) * (m.maxY - m.minY);
      drawPressureBand({{ px: mx, py: my, xVal, yVal }});
      showChartTooltip(ev, `Отметка Z: <b>${{xVal.toFixed(1)}} м</b><br>Отклонение ΔP: <b>${{yVal.toFixed(1)}} м</b>`);
    }} else {{
      const idx = Math.max(0, Math.min(m.n - 1, Math.round(((mx - m.pad.l) / m.pw) * (m.n - 1))));
      const yVal = m.pb.p50[idx] ?? 0;
      drawPressureBand({{ px: m.xOf(idx), py: m.yOf(yVal), xLabel: `${{idx}}:00`, yVal }});
      showChartTooltip(ev, `<b>Час ${{idx}}:00</b><br>Медиана P50: <b>${{yVal}} м</b><br>Коридор P10..P90: ${{m.pb.p10[idx]}} .. ${{m.pb.p90[idx]}} м`);
    }}
  }});
  pbC.addEventListener('mouseleave', () => {{ hideChartTooltip(); drawPressureBand(); }});

  const bnC = document.getElementById('bottlenecksCanvas');
  bnC.addEventListener('mousemove', ev => {{
    const m = bnC._meta; if (!m) return;
    const rect = bnC.getBoundingClientRect();
    const mx = ev.clientX - rect.left, my = ev.clientY - rect.top;
    if (mx < m.pad.l || mx > m.pad.l + m.pw || my < m.pad.t || my > m.pad.t + m.ph) {{ hideChartTooltip(); drawBottlenecksChart(); return; }}
    let best = null, bestD = 18;
    m.pts.forEach(p => {{
      const d = Math.hypot(m.xOf(p.v || 0) - mx, m.yOf(p.i || 0) - my);
      if (d < bestD) {{ bestD = d; best = p; }}
    }});
    if (best) {{
      drawBottlenecksChart({{ px: m.xOf(best.v || 0), py: m.yOf(best.i || 0), xVal: best.v || 0, yVal: best.i || 0 }});
      showChartTooltip(ev, `<b>Участок #${{best.clean_id}}</b> (D=${{best.d}} мм, L=${{best.l}} м)<br>Скорость V: <b>${{best.v}} м/с</b><br>Уд. потери i: <b>${{best.i}} м/км</b><br>Коэфф. C: ${{best.c}}`);
    }}
  }});
  bnC.addEventListener('mouseleave', () => {{ hideChartTooltip(); drawBottlenecksChart(); }});

  const vC = document.getElementById('valvesCanvas');
  if (vC) {{
    vC.addEventListener('mousemove', ev => {{
      const m = vC._meta; if (!m) return;
      const rect = vC.getBoundingClientRect();
      const mx = ev.clientX - rect.left, my = ev.clientY - rect.top;
      if (mx < m.pad.l || mx > m.pad.l + m.pw || my < m.pad.t || my > m.pad.t + m.ph) {{ hideChartTooltip(); drawValvesChart(); return; }}
      const idx = Math.max(0, Math.min(m.n - 1, Math.round(((mx - m.pad.l) / m.pw) * (m.n - 1))));
      const s0 = m.series[0];
      const yVal = s0.data[idx] || 0;
      const lbl = (DATA.step_labels || [])[idx] || `R${{idx}}`;
      drawValvesChart({{ px: m.xOf(idx), py: m.yOf(yVal), xLabel: lbl, yVal }});
      showChartTooltip(ev, `<b>Шаг ${{lbl}}</b><br>` + m.series.map(s => `${{s.label}}: <b>${{s.data[idx]}} м³/ч</b>`).join('<br>'));
    }});
    vC.addEventListener('mouseleave', () => {{ hideChartTooltip(); drawValvesChart(); }});
  }}

  const dfC = document.getElementById('diurnalFlowCanvas');
  if (dfC) {{
    dfC.addEventListener('mousemove', ev => {{
      const m = dfC._meta; if (!m) return;
      const rect = dfC.getBoundingClientRect();
      const mx = ev.clientX - rect.left, my = ev.clientY - rect.top;
      if (mx < m.pad.l || mx > m.pad.l + m.pw || my < m.pad.t || my > m.pad.t + m.ph) {{ hideChartTooltip(); drawDiurnalFlows(); return; }}
      const idx = Math.max(0, Math.min(m.n - 1, Math.round(((mx - m.pad.l) / m.pw) * (m.n - 1))));
      const yVal = (m.dem && m.dem[idx]) || (m.pmp && m.pmp[idx]) || 0;
      drawDiurnalFlows({{ px: m.xOf(idx), py: m.yOf(yVal), xLabel: `${{idx}}:00`, yVal }});
      showChartTooltip(ev, `<b>Время ${{idx}}:00</b><br>Потребление: <b>${{m.dem ? m.dem[idx] : '—'}} м³/ч</b><br>Подача насосов: <b>${{m.pmp ? m.pmp[idx] : '—'}} м³/ч</b>`);
    }});
    dfC.addEventListener('mouseleave', () => {{ hideChartTooltip(); drawDiurnalFlows(); }});
  }}
}}

window.addEventListener('DOMContentLoaded', () => {{
  const sel = document.getElementById('qhSelect');
  (DATA.qh_curves || []).forEach((c, idx) => {{
    const opt = document.createElement('option');
    opt.value = idx;
    opt.textContent = `${{c.clean_id}} ${{c.is_borehole ? '[Скважинный]' : '[Сетевой]'}} ${{c.active ? '[РАБ]' : '[ВЫКЛ]'}}`;
    sel.appendChild(opt);
  }});
  drawQHCurve(0);
  drawPressureBand();
  drawBottlenecksChart();
  drawStagnationChart();
  drawValvesChart();
  drawDiurnalFlows();
  mapFit();
  renderTable();
  attachInteractiveCrosshairs();
}});
</script>
</body>
</html>
"""
    if output_html_path is not None:
        out_p = Path(output_html_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(html_doc, encoding="utf-8")
    return html_doc
