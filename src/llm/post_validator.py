"""Детерминированный поствалидатор и санитайзер ответов локальной LLM (защита от галлюцинаций)."""
from __future__ import annotations
import re
from typing import Any
from processing.identifiers import clean_object_id

# JSON-схема для Constrained Decoding (llama-server response_format / json_schema)
DIAGNOSIS_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "object_id",
        "object_type",
        "diag_code",
        "severity",
        "root_cause",
        "engineering_action_network",
        "action_in_aquabit",
        "norm_reference",
    ],
    "properties": {
        "object_id": {"type": "string", "description": "Чистый числовой ID объекта из модели"},
        "object_type": {"type": "string", "enum": ["node", "pipe", "pump", "valve", "tank", "source"]},
        "diag_code": {
            "type": "string",
            "enum": [
                "R01_SHUTOFF_WELL_NEGATIVE_PRESSURE",
                "R02_EXTREME_DATA_ERROR_PRESSURE",
                "R03_REQUIRED_PRESSURE_DEFICIT",
                "R04_HIGH_VELOCITY_BOTTLENECK",
                "R05_LOW_VELOCITY_PIPES",
                "R06_BOOSTER_PUMP_UNDERLOAD_VALVE_CHECK",
                "R07_BOREHOLE_PUMP_UNDERLOAD",
                "R08_PUMP_OVERLOAD_OUTLET_REGULATION",
                "R11_HAZEN_WILLIAMS_DEGRADATION",
                "R12_DIURNAL_HYDROGRAPH_IMBALANCE",
                "R14_VALVES_AND_TANKS_REGULATION",
                "R15_CLOSED_GATE_VALVE_PIPE",
                "R16_EXCESS_PRESSURE_OVER_60M",
                "R17_REVERSE_FLOW_INSTABILITY",
            ],
        },
        "severity": {"type": "string", "enum": ["critical", "high", "medium", "low", "info"]},
        "root_cause": {"type": "string"},
        "engineering_action_network": {"type": "string"},
        "action_in_aquabit": {"type": "string"},
        "norm_reference": {"type": "string", "description": "Ссылка на пункт СП 31.13330.2012 (например, п. 5.11)"},
    },
    "additionalProperties": False,
}


def collect_known_model_ids(facts: dict[str, Any]) -> set[str]:
    """Собирает все реально существующие в модели города чистые ID объектов."""
    known: set[str] = set()
    pa = facts.get("pressure_analysis") or {}
    ppa = facts.get("pipes_analysis") or {}
    pma = facts.get("pumps_analysis") or {}
    vra = facts.get("valves_and_reservoirs_analysis") or {}
    charts = facts.get("chart_payload") or {}

    for lst in (
        pa.get("worst_deficit_nodes") or [],
        pa.get("all_deficit_nodes") or [],
        pa.get("top_critical_nodes") or [],
        pa.get("worst_data_error_nodes") or [],
        pa.get("top_excess_nodes") or [],
        pa.get("shutoff_wells_nodes") or [],
    ):
        for r in lst:
            cid = clean_object_id(r.get("clean_id") or r.get("node_id"))
            if cid:
                known.add(cid)

    for lst in (
        ppa.get("top_high_loss_pipes") or [],
        ppa.get("all_high_loss_pipes") or [],
        ppa.get("top_high_velocity_pipes") or [],
        ppa.get("top_low_hazen_williams_c_pipes") or [],
        ppa.get("degraded_high_loss_pipes") or [],
        ppa.get("closed_pipes_sample") or [],
        ppa.get("table_pipes_records") or [],
    ):
        for p in lst:
            for k in ("clean_id", "pipe_id", "start_node", "end_node"):
                cid = clean_object_id(p.get(k))
                if cid:
                    known.add(cid)

    for lst in (
        pma.get("all_pumps_table") or [],
        pma.get("pumps") or [],
        pma.get("underloaded_pumps") or [],
        pma.get("overloaded_pumps") or [],
    ):
        for p in lst:
            for k in ("clean_id", "pump_id", "start_node", "end_node", "connected_out_pipe_id", "curve_id"):
                cid = clean_object_id(p.get(k))
                if cid:
                    known.add(cid)

    for lst in (vra.get("valves_table") or [], vra.get("sources_table") or [], vra.get("tanks_table") or []):
        for item in lst:
            for k in ("clean_id", "valve_id", "source_id", "tank_id", "start_node", "end_node"):
                cid = clean_object_id(item.get(k))
                if cid:
                    known.add(cid)

    for k in (charts.get("node_props") or {}).keys():
        cid = clean_object_id(k)
        if cid:
            known.add(cid)
    for k in (charts.get("pipe_props") or {}).keys():
        cid = clean_object_id(k)
        if cid:
            known.add(cid)

    return known


_KIND_PREFIX_MAP = {
    "node_": "node",
    "ju": "node",
    "pipe_": "pipe",
    "pi": "pipe",
    "pump_": "pump",
    "pu": "pump",
    "valve_": "valve",
    "va": "valve",
    "reserv_": "node",
    "re": "node",
    "tank_": "node",
    "ta": "node",
}


def _normalize_table_or_map_link(m: re.Match[str]) -> str:
    target = m.group(1).lower()
    prefix = (m.group(2) or "").lower()
    num_id = m.group(3)
    kind = _KIND_PREFIX_MAP.get(prefix)
    if target == "table" and kind:
        return f"(table:{kind}:{num_id})"
    return f"({target}:{num_id})"


def sanitize_ai_report_text(raw_text: str, facts: dict[str, Any] | None = None) -> str:
    """Устраняет известные артефакты и галлюцинации локальной LLM (Qwen 3.5-4B / 7B):
    1. Разворачивает утекшие сырые HTML-теги (<button ...>, <br>) в чистый Markdown
    2. Убирает обратные кавычки вокруг markdown-ссылок и сырых ID: `[3467991](table:node_3467991)` -> [3467991](table:node:3467991)
    3. Убирает дублирование вида `#3381717 объект #3381717` или `#3381717 [объект #3381717](...)`
    4. Убирает утечки шаблонов (`#ID`, `участка #ID`, `[ID]`)
    5. Заменяет выдуманные файлы исходного кода C# (`Node.cs`, `Pipe.cs`) и несуществующие кнопки
    6. Исправляет неверную расшифровку BEP и запрещенный перевод м вод. ст. в кПа/бар
    7. Исправляет устаревшую фразу про аккумуляцию разницы подачи и потребления в РЧВ на корректный вывод о потерях воды.
    """
    if not raw_text:
        return ""
    text = raw_text

    # 0. Разворачиваем утекшие сырые HTML-кнопки <button ... onclick="scrollToTableRow('node','3459771')">#3459771</button>
    def _unwrap_raw_button(m: re.Match[str]) -> str:
        attrs = m.group(1) or ""
        inner = re.sub(r"<[^>]+>", "", m.group(2) or "").strip()
        m_scroll = re.search(
            r"scrollToTableRow\(\s*['\"](node|pipe|pump|valve)['\"]\s*,\s*['\"]#?([A-Za-z_]*\d+)['\"]\s*\)",
            attrs,
            flags=re.IGNORECASE,
        )
        if m_scroll:
            kind = m_scroll.group(1).lower()
            cid = clean_object_id(m_scroll.group(2))
            lbl = inner or f"#{cid}"
            return f"[{lbl}](table:{kind}:{cid})"
        m_tbl = re.search(r"data-table-id=['\"]#?([A-Za-z_]*\d+)['\"]", attrs, flags=re.IGNORECASE)
        if m_tbl:
            cid = clean_object_id(m_tbl.group(1))
            lbl = inner or f"#{cid}"
            return f"[{lbl}](table:{cid})"
        m_map = re.search(r"data-map-id=['\"]#?([A-Za-z_]*\d+)['\"]", attrs, flags=re.IGNORECASE)
        if m_map:
            cid = clean_object_id(m_map.group(1))
            lbl = inner or f"#{cid}"
            return f"[{lbl}](map:{cid})"
        m_inner_id = re.search(r"#?(\d{2,})", inner)
        if m_inner_id:
            cid = m_inner_id.group(1)
            return f"[#{cid}](table:{cid})"
        return inner

    text = re.sub(r"<button\b([^>]*)>(.*?)</button>", _unwrap_raw_button, text, flags=re.IGNORECASE | re.DOTALL)
    # Убираем сырые <br> / <br/> внутри markdown-текста
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)

    # 1. Снимаем обратные кавычки вокруг markdown-ссылок: `[3467991](table:node_3467991)`
    text = re.sub(
        r"`+\s*(\[[^\]]+\]\((?:table|map|filter):[^)]+\))\s*`+",
        r"\1",
        text,
    )

    # 1b. Снимаем обратные кавычки с префиксных ID из Report.json (`Ju3459771` -> [#3459771](table:node:3459771))
    text = re.sub(
        r"`+\s*#?(node_|pipe_|pump_|valve_|reserv_|tank_|Ju|Pi|Pu|Va|Re|Ta)(\d{3,})\s*`+",
        lambda m: f"[#{m.group(2)}](table:{_KIND_PREFIX_MAP.get(m.group(1).lower(), 'node')}:{m.group(2)})",
        text,
        flags=re.IGNORECASE,
    )

    # 2. Нормализуем префиксы внутри (table:node_3467991) -> (table:node:3467991) / (map:3467991)
    text = re.sub(
        r"\((table|map):#?(node_|pipe_|pump_|valve_|reserv_|tank_|ju|pi|pu|va|re|ta|[ceputvk])?(\d+)\)",
        _normalize_table_or_map_link,
        text,
        flags=re.IGNORECASE,
    )

    # 3. Устраняем дублирование вида "#3381717 [объект #3381717](table:3381717)" или "#3381717 объект #3381717"
    text = re.sub(
        r"#?(\d{2,})\s+\[(?:объект|узел|труба|участок|насос|агрегат|клапан)?\s*#?\1\]\((table|map):((?:node|pipe|pump|valve):)?\1\)",
        r"[#\1](\2:\3\1)",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"#(\d{2,})\s+(?:объект|узел|труба|участок|насос|агрегат)\s+#\1\b",
        r"#\1",
        text,
        flags=re.IGNORECASE,
    )

    # 4. Преобразуем псевдоссылки без URL вида [РЧВ #1] или [узел #3459771] (без круглых скобок следом)
    text = re.sub(
        r"\[((?:РЧВ|источник|скважина|узел|труба|участок|насос|клапан)\s+#?\d+)\](?!\()",
        r"\1",
        text,
        flags=re.IGNORECASE,
    )

    # 5. Убираем утечки незаполненного плейсхолдера #ID
    text = re.sub(r"\s*\(?\b(?:участк[ауеов]|узл[ауеов]|насос[ауеов]|объект[ауеов])?\s*#ID\b\)?", "", text)
    text = re.sub(r"\((?:table|map):ID\)", "", text)

    # 6. Убираем галлюцинации про файлы исходного кода C# (Node.cs, Pipe.cs, Pump.cs) и выдуманные кнопки
    text = re.sub(r"`?\b(?:Node|Pipe|Pump|Valve|Tank|Network|Graph)\.cs\b`?", "слое модели .digWater / Report.json", text)
    text = re.sub(
        r"кнопк[ауеой]\s+[«\"']Восстановить гидравлическую связность[»\"']",
        "проверку статуса секущих задвижек и отметок Z в модели ПК «Аквабит»",
        text,
        flags=re.IGNORECASE,
    )

    # 7. Исправляем галлюцинацию расшифровки BEP ("Безопасная Эксплуатационная Подача")
    text = re.sub(
        r"Безопасн[а-яё]+\s+Эксплуатационн[а-яё]+\s+(?:Подач[а-яё]+|Порог[а-яё]*|Предел[а-яё]*)",
        "Best Efficiency Point (точка оптимального КПД насоса)",
        text,
        flags=re.IGNORECASE,
    )

    # 8. Исправляем подмену м вод. ст. на кПа и бар вида "46.5 кПа (4.65 бар)" или "(-5.24 бар)"
    text = re.sub(
        r"([-+]?\d+(?:[.,]\d+)?)\s*кПа\s*\(\s*([-+]?\d+(?:[.,]\d+)?)\s*бар\s*\)",
        lambda m: f"{round(float(m.group(2).replace(',', '.')) * 10.0, 2)} м вод. ст.",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\(\s*эквивалент\s+[-+]?\d+(?:[.,]\d+)?\s*(?:бар|кПа|МПа)\s*\)",
        "",
        text,
        flags=re.IGNORECASE,
    )

    # 9. Исправляем устаревший вывод про аккумуляцию разницы подачи и потребления в РЧВ
    text = re.sub(
        r"Разница между подачей насосов и потреблением сети аккумулируется в резервуарах чистой воды\s*\(РЧВ\)\s*и компенсирует часы максимального водоразбора\.?",
        (
            "Основная разница между подачей воды в сеть и полезным водопотреблением (реализацией/продажей абонентам) "
            "представляет собой потери воды в сети (физические потери — утечки и аварии + коммерческие потери и несанкционированное потребление), "
            "а также собственные технологические нужды и повторную перекачку между ступенями подъема; "
            "РЧВ компенсируют лишь внутрисуточную часовую неравномерность (СП 31.13330.2012, п. 12.1–12.5)."
        ),
        text,
        flags=re.IGNORECASE,
    )

    return text


def _collect_node_pressures_from_facts(facts: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Собирает карту {clean_id: {p_min, p_req, floors, name}} из всех разделов facts."""
    nodes_map: dict[str, dict[str, Any]] = {}
    pa = facts.get("pressure_analysis") or {}
    charts = facts.get("chart_payload") or {}

    for lst in (
        pa.get("worst_deficit_nodes") or [],
        pa.get("all_deficit_nodes") or [],
        pa.get("top_critical_nodes") or [],
        pa.get("worst_data_error_nodes") or [],
        pa.get("top_excess_nodes") or [],
        pa.get("shutoff_wells_nodes") or [],
    ):
        for r in lst:
            cid = clean_object_id(r.get("clean_id") or r.get("node_id"))
            if not cid:
                continue
            p_min = r.get("free_pressure_min_m")
            if p_min is None:
                p_min = r.get("pressure_min_m")
            if p_min is None:
                p_min = r.get("p_min")
            nodes_map[cid] = {
                "p_min": float(p_min) if isinstance(p_min, (int, float)) else None,
                "p_req": r.get("required_pressure_m"),
                "floors": r.get("floors"),
                "name": r.get("name") or "",
            }

    for k, props in (charts.get("node_props") or {}).items():
        cid = clean_object_id(k)
        if cid and cid not in nodes_map and isinstance(props, dict):
            p_min = props.get("p_min")
            nodes_map[cid] = {
                "p_min": float(p_min) if isinstance(p_min, (int, float)) else None,
                "p_req": props.get("req"),
                "floors": props.get("floors"),
                "name": props.get("name") or "",
            }
    return nodes_map


def _collect_reserve_pumps_from_facts(facts: dict[str, Any]) -> set[str]:
    reserve: set[str] = set()
    pma = facts.get("pumps_analysis") or {}
    for lst in (pma.get("all_pumps_table") or [], pma.get("pumps") or []):
        for p in lst:
            cid = clean_object_id(p.get("clean_id") or p.get("pump_id"))
            if not cid:
                continue
            q_w = p.get("q_working_m3_h")
            is_act = p.get("active")
            if (is_act is False) or (isinstance(q_w, (int, float)) and q_w <= 0.001):
                reserve.add(cid)
    return reserve


def validate_ai_report_against_facts(ai_text: str, facts: dict[str, Any]) -> dict[str, Any]:
    """Проверяет сгенерированный ИИ-отчёт на соответствие фактам модели, физическим инвариантам и СП 31.13330.
    Возвращает структурированный список нарушений (`violations`) и скорректированный текст (`corrected_text`).
    """
    raw_text = ai_text or ""
    # Идемпотентность: исключаем ранее добавленный блок примечаний верификатора из повторной проверки
    base_text = re.sub(
        r"\n*###\s+Примечание верификатора\b[\s\S]*?(?=\n*\[SELECTED_DASHBOARDS\]|\Z)",
        "",
        raw_text,
    ).rstrip()
    known_ids = collect_known_model_ids(facts)
    mentioned_ids = set(re.findall(r"(?:#|table:(?:(?:node|pipe|pump|valve):)?|map:)(\d{3,})\b", base_text))

    valid_mentioned = mentioned_ids & known_ids if known_ids else mentioned_ids
    hallucinated_ids = sorted(mentioned_ids - known_ids) if known_ids else []
    id_precision = round(len(valid_mentioned) / len(mentioned_ids), 4) if mentioned_ids else 1.0

    issues: list[str] = []
    violations: list[dict[str, Any]] = []
    verifier_notes: list[str] = []

    if hallucinated_ids:
        msg = f"Обнаружены отсутствующие в модели ID: {', '.join('#' + x for x in hallucinated_ids[:5])}"
        issues.append(msg)
        violations.append({
            "type": "HALLUCINATED_OBJECT_ID",
            "severity": "critical",
            "ids": hallucinated_ids[:10],
            "message": msg,
        })
        verifier_notes.append(f"Исключить неподтвержденные ID из модели: {', '.join('#' + x for x in hallucinated_ids[:5])}.")

    # 1. Физический инвариант: Дефицит напора при P_своб >= 0 НЕ ЯВЛЯЕТСЯ вакуумом / кавитацией
    nodes_map = _collect_node_pressures_from_facts(facts)
    negated_vac_re = re.compile(
        r"(?:не\s+(?:является\s+|вызывает\s+)?вакуум|а\s+не\s+вакуум|вакуум[ау]?(?:\s+[а-яё\-]+){0,4}\s+нет|нет\s+(?:[а-яё\-]+\s+){0,3}вакуум|без\s+вакуум|отсутствует\s+вакуум|не\s+наблюдается\s+вакуум|запрещено\s+называть)",
        flags=re.IGNORECASE,
    )
    vac_claim_re = re.compile(r"\b(?:вакуум[а-яё]*|кавитац[а-яё]*|разрыв[а-яё]*\s+стру[ией])\b", flags=re.IGNORECASE)

    sentences = re.split(r"(?<=[.!?])\s+|\n+", base_text)
    seen_false_vac: set[str] = set()
    for sent in sentences:
        if not vac_claim_re.search(sent) or negated_vac_re.search(sent):
            continue
        sent_ids = set(re.findall(r"(?:#|table:(?:(?:node|pipe|pump|valve):)?|map:)(\d{3,})\b", sent))
        for cid in sent_ids:
            info = nodes_map.get(cid)
            if info and info.get("p_min") is not None and info["p_min"] >= 0.0 and cid not in seen_false_vac:
                seen_false_vac.add(cid)
                p_min = info["p_min"]
                p_req = info.get("p_req")
                req_part = f" при требуемом Pтреб = {p_req} м" if p_req is not None else ""
                msg = (
                    f"Ложное утверждение о вакууме/кавитации в узле #{cid}: фактический свободный напор "
                    f"положительный (Pсвоб = +{p_min:.2f} м{req_part}), это дефицит напора по этажности (СП 31.13330 п. 5.11), а не вакуум."
                )
                issues.append(msg)
                violations.append({
                    "type": "FALSE_VACUUM_ON_POSITIVE_PRESSURE",
                    "severity": "critical",
                    "object_id": cid,
                    "p_min_m": p_min,
                    "message": msg,
                })
                verifier_notes.append(msg)

    # 2. Физический инвариант: физический вакуум в воде не может быть ниже -10 м вод. ст.
    for sent in sentences:
        m_neg = re.search(r"(-\d{2,}(?:[.,]\d+)?)\s*м(?:\s*вод\.?\s*ст\.?)?", sent)
        if m_neg:
            val = float(m_neg.group(1).replace(",", "."))
            if val < -10.5 and vac_claim_re.search(sent) and not re.search(r"артефакт|dda|ошибк|отметк|не\s+может\s+превышать", sent, flags=re.IGNORECASE):
                msg = (
                    f"Физически невозможный вакуум ({val:.1f} м вод. ст.): физический предел вакуума в воде -10 м вод. ст.; "
                    "значения ниже -10 м являются расчетным артефактом DDA (отсеченный узел или ошибка отметки Z)."
                )
                issues.append(msg)
                violations.append({
                    "type": "IMPOSSIBLE_PHYSICAL_VACUUM",
                    "severity": "high",
                    "value_m": val,
                    "message": msg,
                })
                verifier_notes.append(msg)
                break

    # 3. Запрет дросселирования на всасывающей линии насоса
    if re.search(r"(?:прикрыть|дросселир[а-яё]*|установить\s+(?:клапан|prv|регулятор))[^.\n]{0,55}(?:на\s+всас|перед\s+насос|на\s+входе\s+насос)", base_text, flags=re.IGNORECASE):
        msg = "Нарушение гидравлики насосных станций: дросселирование/PRV допускается только на напорной линии ПОСЛЕ насоса, а не на всасывании (риск кавитации, СП 31.13330 п. 10.7)."
        issues.append(msg)
        violations.append({
            "type": "SUCTION_THROTTLING_VIOLATION",
            "severity": "critical",
            "message": msg,
        })
        verifier_notes.append(msg)

    # 4. Ложная диагностика резервного насоса (Q=0) как неисправного
    reserve_pumps = _collect_reserve_pumps_from_facts(facts)
    if reserve_pumps:
        for sent in sentences:
            if re.search(r"(?:неисправн|сломан|поломк|вышел\s+из\s+строя|авари[яй]\s+насос)", sent, flags=re.IGNORECASE) and "резерв" not in sent.lower():
                for cid in re.findall(r"(?:#|table:(?:pump:)?|map:)(\d{3,})\b", sent):
                    if cid in reserve_pumps:
                        msg = f"Насос #{cid} имеет Q = 0 м³/ч и относится к штатному холодному резерву по СП 31.13330 п. 10.3 (Таблица 23), а не к неисправным агрегатам."
                        issues.append(msg)
                        violations.append({
                            "type": "FALSE_RESERVE_PUMP_FAULT",
                            "severity": "high",
                            "object_id": cid,
                            "message": msg,
                        })
                        verifier_notes.append(msg)

    # 5. Устаревшая ошибка про аккумуляцию разницы подачи и потребления в РЧВ
    if re.search(r"Разница между подачей насосов и потреблением сети аккумулируется в резервуарах", base_text, flags=re.IGNORECASE):
        msg = "Некорректный вывод по суточному балансу: основная разница между подачей и реализацией — это потери воды (физические утечки + коммерческие потери), а не аккумуляция в РЧВ."
        issues.append(msg)
        violations.append({
            "type": "RCHV_WATER_LOSS_ERROR",
            "severity": "medium",
            "message": msg,
        })

    if re.search(r"\b(?:Node|Pipe|Pump|Valve|Tank)\.cs\b", base_text):
        msg = "Галлюцинация исходных файлов C# (.cs)"
        issues.append(msg)
        violations.append({"type": "HALLUCINATED_CODE_OR_UI", "severity": "medium", "message": msg})

    if re.search(r"\b(?:кПа|МПа)\b", base_text):
        msg = "Несанкционированный перевод единиц давления в кПа/МПа вместо м вод. ст."
        issues.append(msg)
        violations.append({"type": "FORBIDDEN_PRESSURE_UNITS", "severity": "medium", "message": msg})

    if "Безопасная Эксплуатационная Подача" in base_text:
        msg = "Неверная расшифровка аббревиатуры BEP"
        issues.append(msg)
        violations.append({"type": "WRONG_BEP_EXPANSION", "severity": "medium", "message": msg})

    sp_citations = re.findall(r"(?:СП\s*31\.13330|п\.\s*(?:4\.3|5\.11|5\.12|5\.13|7\.\d+|10\.3|10\.7|11\.5|11\.11|11\.21|11\.38|12\.1))", base_text)

    corrected_text = sanitize_ai_report_text(base_text, facts)
    if verifier_notes:
        note_block = "\n\n### Примечание верификатора (автоматическая проверка физических инвариантов)\n" + "\n".join(
            f"- {n}" for n in verifier_notes
        )
        m_dash = re.search(r"\n*\[SELECTED_DASHBOARDS\]", corrected_text)
        if m_dash:
            pos = m_dash.start()
            corrected_text = corrected_text[:pos].rstrip() + note_block + "\n\n" + corrected_text[pos:].lstrip()
        else:
            corrected_text = corrected_text.rstrip() + note_block

    return {
        "is_valid": len(issues) == 0,
        "id_precision": id_precision,
        "mentioned_ids_count": len(mentioned_ids),
        "hallucinated_ids": hallucinated_ids,
        "normative_citations_count": len(sp_citations),
        "normative_citations": sorted(set(sp_citations)),
        "issues": issues,
        "violations": violations,
        "corrected_text": corrected_text,
    }


def build_self_correction_prompt(ai_text: str, validation_result: dict[str, Any]) -> str:
    """Формирует компактный промпт для 1-шаговой самокоррекции локальной LLM по списку выявленных нарушений."""
    issues = validation_result.get("issues") or []
    bullet_issues = "\n".join(f"- {iss}" for iss in issues[:8])
    return (
        "Исправь ТОЛЬКО перечисленные ниже фактические и физические ошибки в инженерном отчете, "
        "сохранив структуру, стиль, интерактивные ссылки `#ID` / `[...](filter:...)` и блок `[SELECTED_DASHBOARDS]`:\n"
        f"{bullet_issues}\n\n"
        "ИСХОДНЫЙ ТЕКСТ ОТЧЕТА ДЛЯ ИСПРАВЛЕНИЯ:\n"
        f"{ai_text}"
    )
