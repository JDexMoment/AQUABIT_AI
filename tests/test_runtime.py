"""Дымовые тесты рабочего пайплайна без модели и реальных данных."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import ontology_engine as ontology
import v2_engine as engine
import v2_report_and_align as report


def test_csv_and_inp(tmp_path):
    csv = tmp_path / "nodes.csv"
    csv.write_text("ID,Имя,Отметка\nc1,Узел,100\n", encoding="utf-8")
    frame = engine.read_aquabit_csv(csv)
    assert len(frame) == 1
    assert engine.clean_object_id("c123") == "123"
    inp = tmp_path / "network.inp"
    inp.write_text("[COORDINATES]\n1 10 20\n[END]\n", encoding="utf-8")
    parsed = engine.parse_inp_metadata_and_topology(inp, {"c1"}, set(), set())
    assert isinstance(parsed["nodes"], dict)


def test_ontology_and_report(tmp_path):
    city = tmp_path / "city"
    city.mkdir()
    (city / "Узел.csv").write_text("ID,Имя,Отметка\nc1,Узел,100\n", encoding="utf-8")
    facts, charts = engine.process_aquabit_city_v3(city)
    onto = ontology.load_hydraulic_ontology(tmp_path / "missing.ttl")
    rules = ontology.evaluate_ontology_rules(facts, onto)
    domains = ontology.analyze_problem_domains(facts, rules, ontology=onto)
    assert "ranked_domains" in domains
    text = report.generate_reference_pure_facts_report(facts, ontology=onto)
    result = report.render_standalone_html_report(facts, charts, text, tmp_path / "report.html")
    assert "<html" in result.lower()
    assert (tmp_path / "report.html").exists()
    assert "Тест" in report._markdown_to_clean_html("**Тест**")


def test_public_exports():
    from processing.pipeline import process_aquabit_city_v3
    from ontology.diagnostics import evaluate_ontology_rules
    from reporting.html_report import render_standalone_html_report
    assert engine.process_aquabit_city_v3 is process_aquabit_city_v3
    assert ontology.evaluate_ontology_rules is evaluate_ontology_rules
    assert report.render_standalone_html_report is render_standalone_html_report
