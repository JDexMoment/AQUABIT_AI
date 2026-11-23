"""Формирование текстового инженерного отчёта на основе фактов."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any
from ontology.context import clean_object_id
from ontology.loader import load_hydraulic_ontology
from ontology.diagnostics import evaluate_ontology_rules, analyze_problem_domains

def generate_reference_pure_facts_report(
    facts: dict[str, Any],
    ranked_domains: list[dict[str, Any]] | None = None,
    ontology: dict[str, Any] | None = None,
    ontology_path: str | Path | None = None,
) -> str:
    """Формирует точный инженерный ИИ-отчет на основе графа смежности (Источник -> Насос -> Коллектор -> Труба)
    без шаблонных оговорок, с интерактивными ссылками на объекты и автофильтрами таблиц."""
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
        f"Комплексный аудит гидравлической модели города «{cname}» выполнен с трассировкой причинно-следственных связей "
        f"по цепочкам «Источник → Насосный агрегат → Напорный узел коллектора → Лимитирующий водовод → Абонентский узел». "
        f"Протяженность сети составляет {tot_km:,.1f} км ({p_cnt:,} участков), суточное водопотребление — {cons_m3:,.0f} м³/сут "
        f"(источников: {src_cnt} шт., РЧВ: {tanks_cnt} шт., насосов: {tot_pumps} шт., в т.ч. скважинных 1-го подъема — {bh_tot} шт.). "
        f"Нажмите на любой ID объекта для центрирования на карте или на ссылку фильтра для автоматической выборки и сортировки в таблице."
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
            lines.append(f"\n## Приоритет {idx} - Обеспеченность этажности застройки свободным напором и локализация зон депрессии")
            if def_cnt > 0 and top_id:
                nb_pipes = ", ".join(f"`{pid}`" for pid in (top_d.get("connected_pipe_ids") or [])[:3]) or "распределительной сети"
                lines.append(
                    f"- **Локализация дефицита свободного напора:** В расчетной схеме выявлено **{def_cnt} абонентских узлов** с фактическим напором ниже требуемого по этажности "
                    f"([Отфильтровать и отсортировать все {def_cnt} узлов с дефицитом в таблице](filter:nodes:deficit)). "
                    f"Максимальная нехватка зафиксирована в диктующем узле `{top_id}` (отметка Z = {top_d.get('elevation_m', '—')} м, {top_d.get('floors', 1)} эт., Pтреб = {top_d.get('required_pressure_m', 10):.1f} м): "
                    f"дефицит составляет **ΔP = {top_val:.1f} м** на смежных участках {nb_pipes}."
                )
            if err_cnt > 0:
                w_err = pa.get("worst_data_error_nodes", [{}])[0]
                err_id = clean_object_id(w_err.get("node_id", ""))
                err_pipes = ", ".join(f"`{pid}`" for pid in (w_err.get("connected_pipe_ids") or [])[:3])
                lines.append(
                    f"- **Критический вакуум (< -3.0 м, правило R02):** Обнаружено **{err_cnt} активных узлов** с давлением ниже -3.0 м "
                    f"([Показать и отсортировать {err_cnt} узлов с P < -3.0 м в таблице](filter:nodes:data_error)). "
                    f"Экстремальное значение в узле `{err_id}` (Pмин = {w_err.get('free_pressure_min_m', 0):.1f} м, Z = {w_err.get('elevation_m', '—')} м"
                    + (f", смежные трубы: {err_pipes}" if err_pipes else "")
                    + f"): {w_err.get('diagnostic_advice', 'проверить отметку Z и открытость подводящих труб')}."
                )
            if exc_cnt > 0:
                w_exc = pa.get("top_excess_nodes", [{}])[0]
                exc_id = clean_object_id(w_exc.get("node_id", ""))
                lines.append(
                    f"- **Зона избыточного напора (> 60 м по СП 31.13330.2021):** Зафиксировано **{exc_cnt} узлов** с превышением 60 м "
                    f"([Отфильтровать {exc_cnt} узлов с P > 60 м](filter:nodes:excess)), максимум в узле `{exc_id}` (Pмакс = {w_exc.get('free_pressure_max_m', 0):.1f} м при Z = {w_exc.get('elevation_m', '—')} м)."
                )
            if top_id:
                lines.append(
                    f"- **Инженерные мероприятия и действия в ПК «Аквабит»:** (1) На сети: закольцевать подводящую линию к кварталу узла `{top_id}` или установить локальную ПНС; в низинной зоне около `{exc_id if exc_cnt else top_id}` установить регулятор давления PRV. "
                    f"(2) В ПК «Аквабит»: проверить геодезическую отметку Z в `Узел.csv` для узлов с вакуумом (`{err_id if err_cnt else top_id}`) и статус секущих задвижек на подводящих трубах."
                )

        elif dom == "pump_imbalance":
            section_order.append("qhSection")
            under = pma.get("underloaded_pumps", [])
            over = pma.get("overloaded_pumps", [])
            bh_under = [p for p in under if p.get("is_borehole")]
            boost_under = [p for p in under if not p.get("is_borehole")]
            lines.append(f"\n## Приоритет {idx} - Причинно-следственная диагностика насосов, скважин и напорных коллекторов")
            lines.append(
                f"- **Баланс установленной мощности и расхода сети:** Суммарная номинальная подача насосов составляет {inst_q:,.1f} м³/ч при пиковом потреблении сети {peak_q:,.1f} м³/ч "
                f"(скважинных агрегатов 1-го подъема — {bh_tot} шт., в недогрузке — {len(under)} шт. ([Показать в таблице](filter:pumps:underload)), "
                f"в перегрузке — {len(over)} шт. ([Показать в таблице](filter:pumps:overload)))."
            )
            if bh_under:
                p0 = bh_under[0]
                p0_id = clean_object_id(p0.get("pump_id"))
                sn_id = p0.get("start_node") or "—"
                en_id = p0.get("end_node") or "—"
                out_p = p0.get("connected_out_pipe_id")
                lines.append(
                    f"- **Разбор недогрузки скважинного агрегата `{p0_id}` (источник `{sn_id}` → коллектор `{en_id}`):** "
                    f"{p0.get('diagnostic_advice', '')} "
                    + (f"Связанный отходящий участок коллектора: `{out_p}` (потери {p0.get('connected_out_pipe_loss', 0)} м/км)." if out_p else "")
                )
            if boost_under:
                pb = boost_under[0]
                pb_id = clean_object_id(pb.get("pump_id"))
                lines.append(
                    f"- **Разбор недогрузки сетевого насоса `{pb_id}` (вход `{pb.get('start_node', '—')}` → напорный узел `{pb.get('end_node', '—')}`):** "
                    f"{pb.get('diagnostic_advice', '')}"
                )
            if over:
                po = over[0]
                po_id = clean_object_id(po.get("pump_id"))
                lines.append(
                    f"- **Разбор перегрузки агрегата `{po_id}` (напорный узел `{po.get('end_node', '—')}`):** "
                    f"{po.get('diagnostic_advice', '')}"
                )

        elif dom == "hydraulic_bottlenecks":
            section_order.append("bottlenecksSection")
            high_loss = ppa.get("top_high_loss_pipes", [])
            bneck_cnt = ppa.get("bottleneck_pipes_count") or len(high_loss)
            w_p = high_loss[0] if high_loss else {}
            w_id = clean_object_id(w_p.get("pipe_id", ""))
            sn_id = w_p.get("start_node", "—")
            en_id = w_p.get("end_node", "—")
            lines.append(f"\n## Приоритет {idx} - Гидравлические узкие места и потери напора ({bneck_cnt} участков)")
            lines.append(
                f"- **Локализация лимитирующих водоводов:** В сети выявлено **{bneck_cnt} участков** с повышенным градиентом потерь напора "
                f"([Отфильтровать узкие места в таблице труб](filter:pipes:bottlenecks)). "
                f"Лимитирующий участок `{w_id}` между узлами `{sn_id}` и `{en_id}` (D = {w_p.get('diameter_mm', 0):.0f} мм, шероховатость C = {w_p.get('hazen_williams_c', 100)}) "
                f"создает удельные потери **{w_p.get('headloss_gradient_m_km', 0):.1f} м/км** при скорости потока {w_p.get('velocity_max_m_s', 0):.2f} м/с."
            )
            lines.append(
                f"- **Инженерные мероприятия и действия в ПК «Аквабит»:** В модели ПК «Аквабит» проверить корректность коэффициента Хейзена-Уильямса C = {w_p.get('hazen_williams_c', 100)} на участке `{w_id}` (в `Труба.csv`); "
                f"на сети — выполнить санацию/перекладку линии `{sn_id}` → `{en_id}` или переключить часть расхода на параллельную ветку."
            )

        elif dom == "water_stagnation":
            section_order.append("stagnationSection")
            stag_cnt = ppa.get("low_velocity_under_01ms_count", 0)
            closed_cnt = ppa.get("closed_zero_flow_pipes_count", 0)
            lines.append(f"\n## Приоритет {idx} - Скоростной режим сети, перекрытые участки и малопроточные зоны")
            lines.append(
                f"- **Структура скоростей потока:** Выявлено **{closed_cnt} перекрытых участков** с нулевым расходом Q = 0 ([Показать перекрытые трубы в таблице](filter:pipes:closed)) "
                f"и **{stag_cnt} малопроточных участков** со скоростью V < 0.10 м/с ([Отфильтровать малопроточные участки](filter:pipes:stagnant)). "
                f"Кликните по любому сектору круговой диаграммы скоростей для мгновенной фильтрации соответствующей группы труб в таблице."
            )

        elif dom == "valves_and_storage":
            section_order.append("valvesSection")
            v_tbl = vra.get("valves_table", [])
            top_v = max(v_tbl, key=lambda x: x.get("flow_swing_m3_h", 0)) if v_tbl else {}
            v_id = top_v.get("clean_id", "")
            lines.append(f"\n## Приоритет {idx} - Режим работы регулирующих клапанов (PRV/PSV), источников и РЧВ")
            lines.append(
                f"- **Баланс регулирующей арматуры и емкостей:** В расчетной схеме задействовано {valves_cnt} клапанов, {src_cnt} источников и {tanks_cnt} резервуаров РЧВ. "
                + (f"Клапан `{v_id}` ({top_v.get('type_code')}, уставка {top_v.get('setting')}) на участке `{top_v.get('start_node')}` → `{top_v.get('end_node')}` работает в диапазоне расходов от {top_v.get('flow_min_m3_h')} до {top_v.get('flow_max_m3_h')} м³/ч. " if v_id else "")
                + "Согласование уровней РЧВ и уставок редукционных клапанов стабилизирует напор в диктующих точках сети."
            )

        elif dom == "diurnal_imbalance":
            section_order.append("diurnalSection")
            lines.append(f"\n## Приоритет {idx} - Суточный баланс подачи насосных станций и водопотребления сети")
            lines.append(
                f"- **Согласование подачи и потребления (24 ч):** Пиковый часовой отбор абонентов составляет {peak_q:,.1f} м³/ч при коэффициенте часовой неравномерности Kчас = {wb.get('peak_factor_k_hour', 1.0)}. "
                "На графике суточного баланса сопоставлены кривые суммарной подачи насосов и фактического водопотребления абонентов."
            )

    lines.append("\n[SELECTED_DASHBOARDS]")
    lines.append(json.dumps({
        "section_order": section_order,
        "dashboards": [{"id": sec, "priority": i} for i, sec in enumerate(section_order, 1)],
    }, ensure_ascii=False))
    return "\n".join(lines)
