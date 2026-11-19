from __future__ import annotations

"""EPANET-AI v3.2 Interactive HTML Report Generator:
- Table Sorting & Multi-Preset Filtering (linked to Warnings, AI Report, Charts & Pie Sectors)
- Graph -> Table -> Map Interactive Chain
- Fixed Center-Anchored Map Zoom (No Grey Screen) + Pulsing Selected Object Animation
- High-Resolution HH:MM Timeline (without R360) with Live Selected Object Value Updates
- On-Demand Context-Aware AI Analysis with Live Streaming / Typewriter Effect & Custom Q&A."""

import datetime as dt
import html
import json
import re
from pathlib import Path
from typing import Any

try:
    from aquabit_ai.ontology_engine import (
        clean_object_id,
        clean_object_name,
        load_hydraulic_ontology,
        evaluate_ontology_rules,
        analyze_problem_domains,
        extract_ai_dashboards_and_content,
    )
except ImportError:
    from ontology_engine import (
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


def _markdown_to_clean_html(md: str) -> str:
    lines = md.splitlines()
    out = []
    in_ul = False
    in_ol = False

    _MAP_CONTEXT_RE = re.compile(
        r"(на\s+карт|располож|местополож|географ|район|схем[еуа]\s+сет|рядом\s+с|вблизи|маршрут|ветк[иаеу]|контур)",
        flags=re.IGNORECASE,
    )

    def close_lists():
        nonlocal in_ul, in_ol
        if in_ul:
            out.append("</ul>")
            in_ul = False
        if in_ol:
            out.append("</ol>")
            in_ol = False

    def format_inline(text_line: str) -> str:
        s_escaped = html.escape(text_line)
        # 1) Ссылки автофильтрации таблиц: [Текст](filter:nodes:data_error)
        s_escaped = re.sub(
            r"\[([^\]]+)\]\(filter:([a-zA-Z0-9_]+):([a-zA-Z0-9_]+)\)",
            r'<button type="button" class="filter-action-link" onclick="applyQuickFilterFromAI(\'\2\', \'\3\')" title="Применить фильтр и сортировку в таблице">\1 ↗</button>',
            s_escaped,
        )
        # 2) Явные ссылки на таблицу: [Текст](table:3466703)
        s_escaped = re.sub(
            r"\[([^\]]+)\]\(table:#?([ceputvk]?\d+)\)",
            lambda m: f'<button type="button" class="object-link" data-table-id="{clean_object_id(m.group(2))}" title="Открыть объект #{clean_object_id(m.group(2))} в таблице">{m.group(1)}</button>',
            s_escaped,
            flags=re.IGNORECASE,
        )
        # 3) Явные ссылки на карту: [Текст](map:3466703)
        s_escaped = re.sub(
            r"\[([^\]]+)\]\(map:#?([ceputvk]?\d+)\)",
            lambda m: f'<button type="button" class="object-link object-link-map" data-map-id="{clean_object_id(m.group(2))}" title="Показать расположение #{clean_object_id(m.group(2))} на карте">{m.group(1)}</button>',
            s_escaped,
            flags=re.IGNORECASE,
        )
        s_escaped = re.sub(r"\*\*(.*?)\*\*", r"<strong>\1</strong>", s_escaped)
        s_escaped = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", s_escaped)
        s_escaped = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", s_escaped)

        # Контекстный роутинг ID: по умолчанию -> таблица, если рядом контекст местоположения/карты -> карта
        def _make_ctx_btn(raw_id: str, full_str: str, start_idx: int, end_idx: int) -> str:
            clean = clean_object_id(raw_id)
            window = full_str[max(0, start_idx - 70): min(len(full_str), end_idx + 70)]
            if _MAP_CONTEXT_RE.search(window):
                return f'<button type="button" class="object-link object-link-map" data-map-id="{clean}" title="Показать расположение #{clean} на карте">#{clean}</button>'
            return f'<button type="button" class="object-link" data-table-id="{clean}" title="Открыть объект #{clean} в таблице (из таблицы — на карту)">#{clean}</button>'

        s_escaped = re.sub(
            r"<code>#?([ceputvk]\d+|\d{2,})</code>",
            lambda m: _make_ctx_btn(m.group(1), s_escaped, m.start(), m.end()),
            s_escaped,
            flags=re.IGNORECASE,
        )
        # Также преобразуем голые #ID вне HTML-тегов и атрибутов
        parts = re.split(r"(<[^>]+>)", s_escaped)
        for idx_p in range(0, len(parts), 2):
            seg = parts[idx_p]
            if "#" in seg:
                parts[idx_p] = re.sub(
                    r"(?<![A-Za-z0-9_])#([ceputvk]\d+|\d{2,})\b",
                    lambda m, _seg=seg: _make_ctx_btn(m.group(1), _seg, m.start(), m.end()),
                    seg,
                    flags=re.IGNORECASE,
                )
        return "".join(parts)

    for line in lines:
        s = line.strip()
        if not s:
            close_lists()
            continue
        if s.startswith("# "):
            close_lists()
            out.append(f"<h1 class='ai-h1'>{format_inline(s[2:].strip())}</h1>")
        elif s.startswith("## "):
            close_lists()
            out.append(f"<h2 class='ai-h2'>{format_inline(s[3:].strip())}</h2>")
        elif s.startswith("### "):
            close_lists()
            out.append(f"<h3 class='ai-h3'>{format_inline(s[4:].strip())}</h3>")
        elif s.startswith("- ") or s.startswith("* ") or s.startswith("• "):
            if not in_ul:
                close_lists()
                out.append("<ul>")
                in_ul = True
            out.append(f"<li>{format_inline(s[2:].strip())}</li>")
        elif re.match(r"^\d+\.\s", s):
            if not in_ol:
                close_lists()
                out.append("<ol>")
                in_ol = True
            content = re.sub(r"^\d+\.\s*", "", s).strip()
            out.append(f"<li>{format_inline(content)}</li>")
        else:
            close_lists()
            out.append(f"<p>{format_inline(s)}</p>")

    close_lists()
    return "\n".join(out)


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
        pa.get("worst_data_error_nodes", [])
        + (pa.get("all_deficit_nodes") or pa.get("worst_deficit_nodes", []))
        + pa.get("top_excess_nodes", [])
        + pa.get("shutoff_wells_nodes", [])
    )
    seen_node_ids: set[str] = set()
    dedup_nodes = []
    for r in raw_table_nodes:
        nid = str(r.get("node_id") or "")
        if nid and nid not in seen_node_ids:
            seen_node_ids.add(nid)
            p_min_v = r.get("free_pressure_min_m")
            p_max_v = r.get("free_pressure_max_m")
            dp_v = r.get("pressure_surplus_deficit_m", r.get("margin_min_m"))
            p_cat = r.get("pressure_category")
            if not p_cat:
                if isinstance(p_min_v, (int, float)) and p_min_v < -3.0:
                    p_cat = "data_error"
                elif isinstance(dp_v, (int, float)) and dp_v < 0:
                    p_cat = "deficit"
                elif isinstance(p_max_v, (int, float)) and p_max_v > 60.0:
                    p_cat = "excess"
                elif isinstance(p_min_v, (int, float)) and p_min_v < 0:
                    p_cat = "negative"
                else:
                    p_cat = "normal"
            _ai_nodes_map = (facts.get("ai_table_advices") or {}).get("nodes") or {}
            _cid_n = clean_object_id(r.get("node_id"))
            _graph_adv_n = r.get("diagnostic_advice") or (
                f"Критический вакуум Pмин={p_min_v} м (проверить отметку Z={r.get('elevation_m', '—')} м и секущие задвижки)"
                if p_cat == "data_error" else
                (f"Дефицит напора ΔP={dp_v} м для {r.get('floors', 1)}-эт. застройки" if p_cat == "deficit" else
                 (f"Избыточный напор Pмакс={p_max_v} м (>60 м, требуется PRV)" if p_cat == "excess" else "Норма"))
            )
            _llm_adv_n = r.get("ai_advice") or _ai_nodes_map.get(_cid_n) or _ai_nodes_map.get(str(r.get("node_id") or ""))
            dedup_nodes.append({
                "node_id": r.get("node_id"),
                "clean_id": _cid_n,
                "name": clean_object_name(r.get("name")),
                "elevation_m": r.get("elevation_m", "—"),
                "floors": r.get("floors", 0),
                "required_pressure_m": r.get("required_pressure_m", "—"),
                "free_pressure_min_m": p_min_v if p_min_v is not None else "—",
                "free_pressure_max_m": p_max_v if p_max_v is not None else "—",
                "margin_m": dp_v if dp_v is not None else "—",
                "demand_m3_h": r.get("demand_mean_m3_h", "—"),
                "pressure_category": p_cat,
                "graph_fact": _graph_adv_n,
                "ai_advice": _llm_adv_n if _llm_adv_n else "",
                "advice": _llm_adv_n if _llm_adv_n else _graph_adv_n,
                "advice_mode": "ai" if _llm_adv_n else "graph",
                "ai_generated": bool(_llm_adv_n),
            })

    raw_pipes_for_table = ppa.get("table_pipes_records") or (
        ppa.get("top_high_loss_pipes", [])
        + ppa.get("top_low_hazen_williams_c_pipes", [])
        + ppa.get("closed_pipes_sample", [])
    )
    _ai_pipes_map = (facts.get("ai_table_advices") or {}).get("pipes") or {}
    seen_pipe_ids: set[str] = set()
    dedup_pipes = []
    for p in raw_pipes_for_table:
        pid = str(p.get("pipe_id") or "")
        if pid and pid not in seen_pipe_ids:
            seen_pipe_ids.add(pid)
            v_val = p.get("velocity_max_m_s")
            i_val = p.get("headloss_gradient_m_km")
            c_val = p.get("hazen_williams_c")
            is_cl = bool(p.get("is_closed")) or (isinstance(v_val, (int, float)) and v_val <= 0.001)
            v_bin = p.get("velocity_bin")
            if not v_bin:
                if is_cl:
                    v_bin = "Перекрыты / Поток = 0 (задвижка)"
                elif isinstance(v_val, (int, float)):
                    if v_val < 0.10:
                        v_bin = "Низкие скорости (< 0.10 м/с)"
                    elif v_val < 0.50:
                        v_bin = "Умеренные (0.10–0.50 м/с)"
                    elif v_val <= 1.50:
                        v_bin = "Оптимум (0.50–1.50 м/с)"
                    elif v_val < 5.00:
                        v_bin = "Повышенные (1.50–5.00 м/с)"
                    else:
                        v_bin = "Большая скорость (>= 5.00 м/с)"
                else:
                    v_bin = "Не определен"
            sn_clean = clean_object_id(p.get("start_node")) or "—"
            en_clean = clean_object_id(p.get("end_node")) or "—"
            _cid_p = clean_object_id(p.get("pipe_id"))
            _graph_adv_p = p.get("diagnostic_advice") or (
                f"Участок перекрыт (Q=0) между узлами #{sn_clean} и #{en_clean}" if is_cl else
                (f"Высокие потери i={i_val} м/км (узел #{sn_clean} → #{en_clean}, C={c_val})" if isinstance(i_val, (int, float)) and i_val >= 15 else
                 f"Режим: {v_bin} (#{sn_clean} → #{en_clean})")
            )
            _llm_adv_p = p.get("ai_advice") or _ai_pipes_map.get(_cid_p) or _ai_pipes_map.get(pid)
            dedup_pipes.append({
                "pipe_id": p.get("pipe_id"),
                "clean_id": _cid_p,
                "name": clean_object_name(p.get("name")),
                "start_node": sn_clean,
                "end_node": en_clean,
                "diameter_mm": p.get("diameter_mm", "—"),
                "length_m": p.get("length_m", "—"),
                "velocity_max_m_s": v_val if v_val is not None else "—",
                "headloss_m_km": i_val if i_val is not None else "—",
                "hazen_williams_c": c_val if c_val is not None else "—",
                "flow_m3_h": p.get("flow_max_m3_h", "—"),
                "velocity_bin": v_bin,
                "is_closed": is_cl,
                "graph_fact": _graph_adv_p,
                "ai_advice": _llm_adv_p if _llm_adv_p else "",
                "advice": _llm_adv_p if _llm_adv_p else _graph_adv_p,
                "advice_mode": "ai" if _llm_adv_p else "graph",
                "ai_generated": bool(_llm_adv_p),
            })

    _ai_pumps_map = (facts.get("ai_table_advices") or {}).get("pumps") or {}
    _ai_valves_map = (facts.get("ai_table_advices") or {}).get("valves") or {}
    client_tables = {
        "nodes": dedup_nodes,
        "pipes": dedup_pipes,
        "pumps": [
            {
                "pump_id": p.get("pump_id"),
                "clean_id": clean_object_id(p.get("pump_id")),
                "name": clean_object_name(p.get("name")),
                "pump_type": p.get("pump_type", "Насос"),
                "is_borehole": bool(p.get("is_borehole")),
                "active": bool(p.get("active")),
                "start_node": clean_object_id(p.get("start_node")) or "—",
                "end_node": clean_object_id(p.get("end_node")) or "—",
                "collector_pressure_m": p.get("collector_pressure_m", "—"),
                "curve_id": p.get("curve_id", "—"),
                "q_work": p.get("q_working_m3_h") if p.get("q_working_m3_h") is not None else 0.0,
                "q_nom": p.get("q_nominal_m3_h", "—"),
                "h_work": p.get("h_working_m", p.get("h_est_m")) if p.get("h_working_m", p.get("h_est_m")) is not None else 0.0,
                "q_ratio": p.get("q_ratio"),
                "regime": p.get("regime", "—"),
                "graph_fact": p.get("diagnostic_advice", "—"),
                "ai_advice": p.get("ai_advice") or _ai_pumps_map.get(clean_object_id(p.get("pump_id"))) or _ai_pumps_map.get(str(p.get("pump_id") or "")) or "",
                "advice": p.get("ai_advice") or _ai_pumps_map.get(clean_object_id(p.get("pump_id"))) or _ai_pumps_map.get(str(p.get("pump_id") or "")) or p.get("diagnostic_advice", "—"),
                "advice_mode": "ai" if (p.get("ai_advice") or _ai_pumps_map.get(clean_object_id(p.get("pump_id"))) or _ai_pumps_map.get(str(p.get("pump_id") or ""))) else "graph",
                "ai_generated": bool(p.get("ai_advice") or _ai_pumps_map.get(clean_object_id(p.get("pump_id"))) or _ai_pumps_map.get(str(p.get("pump_id") or ""))),
            }
            for p in all_pumps
        ],
        "valves": [
            {
                "valve_id": v.get("valve_id"),
                "clean_id": v.get("clean_id"),
                "start_node": clean_object_id(v.get("start_node")) or "—",
                "end_node": clean_object_id(v.get("end_node")) or "—",
                "type_code": f"{v.get('type_code')} ({v.get('type_id')})",
                "type_desc": v.get("type_desc"),
                "setting": v.get("setting", "—"),
                "p_drop_label": (
                    f"{v.get('p_before_m')} → {v.get('p_after_m')} м (ΔP={v.get('delta_p_m')} м)"
                    if v.get("delta_p_m") is not None else "—"
                ),
                "flow_min": v.get("flow_min_m3_h", 0),
                "flow_mean": v.get("flow_mean_m3_h", 0),
                "flow_max": v.get("flow_max_m3_h", 0),
                "flow_swing": v.get("flow_swing_m3_h", 0),
                "status": v.get("status", "—"),
                "graph_fact": (
                    f"Клапан {v.get('type_code')} (уставка {v.get('setting', '—')}), режим: {v.get('status', '—')}, ΔQ={v.get('flow_swing_m3_h', 0)} м³/ч"
                    + (f", перепад давления {v.get('p_before_m')} → {v.get('p_after_m')} м" if v.get("delta_p_m") is not None else "")
                ),
                "ai_advice": _ai_valves_map.get(str(v.get("clean_id") or "")) or "",
                "advice": _ai_valves_map.get(str(v.get("clean_id") or "")) or (
                    f"Клапан {v.get('type_code')} (уставка {v.get('setting', '—')}), режим: {v.get('status', '—')}, ΔQ={v.get('flow_swing_m3_h', 0)} м³/ч"
                    + (f", перепад давления {v.get('p_before_m')} → {v.get('p_after_m')} м" if v.get("delta_p_m") is not None else "")
                ),
                "advice_mode": "ai" if _ai_valves_map.get(str(v.get("clean_id") or "")) else "graph",
                "ai_generated": bool(_ai_valves_map.get(str(v.get("clean_id") or ""))),
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
            "desc": f"Обнаружено {err_cnt} активных узлов с P < -3.0 м. Минимальный напор в узле {we_id} ({worst_e.get('free_pressure_min_m', 0):.1f} м). Нажмите для автоматической фильтрации, сортировки и подсветки всех {err_cnt} узлов в таблице.",
            "target": "tablesSection",
            "table_tab": "nodes",
            "filter_preset": "data_error",
            "sort_key": "free_pressure_min_m",
            "sort_dir": "asc",
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
            "desc": f"Выявлено {def_cnt} абонентов с дефицитом давления. Лимитирующий узел {worst_id} с нехваткой ΔP = {worst_d.get('pressure_surplus_deficit_m', 0):.1f} м (нажмите для подсветки на графике и фильтрации всех {len(all_def_ids)} точек в таблице).",
            "target": "pressureSection",
            "table_tab": "nodes",
            "filter_preset": "deficit",
            "sort_key": "margin_m",
            "sort_dir": "asc",
            "focus_id": worst_id,
            "highlight_ids": all_def_ids,
        })

    bneck_pts = chart_payload.get("bottleneck_pipes_points") or []
    bneck_total_cnt = chart_payload.get("bottleneck_pipes_total_count") or ppa.get("bottleneck_pipes_count") or len(
        [p for p in bneck_pts if (p.get("i") or 0) >= 15.0 or (p.get("v") or 0) >= 5.0]
    ) or len(bneck_pts)
    if bneck_pts and ((bneck_pts[0].get("i") or 0) >= 15.0 or (bneck_pts[0].get("v") or 0) >= 5.0):
        worst_p = bneck_pts[0]
        wp_id = worst_p["clean_id"]
        warnings.append({
            "kind": "pipe_bottlenecks",
            "level": "warn",
            "title": f"Гидравлические узкие места и завышенные удельные потери напора ({bneck_total_cnt} участков)",
            "desc": f"Обнаружено {bneck_total_cnt} точек (участков) с высокими потерями давления (в таблице и на графике: {len(bneck_pts)}). Лимитирующий участок {wp_id} (D={worst_p.get('d')} мм) создает потери {worst_p.get('i', 0):.1f} м/км при скорости {worst_p.get('v', 0):.2f} м/с (нажмите для подсветки и сортировки).",
            "target": "bottlenecksSection",
            "table_tab": "pipes",
            "filter_preset": "bottlenecks",
            "sort_key": "headloss_m_km",
            "sort_dir": "desc",
            "focus_id": wp_id,
            "highlight_ids": [p["clean_id"] for p in bneck_pts[:60]],
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
            "desc": f"В недогрузке: {len(under_p)} шт., в перегрузке: {len(over_p)} шт. (скважинных 1-го подъема: {bh_cnt} шт.). Нажмите для показа агрегата {focus_p} на кривой Q-H и фильтрации в таблице насосов.",
            "target": "qhSection",
            "table_tab": "pumps",
            "filter_preset": "imbalance",
            "sort_key": "q_work",
            "sort_dir": "desc",
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
            "table_tab": "valves",
            "filter_preset": "all",
            "sort_key": "flow_swing",
            "sort_dir": "desc",
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
            "desc": f"Перекрыто задвижкой: {kpi_closed_pipes} участков, малопроточных участков: {kpi_v_stag} шт. Нажмите для перехода к диаграмме скоростей и фильтрации закрытых труб.",
            "target": "stagnationSection",
            "table_tab": "pipes",
            "filter_preset": "closed",
            "sort_key": "velocity_max_m_s",
            "sort_dir": "asc",
            "focus_id": None,
            "highlight_ids": [clean_object_id(p.get("pipe_id")) for p in ppa.get("closed_pipes_sample", [])],
        })

    kpi_excess = pa.get("excess_over_60m_count", 0)
    if kpi_excess > 0 and pa.get("top_excess_nodes"):
        top_ex = pa["top_excess_nodes"][0]
        ex_id = clean_object_id(top_ex.get("node_id"))
        warnings.append({
            "kind": "excess_pressure",
            "level": "warn",
            "title": "Избыточный свободный напор (> 60 м по СП 31.13330.2021)",
            "desc": f"Выявлено {kpi_excess} узлов с давлением выше 60 м (максимум в узле {ex_id}: {top_ex.get('free_pressure_max_m', 0):.1f} м). Нажмите для автоматической фильтрации и сортировки этих узлов в таблице.",
            "target": "tablesSection",
            "table_tab": "nodes",
            "filter_preset": "excess",
            "sort_key": "free_pressure_max_m",
            "sort_dir": "desc",
            "focus_id": ex_id,
            "highlight_ids": [clean_object_id(r.get("node_id")) for r in pa["top_excess_nodes"]],
        })

    warnings_html = "".join(
        f'<div class="warning-card warning-{w["level"]}" onclick=\'onWarningClick({json.dumps(w, ensure_ascii=False)})\'>'
        f'  <div class="warning-header">'
        f'    <div class="warning-title">{html.escape(w["title"])}</div>'
        f'    <span class="warning-link">Отфильтровать в таблице и показать на графике →</span>'
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

    # Очищаем метки времени от любых (R360)
    raw_hours_labels = chart_payload.get("hours_labels") or [f"{h:02d}:00" for h in range(24)]
    clean_hours_labels = [re.sub(r"\s*\(R\d+\)", "", str(lbl)).strip() for lbl in raw_hours_labels]

    payload_dict = {
        "city_name": cname,
        "hours_labels": clean_hours_labels,
        "step_labels": [re.sub(r"\s*\(R\d+\)", "", str(lbl)).strip() for lbl in (chart_payload.get("step_labels") or [f"{h:02d}:00" for h in range(24)])],
        "diurnal_demand_m3_h": chart_payload.get("diurnal_demand_m3_h"),
        "diurnal_pump_flow_m3_h": chart_payload.get("diurnal_pump_flow_m3_h"),
        "valves_hourly_series": chart_payload.get("valves_hourly_series", []),
        "diurnal_free_pressure_percentiles_m": chart_payload.get("diurnal_free_pressure_percentiles_m"),
        "node_hourly_pressures": chart_payload.get("node_hourly_pressures", {}),
        "pipe_hourly_flows": chart_payload.get("pipe_hourly_flows", {}),
        "pump_hourly_flows": chart_payload.get("pump_hourly_flows", {}),
        "deficit_nodes_points": def_pts,
        "bottleneck_pipes_points": bneck_pts,
        "bottleneck_pipes_total_count": bneck_total_cnt,
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
    payload_json = json.dumps(payload_dict, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")

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
  body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: var(--bg); color: var(--ink); line-height: 1.55; font-variant-numeric: tabular-nums; }}
  .container {{ max-width: 1440px; margin: 0 auto; padding: 22px 20px 60px; }}
  .hero {{ background: linear-gradient(135deg, #0f172a 0%, #1e3a8a 100%); color: #fff; border-radius: var(--radius); padding: 24px 30px; display: flex; justify-content: space-between; align-items: center; gap: 20px; flex-wrap: wrap; }}
  .hero h1 {{ margin: 0 0 6px; font-size: 24px; font-weight: 700; }}
  .hero-sub {{ margin: 0; color: #cbd5e1; font-size: 13.5px; }}
  .btn-print {{ background: #0284c7; color: #fff; border: none; padding: 8px 16px; border-radius: 8px; font-weight: 600; font-size: 13px; cursor: pointer; }}
  .warnings-container {{ margin-top: 18px; display: flex; flex-direction: column; gap: 10px; }}
  .warning-card {{ background: #fff; border: 1px solid var(--line); border-radius: 10px; padding: 14px 18px; cursor: pointer; transition: all 0.15s ease; }}
  .warning-card:hover {{ transform: translateY(-2px); box-shadow: 0 6px 16px rgba(15, 23, 42, 0.08); border-color: #94a3b8; }}
  .warning-crit {{ border-left: 4px solid var(--crit); background: #fef2f2; }}
  .warning-warn {{ border-left: 4px solid var(--warn); background: #fffbeb; }}
  .warning-ok {{ border-left: 4px solid var(--ok); background: #f0fdf4; }}
  .warning-header {{ display: flex; justify-content: space-between; align-items: center; gap: 10px; flex-wrap: wrap; }}
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
  select.curve-select {{ padding: 5px 10px; border-radius: 6px; border: 1px solid var(--line); background: #f8fafc; font-size: 12.5px; font-weight: 600; max-width: 340px; }}
  .btn-subtle {{ background: #f1f5f9; border: 1px solid #cbd5e1; color: var(--ink); padding: 5px 12px; border-radius: 6px; font-size: 12.5px; font-weight: 600; cursor: pointer; transition: all 0.12s; }}
  .btn-subtle:hover, .btn-subtle.active {{ background: var(--brand); color: #fff; border-color: var(--brand); }}
  .canvas-box {{ position: relative; width: 100%; height: 320px; }}
  canvas {{ width: 100%; height: 100%; display: block; cursor: crosshair; }}
  .chart-legend-inline {{ display: flex; gap: 16px; align-items: center; font-size: 12px; font-weight: 600; color: #334155; flex-wrap: wrap; }}
  .legend-item {{ display: inline-flex; align-items: center; gap: 6px; }}
  .legend-swatch {{ width: 18px; height: 4px; border-radius: 2px; display: inline-block; }}
  .accordion-wrap {{ margin-top: 12px; border-top: 1px solid var(--line); padding-top: 10px; }}
  .analysis-actions {{ display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }}
  .btn-analysis {{ background: linear-gradient(135deg, #0284c7, #0369a1); color: #fff; border: none; border-radius: 6px; padding: 7px 14px; font-size: 12.5px; font-weight: 600; cursor: pointer; display: inline-flex; align-items: center; gap: 6px; }}
  .btn-analysis:hover {{ background: #0369a1; }}
  .analysis-box {{ display: none; margin-top: 10px; background: #f8fafc; border: 1px solid #cbd5e1; border-radius: 8px; padding: 12px 14px; font-size: 13px; color: #1e293b; line-height: 1.55; }}
  .ai-chat-scroll {{ max-height: 360px; overflow-y: auto; padding-right: 6px; scroll-behavior: smooth; }}
  .ai-chat-scroll::-webkit-scrollbar {{ width: 6px; }}
  .ai-chat-scroll::-webkit-scrollbar-thumb {{ background: #cbd5e1; border-radius: 4px; }}
  .ai-msg-user {{ margin-top: 10px; padding: 7px 11px; background: #e0f2fe; border-radius: 7px; font-weight: 600; color: #0369a1; font-size: 12.5px; }}
  .ai-msg-ai {{ margin-top: 6px; padding: 9px 12px; background: #fff; border: 1px solid #cbd5e1; border-radius: 7px; }}
  .ai-md h4 {{ margin: 8px 0 4px; font-size: 13.5px; color: #0c4a6e; }}
  .ai-md p {{ margin: 4px 0; }}
  .ai-md ul, .ai-md ol {{ margin: 4px 0 6px 18px; padding: 0; }}
  .ai-md li {{ margin-bottom: 3px; }}
  .ai-md code {{ background: #f1f5f9; padding: 1px 4px; border-radius: 3px; font-size: 12px; }}
  .ai-chat-followup {{ display: flex; gap: 8px; margin-top: 10px; padding-top: 10px; border-top: 1px dashed #cbd5e1; background: #f8fafc; }}
  .ai-chat-followup input {{ flex: 1; padding: 7px 10px; border: 1px solid #cbd5e1; border-radius: 6px; font-size: 12.5px; background: #fff; }}
  .ai-report-card {{ margin-top: 24px; background: var(--card); border: 1px solid #cbd5e1; border-radius: var(--radius); padding: 22px 26px; }}
  .ai-content h1.ai-h1 {{ font-size: 19px; margin: 0 0 12px; color: #0c4a6e; }}
  .ai-content h2.ai-h2 {{ font-size: 15.5px; margin: 18px 0 8px; color: #0284c7; border-bottom: 1px dashed #cbd5e1; padding-bottom: 4px; }}
  .ai-content p, .ai-content li {{ font-size: 13.5px; }}
  .filter-action-link {{ background: #e0f2fe; border: 1px solid #7dd3fc; color: #0369a1; border-radius: 6px; padding: 1px 8px; font-size: 12.5px; font-weight: 700; cursor: pointer; margin: 0 2px; }}
  .filter-action-link:hover {{ background: #0284c7; color: #fff; }}
  .map-section {{ margin-top: 26px; background: var(--card); border: 1px solid var(--line); border-radius: var(--radius); padding: 18px 20px; }}
  .map-controls {{ display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin: 10px 0 12px; }}
  .map-controls input[type=text] {{ padding: 6px 12px; border: 1px solid #cbd5e1; border-radius: 6px; font-size: 13px; min-width: 220px; }}
  .timeline-box {{ background: #f1f5f9; border: 1px solid #e2e8f0; padding: 10px 14px; border-radius: 8px; margin-bottom: 10px; }}
  .timeline-bar {{ display: flex; align-items: center; gap: 10px; font-size: 13px; }}
  .timeline-bar input[type=range] {{ flex: 1; accent-color: var(--brand); cursor: pointer; margin: 0; }}
  .timeline-ticks {{ display: flex; justify-content: space-between; padding: 4px 2px 0; font-size: 10.5px; color: #64748b; font-weight: 600; user-select: none; }}
  .timeline-ticks span {{ cursor: pointer; padding: 1px 3px; border-radius: 3px; }}
  .timeline-ticks span:hover {{ background: #e2e8f0; color: #0f172a; }}
  .map-canvas-wrap {{ position: relative; width: 100%; height: 520px; background: #f8fafc; border: 1px solid var(--line); border-radius: 8px; overflow: hidden; }}
  #networkMap {{ cursor: grab; }}
  #networkMap:active {{ cursor: grabbing; }}
  .map-card-dock {{ position: absolute; bottom: 12px; right: 12px; width: 370px; max-width: calc(100% - 24px); background: rgba(255,255,255,0.98); border: 1px solid #94a3b8; border-radius: 10px; box-shadow: 0 8px 24px rgba(15,23,42,0.16); padding: 14px 16px; font-size: 12.5px; z-index: 10; display: none; }}
  .map-card-header {{ display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #e2e8f0; padding-bottom: 6px; margin-bottom: 8px; font-weight: 700; }}
  .map-card-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 7px; }}
  .map-prop-label {{ font-size: 11px; color: var(--muted); }}
  .map-prop-val {{ font-weight: 600; color: var(--ink); }}
  .tables-section {{ margin-top: 26px; }}
  .table-tabs {{ display: flex; gap: 8px; margin-bottom: 12px; border-bottom: 1px solid var(--line); padding-bottom: 8px; flex-wrap: wrap; align-items: center; }}
  .table-tab-btn {{ background: transparent; border: none; padding: 8px 14px; border-radius: 6px; font-size: 13.5px; font-weight: 600; color: var(--muted); cursor: pointer; }}
  .table-tab-btn.active {{ background: #e0f2fe; color: #0369a1; }}
  .table-toolbar {{ display: flex; justify-content: space-between; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 10px; }}
  .table-filters {{ display: flex; gap: 6px; flex-wrap: wrap; align-items: center; }}
  .filter-chip {{ background: #f1f5f9; border: 1px solid #cbd5e1; color: #334155; padding: 4px 10px; border-radius: 6px; font-size: 12px; font-weight: 600; cursor: pointer; transition: all 0.12s; }}
  .filter-chip:hover {{ background: #e2e8f0; }}
  .filter-chip.active {{ background: #0284c7; color: #fff; border-color: #0284c7; }}
  .table-status-banner {{ font-size: 12px; color: #0369a1; background: #f0f9ff; border: 1px solid #bae6fd; border-radius: 6px; padding: 6px 12px; margin-bottom: 8px; display: none; justify-content: space-between; align-items: center; }}
  .table-wrap {{ overflow-x: auto; background: #fff; border: 1px solid var(--line); border-radius: 8px; max-height: 480px; }}
  table.data-table {{ width: 100%; border-collapse: collapse; font-size: 12.5px; text-align: left; }}
  table.data-table th {{ position: sticky; top: 0; background: #f8fafc; padding: 9px 10px; border-bottom: 2px solid var(--line); font-weight: 700; cursor: pointer; user-select: none; white-space: nowrap; z-index: 2; }}
  table.data-table th:hover {{ background: #e2e8f0; color: var(--brand); }}
  table.data-table td {{ padding: 7px 10px; border-bottom: 1px solid #f1f5f9; vertical-align: top; }}
  table.data-table tr:hover td {{ background: #f8fafc; }}
  table.data-table tr.row-highlighted td {{ background: #fef2f2 !important; font-weight: 600; }}
  table.data-table tr.row-focused td {{ background: #fef08a !important; font-weight: 700; outline: 2px solid #f59e0b; }}
  .object-link {{ background: transparent; border: none; border-bottom: 1px dashed var(--brand); color: var(--brand); font: inherit; font-weight: 700; cursor: pointer; padding: 0 2px; }}
  .object-link:hover {{ color: #0369a1; background: #e0f2fe; border-radius: 3px; }}
  .object-link-map {{ color: #059669; border-bottom-color: #059669; }}
  .object-link-map:hover {{ color: #065f46; background: #dcfce7; }}
  .chart-jump-btn {{ background: #f5f3ff; border: 1px solid #ddd6fe; color: #5b21b6; border-radius: 4px; padding: 1px 6px; font-size: 10.5px; font-weight: 600; cursor: pointer; margin-left: 6px; vertical-align: middle; }}
  .chart-jump-btn:hover {{ background: #6d28d9; color: #fff; border-color: #6d28d9; }}
  .badge-bh {{ color: #166534; font-weight: 700; }}
  .badge-net {{ color: #075985; font-weight: 700; }}
  .tag-ai-real {{ display: inline-block; background: #dcfce7; color: #166534; border: 1px solid #86efac; border-radius: 4px; padding: 1px 5px; font-size: 10.5px; font-weight: 700; margin-right: 5px; vertical-align: middle; }}
  .tag-graph-fact {{ display: inline-block; background: #f1f5f9; color: #475569; border: 1px solid #cbd5e1; border-radius: 4px; padding: 1px 5px; font-size: 10.5px; font-weight: 600; margin-right: 5px; vertical-align: middle; }}
  .row-ai-btn {{ background: #eef2ff; border: 1px solid #c7d2fe; color: #3730a3; border-radius: 4px; padding: 1px 6px; font-size: 10.5px; font-weight: 600; cursor: pointer; margin-left: 5px; vertical-align: middle; transition: all 0.12s; }}
  .row-ai-btn:hover {{ background: #4f46e5; color: #fff; border-color: #4f46e5; }}
  .row-ai-btn:disabled {{ opacity: 0.55; cursor: wait; }}
  .row-toggle-btn {{ background: #f8fafc; border: 1px solid #cbd5e1; color: #475569; border-radius: 4px; padding: 1px 6px; font-size: 10.5px; font-weight: 600; cursor: pointer; margin-left: 4px; vertical-align: middle; }}
  .row-toggle-btn:hover {{ background: #e2e8f0; color: #0f172a; }}
  .advice-cell-wrap {{ line-height: 1.45; min-width: 240px; }}
  .num-crit {{ color: var(--crit); font-weight: 700; }}
  .num-warn {{ color: var(--warn); font-weight: 700; }}
  .num-ok {{ color: var(--ok); font-weight: 700; }}
  #chartTooltip {{ position: absolute; display: none; pointer-events: none; background: rgba(15, 23, 42, 0.96); border: 1px solid #38bdf8; border-radius: 8px; color: #f8fafc; padding: 9px 12px; font-size: 12px; line-height: 1.45; z-index: 10000; white-space: nowrap; box-shadow: 0 4px 14px rgba(0,0,0,0.25); }}
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
    <div style="display:flex;gap:8px;align-items:center;">
      <button class="btn-print" onclick="window.print()">Печать / PDF</button>
    </div>
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
      <div class="kpi-title">Скоростной режим и потери</div>
      <div class="kpi-val">{kpi_max_v_str} м/с</div>
      <div class="kpi-sub">Узких мест (потери): <strong>{bneck_total_cnt}</strong> • Малопроточных (&lt;0.1 м/с): <strong>{kpi_v_stag:,}</strong></div>
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
          <button class="btn-subtle" onclick="jumpFromCurrentPumpToTable()" title="Показать выбранный насос в таблице">В таблицу ↓</button>
        </div>
      </div>
      <div class="canvas-box"><canvas id="qhCanvas"></canvas></div>
      <div class="accordion-wrap">
        <div class="analysis-actions">
          <button class="btn-analysis" onclick="generateLiveAnalysis('qh')">Сгенерировать ИИ-анализ выбранного насоса и коллектора</button>
          <span style="font-size:11.5px;color:var(--muted)">Клик по рабочей точке на графике подсветит насос в таблице</span>
        </div>
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
        <div class="analysis-actions">
          <button class="btn-analysis" onclick="generateLiveAnalysis('pressure')">Сгенерировать ИИ-анализ графика напоров</button>
          <span style="font-size:11.5px;color:var(--muted)">Кликните по любой точке дефицита для перехода к строке в таблице узлов</span>
        </div>
        <div id="pressureAnalysis" class="analysis-box"></div>
      </div>
    </div>

    <div class="panel" id="bottlenecksSection">
      <div class="panel-header">
        <h3 class="panel-title">Гидравлические узкие места ({bneck_total_cnt} уч.): скорость V (м/с) и потери i (м/км)</h3>
        <div class="panel-controls">
          <button class="btn-subtle" onclick="applyQuickFilterFromAI('pipes', 'bottlenecks')">Все {bneck_total_cnt} в таблице ↓</button>
        </div>
      </div>
      <div class="canvas-box"><canvas id="bottlenecksCanvas"></canvas></div>
      <div class="accordion-wrap">
        <div class="analysis-actions">
          <button class="btn-analysis" onclick="generateLiveAnalysis('bottlenecks')">Сгенерировать ИИ-анализ узких мест</button>
          <span style="font-size:11.5px;color:var(--muted)">Кликните по точке на графике для подсветки трубы в таблице и перехода на карту</span>
        </div>
        <div id="bottlenecksAnalysis" class="analysis-box"></div>
      </div>
    </div>

    <div class="panel" id="stagnationSection">
      <div class="panel-header">
        <h3 class="panel-title">Круговая диаграмма распределения скоростей потока в сети</h3>
        <span style="font-size:11.5px;color:var(--brand);font-weight:600">Клик по сектору или легенде фильтрует таблицу труб ↓</span>
      </div>
      <div class="canvas-box"><canvas id="stagnationCanvas"></canvas></div>
      <div class="accordion-wrap">
        <div class="analysis-actions">
          <button class="btn-analysis" onclick="generateLiveAnalysis('stagnation')">Сгенерировать ИИ-анализ скоростного режима</button>
        </div>
        <div id="stagnationAnalysis" class="analysis-box"></div>
      </div>
    </div>

    <div class="panel" id="valvesSection" style="{'display:flex' if len(valves_table) > 0 else 'display:none'}">
      <div class="panel-header">
        <h3 class="panel-title">Режим работы регулирующих клапанов (PRV / PSV / FCV) во времени</h3>
      </div>
      <div class="canvas-box"><canvas id="valvesCanvas"></canvas></div>
      <div class="accordion-wrap">
        <button class="btn-analysis" onclick="generateLiveAnalysis('valves')">Сгенерировать ИИ-анализ работы клапанов</button>
        <div id="valvesAnalysis" class="analysis-box"></div>
      </div>
    </div>

    <div class="panel" id="diurnalSection" style="{'display:flex' if informative_charts.get('diurnal_balance', True) else 'display:none'}">
      <div class="panel-header">
        <h3 class="panel-title">Суточный баланс подачи насосов и потребления сети (24 ч)</h3>
        <div class="chart-legend-inline">
          <span class="legend-item"><span class="legend-swatch" style="background:#0284c7"></span>Потребление сети (абоненты), м³/ч</span>
          <span class="legend-item"><span class="legend-swatch" style="background:#0d9488"></span>Подача насосных станций, м³/ч</span>
        </div>
      </div>
      <div class="canvas-box"><canvas id="diurnalFlowCanvas"></canvas></div>
      <div class="accordion-wrap">
        <button class="btn-analysis" onclick="generateLiveAnalysis('diurnal')">Сгенерировать ИИ-анализ суточного баланса</button>
        <div id="diurnalAnalysis" class="analysis-box"></div>
      </div>
    </div>
  </section>

  <section class="map-section" id="mapSection">
    <div class="panel-header">
      <div>
        <h3 class="panel-title" style="font-size:16px">Интерактивная карта сети с детальной временной шкалой (Скважины, РЧВ, Клапаны и все узлы)</h3>
        <p style="margin:2px 0 0;font-size:12.5px;color:var(--muted)">Выбранный объект подсвечивается пульсирующим маркером. При перемещении ползунка времени значения в карточке объекта обновляются мгновенно.</p>
      </div>
    </div>
    <div class="timeline-box">
      <div class="timeline-bar">
        <strong>Время расчета:</strong>
        <button type="button" class="btn-subtle" style="padding:2px 8px" onclick="stepTimeline(-1)" title="Предыдущий шаг">◀</button>
        <input type="range" id="mapTimelineRange" min="0" max="{max(0, len(clean_hours_labels) - 1)}" value="0" step="1" oninput="onMapTimelineChange(this.value)">
        <button type="button" class="btn-subtle" style="padding:2px 8px" onclick="stepTimeline(1)" title="Следующий шаг">▶</button>
        <span id="mapTimelineLabel" style="font-weight:800;color:var(--brand);min-width:70px;text-align:right">{clean_hours_labels[0] if clean_hours_labels else '00:00'}</span>
      </div>
      <div class="timeline-ticks" id="mapTimelineTicks"></div>
    </div>
    <div class="map-controls">
      <button type="button" id="mapPlus" class="btn-subtle" title="Приблизить к центру">＋ Приблизить</button>
      <button type="button" id="mapMinus" class="btn-subtle" title="Отдалить от центра">− Отдалить</button>
      <button type="button" id="mapReset" class="btn-subtle">Вся сеть</button>
      <input type="text" id="mapSearch" placeholder="Поиск по ID (например, 3459771)" onkeydown="if(event.key==='Enter')document.getElementById('mapFind').click()">
      <button type="button" id="mapFind" class="btn-subtle">Найти на карте</button>
      <span id="mapInfo" style="font-size:12px;color:var(--muted);margin-left:auto"></span>
    </div>
    <div class="map-canvas-wrap">
      <canvas id="networkMap"></canvas>
      <div id="mapObjectCard" class="map-card-dock">
        <div class="map-card-header">
          <span id="mapCardTitle">Паспорт объекта</span>
          <button type="button" style="background:none;border:none;font-size:18px;cursor:pointer;line-height:1" onclick="closeMapCard()">×</button>
        </div>
        <div id="mapCardBody" class="map-card-grid"></div>
      </div>
    </div>
  </section>

  <section class="tables-section" id="tablesSection">
    <div class="panel">
      <div class="table-tabs">
        <button type="button" class="table-tab-btn active" id="tabBtn_nodes" onclick="switchTableTab('nodes', this)">Узлы абонентов ({len(client_tables['nodes'])})</button>
        <button type="button" class="table-tab-btn" id="tabBtn_pipes" onclick="switchTableTab('pipes', this)">Трубопроводы и скорости ({len(client_tables['pipes'])})</button>
        <button type="button" class="table-tab-btn" id="tabBtn_pumps" onclick="switchTableTab('pumps', this)">Насосы и Скважины ({len(client_tables['pumps'])})</button>
        <button type="button" class="table-tab-btn" id="tabBtn_valves" onclick="switchTableTab('valves', this)">Клапаны PRV/PSV ({len(client_tables['valves'])})</button>
        <button type="button" class="btn-analysis" style="margin-left:auto" onclick="generateTableAIAnalysis()">ИИ-разбор текущей выборки таблицы</button>
      </div>
      <div class="table-toolbar">
        <div id="tableFilters" class="table-filters"></div>
        <div style="display:flex;gap:8px;align-items:center;">
          <input id="tableSearchInput" style="padding:6px 12px;border:1px solid #cbd5e1;border-radius:6px;font-size:12.5px;min-width:220px;" placeholder="Поиск по ID, узлу или диагнозу..." oninput="onTableSearch(this.value)">
          <button type="button" class="btn-subtle" onclick="resetTableFilters()">Сбросить фильтр</button>
        </div>
      </div>
      <div id="tableStatusBanner" class="table-status-banner">
        <span id="tableStatusText"></span>
        <button type="button" class="btn-subtle" style="padding:2px 8px;font-size:11.5px" onclick="resetTableFilters()">Показать все строки</button>
      </div>
      <div id="tableAIAnalysisBox" class="analysis-box" style="margin-bottom:10px;"></div>
      <div class="table-wrap" id="mainTableScrollWrap">
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
// Принудительно очищаем любые старые суффиксы (R360) из меток времени
DATA.hours_labels = (DATA.hours_labels || []).map(l => String(l).replace(/\\s*\\(R\\d+\\)/gi, '').trim());
DATA.step_labels = (DATA.step_labels || []).map(l => String(l).replace(/\\s*\\(R\\d+\\)/gi, '').trim());

let currentTimelineStep = 0;
let highlightedObjectId = null;
let selectedMapObjectId = null;
let highlightIdsSet = new Set();
let pressureChartMode = (DATA.deficit_nodes_points && DATA.deficit_nodes_points.length > 0) ? 'points' : 'corridor';
let hoveredPieSliceIdx = -1;

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

const MAP_CTX_REGEX = /(на\\s+карт|располож|местополож|географ|район|схем[еуа]\\s+сет|рядом\\s+с|вблизи|маршрут|ветк[иаеу]|контур)/i;

function makeContextObjectButton(rawId, contextWindow, defaultTarget = 'table') {{
  const cid = clean_object_id(rawId);
  const toMap = (defaultTarget === 'map') || (defaultTarget === 'table' && MAP_CTX_REGEX.test(contextWindow || ''));
  if (toMap) {{
    return `<button type="button" class="object-link object-link-map" data-map-id="${{cid}}" title="Показать расположение #${{cid}} на карте">#${{cid}}</button>`;
  }}
  return `<button type="button" class="object-link" data-table-id="${{cid}}" title="Открыть объект #${{cid}} в таблице (из таблицы — на карту)">#${{cid}}</button>`;
}}

function formatTextWithObjectButtons(str, defaultTarget = 'map') {{
  if (!str) return '—';
  const escaped = String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
  return escaped.replace(/#([ceputvk]\\d+|\\d{{2,}})/gi, (match, rawId, offset) => {{
    const win = escaped.slice(Math.max(0, offset - 60), Math.min(escaped.length, offset + match.length + 60));
    return makeContextObjectButton(rawId, win, defaultTarget);
  }});
}}

function renderMarkdownToHTML(mdText, defaultTarget = 'table') {{
  if (!mdText) return '';
  const fmtInline = (line) => {{
    let s = String(line || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
    // 1) Ссылки фильтров: [Текст](filter:tab:preset)
    s = s.replace(/\\[([^\\]]+)\\]\\(filter:([a-zA-Z0-9_]+):([a-zA-Z0-9_]+)\\)/g,
      (_, txt, tab, preset) => `<button type="button" class="filter-action-link" onclick="applyQuickFilterFromAI('${{tab}}','${{preset}}')" title="Применить фильтр в таблице">${{txt}} ↗</button>`
    );
    // 2) Явные ссылки на таблицу: [Текст](table:ID)
    s = s.replace(/\\[([^\\]]+)\\]\\(table:#?([ceputvk]?\\d+)\\)/gi,
      (_, txt, rawId) => {{
        const cid = clean_object_id(rawId);
        return `<button type="button" class="object-link" data-table-id="${{cid}}" title="Открыть #${{cid}} в таблице">${{txt}}</button>`;
      }}
    );
    // 3) Явные ссылки на карту: [Текст](map:ID)
    s = s.replace(/\\[([^\\]]+)\\]\\(map:#?([ceputvk]?\\d+)\\)/gi,
      (_, txt, rawId) => {{
        const cid = clean_object_id(rawId);
        return `<button type="button" class="object-link object-link-map" data-map-id="${{cid}}" title="Показать #${{cid}} на карте">${{txt}}</button>`;
      }}
    );
    // 4) Жирный, курсив, инлайн-код
    s = s.replace(/\\*\\*([^\\*\\n]+?)\\*\\*/g, '<strong>$1</strong>');
    s = s.replace(/(^|[^*])\\*([^\\*\\n]+?)\\*(?!\\*)/g, '$1<em>$2</em>');
    s = s.replace(/`([^`\\n]+?)`/g, (match, codeTxt, off) => {{
      const mId = codeTxt.trim().match(/^#?([ceputvk]\\d+|\\d{{2,}})$/i);
      if (mId) {{
        const win = s.slice(Math.max(0, off - 60), Math.min(s.length, off + match.length + 60));
        return makeContextObjectButton(mId[1], win, defaultTarget);
      }}
      return `<code>${{codeTxt}}</code>`;
    }});
    // 5) Голые #ID вне HTML-тегов
    const parts = s.split(/(<[^>]+>)/g);
    for (let idx = 0; idx < parts.length; idx += 2) {{
      if (parts[idx] && parts[idx].includes('#')) {{
        const seg = parts[idx];
        parts[idx] = seg.replace(/(^|[^A-Za-z0-9_])#([ceputvk]\\d+|\\d{{2,}})\\b/gi, (full, pfx, rawId, off) => {{
          const win = seg.slice(Math.max(0, off - 60), Math.min(seg.length, off + full.length + 60));
          return pfx + makeContextObjectButton(rawId, win, defaultTarget);
        }});
      }}
    }}
    return parts.join('');
  }};

  const rawLines = String(mdText).replace(/\\r\\n/g, '\\n').split('\\n');
  const out = [];
  let inUl = false, inOl = false;
  const closeLists = () => {{
    if (inUl) {{ out.push('</ul>'); inUl = false; }}
    if (inOl) {{ out.push('</ol>'); inOl = false; }}
  }};

  for (const rawLine of rawLines) {{
    const s = rawLine.trim();
    if (!s) {{ closeLists(); continue; }}
    const hm = s.match(/^(#{{1,4}})\\s+(.*)$/);
    if (hm) {{
      closeLists();
      out.push(`<h4>${{fmtInline(hm[2])}}</h4>`);
    }} else if (/^[-*•]\\s+/.test(s)) {{
      if (!inUl) {{ closeLists(); out.push('<ul>'); inUl = true; }}
      out.push(`<li>${{fmtInline(s.replace(/^[-*•]\\s+/, ''))}}</li>`);
    }} else if (/^\\d+\\.\\s+/.test(s)) {{
      if (!inOl) {{ closeLists(); out.push('<ol>'); inOl = true; }}
      out.push(`<li>${{fmtInline(s.replace(/^\\d+\\.\\s+/, ''))}}</li>`);
    }} else {{
      closeLists();
      out.push(`<p>${{fmtInline(s)}}</p>`);
    }}
  }}
  closeLists();
  return `<div class="ai-md">${{out.join('')}}</div>`;
}}

function setupHiDPICanvas(canvas) {{
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.parentElement.getBoundingClientRect();
  const w = Math.max(rect.width, 320), h = Math.max(rect.height, 240);
  const targetW = Math.round(w * dpr), targetH = Math.round(h * dpr);
  if (canvas.width !== targetW || canvas.height !== targetH) {{
    canvas.width = targetW;
    canvas.height = targetH;
  }}
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
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
  if (tx + 300 > window.innerWidth) tx = ev.pageX - 300;
  chartTooltipEl.style.left = tx + 'px';
  chartTooltipEl.style.top = ty + 'px';
}}
function hideChartTooltip() {{ chartTooltipEl.style.display = 'none'; }}

function clearAnalysisBox(boxId) {{
  const el = document.getElementById(boxId);
  if (el) {{
    el.style.display = 'none';
    el.innerHTML = '';
  }}
}}

let qhShowAllMode = false, currentQHIndex = 0;
function drawQHCurve(idx, hoverPt = null) {{
  const canvas = document.getElementById('qhCanvas');
  const curves = DATA.qh_curves || [];
  if (!curves.length) return;
  const {{ ctx, w, h }} = setupHiDPICanvas(canvas);
  const pad = {{ l: 58, r: 24, t: 28, b: 42 }};
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

  const opPoints = [];
  curvesToPlot.forEach((item, cIdx) => {{
    const pts = item.curve_points || [];
    if (!qhShowAllMode && item.q_nominal_m3_h > 0) {{
      const x1 = xOf(item.q_nominal_m3_h * 0.75), x2 = xOf(Math.min(maxQ, item.q_nominal_m3_h * 1.15));
      ctx.fillStyle = 'rgba(16, 185, 129, 0.13)';
      ctx.fillRect(x1, pad.t, Math.max(0, x2 - x1), ph);
      ctx.fillStyle = '#059669'; ctx.font = 'bold 10.5px sans-serif';
      ctx.fillText('Оптимум BEP (75–115%)', (x1 + x2) / 2, pad.t + 14);
    }}
    ctx.strokeStyle = item.is_borehole ? '#059669' : '#0284c7';
    ctx.lineWidth = qhShowAllMode ? 1.6 : 2.8;
    ctx.beginPath();
    pts.forEach((p, i) => {{ if (i === 0) ctx.moveTo(xOf(p[0]), yOf(p[1])); else ctx.lineTo(xOf(p[0]), yOf(p[1])); }});
    ctx.stroke();

    if (item.active && item.operating_q_mean !== null && item.operating_h_est !== null) {{
      const ox = xOf(item.operating_q_mean), oy = yOf(item.operating_h_est);
      opPoints.push({{ px: ox, py: oy, item }});
      ctx.fillStyle = '#ef4444'; ctx.beginPath(); ctx.arc(ox, oy, 6.5, 0, Math.PI * 2); ctx.fill();
      ctx.strokeStyle = '#fff'; ctx.lineWidth = 2; ctx.stroke();
      if (!qhShowAllMode) {{
        const tag = item.is_borehole ? 'Скважинный' : 'Сетевой';
        ctx.fillStyle = '#0f172a'; ctx.font = 'bold 11px sans-serif'; ctx.textAlign = 'left';
        ctx.fillText(`${{tag}} #${{item.clean_id}}: Q=${{item.operating_q_mean}} м³/ч, H=${{item.operating_h_est}} м`, Math.min(ox + 10, w - 270), Math.max(pad.t + 26, oy - 8));
      }}
    }} else if (!qhShowAllMode && !item.active) {{
      ctx.fillStyle = '#64748b'; ctx.font = 'bold 11.5px sans-serif'; ctx.textAlign = 'left';
      ctx.fillText(`Агрегат #${{item.clean_id}} выключен / в резерве (Q = 0 м³/ч)`, pad.l + 12, pad.t + 28);
    }}
  }});

  if (hoverPt) {{
    drawCrosshairProjections(ctx, pad, pw, ph, hoverPt.px, hoverPt.py, `Q=${{hoverPt.q.toFixed(1)}}`, `H=${{hoverPt.h.toFixed(1)}}`);
  }}
  canvas._meta = {{ pad, pw, ph, maxQ, maxH, curvesToPlot, opPoints }};
}}

function switchPressureMode(mode) {{
  pressureChartMode = mode;
  document.getElementById('btnModeDeficitPts').classList.toggle('active', mode === 'points');
  document.getElementById('btnModeCorridor').classList.toggle('active', mode === 'corridor');
  clearAnalysisBox('pressureAnalysis');
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
    ctx.fillText('Геодезическая отметка абонента Z, м (клик по точке → строка в таблице)', pad.l + pw / 2, h - 6);
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
      ctx.fillStyle = isWorst ? '#991b1b' : 'rgba(239, 68, 68, 0.82)';
      ctx.fill();
      ctx.strokeStyle = '#ffffff'; ctx.lineWidth = isWorst ? 2.2 : 1; ctx.stroke();

      if (isWorst) {{
        ctx.fillStyle = '#7f1d1d'; ctx.font = 'bold 11.5px sans-serif'; ctx.textAlign = 'left';
        ctx.fillText(`Узел #${{p.clean_id}} (ΔP = ${{p.dp}} м)`, Math.min(px + 10, w - 210), Math.max(pad.t + 16, py - 8));
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
    ctx.textAlign = 'center'; ctx.fillText(`${{String(i).padStart(2, '0')}}:00`, xOf(i), h - pad.b + 16);
  }}
  ctx.fillStyle = '#0f172a'; ctx.font = 'bold 11.5px sans-serif'; ctx.textAlign = 'center';
  ctx.fillText('Время суток, ч (коридор P10..P90 и медиана P50)', pad.l + pw / 2, h - 6);
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
  ctx.fillText('Скорость потока в трубе V, м/с (клик по точке → таблица труб)', pad.l + pw / 2, h - 6);
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
      ctx.fillText(`Участок #${{p.clean_id}}: i=${{p.i}} м/км (V=${{p.v}} м/с)`, Math.min(px + 10, w - 240), Math.max(pad.t + 16, py - 8));
    }}
  }});

  if (hoverPt) {{
    drawCrosshairProjections(ctx, pad, pw, ph, hoverPt.px, hoverPt.py, `V=${{hoverPt.xVal.toFixed(2)}}`, `i=${{hoverPt.yVal.toFixed(1)}}`);
  }}
  canvas._meta = {{ pad, pw, ph, maxX, maxY, pts, xOf, yOf }};
}}

const VELOCITY_BIN_TO_FILTER = {{
  'Перекрыты / Поток = 0 (задвижка)': 'closed',
  'Низкие скорости (< 0.10 м/с)': 'stagnant',
  'Умеренные (0.10–0.50 м/с)': 'vel_moderate',
  'Оптимум (0.50–1.50 м/с)': 'vel_optimum',
  'Повышенные (1.50–5.00 м/с)': 'vel_elevated',
  'Большая скорость (>= 5.00 м/с)': 'vel_extreme'
}};

function drawStagnationChart(hoverIdx = hoveredPieSliceIdx) {{
  const canvas = document.getElementById('stagnationCanvas');
  const {{ ctx, w, h }} = setupHiDPICanvas(canvas);
  const vBins = DATA.velocity_bins || {{}};
  const rawItems = Object.entries(vBins).filter(([_, v]) => typeof v === 'number');
  if (!rawItems.length) return;

  const colors = ['#94a3b8', '#f59e0b', '#38bdf8', '#10b981', '#6366f1', '#ef4444'];
  const total = rawItems.reduce((acc, [_, v]) => acc + v, 0);
  const items = rawItems.map(([label, val], i) => ({{
    idx: i,
    label,
    val,
    ratio: total > 0 ? val / total : 0,
    color: colors[i % colors.length],
    filterCode: VELOCITY_BIN_TO_FILTER[label] || 'all'
  }}));

  const cx = Math.round(w * 0.25), cy = Math.round(h * 0.50);
  const outerR = Math.max(48, Math.min(cx - 24, cy - 24, 108)), innerR = Math.round(outerR * 0.55);
  let curAngle = -Math.PI / 2;
  const slices = [];

  items.forEach((it, idx) => {{
    const sweep = it.ratio * Math.PI * 2;
    const midA = curAngle + sweep / 2;
    const isHovered = idx === hoverIdx;
    const offset = isHovered ? 9 : 0;
    const scx = cx + Math.cos(midA) * offset;
    const scy = cy + Math.sin(midA) * offset;
    slices.push({{ ...it, startA: curAngle, endA: curAngle + sweep, midA }});

    if (it.val > 0) {{
      ctx.save();
      if (isHovered) {{
        ctx.shadowColor = 'rgba(15, 23, 42, 0.22)';
        ctx.shadowBlur = 12;
      }}
      ctx.beginPath();
      ctx.arc(scx, scy, isHovered ? outerR + 3 : outerR, curAngle, curAngle + sweep);
      ctx.arc(scx, scy, innerR, curAngle + sweep, curAngle, true);
      ctx.closePath();
      ctx.fillStyle = it.color;
      ctx.fill();
      ctx.strokeStyle = '#fff';
      ctx.lineWidth = isHovered ? 2.5 : 1.5;
      ctx.stroke();
      ctx.restore();
    }}
    curAngle += sweep;
  }});

  ctx.fillStyle = '#0f172a'; ctx.font = 'bold 15px sans-serif'; ctx.textAlign = 'center';
  ctx.fillText(total.toLocaleString('ru-RU'), cx, cy + 2);
  ctx.font = '11px sans-serif'; ctx.fillStyle = '#64748b'; ctx.fillText('труб всего', cx, cy + 18);

  const legX = Math.round(w * 0.48), legStartY = Math.max(16, Math.round(cy - (items.length * 28) / 2));
  const legendBoxes = [];
  items.forEach((it, idx) => {{
    const ly = legStartY + idx * 28;
    const isHovered = idx === hoverIdx;
    const boxW = w - legX - 8;
    legendBoxes.push({{ idx, x: legX - 6, y: ly - 4, w: boxW, h: 24, item: it }});

    if (isHovered) {{
      ctx.fillStyle = '#e0f2fe';
      ctx.beginPath();
      ctx.roundRect(legX - 6, ly - 4, boxW, 24, 6);
      ctx.fill();
    }}
    ctx.beginPath(); ctx.arc(legX + 6, ly + 8, isHovered ? 6.5 : 5, 0, Math.PI * 2); ctx.fillStyle = it.color; ctx.fill();
    ctx.font = isHovered ? 'bold 12px sans-serif' : '12px sans-serif';
    ctx.fillStyle = isHovered ? '#0369a1' : '#334155';
    ctx.textAlign = 'left';
    ctx.fillText(it.label, legX + 18, ly + 12);
    ctx.font = 'bold 12px sans-serif';
    ctx.fillStyle = isHovered ? '#0369a1' : '#0f172a';
    ctx.textAlign = 'right';
    ctx.fillText(`${{it.val}} (${{(it.ratio * 100).toFixed(1)}}%) ↗`, w - 16, ly + 12);
  }});
  canvas._meta = {{ cx, cy, outerR, innerR, slices, legendBoxes }};
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
  const stepStride = Math.max(1, Math.floor(n / 6));
  for (let i = 0; i < n; i += stepStride) {{
    ctx.textAlign = 'center';
    ctx.fillText((DATA.hours_labels || [])[i] || `${{i}}:00`, xOf(i), h - pad.b + 16);
  }}
  ctx.fillStyle = '#0f172a'; ctx.font = 'bold 11.5px sans-serif'; ctx.textAlign = 'center';
  ctx.fillText('Время суток, ЧЧ:ММ', pad.l + pw / 2, h - 6);
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
  if (dem.some(v => v > 0)) series.push({{ name: 'Потребление сети (абоненты)', data: dem, color: '#0284c7', dashed: false }});
  if (pmp.some(v => v > 0)) series.push({{ name: 'Подача насосных станций', data: pmp, color: '#0d9488', dashed: true }});
  if (!series.length) return;

  const {{ ctx, w, h }} = setupHiDPICanvas(canvas);
  const pad = {{ l: 64, r: 24, t: 36, b: 42 }};
  const pw = w - pad.l - pad.r, ph = h - pad.t - pad.b;
  const maxY = Math.max(...series.flatMap(s => s.data), 1) * 1.16;
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
    ctx.textAlign = 'center'; ctx.fillText(`${{String(i).padStart(2, '0')}}:00`, xOf(i), h - pad.b + 16);
  }}
  ctx.fillStyle = '#0f172a'; ctx.font = 'bold 11.5px sans-serif'; ctx.textAlign = 'center';
  ctx.fillText('Время суток, ч', pad.l + pw / 2, h - 6);
  ctx.save(); ctx.translate(15, pad.t + ph / 2); ctx.rotate(-Math.PI / 2);
  ctx.fillText('Объемный расход Q, м³/ч', 0, 0); ctx.restore();

  // Встроенная легенда на самом полотне графика для однозначного чтения
  let legCursorX = pad.l;
  series.forEach(s => {{
    ctx.save();
    ctx.strokeStyle = s.color;
    ctx.lineWidth = 3;
    if (s.dashed) ctx.setLineDash([6, 4]);
    ctx.beginPath();
    ctx.moveTo(legCursorX, 14);
    ctx.lineTo(legCursorX + 24, 14);
    ctx.stroke();
    ctx.restore();

    ctx.fillStyle = s.color;
    ctx.beginPath();
    ctx.arc(legCursorX + 12, 14, 3.5, 0, Math.PI * 2);
    ctx.fill();

    ctx.font = 'bold 11.5px sans-serif';
    ctx.fillStyle = '#0f172a';
    ctx.textAlign = 'left';
    ctx.fillText(s.name, legCursorX + 30, 18);
    legCursorX += ctx.measureText(s.name).width + 52;
  }});

  series.forEach(s => {{
    ctx.save();
    ctx.strokeStyle = s.color;
    ctx.lineWidth = 2.6;
    if (s.dashed) ctx.setLineDash([7, 4]);
    ctx.beginPath();
    s.data.forEach((v, i) => {{ if (i === 0) ctx.moveTo(xOf(i), yOf(v)); else ctx.lineTo(xOf(i), yOf(v)); }});
    ctx.stroke();
    ctx.restore();

    s.data.forEach((v, i) => {{
      if (i % 3 === 0) {{
        ctx.fillStyle = s.color;
        ctx.beginPath();
        ctx.arc(xOf(i), yOf(v), 3, 0, Math.PI * 2);
        ctx.fill();
      }}
    }});
  }});

  if (hoverPt) {{
    drawCrosshairProjections(ctx, pad, pw, ph, hoverPt.px, hoverPt.py, hoverPt.xLabel, `${{hoverPt.yVal.toFixed(1)}}`);
  }}
  canvas._meta = {{ pad, pw, ph, maxY, n, dem, pmp, xOf, yOf }};
}}

// =================== ЖИВОЙ МНОГОХОДОВЫЙ ЧАТ ИИ С ПРОКРУТКОЙ И MARKDOWN ===================
const aiChatHistory = {{}};

function ensureChatLayout(boxEl, kind) {{
  boxEl.style.display = 'block';
  let scrollEl = boxEl.querySelector('.ai-chat-scroll');
  if (!scrollEl) {{
    boxEl.innerHTML = '';
    scrollEl = document.createElement('div');
    scrollEl.className = 'ai-chat-scroll';
    boxEl.appendChild(scrollEl);
  }}
  let followup = boxEl.querySelector('.ai-chat-followup');
  if (!followup) {{
    followup = document.createElement('div');
    followup.className = 'ai-chat-followup';
    followup.innerHTML = `
      <input type="text" placeholder="Задать уточняющий вопрос ИИ (диалог сохраняется выше)..." onkeydown="if(event.key==='Enter')askCustomAIQuestion('${{kind}}', this)">
      <button type="button" class="btn-subtle" onclick="askCustomAIQuestion('${{kind}}', this.previousElementSibling)">Спросить ИИ</button>
    `;
    boxEl.appendChild(followup);
  }}
  return scrollEl;
}}

function appendFollowupInput(boxEl, kind) {{
  ensureChatLayout(boxEl, kind);
}}

function streamHTMLIntoBox(boxEl, htmlContent, kind, sourceLabel = 'Автономный графовый разбор (локальный llama-server :8080 недоступен)') {{
  aiChatHistory[kind] = [];
  const scrollEl = ensureChatLayout(boxEl, kind);
  scrollEl.innerHTML = `<div class="ai-box-status" style="color:#0284c7;font-weight:700;font-size:12px;margin-bottom:6px;">Формирование инженерного заключения...</div><div class="stream-target"></div>`;
  const target = scrollEl.querySelector('.stream-target');
  const tokens = htmlContent.split(/(<[^>]+>|\\s+)/);
  let i = 0;
  let acc = '';
  const timer = setInterval(() => {{
    const step = Math.min(tokens.length, i + 12);
    for (; i < step; i++) acc += tokens[i];
    target.innerHTML = acc;
    if (i >= tokens.length) {{
      clearInterval(timer);
      const header = scrollEl.querySelector('.ai-box-status');
      if (header) header.innerHTML = sourceLabel;
    }}
  }}, 18);
}}

async function checkLocalLLMHealth(baseUrl) {{
  try {{
    const ctrl = new AbortController();
    const id = setTimeout(() => ctrl.abort(), 1500);
    const r = await fetch(baseUrl + '/health', {{ method: 'GET', signal: ctrl.signal }});
    clearTimeout(id);
    return r.ok;
  }} catch (_e) {{
    return false;
  }}
}}

async function streamLocalLLMMessages(messages, onTokenChunk, maxTokens = 700) {{
  const baseUrls = [];
  if (window.LOCAL_LLM_BASE_URL) baseUrls.push(window.LOCAL_LLM_BASE_URL.replace(/\\/$/, ''));
  baseUrls.push('http://127.0.0.1:8080');

  const cleanLLMText = (t) => String(t || '').replace(/<think>[\\s\\S]*?<\\/think>/gi, '').replace(/<think>[\\s\\S]*$/gi, '').trim();

  for (const base of baseUrls) {{
    const alive = await checkLocalLLMHealth(base);
    if (!alive) continue;

    const t0 = performance.now();
    const ctrl = new AbortController();
    const hardTimeout = setTimeout(() => ctrl.abort(), 90000);
    try {{
      const resp = await fetch(base + '/v1/chat/completions', {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify({{
          model: 'local',
          messages: messages,
          max_tokens: maxTokens,
          temperature: 0.25,
          stream: true,
          chat_template_kwargs: {{ enable_thinking: false }}
        }}),
        signal: ctrl.signal
      }});
      if (!resp.ok || !resp.body) {{
        clearTimeout(hardTimeout);
        continue;
      }}
      const reader = resp.body.getReader();
      const decoder = new TextDecoder('utf-8');
      let fullText = '';
      let buffer = '';
      let tokenChunks = 0;

      while (true) {{
        const {{ done, value }} = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, {{ stream: true }});
        const lines = buffer.split('\\n');
        buffer = lines.pop() || '';
        for (const line of lines) {{
          const trimmed = line.trim();
          if (!trimmed.startsWith('data:')) continue;
          const payloadStr = trimmed.slice(5).trim();
          if (!payloadStr || payloadStr === '[DONE]') continue;
          try {{
            const parsed = JSON.parse(payloadStr);
            const delta = parsed?.choices?.[0]?.delta?.content || '';
            if (delta) {{
              fullText += delta;
              tokenChunks += 1;
              if (onTokenChunk) {{
                const vis = cleanLLMText(fullText);
                if (vis) onTokenChunk(vis);
              }}
            }}
          }} catch (_err) {{}}
        }}
      }}
      clearTimeout(hardTimeout);
      const sec = Math.max(0.1, (performance.now() - t0) / 1000);
      const tps = (tokenChunks / sec).toFixed(1);
      const finalClean = cleanLLMText(fullText);
      if (finalClean) {{
        return {{ text: finalClean, sec: sec.toFixed(1), tps, base }};
      }}
    }} catch (_e) {{
      clearTimeout(hardTimeout);
    }}
  }}
  return null;
}}

async function streamLocalLLM(systemPrompt, userPrompt, onTokenChunk, maxTokens = 700) {{
  return streamLocalLLMMessages(
    [
      {{ role: 'system', content: systemPrompt }},
      {{ role: 'user', content: userPrompt }}
    ],
    onTokenChunk,
    maxTokens
  );
}}

function buildContextExactAnalysisHTML(kind) {{
  if (kind === 'qh') {{
    const curves = DATA.qh_curves || [];
    const cur = curves[currentQHIndex] || curves[0] || {{}};
    const pumpRow = (DATA.tables.pumps || []).find(p => String(p.clean_id) === String(cur.clean_id)) || cur;
    const isBh = Boolean(cur.is_borehole);
    const isAct = Boolean(cur.active);
    const snId = pumpRow.start_node || cur.start_node || '—';
    const enId = pumpRow.end_node || cur.end_node || '—';
    const qWork = cur.operating_q_mean ?? 0;
    const qNom = cur.q_nominal_m3_h ?? 0;
    const hWork = cur.operating_h_est ?? 0;
    const hNom = cur.h_nominal_m ?? 0;
    const ratioPct = qNom > 0 ? Math.round((qWork / qNom) * 100) : 0;

    const enProps = DATA.node_props[enId] || DATA.node_props['c' + enId] || {{}};
    const snProps = DATA.node_props[snId] || DATA.node_props['e' + snId] || DATA.node_props['c' + snId] || {{}};
    const colP = enProps.p_mean ?? enProps.p_max ?? pumpRow.collector_pressure_m ?? '—';
    const colZ = enProps.z ?? pumpRow.collector_elevation_m ?? '—';
    const srcZ = snProps.z ?? pumpRow.source_head_m ?? '—';
    const nbPipes = (enProps.connected_pipe_ids || []).map(id => `<button type="button" class="object-link" data-map-id="${{id}}">#${{id}}</button>`).join(', ');

    if (qhShowAllMode) {{
      const actCnt = curves.filter(c => c.active).length;
      const bhCnt = curves.filter(c => c.is_borehole).length;
      return `<strong>Сводный разбор парка насосных агрегатов (${{curves.length}} кривых Q-H):</strong><br>
      • <b>Структура парка:</b> Скважинных агрегатов 1-го подъема — <b>${{bhCnt}} шт.</b>, сетевых/повысительных — <b>${{curves.length - bhCnt}} шт.</b> (в работе: ${{actCnt}} шт., в резерве: ${{curves.length - actCnt}} шт.).<br>
      • <b>Навигация:</b> Выберите конкретный насос в выпадающем списке выше или нажмите <button type="button" class="filter-action-link" onclick="applyQuickFilterFromAI('pumps', 'imbalance')">Отфильтровать проблемные насосы в таблице ↗</button> для адресного разбора его приемного коллектора и водоводов.`;
    }}

    if (!isAct || qWork <= 0.001) {{
      return `<strong>Диагностика агрегата <button type="button" class="object-link" data-map-id="${{cur.clean_id}}">#${{cur.clean_id}}</button> (${{isBh ? 'Скважинный 1-го подъема' : 'Сетевой насос НС'}}):</strong><br>
      • <b>Фактическое состояние:</b> Насос находится в резерве / отключен (подача <b>Q = 0 м³/ч</b>, развиваемый напор <b>H = 0 м</b>; паспортный номинал кривой <code>${{cur.curve_id}}</code>: Qном = ${{qNom}} м³/ч, Hном = ${{hNom}} м).<br>
      • <b>Состояние приемного узла коллектора <button type="button" class="object-link" data-map-id="${{enId}}">#${{enId}}</button>:</b> Отметка Z = ${{colZ}} м, фактическое давление в узле P = <b>${{colP}} м</b>${{nbPipes ? ` (отходящие участки: ${{nbPipes}})` : ''}}.<br>
      • <b>Мероприятие в ПК «Аквабит»:</b> Если агрегат <button type="button" class="object-link" data-map-id="${{cur.clean_id}}">#${{cur.clean_id}}</button> должен работать по технологическому графику, проверьте его статус в <code>Поток Насос.csv</code> и открытость отходящего участка за узлом <button type="button" class="object-link" data-map-id="${{enId}}">#${{enId}}</button>.`;
    }}

    return `<strong>Диагностика работающего агрегата <button type="button" class="object-link" data-map-id="${{cur.clean_id}}">#${{cur.clean_id}}</button> (${{isBh ? 'Скважинный 1-го подъема' : 'Сетевой насос НС'}}):</strong><br>
    • <b>Рабочая точка на кривой <code>${{cur.curve_id}}</code>:</b> Подача <b>Q = ${{qWork}} м³/ч</b> (${{ratioPct}}% от Qном = ${{qNom}} м³/ч), развиваемый напор <b>H = ${{hWork}} м</b> (Hном = ${{hNom}} м).<br>
    • <b>Привязка к смежным узлам сети:</b> Входной узел ${{isBh ? '(Источник/скважина)' : ''}} <button type="button" class="object-link" data-map-id="${{snId}}">#${{snId}}</button> (H/Z = ${{srcZ}} м) → приемный напорный узел коллектора <button type="button" class="object-link" data-map-id="${{enId}}">#${{enId}}</button> (Z = ${{colZ}} м, давление <b>Pвых = ${{colP}} м</b>)${{nbPipes ? `, смежные водоводы: ${{nbPipes}}` : ''}}.<br>
    • <b>Инженерное заключение и действия в Аквабит:</b> ${{formatTextWithObjectButtons(pumpRow.advice || cur.diagnostic_advice)}}`;
  }}

  if (kind === 'pressure') {{
    const pts = DATA.deficit_nodes_points || [];
    const worst = pts[0] || {{}};
    const wProps = DATA.node_props[worst.node_id] || DATA.node_props[worst.clean_id] || {{}};
    const nbLinks = (wProps.connected_pipe_ids || []).map(id => `<button type="button" class="object-link" data-map-id="${{id}}">#${{id}}</button>`).join(', ');
    return `<strong>Разбор распределения свободного напора (${{pressureChartMode === 'points' ? 'Режим точек дефицита' : 'Суточный коридор 24 ч'}}):</strong><br>
    • <b>Диктующий узел депрессии:</b> Абонент <button type="button" class="object-link" data-map-id="${{worst.clean_id}}">#${{worst.clean_id || '—'}}</button> (${{worst.floors ?? 1}} эт., Z = ${{worst.z ?? '—'}} м) имеет минимальный свободный напор <b>Pсвоб = ${{worst.p_free ?? '—'}} м</b> при требуемом Pтреб = ${{worst.p_req ?? '—'}} м (дефицит <b>ΔP = ${{worst.dp ?? 0}} м</b>).<br>
    • <b>Смежные подводящие участки к узлу #${{worst.clean_id}}:</b> ${{nbLinks || 'уточните на карте'}}.<br>
    • <b>Переход к выборке:</b> <button type="button" class="filter-action-link" onclick="applyQuickFilterFromAI('nodes', 'deficit')">Отфильтровать все ${{pts.length}} узлов с дефицитом в таблице ↗</button> или <button type="button" class="filter-action-link" onclick="applyQuickFilterFromAI('nodes', 'data_error')">Показать узлы с вакуумом P &lt; -3.0 м ↗</button>.`;
  }}

  if (kind === 'bottlenecks') {{
    const pts = DATA.bottleneck_pipes_points || [];
    const top = pts[0] || {{}};
    const totBneck = DATA.bottleneck_pipes_total_count || pts.length;
    return `<strong>Разбор гидравлических узких мест (всего проблемных участков: ${{totBneck}} шт.):</strong><br>
    • <b>Главный дроссель сети:</b> Участок <button type="button" class="object-link" data-map-id="${{top.clean_id}}">#${{top.clean_id || '—'}}</button> между узлами <button type="button" class="object-link" data-map-id="${{top.sn}}">#${{top.sn || '—'}}</button> и <button type="button" class="object-link" data-map-id="${{top.en}}">#${{top.en || '—'}}</button> (D = ${{top.d ?? 0}} мм, L = ${{top.l ?? 0}} м, C = ${{top.c ?? '—'}}) теряет <b>${{top.i ?? 0}} м/км</b> при скорости <b>${{top.v ?? 0}} м/с</b> и расходе ${{top.q ?? 0}} м³/ч.<br>
    • <b>Причина и мероприятие в Аквабит:</b> ${{formatTextWithObjectButtons(top.advice)}}<br>
    • <b>Быстрый фильтр:</b> <button type="button" class="filter-action-link" onclick="applyQuickFilterFromAI('pipes', 'bottlenecks')">Показать и отсортировать узкие места в таблице труб ↗</button>.`;
  }}

  if (kind === 'stagnation') {{
    const vBins = DATA.velocity_bins || {{}};
    const closedCnt = vBins['Перекрыты / Поток = 0 (задвижка)'] || 0;
    const stagCnt = vBins['Низкие скорости (< 0.10 м/с)'] || 0;
    const optCnt = vBins['Оптимум (0.50–1.50 м/с)'] || 0;
    return `<strong>Разбор распределения скоростей потока по участкам сети:</strong><br>
    • <b>Оптимальное ядро (0.50–1.50 м/с):</b> <b>${{optCnt}} участков</b> обеспечивают нормативный гидравлический уклон без застоя.<br>
    • <b>Перекрытые задвижками участки (Q = 0):</b> <b>${{closedCnt}} шт.</b> (<button type="button" class="filter-action-link" onclick="applyQuickFilterFromAI('pipes', 'closed')">Отфильтровать в таблице ↗</button>) — проверить необходимость открытия для закольцовки зон депрессии.<br>
    • <b>Малопроточные зоны (&lt; 0.10 м/с):</b> <b>${{stagCnt}} шт.</b> (<button type="button" class="filter-action-link" onclick="applyQuickFilterFromAI('pipes', 'stagnant')">Отфильтровать в таблице ↗</button>) — рекомендуется перераспределение потоков и ревизия тупиковых линий.`;
  }}

  if (kind === 'valves') {{
    const vs = DATA.tables.valves || [];
    const topV = vs[0] || {{}};
    return `<strong>Разбор работы регулирующих клапанов (PRV/PSV/FCV):</strong><br>
    • <b>Интенсивность регулирования:</b> Клапан <button type="button" class="object-link" data-map-id="${{topV.clean_id}}">#${{topV.clean_id || '—'}}</button> (${{topV.type_code || 'PRV'}}, уставка ${{topV.setting ?? '—'}}) между узлами <button type="button" class="object-link" data-map-id="${{topV.start_node}}">#${{topV.start_node || '—'}}</button> и <button type="button" class="object-link" data-map-id="${{topV.end_node}}">#${{topV.end_node || '—'}}</button> изменяет расход от ${{topV.flow_min ?? 0}} до ${{topV.flow_max ?? 0}} м³/ч (амплитуда ΔQ = ${{topV.flow_swing ?? 0}} м³/ч).`;
  }}

  if (kind === 'diurnal') {{
    const dem = DATA.diurnal_demand_m3_h || [], pmp = DATA.diurnal_pump_flow_m3_h || [];
    const maxDem = Math.max(...dem, 0), maxPmp = Math.max(...pmp, 0);
    return `<strong>Разбор суточного баланса подачи насосов и водопотребления (24 ч):</strong><br>
    • <b>Пиковые нагрузки:</b> Максимальное часовое потребление абонентов (синяя линия) достигает <b>${{maxDem.toFixed(1)}} м³/ч</b>, пиковая подача насосных станций (изумрудная пунктирная линия) — <b>${{maxPmp.toFixed(1)}} м³/ч</b>.<br>
    • <b>Инженерный вывод:</b> Разница между подачей насосов и потреблением сети аккумулируется в резервуарах чистой воды (РЧВ) и компенсирует часы максимального водоразбора.`;
  }}
  return '';
}}

async function generateLiveAnalysis(kind) {{
  const mapIds = {{
    qh: 'qhAnalysis', pressure: 'pressureAnalysis', bottlenecks: 'bottlenecksAnalysis',
    stagnation: 'stagnationAnalysis', valves: 'valvesAnalysis', diurnal: 'diurnalAnalysis'
  }};
  const box = document.getElementById(mapIds[kind]);
  if (!box) return;

  const baseHtml = buildContextExactAnalysisHTML(kind);
  const plainCtx = baseHtml.replace(/<[^>]+>/g, ' ').replace(/\\s+/g, ' ').trim();
  aiChatHistory[kind] = [];

  const scrollEl = ensureChatLayout(box, kind);
  scrollEl.innerHTML = `<div class="ai-box-status" style="color:#0284c7;font-weight:700;font-size:12px;margin-bottom:6px;">Подключение к локальной нейросети (http://127.0.0.1:8080)...</div><div class="stream-target"></div>`;
  const target = scrollEl.querySelector('.stream-target');
  const statusEl = scrollEl.querySelector('.ai-box-status');

  const sysPrompt = 'Ты главный инженер-гидравлик ПК «Аквабит». Дай однозначный, краткий и точный инженерный вывод строго по переданным цифрам и ID. Для сравнения параметров ссылайся на таблицу как #ID или [объект #ID](table:ID), а если важно расположение на схеме — [объект #ID на карте](map:ID). Без шаблонных оговорок и лишней воды.';
  const userPrompt = `Проанализируй текущее состояние графика:\\n${{plainCtx}}`;

  const llmRes = await streamLocalLLM(
    sysPrompt,
    userPrompt,
    (partial) => {{
      statusEl.innerHTML = `🟢 Живая генерация ИИ (GPU / llama-server @ 127.0.0.1:8080)...`;
      target.innerHTML = renderMarkdownToHTML(partial, 'table');
      scrollEl.scrollTop = scrollEl.scrollHeight;
    }}
  );

  if (llmRes && llmRes.text) {{
    aiChatHistory[kind] = [
      {{ role: 'user', content: userPrompt }},
      {{ role: 'assistant', content: llmRes.text }}
    ];
    statusEl.innerHTML = `🟢 Живой вывод локальной LLM (${{llmRes.base}} • ${{llmRes.sec}} сек, ~${{llmRes.tps}} ток/с):`;
    target.innerHTML = renderMarkdownToHTML(llmRes.text, 'table')
      + '<hr style="border:none;border-top:1px dashed #cbd5e1;margin:8px 0">'
      + `<div style="font-size:12px;color:#475569">${{baseHtml}}</div>`;
    scrollEl.scrollTop = 0;
  }} else {{
    streamHTMLIntoBox(
      box,
      baseHtml,
      kind,
      '🟡 Автономный графовый разбор по данным модели (локальный сервер LLM http://127.0.0.1:8080 не запущен):'
    );
  }}
}}

async function askCustomAIQuestion(kind, inputEl) {{
  if (!inputEl || !inputEl.value.trim()) return;
  const q = inputEl.value.trim();
  inputEl.value = '';
  const mapIds = {{
    qh: 'qhAnalysis', pressure: 'pressureAnalysis', bottlenecks: 'bottlenecksAnalysis',
    stagnation: 'stagnationAnalysis', valves: 'valvesAnalysis', diurnal: 'diurnalAnalysis',
    table: 'tableAIAnalysisBox'
  }};
  const box = document.getElementById(mapIds[kind]);
  if (!box) return;

  const scrollEl = ensureChatLayout(box, kind);
  const baseCtx = (kind === 'table')
    ? getFilteredAndSortedRows().slice(0, 8).map(r => `#${{r.clean_id}}: ${{r.advice || r.status || JSON.stringify(r)}}`).join('; ')
    : buildContextExactAnalysisHTML(kind).replace(/<[^>]+>/g, ' ').replace(/\\s+/g, ' ').trim();

  const userBubble = document.createElement('div');
  userBubble.className = 'ai-msg-user';
  userBubble.textContent = 'Вы: ' + q;
  scrollEl.appendChild(userBubble);

  const ansBubble = document.createElement('div');
  ansBubble.className = 'ai-msg-ai';
  ansBubble.innerHTML = '<div style="font-size:11.5px;color:#0284c7;font-weight:700;">Генерация ответа локальной ИИ-моделью...</div>';
  scrollEl.appendChild(ansBubble);
  scrollEl.scrollTop = scrollEl.scrollHeight;

  if (!Array.isArray(aiChatHistory[kind])) aiChatHistory[kind] = [];
  const sysPrompt = 'Ты инженер-аналитик ПК «Аквабит». Продолжай инженерный диалог строго по фактам контекста. Используй аккуратное форматирование Markdown (списки, **жирный**). Для сравнения параметров указывай объекты как #ID (ведет в таблицу), а если важно местоположение на схеме — [объект #ID на карте](map:ID).';
  const msgs = [
    {{ role: 'system', content: sysPrompt + '\\nБазовый контекст текущего раздела: ' + baseCtx.slice(0, 2400) }},
    ...aiChatHistory[kind].slice(-6),
    {{ role: 'user', content: q }}
  ];

  const llmRes = await streamLocalLLMMessages(
    msgs,
    (partial) => {{
      ansBubble.innerHTML = `<div style="font-size:11px;color:#059669;font-weight:700;margin-bottom:4px">🟢 Живая генерация LLM...</div>` + renderMarkdownToHTML(partial, 'table');
      scrollEl.scrollTop = scrollEl.scrollHeight;
    }},
    750
  );
  if (llmRes && llmRes.text) {{
    aiChatHistory[kind].push({{ role: 'user', content: q }}, {{ role: 'assistant', content: llmRes.text }});
    if (aiChatHistory[kind].length > 12) aiChatHistory[kind] = aiChatHistory[kind].slice(-12);
    ansBubble.innerHTML = `<div style="font-size:11px;color:#059669;font-weight:700;margin-bottom:4px">🟢 Ответ локальной LLM (${{llmRes.sec}} сек, ~${{llmRes.tps}} ток/с):</div>` + renderMarkdownToHTML(llmRes.text, 'table');
  }} else {{
    ansBubble.innerHTML = `<div style="font-size:11px;color:#b45309;font-weight:700;margin-bottom:4px">🟡 Локальный llama-server (:8080) не запущен — ответ по предрасчитанному графу:</div>По вашему запросу («${{q}}») ключевые параметры определяются состоянием связанных объектов: ${{formatTextWithObjectButtons(baseCtx, 'table')}}`;
  }}
  scrollEl.scrollTop = scrollEl.scrollHeight;
  inputEl.focus();
}}

// =================== УВЕДОМЛЕНИЯ И ПЕРЕХОДЫ ГРАФИК -> ТАБЛИЦА -> КАРТА ===================
function onWarningClick(w) {{
  highlightedObjectId = w.focus_id || null;
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active-highlight'));

  if (w.kind === 'pressure_deficit') {{
    switchPressureMode('points');
  }} else if (w.kind === 'pipe_bottlenecks') {{
    drawBottlenecksChart();
  }} else if (w.kind === 'stagnation') {{
    drawStagnationChart();
  }} else if (w.kind === 'pump_qh') {{
    const pIdx = (DATA.qh_curves || []).findIndex(c => String(c.clean_id) === String(w.focus_id));
    if (pIdx >= 0) {{
      currentQHIndex = pIdx;
      document.getElementById('qhSelect').value = pIdx;
      qhShowAllMode = false;
      drawQHCurve(pIdx);
    }}
  }} else if (w.kind === 'valves') {{
    drawValvesChart();
  }}

  // Применяем автофильтр, сортировку и подсветку в таблице
  if (w.table_tab) {{
    applyTablePresetAndHighlight(
      w.table_tab,
      w.filter_preset || 'all',
      w.sort_key || null,
      w.sort_dir || 'asc',
      w.highlight_ids || [],
      w.focus_id || null,
      w.title || ''
    );
  }}

  const targetEl = document.getElementById(w.target || 'tablesSection');
  if (targetEl) {{
    targetEl.classList.add('active-highlight');
    targetEl.scrollIntoView({{ behavior: 'smooth', block: 'center' }});
  }}
}}

function applyQuickFilterFromAI(tab, preset) {{
  const defaultSorts = {{
    data_error: ['free_pressure_min_m', 'asc'],
    deficit: ['margin_m', 'asc'],
    excess: ['free_pressure_max_m', 'desc'],
    bottlenecks: ['headloss_m_km', 'desc'],
    closed: ['velocity_max_m_s', 'asc'],
    stagnant: ['velocity_max_m_s', 'asc'],
    underload: ['q_work', 'asc'],
    overload: ['q_work', 'desc'],
    imbalance: ['q_work', 'desc']
  }};
  const [sKey, sDir] = defaultSorts[preset] || [null, 'asc'];
  applyTablePresetAndHighlight(tab, preset, sKey, sDir, [], null, `Фильтр ИИ: ${{preset}}`);
  document.getElementById('tablesSection').scrollIntoView({{ behavior: 'smooth', block: 'start' }});
}}

function jumpToTableRowFromChart(tab, cleanId) {{
  if (!cleanId) return;
  highlightedObjectId = String(cleanId);
  currentTable = tab;
  currentFilterPreset = 'all';
  currentSearch = '';
  document.getElementById('tableSearchInput').value = '';
  highlightIdsSet = new Set([String(cleanId)]);
  highlightRowId = String(cleanId);

  document.querySelectorAll('.table-tab-btn').forEach(b => b.classList.remove('active'));
  const btn = document.getElementById('tabBtn_' + tab);
  if (btn) btn.classList.add('active');

  renderTableFilters();
  renderTable();

  const tablesSec = document.getElementById('tablesSection');
  if (tablesSec) tablesSec.scrollIntoView({{ behavior: 'smooth', block: 'center' }});

  setTimeout(() => {{
    const focusedTr = document.querySelector('#mainTableBody tr.row-focused, #mainTableBody tr.row-highlighted');
    if (focusedTr) focusedTr.scrollIntoView({{ behavior: 'smooth', block: 'center' }});
  }}, 250);
}}

function jumpFromCurrentPumpToTable() {{
  const curves = DATA.qh_curves || [];
  const cur = curves[currentQHIndex] || curves[0];
  if (cur && cur.clean_id) jumpToTableRowFromChart('pumps', cur.clean_id);
}}

// =================== ИНТЕРАКТИВНАЯ КАРТА (ФИКС ЗУМА + МИГАНИЕ + ТАЙМЛАЙН) ===================
const networkCanvas = document.getElementById('networkMap');
const networkGeo = DATA.geo || {{ nodes: {{}}, links: [] }};
const networkView = {{
  scale: 1,
  tx: 0,
  ty: 0,
  baseScale: 1,
  selectedPts: null,
  dragging: false,
  dragStartX: 0,
  dragStartY: 0,
  pulsePhase: 0,
  animFrameId: null
}};

function mapSize() {{
  return {{
    w: Math.max(320, networkCanvas.parentElement?.clientWidth || networkCanvas.clientWidth || 800),
    h: Math.max(300, networkCanvas.parentElement?.clientHeight || networkCanvas.clientHeight || 500)
  }};
}}

function screenPoint(p) {{
  return [p[0] * networkView.scale + networkView.tx, networkView.ty - p[1] * networkView.scale];
}}

function zoomMapAtPoint(factor, centerX, centerY) {{
  const oldScale = networkView.scale;
  const minScale = Math.max(1e-6, networkView.baseScale * 0.25);
  const maxScale = Math.max(100, networkView.baseScale * 250);
  const newScale = Math.max(minScale, Math.min(maxScale, oldScale * factor));
  const ratio = newScale / oldScale;
  networkView.tx = centerX - (centerX - networkView.tx) * ratio;
  networkView.ty = centerY - (centerY - networkView.ty) * ratio;
  networkView.scale = newScale;
  redrawMap();
}}

function ensurePulseAnimation() {{
  if (networkView.animFrameId) return;
  function tick() {{
    if (!networkView.selectedPts) {{
      networkView.animFrameId = null;
      return;
    }}
    networkView.pulsePhase = (networkView.pulsePhase + 0.08) % (Math.PI * 2);
    redrawMap();
    networkView.animFrameId = requestAnimationFrame(tick);
  }}
  networkView.animFrameId = requestAnimationFrame(tick);
}}

function redrawMap() {{
  const {{ w, h }} = mapSize();
  const dpr = window.devicePixelRatio || 1;
  const targetW = Math.round(w * dpr), targetH = Math.round(h * dpr);
  if (networkCanvas.width !== targetW || networkCanvas.height !== targetH) {{
    networkCanvas.width = targetW;
    networkCanvas.height = targetH;
  }}
  const ctx = networkCanvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.fillStyle = '#f8fafc';
  ctx.fillRect(0, 0, w, h);

  // Отрисовка трубопроводов с отсечением невидимых сегментов (предотвращает переполнение Canvas при большом зуме)
  ctx.strokeStyle = '#cbd5e1';
  ctx.lineWidth = 1.0;
  ctx.beginPath();
  for (const [_id, pts] of (networkGeo.links || [])) {{
    if (!pts || pts.length < 2) continue;
    let prevX = null, prevY = null;
    for (let i = 0; i < pts.length; i++) {{
      const [x, y] = screenPoint(pts[i]);
      if (prevX !== null) {{
        const inBounds = !(
          (prevX < -200 && x < -200) || (prevX > w + 200 && x > w + 200) ||
          (prevY < -200 && y < -200) || (prevY > h + 200 && y > h + 200)
        );
        if (inBounds) {{
          ctx.moveTo(prevX, prevY);
          ctx.lineTo(x, y);
        }}
      }}
      prevX = x; prevY = y;
    }}
  }}
  ctx.stroke();

  const hrPressures = DATA.node_hourly_pressures || {{}};
  const badSet = new Set((networkGeo.bad_nodes || []).map(clean_object_id));
  const srcSet = new Set(networkGeo.sources || []);
  const tankSet = new Set(networkGeo.tanks || []);

  for (const [rawId, p] of Object.entries(networkGeo.nodes || {{}})) {{
    if (!p || !Number.isFinite(p[0]) || !Number.isFinite(p[1])) continue;
    const [x, y] = screenPoint(p);
    if (x < -12 || x > w + 12 || y < -12 || y > h + 12) continue;
    const cid = clean_object_id(rawId);
    const hrArr = hrPressures[rawId] || hrPressures['c' + cid] || hrPressures[cid];
    const pNow = (hrArr && hrArr[currentTimelineStep] !== undefined) ? hrArr[currentTimelineStep] : null;

    ctx.beginPath();
    if (srcSet.has(cid)) {{
      ctx.fillStyle = '#059669'; ctx.arc(x, y, 6, 0, Math.PI * 2);
    }} else if (tankSet.has(cid)) {{
      ctx.fillStyle = '#0284c7'; ctx.rect(x - 5, y - 5, 10, 10);
    }} else if ((pNow !== null && pNow < 0) || badSet.has(cid)) {{
      ctx.fillStyle = '#ef4444'; ctx.arc(x, y, 3.6, 0, Math.PI * 2);
    }} else {{
      ctx.fillStyle = '#64748b'; ctx.arc(x, y, 2.1, 0, Math.PI * 2);
    }}
    ctx.fill();
  }}

  // Яркая анимация мигания (пульсации) выбранного объекта на карте
  if (networkView.selectedPts && networkView.selectedPts.length) {{
    const pulse = (Math.sin(networkView.pulsePhase) + 1) / 2; // 0..1
    ctx.save();
    if (networkView.selectedPts.length > 1) {{
      ctx.strokeStyle = `rgba(239, 68, 68, ${{0.35 + 0.55 * pulse}})`;
      ctx.lineWidth = 7 + 5 * pulse;
      ctx.lineCap = 'round';
      ctx.beginPath();
      networkView.selectedPts.forEach((p, i) => {{ const [x, y] = screenPoint(p); if (i) ctx.lineTo(x, y); else ctx.moveTo(x, y); }});
      ctx.stroke();

      ctx.strokeStyle = '#f59e0b';
      ctx.lineWidth = 3.5;
      ctx.beginPath();
      networkView.selectedPts.forEach((p, i) => {{ const [x, y] = screenPoint(p); if (i) ctx.lineTo(x, y); else ctx.moveTo(x, y); }});
      ctx.stroke();
    }} else {{
      const [x, y] = screenPoint(networkView.selectedPts[0]);
      const outerRadius = 12 + pulse * 14;
      ctx.beginPath();
      ctx.arc(x, y, outerRadius, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(239, 68, 68, ${{0.38 - pulse * 0.25}})`;
      ctx.fill();
      ctx.strokeStyle = `rgba(239, 68, 68, ${{0.9 - pulse * 0.4}})`;
      ctx.lineWidth = 2.5;
      ctx.stroke();

      ctx.beginPath();
      ctx.arc(x, y, 7.5, 0, Math.PI * 2);
      ctx.fillStyle = '#ef4444';
      ctx.fill();
      ctx.strokeStyle = '#ffffff';
      ctx.lineWidth = 2.5;
      ctx.stroke();
    }}
    ctx.restore();
  }}
}}

function mapFit() {{
  const pts = Object.values(networkGeo.nodes || {{}}).filter(p => p && Number.isFinite(p[0]) && Number.isFinite(p[1]));
  if (!pts.length) return;
  let xmin = Infinity, xmax = -Infinity, ymin = Infinity, ymax = -Infinity;
  pts.forEach(([x, y]) => {{ if (x < xmin) xmin = x; if (x > xmax) xmax = x; if (y < ymin) ymin = y; if (y > ymax) ymax = y; }});
  const {{ w, h }} = mapSize();
  const scale = Math.min((w - 48) / Math.max(xmax - xmin, 1), (h - 48) / Math.max(ymax - ymin, 1));
  networkView.baseScale = Math.max(scale, 1e-6);
  networkView.scale = networkView.baseScale;
  networkView.tx = w / 2 - ((xmin + xmax) / 2) * networkView.scale;
  networkView.ty = h / 2 + ((ymin + ymax) / 2) * networkView.scale;
  document.getElementById('mapInfo').textContent = `Объектов с координатами: ${{pts.length.toLocaleString('ru-RU')}}`;
  redrawMap();
}}

function initTimelineTicks() {{
  const labels = DATA.hours_labels || [];
  const rangeEl = document.getElementById('mapTimelineRange');
  const ticksEl = document.getElementById('mapTimelineTicks');
  if (!rangeEl || !ticksEl || !labels.length) return;
  rangeEl.max = Math.max(0, labels.length - 1);
  ticksEl.innerHTML = '';
  const tickCount = Math.min(12, labels.length);
  for (let i = 0; i < tickCount; i++) {{
    const idx = Math.round((i / Math.max(tickCount - 1, 1)) * (labels.length - 1));
    const span = document.createElement('span');
    span.textContent = labels[idx];
    span.onclick = () => {{
      rangeEl.value = idx;
      onMapTimelineChange(idx);
    }};
    ticksEl.appendChild(span);
  }}
}}

function stepTimeline(delta) {{
  const rangeEl = document.getElementById('mapTimelineRange');
  if (!rangeEl) return;
  const maxVal = Number(rangeEl.max) || 23;
  const nextVal = Math.max(0, Math.min(maxVal, currentTimelineStep + delta));
  rangeEl.value = nextVal;
  onMapTimelineChange(nextVal);
}}

function onMapTimelineChange(val) {{
  currentTimelineStep = Number(val);
  const lbl = (DATA.hours_labels || [])[currentTimelineStep] || `${{String(currentTimelineStep).padStart(2, '0')}}:00`;
  document.getElementById('mapTimelineLabel').textContent = lbl;
  // Мгновенно обновляем карточку выбранного объекта на карте для нового времени!
  if (selectedMapObjectId) {{
    renderMapObjectCard(selectedMapObjectId);
  }}
  redrawMap();
}}

function renderMapObjectCard(rawId) {{
  if (!rawId) return;
  const cid = clean_object_id(rawId);
  const np = DATA.node_props[rawId] || DATA.node_props['c' + cid] || DATA.node_props['e' + cid] || DATA.node_props['k' + cid] || DATA.node_props[cid];
  const pp = DATA.pipe_props[rawId] || DATA.pipe_props['e' + cid] || DATA.pipe_props[cid];
  const pumpObj = (DATA.tables.pumps || []).find(p => String(p.clean_id) === cid);

  const totalSteps = Math.max(1, (DATA.hours_labels || []).length);
  function sampleSeriesAtStep(arr, stepIdx) {{
    if (!arr || !arr.length) return null;
    if (arr[stepIdx] !== undefined) return arr[stepIdx];
    const pos = (stepIdx / Math.max(1, totalSteps - 1)) * (arr.length - 1);
    const i0 = Math.floor(pos), i1 = Math.min(arr.length - 1, i0 + 1);
    const frac = pos - i0;
    return Number((arr[i0] * (1 - frac) + arr[i1] * frac).toFixed(2));
  }}

  const hrNodeArr = (DATA.node_hourly_pressures || {{}})[rawId] || (DATA.node_hourly_pressures || {{}})['c' + cid] || (DATA.node_hourly_pressures || {{}})[cid];
  let pAtStep = sampleSeriesAtStep(hrNodeArr, currentTimelineStep);
  if (pAtStep === null && np && typeof np.p_min === 'number' && typeof np.p_max === 'number') {{
    const p50 = DATA.diurnal_free_pressure_percentiles_m?.p50 || [];
    const p50Val = sampleSeriesAtStep(p50, currentTimelineStep);
    if (p50Val !== null && p50.length) {{
      const min50 = Math.min(...p50), max50 = Math.max(...p50);
      const norm = max50 > min50 ? (p50Val - min50) / (max50 - min50) : 0.5;
      pAtStep = Number((np.p_min + norm * (np.p_max - np.p_min)).toFixed(2));
    }} else {{
      pAtStep = np.p_mean ?? np.p_min;
    }}
  }}

  const hrPipeArr = (DATA.pipe_hourly_flows || {{}})[rawId] || (DATA.pipe_hourly_flows || {{}})['e' + cid] || (DATA.pipe_hourly_flows || {{}})[cid];
  let qPipeAtStep = sampleSeriesAtStep(hrPipeArr, currentTimelineStep);
  if (qPipeAtStep === null && pp && typeof pp.q === 'number') {{
    const dem = DATA.diurnal_demand_m3_h || [];
    const demVal = sampleSeriesAtStep(dem, currentTimelineStep);
    const maxDem = dem.length ? Math.max(...dem, 1) : 1;
    qPipeAtStep = demVal !== null ? Number((pp.q * (demVal / maxDem)).toFixed(2)) : pp.q;
  }}

  const hrPumpArr = (DATA.pump_hourly_flows || {{}})[rawId] || (DATA.pump_hourly_flows || {{}})['p' + cid] || (DATA.pump_hourly_flows || {{}})[cid];
  const qPumpAtStep = sampleSeriesAtStep(hrPumpArr, currentTimelineStep);

  const stepLabel = (DATA.hours_labels || [])[currentTimelineStep] || `${{String(currentTimelineStep).padStart(2, '0')}}:00`;

  const card = document.getElementById('mapObjectCard');
  const title = document.getElementById('mapCardTitle');
  const body = document.getElementById('mapCardBody');
  title.innerHTML = `Объект #${{cid}} <span style="font-size:11.5px;color:var(--brand);font-weight:700">• Время ${{stepLabel}}</span>`;
  body.innerHTML = '';
  const addProp = (k, v, highlight = false) => {{
    body.innerHTML += `<div style="${{highlight ? 'background:#f0f9ff;padding:4px 6px;border-radius:6px;border:1px solid #bae6fd;' : ''}}"><div class="map-prop-label">${{k}}</div><div class="map-prop-val" style="${{highlight ? 'color:#0284c7;font-size:13.5px;' : ''}}">${{v ?? '—'}}</div></div>`;
  }};

  if (np) {{
    addProp('Роль в схеме', np.role);
    addProp('Отметка земли Z', `${{np.z ?? '—'}} м`);
    addProp(`Напор в ${{stepLabel}}`, `${{pAtStep ?? np.p_min ?? '—'}} м`, true);
    addProp('Мин / Макс Pсвоб', `${{np.p_min ?? '—'}} / ${{np.p_max ?? '—'}} м`);
    addProp('Требуемый напор', `${{np.req}}`);
    addProp('Отбор абонента', `${{np.demand ?? 0}} м³/ч`);
    if (np.advice || np.diagnostic_advice) {{
      body.innerHTML += `<div style="grid-column:1/-1;margin-top:4px;padding-top:6px;border-top:1px dashed #cbd5e1;font-size:11.5px;color:#334155;">${{formatTextWithObjectButtons(np.advice || np.diagnostic_advice)}}</div>`;
    }}
  }} else if (pp) {{
    addProp('Тип элемента', pp.closed ? 'Труба (Перекрыта, Q=0)' : 'Трубопровод');
    addProp('Диаметр / Длина', `${{pp.d}} мм / ${{pp.l}} м`);
    addProp(`Поток в ${{stepLabel}}`, `${{qPipeAtStep ?? pp.q ?? 0}} м³/ч`, true);
    addProp('Макс. скорость / Потери', `${{pp.v ?? 0}} м/с / ${{pp.i ?? 0}} м/км`);
    addProp('Узлы (Нач → Кон)', `#${{pp.sn || '—'}} → #${{pp.en || '—'}}`);
    addProp('Шероховатость C', `${{pp.c ?? '—'}}`);
  }} else if (pumpObj) {{
    addProp('Тип насоса', pumpObj.pump_type);
    addProp(`Подача в ${{stepLabel}}`, `${{qPumpAtStep ?? pumpObj.q_work ?? 0}} м³/ч`, true);
    addProp('Номинал Qном / Hраб', `${{pumpObj.q_nom ?? '—'}} м³/ч / ${{pumpObj.h_work ?? '—'}} м`);
    addProp('Узлы (Вход → Коллектор)', `#${{pumpObj.start_node}} → #${{pumpObj.end_node}}`);
    body.innerHTML += `<div style="grid-column:1/-1;margin-top:4px;padding-top:6px;border-top:1px dashed #cbd5e1;font-size:11.5px;color:#334155;">${{formatTextWithObjectButtons(pumpObj.advice)}}</div>`;
  }} else {{
    addProp('Статус', 'Координатный узел модели');
    addProp(`Значение в ${{stepLabel}}`, `${{pAtStep ?? '—'}}`, true);
  }}
  card.style.display = 'block';
}}

function focusMapObject(rawId, keepCamera = false) {{
  if (!rawId) return;
  const cid = clean_object_id(rawId);
  selectedMapObjectId = rawId;
  const nodes = networkGeo.nodes || {{}};
  let pt = nodes[rawId] || nodes['c' + cid] || nodes['e' + cid] || nodes['k' + cid] || nodes[cid];
  const link = (networkGeo.links || []).find(([lid]) => clean_object_id(lid) === cid);

  // Если ищем насос, клапан или трубу, которой нет в links, найдем координаты её начального/конечного узла
  let derivedPts = null;
  if (!pt && !link) {{
    const pumpRow = (DATA.tables.pumps || []).find(p => String(p.clean_id) === cid);
    const pipeRow = (DATA.tables.pipes || []).find(p => String(p.clean_id) === cid);
    const valveRow = (DATA.tables.valves || []).find(v => String(v.clean_id) === cid);
    const sn = pumpRow?.start_node || pipeRow?.start_node || valveRow?.start_node;
    const en = pumpRow?.end_node || pipeRow?.end_node || valveRow?.end_node;
    const p1 = sn ? (nodes[sn] || nodes['c' + sn] || nodes['e' + sn]) : null;
    const p2 = en ? (nodes[en] || nodes['c' + en] || nodes['e' + en]) : null;
    if (p1 && p2) derivedPts = [p1, p2];
    else if (p1 || p2) pt = p1 || p2;
  }}

  const pts = link ? link[1] : (derivedPts || (pt ? [pt] : null));
  if (pts && pts.length) {{
    if (!keepCamera) {{
      const {{ w, h }} = mapSize();
      const targetScale = Math.max(networkView.baseScale * 8, networkView.scale);
      networkView.scale = Math.min(networkView.baseScale * 80, targetScale);
      const centerPt = pts[Math.floor(pts.length / 2)];
      networkView.tx = w / 2 - centerPt[0] * networkView.scale;
      networkView.ty = h / 2 + centerPt[1] * networkView.scale;
    }}
    networkView.selectedPts = pts;
    ensurePulseAnimation();
    redrawMap();
  }}

  renderMapObjectCard(rawId);
}}

function closeMapCard() {{
  document.getElementById('mapObjectCard').style.display = 'none';
  selectedMapObjectId = null;
  networkView.selectedPts = null;
  redrawMap();
}}

function pointToSegmentDist(px, py, x1, y1, x2, y2) {{
  const dx = x2 - x1, dy = y2 - y1;
  const lenSq = dx * dx + dy * dy;
  if (lenSq < 1e-12) return Math.hypot(px - x1, py - y1);
  let t = ((px - x1) * dx + (py - y1) * dy) / lenSq;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(px - (x1 + t * dx), py - (y1 + t * dy));
}}

function hitTestMapObject(wx, wy) {{
  // 1) Приоритет узлам в радиусе 10 px
  let bestNodeId = null, bestNodeD = 10 / networkView.scale;
  for (const [nid, p] of Object.entries(networkGeo.nodes || {{}})) {{
    if (!p) continue;
    const d = Math.hypot(p[0] - wx, p[1] - wy);
    if (d < bestNodeD) {{ bestNodeD = d; bestNodeId = nid; }}
  }}
  if (bestNodeId) return bestNodeId;

  // 2) Прямой клик по трубе / участку (расстояние от точки клика до каждого отрезка полилинии <= 10 px)
  let bestPipeId = null, bestPipeD = 10 / networkView.scale;
  const links = networkGeo.links || [];
  for (let li = 0; li < links.length; li++) {{
    const lid = links[li][0];
    const pts = links[li][1];
    if (!pts || pts.length < 2) continue;
    for (let k = 0; k < pts.length - 1; k++) {{
      const p1 = pts[k], p2 = pts[k + 1];
      // Быстрая отсечка по bounding box отрезка
      const minX = Math.min(p1[0], p2[0]) - bestPipeD;
      const maxX = Math.max(p1[0], p2[0]) + bestPipeD;
      if (wx < minX || wx > maxX) continue;
      const minY = Math.min(p1[1], p2[1]) - bestPipeD;
      const maxY = Math.max(p1[1], p2[1]) + bestPipeD;
      if (wy < minY || wy > maxY) continue;
      const d = pointToSegmentDist(wx, wy, p1[0], p1[1], p2[0], p2[1]);
      if (d < bestPipeD) {{ bestPipeD = d; bestPipeId = lid; }}
    }}
  }}
  return bestPipeId;
}}

// Перетаскивание карты (Pan), подсветка курсора и клик по узлам и трубам
networkCanvas.addEventListener('mousedown', ev => {{
  networkView.dragging = true;
  networkView.moved = false;
  networkView.dragStartX = ev.clientX - networkView.tx;
  networkView.dragStartY = ev.clientY - networkView.ty;
}});
window.addEventListener('mousemove', ev => {{
  if (networkView.dragging) {{
    networkView.moved = true;
    networkView.tx = ev.clientX - networkView.dragStartX;
    networkView.ty = ev.clientY - networkView.dragStartY;
    redrawMap();
  }}
}});
networkCanvas.addEventListener('mousemove', ev => {{
  if (networkView.dragging) return;
  const rect = networkCanvas.getBoundingClientRect();
  const wx = (ev.clientX - rect.left - networkView.tx) / networkView.scale;
  const wy = (networkView.ty - (ev.clientY - rect.top)) / networkView.scale;
  networkCanvas.style.cursor = hitTestMapObject(wx, wy) ? 'pointer' : 'grab';
}});
window.addEventListener('mouseup', () => {{ networkView.dragging = false; }});

networkCanvas.addEventListener('click', ev => {{
  if (networkView.moved) return;
  const rect = networkCanvas.getBoundingClientRect();
  const wx = (ev.clientX - rect.left - networkView.tx) / networkView.scale;
  const wy = (networkView.ty - (ev.clientY - rect.top)) / networkView.scale;
  const hitId = hitTestMapObject(wx, wy);
  if (hitId) focusMapObject(hitId, true);
}});

networkCanvas.addEventListener('wheel', ev => {{
  ev.preventDefault();
  const rect = networkCanvas.getBoundingClientRect();
  const mx = ev.clientX - rect.left;
  const my = ev.clientY - rect.top;
  const factor = ev.deltaY < 0 ? 1.25 : 0.8;
  zoomMapAtPoint(factor, mx, my);
}}, {{ passive: false }});

document.getElementById('mapPlus').onclick = () => {{
  const {{ w, h }} = mapSize();
  zoomMapAtPoint(1.35, w / 2, h / 2);
}};
document.getElementById('mapMinus').onclick = () => {{
  const {{ w, h }} = mapSize();
  zoomMapAtPoint(1 / 1.35, w / 2, h / 2);
}};
document.getElementById('mapReset').onclick = () => {{
  networkView.selectedPts = null;
  selectedMapObjectId = null;
  closeMapCard();
  mapFit();
}};
document.getElementById('mapFind').onclick = () => focusMapObject(document.getElementById('mapSearch').value);

function jumpToObjectInTable(rawId) {{
  const cid = clean_object_id(rawId);
  const tabOrder = ['pumps', 'pipes', 'nodes', 'valves'];
  let foundTab = null;
  for (const t of tabOrder) {{
    if ((DATA.tables[t] || []).some(r => String(r.clean_id) === cid)) {{
      foundTab = t;
      break;
    }}
  }}
  if (!foundTab) {{
    // Если транзитного узла нет в таблицах, сразу центрируем его на карте
    document.getElementById('mapSection').scrollIntoView({{ behavior: 'smooth', block: 'center' }});
    focusMapObject(rawId);
    return;
  }}
  const tabLabels = {{ nodes: 'Узлы абонентов', pipes: 'Трубопроводы', pumps: 'Насосы и скважины', valves: 'Клапаны' }};
  applyTablePresetAndHighlight(foundTab, 'all', null, 'asc', [cid], cid, `Переход к объекту #${{cid}} (${{tabLabels[foundTab]}})`);
  const sec = document.getElementById('tablesSection');
  if (sec) sec.scrollIntoView({{ behavior: 'smooth', block: 'start' }});
  setTimeout(() => {{
    const focusedTr = document.querySelector('#mainTableBody tr.row-focused');
    if (focusedTr) focusedTr.scrollIntoView({{ behavior: 'smooth', block: 'center' }});
  }}, 120);
}}

document.addEventListener('click', ev => {{
  const tblBtn = ev.target.closest('[data-table-id]');
  if (tblBtn) {{
    ev.preventDefault();
    jumpToObjectInTable(tblBtn.dataset.tableId);
    return;
  }}
  const mapBtn = ev.target.closest('[data-map-id]');
  if (mapBtn) {{
    ev.preventDefault();
    const rawId = mapBtn.dataset.mapId;
    document.getElementById('mapSection').scrollIntoView({{ behavior: 'smooth', block: 'center' }});
    focusMapObject(rawId);
  }}
}});

// =================== ТАБЛИЦЫ: СОРТИРОВКА, БЫСТРЫЕ ФИЛЬТРЫ И ПОДСВЕТКА ===================
let currentTable = 'nodes';
let currentSearch = '';
let currentFilterPreset = 'all';
let currentSortKey = null;
let currentSortDir = 'asc';
let highlightRowId = null;

const TABLE_FILTER_PRESETS = {{
  nodes: [
    {{ id: 'all', label: 'Все узлы', fn: () => true }},
    {{ id: 'data_error', label: 'Критический вакуум (< -3.0 м)', fn: r => Number(r.free_pressure_min_m) < -3.0 }},
    {{ id: 'deficit', label: 'Дефицит напора (ΔP < 0)', fn: r => Number(r.margin_m) < 0 && Number(r.free_pressure_min_m) >= -3.0 }},
    {{ id: 'excess', label: 'Избыток (> 60 м)', fn: r => Number(r.free_pressure_max_m) > 60.0 }},
    {{ id: 'negative', label: 'Любое P < 0 м', fn: r => Number(r.free_pressure_min_m) < 0.0 }}
  ],
  pipes: [
    {{ id: 'all', label: 'Все участки', fn: () => true }},
    {{ id: 'bottlenecks', label: 'Узкие места (i ≥ 15 м/км или V ≥ 5 м/с)', fn: r => Number(r.headloss_m_km) >= 15.0 || Number(r.velocity_max_m_s) >= 5.0 }},
    {{ id: 'low_c', label: 'Деградация C (< 80)', fn: r => r.hazen_williams_c !== '—' && Number(r.hazen_williams_c) < 80 }},
    {{ id: 'closed', label: 'Перекрыты (Q = 0)', fn: r => Boolean(r.is_closed) || r.velocity_bin === 'Перекрыты / Поток = 0 (задвижка)' || Number(r.velocity_max_m_s) <= 0.001 }},
    {{ id: 'stagnant', label: 'Низкие скорости (< 0.10 м/с)', fn: r => r.velocity_bin === 'Низкие скорости (< 0.10 м/с)' || (Number(r.velocity_max_m_s) > 0.001 && Number(r.velocity_max_m_s) < 0.10) }},
    {{ id: 'vel_moderate', label: 'Умеренные (0.10–0.50 м/с)', fn: r => r.velocity_bin === 'Умеренные (0.10–0.50 м/с)' || (Number(r.velocity_max_m_s) >= 0.10 && Number(r.velocity_max_m_s) < 0.50) }},
    {{ id: 'vel_optimum', label: 'Оптимум (0.50–1.50 м/с)', fn: r => r.velocity_bin === 'Оптимум (0.50–1.50 м/с)' || (Number(r.velocity_max_m_s) >= 0.50 && Number(r.velocity_max_m_s) <= 1.50) }},
    {{ id: 'vel_elevated', label: 'Повышенные (1.50–5.00 м/с)', fn: r => r.velocity_bin === 'Повышенные (1.50–5.00 м/с)' || (Number(r.velocity_max_m_s) > 1.50 && Number(r.velocity_max_m_s) < 5.00) }},
    {{ id: 'vel_extreme', label: 'Большая скорость (≥ 5.00 м/с)', fn: r => r.velocity_bin === 'Большая скорость (>= 5.00 м/с)' || Number(r.velocity_max_m_s) >= 5.00 }}
  ],
  pumps: [
    {{ id: 'all', label: 'Все насосы', fn: () => true }},
    {{ id: 'borehole', label: 'Скважинные (1-й подъем)', fn: r => Boolean(r.is_borehole) }},
    {{ id: 'booster', label: 'Сетевые (НС)', fn: r => !r.is_borehole }},
    {{ id: 'underload', label: 'Недогрузка (< 75% Qном)', fn: r => String(r.regime).includes('Недогрузка') }},
    {{ id: 'overload', label: 'Перегрузка (> 115% Qном)', fn: r => String(r.regime).includes('Перегрузка') }},
    {{ id: 'imbalance', label: 'Все отклонения (Недогрузка + Перегрузка)', fn: r => String(r.regime).includes('Недогрузка') || String(r.regime).includes('Перегрузка') }},
    {{ id: 'bep', label: 'Оптимум BEP', fn: r => String(r.regime).includes('Оптимум') }},
    {{ id: 'off', label: 'Выключены / Резерв (Q = 0)', fn: r => !r.active || Number(r.q_work) <= 0.001 }}
  ],
  valves: [
    {{ id: 'all', label: 'Все клапаны', fn: () => true }},
    {{ id: 'active_reg', label: 'Активное регулирование', fn: r => String(r.status).includes('Активное') }},
    {{ id: 'closed_v', label: 'Закрыты (Q = 0)', fn: r => String(r.status).includes('Закрыт') }}
  ]
}};

const tableSchemas = {{
  nodes: [
    {{ key: 'clean_id', label: 'ID узла (на карту)', isId: true }},
    {{ key: 'elevation_m', label: 'Отметка Z, м' }},
    {{ key: 'floors', label: 'Этажность' }},
    {{ key: 'required_pressure_m', label: 'P_треб, м' }},
    {{ key: 'free_pressure_min_m', label: 'P_своб мин, м', isCrit: r => Number(r.free_pressure_min_m) < 0 }},
    {{ key: 'free_pressure_max_m', label: 'P_своб макс, м', isCrit: r => Number(r.free_pressure_max_m) > 60 }},
    {{ key: 'margin_m', label: 'Дефицит ΔP, м', isCrit: r => Number(r.margin_m) < 0 }},
    {{ key: 'demand_m3_h', label: 'Отбор, м³/ч' }},
    {{ key: 'advice', label: 'Инженерный вывод (ИИ / Граф)', isRichAdvice: true }}
  ],
  pipes: [
    {{ key: 'clean_id', label: 'ID участка (на карту)', isId: true }},
    {{ key: 'start_node', label: 'Нач. узел', isNodeRef: true }},
    {{ key: 'end_node', label: 'Кон. узел', isNodeRef: true }},
    {{ key: 'diameter_mm', label: 'Диаметр, мм' }},
    {{ key: 'length_m', label: 'Длина, м' }},
    {{ key: 'velocity_max_m_s', label: 'Скорость V, м/с' }},
    {{ key: 'headloss_m_km', label: 'Потери i, м/км', isCrit: r => Number(r.headloss_m_km) >= 15 }},
    {{ key: 'hazen_williams_c', label: 'Коэфф. C' }},
    {{ key: 'flow_m3_h', label: 'Поток Q, м³/ч' }},
    {{ key: 'advice', label: 'Инженерный вывод (ИИ / Граф)', isRichAdvice: true }}
  ],
  pumps: [
    {{ key: 'clean_id', label: 'ID насоса (на карту)', isId: true }},
    {{ key: 'pump_type', label: 'Тип агрегата', isBadge: true }},
    {{ key: 'start_node', label: 'Вход / Источник', isNodeRef: true }},
    {{ key: 'end_node', label: 'Коллектор (Выход)', isNodeRef: true }},
    {{ key: 'q_work', label: 'Q_раб, м³/ч' }},
    {{ key: 'q_nom', label: 'Q_ном, м³/ч' }},
    {{ key: 'h_work', label: 'H_раб, м' }},
    {{ key: 'regime', label: 'Режим работы' }},
    {{ key: 'advice', label: 'Инженерный вывод (ИИ / Граф)', isRichAdvice: true }}
  ],
  valves: [
    {{ key: 'clean_id', label: 'ID клапана (на карту)', isId: true }},
    {{ key: 'type_code', label: 'Тип клапана' }},
    {{ key: 'start_node', label: 'Нач. узел', isNodeRef: true }},
    {{ key: 'end_node', label: 'Кон. узел', isNodeRef: true }},
    {{ key: 'setting', label: 'Уставка' }},
    {{ key: 'p_drop_label', label: 'P_до → P_после' }},
    {{ key: 'flow_min', label: 'Q_мин, м³/ч' }},
    {{ key: 'flow_max', label: 'Q_макс, м³/ч' }},
    {{ key: 'flow_swing', label: 'Перепад ΔQ, м³/ч' }},
    {{ key: 'status', label: 'Статус' }},
    {{ key: 'advice', label: 'Инженерный вывод (ИИ / Граф)', isRichAdvice: true }}
  ]
}};

function renderTableFilters() {{
  const container = document.getElementById('tableFilters');
  if (!container) return;
  const presets = TABLE_FILTER_PRESETS[currentTable] || [];
  const allRows = DATA.tables[currentTable] || [];
  container.innerHTML = presets.map(p => {{
    const cnt = allRows.filter(p.fn).length;
    const activeCls = currentFilterPreset === p.id ? 'active' : '';
    return `<button type="button" class="filter-chip ${{activeCls}}" onclick="setTableFilterPreset('${{p.id}}')">${{p.label}} (${{cnt}})</button>`;
  }}).join('');
}}

function setTableFilterPreset(presetId) {{
  currentFilterPreset = presetId;
  highlightIdsSet.clear();
  clearAnalysisBox('tableAIAnalysisBox');
  renderTableFilters();
  renderTable();
}}

function resetTableFilters() {{
  currentFilterPreset = 'all';
  currentSearch = '';
  currentSortKey = null;
  highlightIdsSet.clear();
  highlightRowId = null;
  const sInp = document.getElementById('tableSearchInput');
  if (sInp) sInp.value = '';
  const banner = document.getElementById('tableStatusBanner');
  if (banner) banner.style.display = 'none';
  clearAnalysisBox('tableAIAnalysisBox');
  renderTableFilters();
  renderTable();
}}

function applyTablePresetAndHighlight(tab, preset, sortKey, sortDir, highlightIds, focusId, bannerTitle) {{
  currentTable = tab;
  currentFilterPreset = preset || 'all';
  currentSortKey = sortKey || null;
  currentSortDir = sortDir || 'asc';
  highlightIdsSet = new Set((highlightIds || []).map(String));
  highlightRowId = focusId ? String(focusId) : null;

  document.querySelectorAll('.table-tab-btn').forEach(b => b.classList.remove('active'));
  const btn = document.getElementById('tabBtn_' + tab);
  if (btn) btn.classList.add('active');

  renderTableFilters();
  renderTable();

  const banner = document.getElementById('tableStatusBanner');
  const bannerTxt = document.getElementById('tableStatusText');
  if (banner && bannerTxt) {{
    banner.style.display = 'flex';
    const colMeta = (tableSchemas[tab] || []).find(c => c.key === sortKey);
    const sortRuLabel = colMeta ? colMeta.label.replace(/\\s*\\(на карту\\)/i, '') : 'ID объекта';
    const dirRuLabel = sortKey ? (sortDir === 'desc' ? 'по убыванию' : 'по возрастанию') : 'по умолчанию';
    bannerTxt.innerHTML = `Активна выборка: <b>${{bannerTitle || preset}}</b> (подсвечено объектов: <b>${{highlightIdsSet.size || (focusId ? 1 : 'все в фильтре')}}</b>, сортировка: <b>${{sortRuLabel}}</b>, ${{dirRuLabel}})`;
  }}
}}

function switchTableTab(tab, btn, focusId = null) {{
  currentTable = tab;
  currentFilterPreset = 'all';
  currentSortKey = null;
  highlightRowId = focusId ? String(focusId) : null;
  highlightIdsSet.clear();
  if (focusId) highlightIdsSet.add(String(focusId));
  document.querySelectorAll('.table-tab-btn').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');
  const banner = document.getElementById('tableStatusBanner');
  if (banner) banner.style.display = 'none';
  clearAnalysisBox('tableAIAnalysisBox');
  renderTableFilters();
  renderTable();
}}

function onTableSearch(val) {{
  currentSearch = (val || '').toLowerCase();
  renderTable();
}}

function onTableColumnSort(colKey) {{
  if (currentSortKey === colKey) {{
    currentSortDir = currentSortDir === 'asc' ? 'desc' : 'asc';
  }} else {{
    currentSortKey = colKey;
    currentSortDir = 'asc';
  }}
  renderTable();
}}

function getFilteredAndSortedRows() {{
  let rows = [...(DATA.tables[currentTable] || [])];
  const presets = TABLE_FILTER_PRESETS[currentTable] || [];
  const activePreset = presets.find(p => p.id === currentFilterPreset);
  if (activePreset && activePreset.id !== 'all') {{
    const filtered = rows.filter(activePreset.fn);
    if (filtered.length > 0 || highlightIdsSet.size === 0) {{
      rows = filtered;
    }}
  }}
  if (highlightIdsSet.size > 0 && currentFilterPreset === 'all') {{
    // Если передан список ID для подсветки без пресета, поднимаем их наверх
    rows.sort((a, b) => (highlightIdsSet.has(String(b.clean_id)) ? 1 : 0) - (highlightIdsSet.has(String(a.clean_id)) ? 1 : 0));
  }}
  if (currentSearch) {{
    rows = rows.filter(r => Object.values(r).some(v => String(v ?? '').toLowerCase().includes(currentSearch)));
  }}
  if (currentSortKey) {{
    const dir = currentSortDir === 'asc' ? 1 : -1;
    rows.sort((a, b) => {{
      const va = a[currentSortKey], vb = b[currentSortKey];
      const na = Number(va), nb = Number(vb);
      if (Number.isFinite(na) && Number.isFinite(nb) && va !== '—' && vb !== '—') {{
        return (na - nb) * dir;
      }}
      return String(va ?? '').localeCompare(String(vb ?? ''), 'ru') * dir;
    }});
  }}
  return rows;
}}

function hasChartForRow(tab, r) {{
  const cid = String(r.clean_id || '');
  if (tab === 'pumps') return (DATA.qh_curves || []).some(c => String(c.clean_id) === cid);
  if (tab === 'pipes') return (DATA.bottleneck_pipes_points || []).some(p => String(p.clean_id) === cid) || Number(r.headloss_m_km) >= 15 || Boolean(DATA.velocity_bins);
  if (tab === 'nodes') return (DATA.deficit_nodes_points || []).some(p => String(p.clean_id) === cid) || Number(r.margin_m) < 0 || Number(r.free_pressure_min_m) < 0;
  if (tab === 'valves') return (DATA.valves_hourly_series || []).length > 0;
  return false;
}}

function jumpFromTableToChart(tab, cleanId) {{
  const cid = String(cleanId || '');
  highlightedObjectId = cid;
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active-highlight'));
  let targetSecId = null;

  if (tab === 'pumps') {{
    const pIdx = (DATA.qh_curves || []).findIndex(c => String(c.clean_id) === cid);
    if (pIdx >= 0) {{
      currentQHIndex = pIdx;
      const sel = document.getElementById('qhSelect');
      if (sel) sel.value = pIdx;
      qhShowAllMode = false;
      drawQHCurve(pIdx);
      targetSecId = 'qhSection';
    }}
  }} else if (tab === 'pipes') {{
    const isBneck = (DATA.bottleneck_pipes_points || []).some(p => String(p.clean_id) === cid);
    if (isBneck) {{
      drawBottlenecksChart();
      targetSecId = 'bottlenecksSection';
    }} else {{
      drawStagnationChart();
      targetSecId = 'stagnationSection';
    }}
  }} else if (tab === 'nodes') {{
    switchPressureMode('points');
    targetSecId = 'pressureSection';
  }} else if (tab === 'valves') {{
    drawValvesChart();
    targetSecId = 'valvesSection';
  }}

  if (targetSecId) {{
    const el = document.getElementById(targetSecId);
    if (el) {{
      el.classList.add('active-highlight');
      el.scrollIntoView({{ behavior: 'smooth', block: 'center' }});
    }}
  }}
}}

function buildAdviceCellInnerHTML(tab, r) {{
  const safeId = String(r.clean_id || '').replace(/'/g, '');
  const hasAI = Boolean(r.ai_advice && r.ai_advice.trim());
  const mode = (r.advice_mode === 'ai' && hasAI) ? 'ai' : 'graph';
  const shownText = mode === 'ai' ? r.ai_advice : (r.graph_fact || r.advice || '—');
  const tagHtml = mode === 'ai'
    ? `<span class="tag-ai-real" title="Сгенерировано локальной нейросетью (GPU)">🟢 ИИ</span>`
    : `<span class="tag-graph-fact" title="Предрасчет по графу Python">Граф</span>`;
  let btns = '';
  if (mode === 'ai') {{
    btns = `<button type="button" class="row-toggle-btn" onclick="toggleRowAdviceMode('${{tab}}','${{safeId}}')" title="Переключить обратно на графовое описание">⇄ Граф</button>`
         + `<button type="button" class="row-ai-btn" onclick="generateRowAIAdvice('${{tab}}','${{safeId}}',this)" title="Перегенерировать вывод ИИ на GPU">↻ ИИ</button>`;
  }} else if (hasAI) {{
    btns = `<button type="button" class="row-toggle-btn" onclick="toggleRowAdviceMode('${{tab}}','${{safeId}}')" title="Вернуться к сгенерированному выводу ИИ">⇄ ИИ</button>`
         + `<button type="button" class="row-ai-btn" onclick="generateRowAIAdvice('${{tab}}','${{safeId}}',this)" title="Перегенерировать вывод ИИ на GPU">↻ ИИ</button>`;
  }} else {{
    btns = `<button type="button" class="row-ai-btn" onclick="generateRowAIAdvice('${{tab}}','${{safeId}}',this)" title="Сформировать живой инженерный вывод нейросетью (GPU) для объекта #${{safeId}}">✨ ИИ</button>`;
  }}
  return `${{tagHtml}}<span class="advice-text">${{formatTextWithObjectButtons(shownText, 'map')}}</span>${{btns}}`;
}}

function toggleRowAdviceMode(tab, cleanId) {{
  const rows = DATA.tables[tab] || [];
  const row = rows.find(x => String(x.clean_id) === String(cleanId));
  if (!row || !row.ai_advice) return;
  row.advice_mode = (row.advice_mode === 'ai') ? 'graph' : 'ai';
  row.advice = (row.advice_mode === 'ai') ? row.ai_advice : (row.graph_fact || row.advice);
  const cellWrap = document.querySelector(`[data-ai-cell="${{tab}}:${{cleanId}}"]`);
  if (cellWrap) cellWrap.innerHTML = buildAdviceCellInnerHTML(tab, row);
}}

function renderTable() {{
  const schema = tableSchemas[currentTable] || [];
  const rows = getFilteredAndSortedRows();

  const thead = document.getElementById('mainTableHead');
  thead.innerHTML = '<tr>' + schema.map(c => {{
    const isSorted = currentSortKey === c.key;
    const arrow = isSorted ? (currentSortDir === 'asc' ? ' ↑' : ' ↓') : ' ↕';
    return `<th onclick="onTableColumnSort('${{c.key}}')" title="Нажмите для сортировки по столбцу «${{c.label}}»">${{c.label}}<span style="color:${{isSorted ? '#0284c7' : '#94a3b8'}};font-size:11px">${{arrow}}</span></th>`;
  }}).join('') + '</tr>';

  const tbody = document.getElementById('mainTableBody');
  tbody.innerHTML = '';

  if (rows.length === 0) {{
    const tr = tbody.insertRow();
    const td = tr.insertCell();
    td.colSpan = schema.length;
    td.style.cssText = 'text-align:center;padding:24px;color:#64748b;';
    td.innerHTML = `По выбранному фильтру записей не найдено. <button type="button" class="btn-subtle" onclick="resetTableFilters()">Показать все строки</button>`;
    return;
  }}

  let visibleRows = rows.slice(0, 200);
  if (highlightRowId && !visibleRows.some(r => String(r.clean_id) === String(highlightRowId))) {{
    const exact = rows.find(r => String(r.clean_id) === String(highlightRowId));
    if (exact) visibleRows = [exact, ...visibleRows.slice(0, 199)];
  }}

  visibleRows.forEach(r => {{
    const tr = tbody.insertRow();
    const cidStr = String(r.clean_id);
    if (highlightRowId && cidStr === String(highlightRowId)) {{
      tr.className = 'row-focused';
    }} else if (highlightIdsSet.has(cidStr)) {{
      tr.className = 'row-highlighted';
    }}

    schema.forEach(col => {{
      const td = tr.insertCell();
      const v = r[col.key] ?? '—';
      if (col.isId) {{
        const safeId = String(v || '').replace(/'/g, '');
        const chartBtn = hasChartForRow(currentTable, r)
          ? `<button type="button" class="chart-jump-btn" onclick="jumpFromTableToChart('${{currentTable}}','${{safeId}}')" title="Перейти к объекту #${{safeId}} на графике">📈</button>`
          : '';
        td.innerHTML = `<button type="button" class="object-link object-link-map" data-map-id="${{safeId}}" title="Показать и подсветить #${{safeId}} на карте">${{v}}</button>${{chartBtn}}`;
      }} else if (col.isNodeRef && v !== '—') {{
        td.innerHTML = `<button type="button" class="object-link object-link-map" data-map-id="${{v}}" title="Показать узел #${{v}} на карте">#${{v}}</button>`;
      }} else if (col.isBadge) {{
        td.innerHTML = `<span class="${{r.is_borehole ? 'badge-bh' : 'badge-net'}}">${{v}}</span>`;
      }} else if (col.isRichAdvice) {{
        const safeId = String(r.clean_id || '').replace(/'/g, '');
        td.innerHTML = `<div class="advice-cell-wrap" data-ai-cell="${{currentTable}}:${{safeId}}">${{buildAdviceCellInnerHTML(currentTable, r)}}</div>`;
      }} else {{
        td.textContent = String(v);
      }}
      if (col.isCrit && col.isCrit(r)) td.className = 'num-crit';
    }});
  }});
}}

function buildRowContextPayload(tab, r) {{
  const payload = {{ table: tab, id: r.clean_id }};
  if (tab === 'nodes') {{
    payload.z_m = r.elevation_m;
    payload.floors = r.floors;
    payload.p_req_m = r.required_pressure_m;
    payload.p_min_m = r.free_pressure_min_m;
    payload.p_max_m = r.free_pressure_max_m;
    payload.dp_margin_m = r.margin_m;
    payload.demand_m3_h = r.demand_m3_h;
    payload.category = r.pressure_category;
    const np = DATA.node_props[r.clean_id] || DATA.node_props['c' + r.clean_id] || {{}};
    if (np.connected_pipe_ids) payload.connected_pipes = np.connected_pipe_ids.slice(0, 5);
  }} else if (tab === 'pipes') {{
    payload.start_node = r.start_node;
    payload.end_node = r.end_node;
    payload.d_mm = r.diameter_mm;
    payload.l_m = r.length_m;
    payload.v_m_s = r.velocity_max_m_s;
    payload.i_m_km = r.headloss_m_km;
    payload.c_hw = r.hazen_williams_c;
    payload.q_m3_h = r.flow_m3_h;
    payload.velocity_bin = r.velocity_bin;
    payload.is_closed = r.is_closed;
    const snP = DATA.node_props[r.start_node] || DATA.node_props['c' + r.start_node];
    const enP = DATA.node_props[r.end_node] || DATA.node_props['c' + r.end_node];
    if (snP) payload.start_node_p_m = snP.p_min;
    if (enP) payload.end_node_p_m = enP.p_min;
  }} else if (tab === 'pumps') {{
    payload.pump_type = r.pump_type;
    payload.is_borehole = r.is_borehole;
    payload.active = r.active;
    payload.start_node = r.start_node;
    payload.end_node = r.end_node;
    payload.collector_p_m = r.collector_pressure_m;
    payload.q_work_m3_h = r.q_work;
    payload.q_nom_m3_h = r.q_nom;
    payload.h_work_m = r.h_work;
    payload.regime = r.regime;
    const enP = DATA.node_props[r.end_node] || DATA.node_props['c' + r.end_node];
    if (enP) {{ payload.collector_z_m = enP.z; payload.collector_pipes = (enP.connected_pipe_ids || []).slice(0, 4); }}
  }} else if (tab === 'valves') {{
    payload.type = r.type_code;
    payload.start_node = r.start_node;
    payload.end_node = r.end_node;
    payload.setting = r.setting;
    payload.p_drop = r.p_drop_label;
    payload.q_min = r.flow_min;
    payload.q_max = r.flow_max;
    payload.status = r.status;
  }}
  if (r.graph_fact) payload.graph_fact = r.graph_fact;
  return payload;
}}

async function generateRowAIAdvice(tab, cleanId, btnEl) {{
  const rows = DATA.tables[tab] || [];
  const row = rows.find(x => String(x.clean_id) === String(cleanId));
  if (!row) return false;
  const cellWrap = document.querySelector(`[data-ai-cell="${{tab}}:${{cleanId}}"]`);
  const txtSpan = cellWrap ? cellWrap.querySelector('.advice-text') : null;
  if (btnEl) {{ btnEl.disabled = true; btnEl.textContent = '⏳ ИИ...'; }}
  if (txtSpan) txtSpan.innerHTML = `<span style="color:#0284c7;font-style:italic;">Нейросеть (GPU) анализирует объект #${{cleanId}}...</span>`;

  const sysPrompt = 'Ты главный инженер-гидравлик ПК «Аквабит». Дай КРАТКИЙ (1-2 предложения, до 280 символов), конкретный и однозначный инженерный вывод для одной строки таблицы: физическая причина + точное действие на сети или в ПК «Аквабит». Указывай ID смежных узлов/труб как #ID. Без вступлений, без списков, без markdown-звездочек, без шаблонных оговорок про задвижки у скважины.';
  const userPrompt = 'Данные объекта:\\n' + JSON.stringify(buildRowContextPayload(tab, row));

  const res = await streamLocalLLM(
    sysPrompt,
    userPrompt,
    (partial) => {{
      if (txtSpan) txtSpan.innerHTML = formatTextWithObjectButtons(partial.replace(/\\*\\*/g, ''), 'map');
    }},
    220
  );

  if (res && res.text) {{
    const cleanTxt = res.text.replace(/\\*\\*/g, '').trim();
    row.ai_advice = cleanTxt;
    row.advice = cleanTxt;
    row.advice_mode = 'ai';
    row.ai_generated = true;
    if (cellWrap) {{
      cellWrap.innerHTML = buildAdviceCellInnerHTML(tab, row);
    }}
    return true;
  }} else {{
    if (cellWrap) {{
      cellWrap.innerHTML = buildAdviceCellInnerHTML(tab, row) + ` <span style="color:#b45309;font-size:11px;">(⚠️ сервер LLM 127.0.0.1:8080 не отвечает)</span>`;
    }}
    return false;
  }}
}}

async function generateTableAIAnalysis() {{
  const box = document.getElementById('tableAIAnalysisBox');
  if (!box) return;
  const rows = getFilteredAndSortedRows();
  const sample = rows.slice(0, 8);
  const tabNames = {{ nodes: 'Узлы абонентов', pipes: 'Трубопроводы', pumps: 'Насосные агрегаты', valves: 'Клапаны' }};
  const tabTitle = tabNames[currentTable] || currentTable;
  const topSummary = sample.slice(0, 5).map(r => `#${{r.clean_id}}: ${{r.advice || r.status || ''}}`).join('<br>• ');
  const fallbackHtml = `<strong>Разбор текущей выборки таблицы «${{tabTitle}}» (найдено строк: ${{rows.length}}, фильтр: ${{currentFilterPreset}}):</strong><br>
  • <b>Приоритетные объекты выборки:</b><br>• ${{formatTextWithObjectButtons(topSummary || 'Нет данных', 'table')}}<br>
  • <b>Рекомендация:</b> Нажмите на ID любого объекта в таблице, чтобы показать его на карте, или на кнопку 📈 для перехода к графику.`;

  aiChatHistory['table'] = [];
  const scrollEl = ensureChatLayout(box, 'table');
  scrollEl.innerHTML = `<div class="ai-box-status" style="color:#0284c7;font-weight:700;font-size:12px;margin-bottom:6px;">Подключение к локальной нейросети (http://127.0.0.1:8080) для анализа ${{rows.length}} строк...</div><div class="stream-target"></div>`;
  const target = scrollEl.querySelector('.stream-target');
  const statusEl = scrollEl.querySelector('.ai-box-status');

  const compactRows = sample.map(r => {{
    const o = {{ id: r.clean_id }};
    if (r.free_pressure_min_m !== undefined) o.p_min = r.free_pressure_min_m;
    if (r.margin_m !== undefined) o.dp = r.margin_m;
    if (r.elevation_m !== undefined) o.z = r.elevation_m;
    if (r.diameter_mm !== undefined) o.d_mm = r.diameter_mm;
    if (r.velocity_max_m_s !== undefined) o.v = r.velocity_max_m_s;
    if (r.headloss_m_km !== undefined) o.i_m_km = r.headloss_m_km;
    if (r.q_work !== undefined) o.q_work = r.q_work;
    if (r.q_nom !== undefined) o.q_nom = r.q_nom;
    if (r.start_node) o.start_node = r.start_node;
    if (r.end_node) o.end_node = r.end_node;
    if (r.advice) o.fact = r.advice;
    return o;
  }});

  const sysPrompt = 'Ты главный инженер-гидравлик ПК «Аквабит». Проанализируй отфильтрованную выборку таблицы, выдели общую причину проблемы и дай четкий план действий в ПК «Аквабит» и на сети без шаблонных оговорок. Используй Markdown (**жирный**, списки). Для сравнения параметров указывай объекты как #ID (ведет в таблицу), а если важно расположение на схеме — [объект #ID на карте](map:ID).';
  const userPrompt = `Таблица: ${{tabTitle}} (всего отфильтровано: ${{rows.length}}, фильтр: ${{currentFilterPreset}}).\\nТоп-${{compactRows.length}} строк выборки:\\n${{JSON.stringify(compactRows)}}`;

  const llmRes = await streamLocalLLM(
    sysPrompt,
    userPrompt,
    (partial) => {{
      statusEl.innerHTML = `🟢 Живая генерация ИИ по таблице (GPU / llama-server @ 127.0.0.1:8080)...`;
      target.innerHTML = renderMarkdownToHTML(partial, 'table');
      scrollEl.scrollTop = scrollEl.scrollHeight;
    }}
  );

  if (llmRes && llmRes.text) {{
    aiChatHistory['table'] = [
      {{ role: 'user', content: userPrompt }},
      {{ role: 'assistant', content: llmRes.text }}
    ];
    statusEl.innerHTML = `🟢 Живой вывод локальной LLM по выборке таблицы (${{llmRes.base}} • ${{llmRes.sec}} сек, ~${{llmRes.tps}} ток/с):`;
    target.innerHTML = renderMarkdownToHTML(llmRes.text, 'table')
      + '<hr style="border:none;border-top:1px dashed #cbd5e1;margin:8px 0">'
      + `<div style="font-size:12px;color:#475569">${{fallbackHtml}}</div>`;
    scrollEl.scrollTop = 0;
  }} else {{
    streamHTMLIntoBox(
      box,
      fallbackHtml,
      'table',
      '🟡 Автономный графовый разбор выборки (локальный сервер LLM http://127.0.0.1:8080 не запущен):'
    );
  }}
}}

function onQHSelectChange(v) {{
  currentQHIndex = Number(v);
  qhShowAllMode = false;
  clearAnalysisBox('qhAnalysis');
  drawQHCurve(currentQHIndex);
}}

function toggleQHShowAll() {{
  qhShowAllMode = !qhShowAllMode;
  clearAnalysisBox('qhAnalysis');
  drawQHCurve(currentQHIndex);
}}

function attachInteractiveCrosshairs() {{
  // 1. Кривая Q-H: наведение и клик по точке -> переход к насосу в таблице
  const qhC = document.getElementById('qhCanvas');
  qhC.addEventListener('mousemove', ev => {{
    const m = qhC._meta; if (!m) return;
    const rect = qhC.getBoundingClientRect();
    const px = ev.clientX - rect.left, py = ev.clientY - rect.top;
    if (px < m.pad.l || px > m.pad.l + m.pw || py < m.pad.t || py > m.pad.t + m.ph) {{ hideChartTooltip(); drawQHCurve(currentQHIndex); return; }}
    let hitOp = null;
    for (const op of (m.opPoints || [])) {{
      if (Math.hypot(op.px - px, op.py - py) < 14) {{ hitOp = op; break; }}
    }}
    const q = ((px - m.pad.l) / m.pw) * m.maxQ;
    const h = ((m.pad.t + m.ph - py) / m.ph) * m.maxH;
    drawQHCurve(currentQHIndex, {{ px: hitOp ? hitOp.px : px, py: hitOp ? hitOp.py : py, q: hitOp ? hitOp.item.operating_q_mean : q, h: hitOp ? hitOp.item.operating_h_est : h }});
    if (hitOp) {{
      qhC.style.cursor = 'pointer';
      showChartTooltip(ev, `<b>Насос #${{hitOp.item.clean_id}}</b> (${{hitOp.item.is_borehole ? 'Скважинный' : 'Сетевой'}})<br>Подача Q: <b>${{hitOp.item.operating_q_mean}} м³/ч</b> • Напор H: <b>${{hitOp.item.operating_h_est}} м</b><br><span style="color:#38bdf8">Кликните для перехода к строке в таблице ↓</span>`);
    }} else {{
      qhC.style.cursor = 'crosshair';
      showChartTooltip(ev, `Подача Q: <b>${{q.toFixed(1)}} м³/ч</b><br>Напор H: <b>${{h.toFixed(1)}} м</b>`);
    }}
  }});
  qhC.addEventListener('click', ev => {{
    const m = qhC._meta; if (!m) return;
    const rect = qhC.getBoundingClientRect();
    const px = ev.clientX - rect.left, py = ev.clientY - rect.top;
    for (const op of (m.opPoints || [])) {{
      if (Math.hypot(op.px - px, op.py - py) < 16) {{
        jumpToTableRowFromChart('pumps', op.item.clean_id);
        return;
      }}
    }}
    if (!qhShowAllMode && m.curvesToPlot?.[0]?.clean_id) {{
      jumpToTableRowFromChart('pumps', m.curvesToPlot[0].clean_id);
    }}
  }});
  qhC.addEventListener('mouseleave', () => {{ hideChartTooltip(); drawQHCurve(currentQHIndex); }});

  // 2. График дефицитов напора: наведение и клик по точке -> переход к узлу в таблице
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
      pbC._hoveredPoint = best;
      if (best) {{
        pbC.style.cursor = 'pointer';
        const px = m.xOf(best.z || 0), py = m.yOf(best.dp || 0);
        drawPressureBand({{ px, py, xVal: best.z || 0, yVal: best.dp || 0 }});
        showChartTooltip(ev, `<b>Узел #${{best.clean_id}}</b> (${{best.floors}} эт.)<br>Отметка Z: <b>${{best.z}} м</b> • Pсвоб: <b>${{best.p_free}} м</b><br>Дефицит ΔP: <b>${{best.dp}} м</b><br><span style="color:#38bdf8">Кликните для подсветки в таблице узлов ↓</span>`);
        return;
      }}
      pbC.style.cursor = 'crosshair';
      const xVal = m.minX + ((mx - m.pad.l) / m.pw) * (m.maxX - m.minX);
      const yVal = m.minY + ((m.pad.t + m.ph - my) / m.ph) * (m.maxY - m.minY);
      drawPressureBand({{ px: mx, py: my, xVal, yVal }});
      showChartTooltip(ev, `Отметка Z: <b>${{xVal.toFixed(1)}} м</b><br>Отклонение ΔP: <b>${{yVal.toFixed(1)}} м</b>`);
    }} else {{
      const idx = Math.max(0, Math.min(m.n - 1, Math.round(((mx - m.pad.l) / m.pw) * (m.n - 1))));
      const yVal = m.pb.p50[idx] ?? 0;
      drawPressureBand({{ px: m.xOf(idx), py: m.yOf(yVal), xLabel: `${{String(idx).padStart(2, '0')}}:00`, yVal }});
      showChartTooltip(ev, `<b>Час ${{String(idx).padStart(2, '0')}}:00</b><br>Медиана P50: <b>${{yVal}} м</b><br>Коридор P10..P90: ${{m.pb.p10[idx]}} .. ${{m.pb.p90[idx]}} м`);
    }}
  }});
  pbC.addEventListener('click', () => {{
    if (pbC._hoveredPoint && pbC._hoveredPoint.clean_id) {{
      jumpToTableRowFromChart('nodes', pbC._hoveredPoint.clean_id);
    }}
  }});
  pbC.addEventListener('mouseleave', () => {{ hideChartTooltip(); drawPressureBand(); }});

  // 3. График узких мест: наведение и клик по точке -> переход к трубе в таблице
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
    bnC._hoveredPipe = best;
    if (best) {{
      bnC.style.cursor = 'pointer';
      drawBottlenecksChart({{ px: m.xOf(best.v || 0), py: m.yOf(best.i || 0), xVal: best.v || 0, yVal: best.i || 0 }});
      showChartTooltip(ev, `<b>Участок #${{best.clean_id}}</b> (D=${{best.d}} мм, L=${{best.l}} м)<br>Скорость V: <b>${{best.v}} м/с</b> • Потери i: <b>${{best.i}} м/км</b><br><span style="color:#38bdf8">Кликните для перехода к строке в таблице ↓</span>`);
    }} else {{
      bnC.style.cursor = 'crosshair';
      hideChartTooltip();
      drawBottlenecksChart();
    }}
  }});
  bnC.addEventListener('click', () => {{
    if (bnC._hoveredPipe && bnC._hoveredPipe.clean_id) {{
      jumpToTableRowFromChart('pipes', bnC._hoveredPipe.clean_id);
    }}
  }});
  bnC.addEventListener('mouseleave', () => {{ hideChartTooltip(); drawBottlenecksChart(); }});

  // 4. Круговая диаграмма скоростей: анимация при наведении на сектор или легенду + клик для фильтрации таблицы труб
  const stC = document.getElementById('stagnationCanvas');
  if (stC) {{
    function hitTestPieOrLegend(ev) {{
      const m = stC._meta; if (!m) return -1;
      const rect = stC.getBoundingClientRect();
      const mx = ev.clientX - rect.left, my = ev.clientY - rect.top;
      for (const lb of (m.legendBoxes || [])) {{
        if (mx >= lb.x && mx <= lb.x + lb.w && my >= lb.y && my <= lb.y + lb.h) return lb.idx;
      }}
      const dx = mx - m.cx, dy = my - m.cy;
      const dist = Math.hypot(dx, dy);
      if (dist >= m.innerR && dist <= m.outerR + 10) {{
        let ang = Math.atan2(dy, dx);
        if (ang < -Math.PI / 2) ang += Math.PI * 2;
        for (const s of (m.slices || [])) {{
          if (ang >= s.startA && ang <= s.endA && s.val > 0) return s.idx;
        }}
      }}
      return -1;
    }}
    stC.addEventListener('mousemove', ev => {{
      const idx = hitTestPieOrLegend(ev);
      if (idx !== hoveredPieSliceIdx) {{
        hoveredPieSliceIdx = idx;
        drawStagnationChart(idx);
      }}
      if (idx >= 0 && stC._meta?.slices?.[idx]) {{
        const sl = stC._meta.slices[idx];
        stC.style.cursor = 'pointer';
        showChartTooltip(ev, `<b>${{sl.label}}</b><br>Участков: <b>${{sl.val}} шт. (${{(sl.ratio * 100).toFixed(1)}}%)</b><br><span style="color:#38bdf8">Нажмите для фильтрации этих труб в таблице ↓</span>`);
      }} else {{
        stC.style.cursor = 'default';
        hideChartTooltip();
      }}
    }});
    stC.addEventListener('click', ev => {{
      const idx = hitTestPieOrLegend(ev);
      if (idx >= 0 && stC._meta?.slices?.[idx]) {{
        const sl = stC._meta.slices[idx];
        applyTablePresetAndHighlight('pipes', sl.filterCode, 'velocity_max_m_s', 'desc', [], null, sl.label);
        document.getElementById('tablesSection').scrollIntoView({{ behavior: 'smooth', block: 'start' }});
      }}
    }});
    stC.addEventListener('mouseleave', () => {{
      hoveredPieSliceIdx = -1;
      hideChartTooltip();
      drawStagnationChart(-1);
    }});
  }}

  // 5. Клапаны и Суточный баланс
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
      const lbl = (DATA.hours_labels || [])[idx] || `${{String(idx).padStart(2, '0')}}:00`;
      drawValvesChart({{ px: m.xOf(idx), py: m.yOf(yVal), xLabel: lbl, yVal }});
      showChartTooltip(ev, `<b>Время ${{lbl}}</b><br>` + m.series.map(s => `${{s.label}}: <b>${{s.data[idx]}} м³/ч</b>`).join('<br>'));
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
      const lbl = `${{String(idx).padStart(2, '0')}}:00`;
      drawDiurnalFlows({{ px: m.xOf(idx), py: m.yOf(yVal), xLabel: lbl, yVal }});
      showChartTooltip(ev, `<b>Время ${{lbl}}</b><br><span style="color:#38bdf8">●</span> Потребление сети: <b>${{m.dem ? m.dem[idx] : '—'}} м³/ч</b><br><span style="color:#2dd4bf">●</span> Подача насосов: <b>${{m.pmp ? m.pmp[idx] : '—'}} м³/ч</b>`);
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
  initTimelineTicks();
  drawQHCurve(0);
  drawPressureBand();
  drawBottlenecksChart();
  drawStagnationChart(-1);
  drawValvesChart();
  drawDiurnalFlows();
  mapFit();
  renderTableFilters();
  renderTable();
  attachInteractiveCrosshairs();
}});

window.addEventListener('resize', () => {{
  drawQHCurve(currentQHIndex);
  drawPressureBand();
  drawBottlenecksChart();
  drawStagnationChart(hoveredPieSliceIdx);
  drawValvesChart();
  drawDiurnalFlows();
  redrawMap();
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


build_interactive_html_report = render_standalone_html_report

