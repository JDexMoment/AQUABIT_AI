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

    for p in pma.get("all_pumps_table") or []:
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


def sanitize_ai_report_text(raw_text: str, facts: dict[str, Any] | None = None) -> str:
    """Устраняет известные артефакты и галлюцинации локальной LLM (Qwen 3.5-4B / 7B):
    1. Убирает обратные кавычки вокруг markdown-ссылок: `[3467991](table:node_3467991)` -> [узел #3467991](table:3467991)
    2. Убирает дублирование вида `#3381717 объект #3381717` или `#3381717 [объект #3381717](...)`
    3. Убирает утечки шаблонов (`#ID`, `участка #ID`, `[ID]`)
    4. Заменяет выдуманные файлы исходного кода C# (`Node.cs`, `Pipe.cs`) и несуществующие кнопки
    5. Исправляет неверную расшифровку BEP и запрещенный перевод м вод. ст. в кПа/бар
    6. Исправляет устаревшую фразу про аккумуляцию разницы подачи и потребления в РЧВ на корректный вывод о потерях воды.
    """
    if not raw_text:
        return ""
    text = raw_text

    # 1. Снимаем обратные кавычки вокруг markdown-ссылок: `[3467991](table:node_3467991)`
    text = re.sub(
        r"`+\s*(\[[^\]]+\]\((?:table|map|filter):[^)]+\))\s*`+",
        r"\1",
        text,
    )

    # 2. Нормализуем префиксы внутри (table:node_3467991) -> (table:3467991)
    text = re.sub(
        r"\((table|map):#?(?:node_|pipe_|pump_|valve_|reserv_|tank_|ju|pi|pu|va|re|ta|[ceputvk])?(\d+)\)",
        r"(\1:\2)",
        text,
        flags=re.IGNORECASE,
    )

    # 3. Устраняем дублирование вида "#3381717 [объект #3381717](table:3381717)" или "#3381717 объект #3381717"
    text = re.sub(
        r"#?(\d{2,})\s+\[(?:объект|узел|труба|участок|насос|агрегат|клапан)?\s*#?\1\]\((table|map):\1\)",
        r"[#\1](\2:\1)",
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
    text = re.sub(r"\b(?:Node|Pipe|Pump|Valve|Tank|Network|Graph)\.cs\b", "слое модели .digWater / Report.json", text)
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

    # 8. Исправляем подмену м вод. ст. на кПа и бар вида "-524.3 кПа (-5.24 бар)" или "(-5.24 бар)"
    text = re.sub(
        r"([-+]?\d+(?:[.,]\d+)?)\s*кПа\s*\(\s*[-+]?\d+(?:[.,]\d+)?\s*бар\s*\)",
        r"\1 м вод. ст.",
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
            "представляет собой потери воды в сети (физические утечки + коммерческие потери и несанкционированное потребление), "
            "а также собственные технологические нужды и повторную перекачку между ступенями подъема; "
            "РЧВ компенсируют лишь внутрисуточную часовую неравномерность (СП 31.13330.2012, п. 12.1–12.5)."
        ),
        text,
        flags=re.IGNORECASE,
    )

    return text


def validate_ai_report_against_facts(ai_text: str, facts: dict[str, Any]) -> dict[str, Any]:
    """Проверяет сгенерированный ИИ-отчёт на соответствие фактам модели, физическим инвариантам и СП 31.13330."""
    known_ids = collect_known_model_ids(facts)
    mentioned_ids = set(re.findall(r"(?:#|table:|map:)(\d{3,})\b", ai_text))

    valid_mentioned = mentioned_ids & known_ids if known_ids else mentioned_ids
    hallucinated_ids = sorted(mentioned_ids - known_ids) if known_ids else []
    id_precision = round(len(valid_mentioned) / len(mentioned_ids), 4) if mentioned_ids else 1.0

    issues: list[str] = []
    if hallucinated_ids:
        issues.append(f"Обнаружены отсутствующие в модели ID: {', '.join('#' + x for x in hallucinated_ids[:5])}")

    if re.search(r"\b(?:Node|Pipe|Pump)\.cs\b", ai_text):
        issues.append("Галлюцинация исходных файлов C# (.cs)")

    if re.search(r"\b(?:кПа|МПа)\b", ai_text):
        issues.append("Несанкционированный перевод единиц давления в кПа/МПа вместо м вод. ст.")

    if "Безопасная Эксплуатационная Подача" in ai_text:
        issues.append("Неверная расшифровка аббревиатуры BEP")

    sp_citations = re.findall(r"(?:СП\s*31\.13330|п\.\s*(?:4\.3|5\.11|5\.12|5\.13|7\.\d+|10\.3|10\.7|11\.5|11\.11|11\.21|11\.38|12\.1))", ai_text)

    return {
        "is_valid": len(issues) == 0,
        "id_precision": id_precision,
        "mentioned_ids_count": len(mentioned_ids),
        "hallucinated_ids": hallucinated_ids,
        "normative_citations_count": len(sp_citations),
        "normative_citations": sorted(set(sp_citations)),
        "issues": issues,
    }
