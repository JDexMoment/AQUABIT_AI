from __future__ import annotations

from pathlib import Path
import json

from aquabit_ai.v2_engine import (
    classify_roughness_score,
    process_aquabit_city_fast,
    read_aquabit_csv,
    repair_aquabit_header_tokens,
)
from aquabit_ai.v2_report_and_align import (
    build_dpo_rejected_variant,
    generate_reference_pure_facts_report,
    render_standalone_html_report,
    sanitize_pure_facts_response,
)


def test_repair_aquabit_comma_unit_headers(tmp_path: Path) -> None:
    # Real Dialect B headers from куляб / Алматы / модель семей / хорезмская
    qh_hdr = repair_aquabit_header_tokens("ID,Поток, м3/час,Давление, м", "u3381741,1250,60")
    assert len(qh_hdr) == 3
    assert qh_hdr[0] == "ID"
    assert "Поток" in qh_hdr[1] and "м3/час" in qh_hdr[1]
    assert "Давление" in qh_hdr[2] and "м" in qh_hdr[2]

    pipe_hdr = repair_aquabit_header_tokens(
        "ID,Начальный узел,Конечный узел,Диаметр, мм,Длина, м,Шероховатость,Малые потери",
        "e635441,c635441,c635441,426,43.42,80,0",
    )
    assert len(pipe_hdr) == 7

    f_csv = tmp_path / "Кривая QH.csv"
    f_csv.write_text("ID,Поток, м3/час,Давление, м\nu3381741,1250,60\nu3381742,630,90\n", encoding="utf-8")
    df = read_aquabit_csv(f_csv)
    assert list(df["ID"]) == ["u3381741", "u3381742"]
    assert float(df.iloc[0, 1]) == 1250.0
    assert float(df.iloc[0, 2]) == 60.0


def test_relative_roughness_score_scale() -> None:
    assert "хорошо" in classify_roughness_score(100.0)
    assert "норма" in classify_roughness_score(80.0)
    assert "ниже нормы" in classify_roughness_score(68.0)
    assert "ухудшенное" in classify_roughness_score(45.36)


def test_partial_export_like_almaty_and_html_report(tmp_path: Path) -> None:
    city = tmp_path / "Алматы_тест"
    city.mkdir()
    (city / "Узел.csv").write_text(
        "ID,Высотная отметка, м,Требуемое давление, м\nc1,700.0,18\nc2,710.0,\n", encoding="utf-8"
    )
    (city / "Напор Узел.csv").write_text("ID,R0,R1,R2\nc1,712.0,715.0,720.0\nc2,750.0,750.0,750.0\n", encoding="utf-8")
    (city / "Труба.csv").write_text(
        "ID,Начальный узел,Конечный узел,Диаметр, мм,Длина, м,Шероховатость,Малые потери\n"
        "e1,c1,c2,200,150.0,80,0\ne2,c2,c1,150,90.0,52.5,0\n",
        encoding="utf-8",
    )
    (city / "Насос.csv").write_text("ID,Начальный узел,Конечный узел,Кривая насоса\np1,c1,c2,u1\n", encoding="utf-8")
    (city / "Кривая QH.csv").write_text("ID,Поток, м3/час,Давление, м\nu1,1250,60\n", encoding="utf-8")
    (city / "Потребители Узел.csv").write_text("ID,Потребление, м3/час,График потребления\nc1,12.5,t1\nc2,0,t\n", encoding="utf-8")
    (city / "График потребления.csv").write_text("ID,Данные\n" + "\n".join(["t1,1.0"] * 24) + "\n", encoding="utf-8")

    out_clean = tmp_path / "cleaned"
    facts, charts = process_aquabit_city_fast(city, out_clean)

    # Free pressure on c1: H=[712, 715, 720] - Z=700 -> P_free=[12, 15, 20], required=18 (3 floors) -> deficit -6.0 m!
    ns = facts["nodes_summary"]
    assert ns["free_pressure_min_m"] == 12.0
    assert ns["deficit_nodes_below_required_count"] == 1
    assert ns["floors_distribution"] == {"3_эт": 1}

    ps = facts["pipes_summary"]
    assert ps["flow_timeseries_available"] is False
    assert ps["pipes_count"] == 2
    assert ps["degraded_roughness_below_78_pipes_count"] == 1
    assert len(charts["qh_curves"]) == 1
    assert charts["qh_curves"][0]["q_nominal_m3_h"] == 1250.0

    # Sanitizer strips greetings and <think> tags
    dirty = "<think>internal</think>\nКоллега, приветствую! Рассмотрим сеть.\n\n" + generate_reference_pure_facts_report(facts)
    clean = sanitize_pure_facts_response(dirty)
    assert clean.startswith("## 1.")
    assert "Коллега" not in clean

    rej = build_dpo_rejected_variant(facts, 0)
    assert "Коллега" in rej

    html_out = tmp_path / "report.html"
    doc = render_standalone_html_report(facts, charts, clean, output_html_path=html_out)
    assert html_out.exists()
    assert "Кривая QH" in doc
    assert "u1" in doc
