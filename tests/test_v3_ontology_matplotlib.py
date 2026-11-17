"""Тесты гидравлической онтологии, динамической структуры отчетов и пересчета графиков Matplotlib."""

import json
import os
import tempfile
from pathlib import Path

# Fix MPL config dir for headless test execution
if "MPLCONFIGDIR" not in os.environ:
    os.environ["MPLCONFIGDIR"] = os.path.join(tempfile.gettempdir(), "mpl_epanet_ai_test")

import pytest

from aquabit_ai.v2_engine import process_aquabit_city_v3
from aquabit_ai.ontology_engine import (
    load_hydraulic_ontology,
    evaluate_ontology_rules,
    analyze_problem_domains,
    build_ontology_ai_system_prompt,
    extract_ai_dashboards_and_content,
)
from aquabit_ai.hydraulic_matplotlib import (
    render_all_recalculated_charts,
    assemble_adaptive_dashboard_html,
    plot_pump_qh_curves,
    plot_diurnal_hydraulic_balance,
    plot_diurnal_pressure_corridor,
    plot_pressure_deficit_distribution,
    plot_pipe_bottlenecks_and_velocity,
    plot_low_velocity_stagnation_risk,
)
from aquabit_ai.v2_report_and_align import generate_reference_pure_facts_report, render_standalone_html_report


@pytest.fixture
def tenino_facts_and_charts():
    city_dir = Path("data/тенино")
    assert city_dir.exists(), "Папка data/тенино должна существовать для тестов"
    facts, charts = process_aquabit_city_v3(city_dir)
    facts["chart_payload"] = charts
    return facts, charts


def test_ontology_loading_and_rules():
    ontology = load_hydraulic_ontology("data/aquabit_hydraulic_ontology_v3.json")
    assert "terminology_and_style_constraints" in ontology
    assert "diagnostic_rules" in ontology
    assert len(ontology["diagnostic_rules"]) >= 8

    # Verify key forbidden terms
    forbidden = [x["forbidden"] for x in ontology["terminology_and_style_constraints"]["forbidden_terms_and_phrases"]]
    assert "вакуум" in forbidden
    assert "застой / застойные трубы" in forbidden


def test_evaluate_ontology_rules_on_real_city(tenino_facts_and_charts):
    facts, charts = tenino_facts_and_charts
    ontology = load_hydraulic_ontology()
    rules = evaluate_ontology_rules(facts, ontology)
    assert len(rules) > 0

    rule_ids = [r["rule_id"] for r in rules]
    # In Tenino there are pressure deficits and low velocity pipes
    assert "R03_REQUIRED_PRESSURE_DEFICIT" in rule_ids
    assert "R05_LOW_VELOCITY_PIPES" in rule_ids


def test_problem_domain_ranking(tenino_facts_and_charts):
    facts, charts = tenino_facts_and_charts
    ontology = load_hydraulic_ontology()
    rules = evaluate_ontology_rules(facts, ontology)
    domains = analyze_problem_domains(facts, rules)

    assert "ranked_domains" in domains
    assert len(domains["ranked_domains"]) > 0
    assert domains["primary_domain"] in ["hydraulic_bottlenecks", "pressure_deficit", "pump_imbalance", "water_stagnation"]


def test_matplotlib_charts_recalculation(tenino_facts_and_charts):
    facts, charts = tenino_facts_and_charts
    all_charts = render_all_recalculated_charts(facts)

    expected_charts = [
        "pump_qh",
        "diurnal_balance",
        "pressure_corridor",
        "pressure_deficit",
        "pipe_bottlenecks",
        "low_velocity_stagnation",
    ]
    for cid in expected_charts:
        assert cid in all_charts, f"График {cid} должен быть успешно построен через Matplotlib"
        assert all_charts[cid]["b64"].startswith("data:image/png;base64,")
        assert len(all_charts[cid]["b64"]) > 1000


def test_dynamic_report_generation_and_ai_selection(tenino_facts_and_charts):
    facts, charts = tenino_facts_and_charts
    report_text = generate_reference_pure_facts_report(facts)

    # Report must contain dynamic sections, not just fixed boilerplate
    assert "## 1." in report_text
    assert "[SELECTED_DASHBOARDS]" in report_text

    clean_md, parsed_dashboards = extract_ai_dashboards_and_content(report_text)
    assert len(parsed_dashboards["dashboards"]) >= 3
    assert "chart_notes" in parsed_dashboards

    # Test HTML dashboard assembly
    all_charts = render_all_recalculated_charts(facts)
    html_dashboards = assemble_adaptive_dashboard_html(all_charts, parsed_dashboards)
    assert "chart-card" in html_dashboards
    assert "Приоритет 1" in html_dashboards


def test_standalone_html_report_interactive_canvas(tenino_facts_and_charts, tmp_path):
    facts, charts = tenino_facts_and_charts
    ai_md = generate_reference_pure_facts_report(facts)
    out_file = tmp_path / "test_report.html"

    render_standalone_html_report(
        facts=facts,
        chart_payload=charts,
        ai_markdown=ai_md,
        output_html_path=out_file,
        generation_meta={"model_name": "Pytest Model", "inference_sec": 0.5}
    )

    assert out_file.exists()
    content = out_file.read_text(encoding="utf-8")
    assert "networkMap" in content
    assert "qhCanvas" in content
    assert "object-link" in content
    assert "focusMapObject" in content
    # No static matplotlib base64 PNG images in HTML report
    assert "data:image/png;base64," not in content
    # Clean titles and emojis stripped
    assert "⚠️" not in content
    assert "(Google)" not in content
    assert "(Хейзен-Вильямс)" not in content
    assert "ИИ отчет" in content
