"""Формирование текстового инженерного отчёта на основе фактов и RAG-слоя СП 31.13330 (Qdrant)."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any
from ontology.context import clean_object_id
from ontology.loader import load_hydraulic_ontology
from ontology.diagnostics import evaluate_ontology_rules, analyze_problem_domains

try:
    from rag.retriever import retrieve_norms_for_facts
except Exception:
    retrieve_norms_for_facts = None  # type: ignore[assignment]


def generate_reference_pure_facts_report(
    facts: dict[str, Any],
    ranked_domains: list[dict[str, Any]] | None = None,
    ontology: dict[str, Any] | None = None,
    ontology_path: str | Path | None = None,
) -> str:
    """Формирует точный инженерный ИИ-отчет на основе графа смежности (Источник -> Насос -> Коллектор -> Труба)
    и нормативных пунктов СП 31.13330 из Qdrant RAG, с интерактивными ссылками на объекты и автофильтрами таблиц."""
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

    norm_clauses: list[dict[str, Any]] = []
    if retrieve_norms_for_facts is not None:
        try:
            norm_clauses = retrieve_norms_for_facts(facts, rules, top_k=6)
        except Exception:
            norm_clauses = []

    tot_km = ppa.get("total_length_km", 0.0)
    p_cnt = ppa.get("total_pipes", 0)
    cons_m3 = wb.get("total_daily_consumption_m3_day", 0.0)
    peak_q = wb.get("peak_network_demand_m3_h", 0.0)
    inst_q = wb.get("pumps_installed_capacity_m3_h", 0.0)
    bh_tot = pma.get("borehole_total_count", 0)
    tot_pumps = pma.get("total_pumps", 0)
    act_pumps = pma.get("active_pumps_count", 0)
    res_pumps = max(0, tot_pumps - act_pumps)
    valves_cnt = vra.get("valves_count", 0)
    tanks_cnt = vra.get("tanks_count", 0)
    src_cnt = vra.get("sources_count", 0)

    lines = ["# ИИ отчет"]
    lines.append(
        f"Комплексный аудит гидравлической модели города «{cname}» выполнен с трассировкой причинно-следственных связей "
        f"по цепочкам «Источник → Насосный агрегат → Напорный узел коллектора → Лимитирующий водовод → Абонентский узел» "
        f"и сверкой с нормативами **СП 31.13330.2012 / СП 31.13330.2021**. "
        f"Протяженность сети составляет {tot_km:,.1f} км ({p_cnt:,} участков), суточное водопотребление абонентов — {cons_m3:,.0f} м³/сут "
        f"(источников: {src_cnt} шт., РЧВ: {tanks_cnt} шт., насосов: {tot_pumps} шт., из них в работе — {act_pumps} шт., в холодном резерве по СП 31.13330 п. 10.3 — {res_pumps} шт.). "
        f"Нажмите на любой ID объекта для перехода к строке таблицы или показа на карте."
    )

    section_order = []
    for idx, d in enumerate(ranked, 1):
        dom = d["domain"]
        if dom == "pressure_deficit":
            section_order.append("pressureSection")
            w_def = pa.get("worst_deficit_nodes", [])
            top_d = w_def[0] if w_def else {}
            top_id = clean_object_id(top_d.get("node_id", ""))
            top_nm = top_d.get("name") or ""
            top_lbl = f"#{top_id} ({top_nm})" if top_nm else f"#{top_id}"
            top_pfree = top_d.get("free_pressure_min_m", 0.0)
            top_val = top_d.get("pressure_surplus_deficit_m", 0.0)
            top_abs = abs(float(top_val or 0.0))
            def_cnt = pa.get("real_required_pressure_deficit_count", 0)
            err_cnt = pa.get("data_error_below_minus_3m_count", 0)
            exc_cnt = pa.get("excess_over_60m_count", 0)
            lines.append(f"\n## Приоритет {idx} - Обеспеченность этажности застройки свободным напором (СП 31.13330.2012 п. 5.11, п. 5.13)")
            if def_cnt > 0 and top_id:
                nb_pipes = ", ".join(f"#{pid}" for pid in (top_d.get("connected_pipe_ids") or [])[:3]) or "распределительной сети"
                pos_note = (
                    f"свободный напор в магистрали положительный (**Pсвоб = {top_pfree:.2f} м > 0**, вакуума в уличной сети нет), "
                    if (top_pfree is not None and top_pfree >= 0)
                    else f"свободный напор Pсвоб = {top_pfree:.2f} м, "
                )
                lines.append(
                    f"- **Локализация дефицита свободного напора по этажности (СП 31.13330.2012 п. 5.11):** В расчетной схеме выявлено **{def_cnt} абонентских узлов** с фактическим напором ниже требуемого для верхних этажей застройки "
                    f"([Отфильтровать и отсортировать все {def_cnt} узлов с дефицитом в таблице](filter:nodes:deficit)). "
                    f"Максимальная нехватка зафиксирована в диктующем узле [узел {top_lbl}](table:node:{top_id}) (отметка Z = {top_d.get('elevation_m', '—')} м, {top_d.get('floors', 1)} эт., Pтреб = {top_d.get('required_pressure_m', 10):.1f} м): "
                    f"{pos_note}нехватка напора для верхних этажей составляет **{top_abs:.2f} м** (ΔP = {top_val:.2f} м) на смежных участках {nb_pipes}."
                )
            if err_cnt > 0:
                w_err = pa.get("worst_data_error_nodes", [{}])[0]
                err_id = clean_object_id(w_err.get("node_id", ""))
                err_pipes = ", ".join(f"#{pid}" for pid in (w_err.get("connected_pipe_ids") or [])[:3])
                lines.append(
                    f"- **Расчетные артефакты отрицательного давления (< -3.0 м, правило R02):** Обнаружено **{err_cnt} активных узлов** с расчетным давлением ниже -3.0 м "
                    f"([Показать и отсортировать {err_cnt} узлов с P < -3.0 м в таблице](filter:nodes:data_error)). "
                    f"Поскольку физический вакуум в воде не может превышать -10 м вод. ст., экстремальное значение в узле [узел #{err_id}](table:node:{err_id}) (Pмин = {w_err.get('free_pressure_min_m', 0):.1f} м, Z = {w_err.get('elevation_m', '—')} м"
                    + (f", смежные трубы: {err_pipes}" if err_pipes else "")
                    + f") является математическим артефактом DDA-расчета: {w_err.get('diagnostic_advice', 'проверить отметку Z и открытость подводящих труб')}."
                )
            if exc_cnt > 0:
                w_exc = pa.get("top_excess_nodes", [{}])[0]
                exc_id = clean_object_id(w_exc.get("node_id", ""))
                lines.append(
                    f"- **Зона избыточного напора (> 60 м по СП 31.13330.2012 п. 5.13):** Зафиксировано **{exc_cnt} узлов** с превышением предельно допустимых 60 м вод. ст. "
                    f"([Отфильтровать {exc_cnt} узлов с P > 60 м](filter:nodes:excess)), максимум в узле [узел #{exc_id}](table:node:{exc_id}) (Pмакс = {w_exc.get('free_pressure_max_m', 0):.1f} м при Z = {w_exc.get('elevation_m', '—')} м)."
                )
            if top_id:
                lines.append(
                    f"- **Инженерные мероприятия и действия в ПК «Аквабит»:** (1) На сети: для обеспечения верхних этажей в зоне узла #{top_id} применить квартальную повысительную установку (ПНС по СП 31.13330.2012 п. 5.11, Примечание 2) или закольцевать подводящую линию (п. 11.5); в низинной зоне около #{exc_id if exc_cnt else top_id} установить регулятор давления «после себя» PRV (п. 11.38). "
                    f"(2) В ПК «Аквабит»: верифицировать геодезическую отметку Z для узлов с расчетным артефактом P < -3.0 м (#{err_id if err_cnt else top_id}) и проверить статус секущих задвижек."
                )

        elif dom == "pump_imbalance":
            section_order.append("qhSection")
            under = pma.get("underloaded_pumps", [])
            over = pma.get("overloaded_pumps", [])
            bh_under = [p for p in under if p.get("is_borehole")]
            boost_under = [p for p in under if not p.get("is_borehole")]
            lines.append(f"\n## Приоритет {idx} - Диагностика насосных агрегатов, резерва и напорных коллекторов (СП 31.13330 п. 10.3, п. 10.7)")
            lines.append(
                f"- **Режим работы и нормативный резерв (СП 31.13330 п. 10.3 Таблица 23):** Из {tot_pumps} насосных агрегатов в работе находятся {act_pumps} шт., в штатном холодном резерве — {res_pumps} шт. "
                f"Суммарная паспортная подача всех агрегатов составляет {inst_q:,.1f} м³/ч при пиковом потреблении сети {peak_q:,.1f} м³/ч "
                f"(работают левее зоны оптимального КПД BEP < 75% Qном — {len(under)} шт. ([Показать в таблице](filter:pumps:underload)), "
                f"правее зоны BEP > 115% Qном — {len(over)} шт. ([Показать в таблице](filter:pumps:overload)))."
            )
            if bh_under:
                p0 = bh_under[0]
                p0_id = clean_object_id(p0.get("pump_id"))
                sn_id = p0.get("start_node") or "—"
                en_id = p0.get("end_node") or "—"
                out_p = p0.get("connected_out_pipe_id")
                lines.append(
                    f"- **Разбор режима скважинного агрегата [насос #{p0_id}](table:{p0_id}) (источник #{sn_id} → коллектор #{en_id}):** "
                    f"{p0.get('diagnostic_advice', '')} "
                    + (f"Связанный отходящий участок коллектора: #{out_p} (удельный уклон {p0.get('connected_out_pipe_loss', 0)} м/км)." if out_p else "")
                )
            if boost_under:
                pb = boost_under[0]
                pb_id = clean_object_id(pb.get("pump_id"))
                lines.append(
                    f"- **Разбор режима сетевого насоса [насос #{pb_id}](table:{pb_id}) (вход #{pb.get('start_node', '—')} → напорный узел #{pb.get('end_node', '—')}):** "
                    f"{pb.get('diagnostic_advice', '')}"
                )
            if over:
                po = over[0]
                po_id = clean_object_id(po.get("pump_id"))
                lines.append(
                    f"- **Разбор перегрузки агрегата [насос #{po_id}](table:{po_id}) правее зоны BEP (напорный узел #{po.get('end_node', '—')}):** "
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
            l_m = w_p.get("length_m", 0.0) or 0.0
            i_grad = w_p.get("headloss_gradient_m_km", 0.0) or 0.0
            dh_m = w_p.get("total_headloss_m")
            if dh_m is None:
                dh_m = round(i_grad * l_m / 1000.0, 2)
            c_val = w_p.get("hazen_williams_c", 100)
            lines.append(f"\n## Приоритет {idx} - Гидравлические узкие места, удельные и фактические потери напора ({bneck_cnt} участков)")
            lines.append(
                f"- **Локализация лимитирующих водоводов:** В сети выявлено **{bneck_cnt} участков** с высоким гидравлическим сопротивлением "
                f"([Отфильтровать узкие места в таблице труб](filter:pipes:bottlenecks)). "
                f"Участок [труба #{w_id}](table:{w_id}) между узлами #{sn_id} и #{en_id} (D = {w_p.get('diameter_mm', 0):.0f} мм, длина L = {l_m:.1f} м, C = {c_val}) "
                f"имеет удельный уклон **i = {i_grad:.1f} м/км**, что на длине {l_m:.1f} м дает фактическую потерю напора **Δh = {dh_m:.2f} м** при скорости {w_p.get('velocity_max_m_s', 0):.2f} м/с."
            )
            lines.append(
                f"- **Диагностика и действия в ПК «Аквабит»:** {w_p.get('diagnostic_advice', '')}"
            )

        elif dom == "water_stagnation":
            section_order.append("stagnationSection")
            stag_cnt = ppa.get("low_velocity_under_01ms_count", 0)
            closed_valve_cnt = ppa.get("closed_by_valve_pipes_count", ppa.get("closed_zero_flow_pipes_count", 0))
            zero_open_cnt = ppa.get("zero_flow_open_pipes_count", 0)
            lines.append(f"\n## Приоритет {idx} - Скоростной режим сети, перекрытые задвижки и тупиковые зоны (СП 31.13330 п. 11.5)")
            lines.append(
                f"- **Разделение перекрытых задвижек и тупиковых участков с Q = 0:** В модели зафиксировано **{closed_valve_cnt} участков, физически перекрытых задвижкой** (статус CLOSED), "
                f"и **{zero_open_cnt} открытых участков с нулевым расходом Q = 0 м³/ч** (тупиковые вводы без абонентской нагрузки и обвязка резервных насосов) "
                f"([Показать участки с Q = 0 в таблице](filter:pipes:closed)), а также **{stag_cnt} малопроточных участков** со скоростью V < 0.10 м/с ([Отфильтровать малопроточные участки](filter:pipes:stagnant)). "
                f"По СП 31.13330.2021 п. 11.5 сети должны быть кольцевыми."
            )

        elif dom == "valves_and_storage":
            section_order.append("valvesSection")
            v_tbl = vra.get("valves_table", [])
            top_v = max(v_tbl, key=lambda x: x.get("flow_swing_m3_h", 0)) if v_tbl else {}
            v_id = top_v.get("clean_id", "")
            lines.append(f"\n## Приоритет {idx} - Режим работы регулирующих клапанов (PRV/PSV), источников и РЧВ (СП 31.13330 п. 11.38, п. 12.1–12.5)")
            lines.append(
                f"- **Баланс регулирующей арматуры и емкостей:** В расчетной схеме задействовано {valves_cnt} клапанов, {src_cnt} источников и {tanks_cnt} резервуаров РЧВ. "
                + (f"Клапан #{v_id} ({top_v.get('type_code')}, уставка {top_v.get('setting')}) на участке #{top_v.get('start_node')} → #{top_v.get('end_node')} работает в диапазоне расходов от {top_v.get('flow_min_m3_h')} до {top_v.get('flow_max_m3_h')} м³/ч. " if v_id else "")
                + "Согласование уровней РЧВ и уставок редукционных клапанов стабилизирует напор в диктующих точках сети."
            )

        elif dom == "diurnal_imbalance":
            section_order.append("diurnalSection")
            first_lift_d = wb.get("first_lift_daily_supply_m3_day", 0.0)
            booster_d = wb.get("booster_daily_repumping_m3_day", 0.0)
            losses_d = wb.get("estimated_water_losses_and_unbilled_m3_day", 0.0)
            losses_pct = wb.get("estimated_water_losses_pct", 0.0)
            lines.append(f"\n## Приоритет {idx} - Суточный баланс подачи, ступеней перекачки и потерь воды в сети")
            lines.append(
                f"- **Структура суточного баланса и потерь воды (24 ч):** Полезное водопотребление абонентов составляет **{cons_m3:,.0f} м³/сут** (пиковый часовой отбор {peak_q:,.1f} м³/ч, Kчас = {wb.get('peak_factor_k_hour', 1.0)}), "
                f"подача насосов 1-го подъема от источников — **{first_lift_d:,.0f} м³/сут**, повторная перекачка сетевыми НС — **{booster_d:,.0f} м³/сут**. "
                f"Основная разница между первичной подачей в сеть и полезным отбором абонентов ({losses_d:,.0f} м³/сут, или ~{losses_pct:.1f}%) формируется за счет **потерь воды в сети** "
                f"(физических утечек из-за износа и избыточного давления P > 60 м + коммерческих потерь и несанкционированного водопотребления), "
                f"тогда как резервуары чистой воды (РЧВ, СП 31.13330 п. 12.1–12.5) выполняют только внутрисуточное регулирование часовой неравномерности."
            )

    if norm_clauses:
        lines.append("\n## Нормативная база (RAG-слой Qdrant по СП 31.13330)")
        for nc in norm_clauses[:6]:
            lines.append(f"- **СП 31.13330 {nc.get('clause_id')} ({nc.get('title')}):** {nc.get('text')}")

    lines.append("\n[SELECTED_DASHBOARDS]")
    lines.append(json.dumps({
        "section_order": section_order,
        "dashboards": [{"id": sec, "priority": i} for i, sec in enumerate(section_order, 1)],
    }, ensure_ascii=False))
    return "\n".join(lines)
