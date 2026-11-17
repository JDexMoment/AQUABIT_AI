import json
from pathlib import Path

def update_v3_notebook():
    nb_path = Path("EPANET_AI_V3.ipynb")
    nb = json.loads(nb_path.read_text(encoding="utf-8"))

    # Cell 1 (index 0)
    c1 = nb["cells"][0]
    source_c1 = "".join(c1["source"])
    if "ONTOLOGY_TTL_PATH" not in source_c1:
        source_c1 = source_c1.replace(
            'ONTOLOGY_PATH = RAW_DATA_DIR / "aquabit_hydraulic_ontology_v3.json"',
            'ONTOLOGY_PATH = RAW_DATA_DIR / "aquabit_hydraulic_ontology_v3.json"\nONTOLOGY_TTL_PATH = RAW_DATA_DIR / "aquabit_hydraulic_ontology_v3.ttl"'
        )
        source_c1 = source_c1.replace(
            'print("✅ Конфигурация EPANET-AI v3 (Ontology + Matplotlib Engine) загружена.")',
            'print("✅ Конфигурация EPANET-AI v3 (Гидравлическая онтология JSON/Turtle + Интерактивный HTML5 движок) загружена.")'
        )
        c1["source"] = [line + "\n" for line in source_c1.splitlines()]

    # Cell 2 (index 1)
    c2 = nb["cells"][1]
    source_c2 = "".join(c2["source"])
    if "render_standalone_html_report" not in source_c2:
        source_c2 = source_c2.replace(
            "from aquabit_ai.ontology_engine import (",
            "from aquabit_ai.v2_report_and_align import render_standalone_html_report, generate_reference_pure_facts_report\nfrom aquabit_ai.ontology_engine import ("
        )
        c2["source"] = [line + "\n" for line in source_c2.splitlines()]

    # Cell 3 (index 2) - replace with clean call to render_standalone_html_report
    c3 = nb["cells"][2]
    new_source_c3 = """# ==============================================================================
# КЛЕТКА 3. Генерация современного интерактивного HTML5-отчета ПК «Аквабит»
#           (динамический подбор дашбордов под проблемные области города,
#            аппаратная интерактивная карта сети, Canvas графики Q-H, табличные фильтры)
# ==============================================================================
from aquabit_ai.v2_report_and_align import render_standalone_html_report

def render_aquabit_html_v3(
    facts: dict[str, Any],
    chart_payload: dict[str, Any],
    ai_markdown: str,
    selected_dashboards_spec: dict[str, Any] | None,
    out_path: Path,
    gen_sec: float
) -> Path:
    \"\"\"Генерация автономного интерактивного инженерного отчета ПК «Аквабит».
    Особенности реализации:
    1. Интерактивная векторная карта сети на HTML5 Canvas с поддержкой аппаратного масштабирования и перемещения без задержек;
    2. Векторные графики Q-H насосов с динамическим выбором агрегата, зоной BEP (75-115%) и фактической точкой строго на линии напора;
    3. Клиентская фильтрация, сквозной поиск и сортировка инженерных реестров;
    4. Прямой переход по клику на ID любого объекта из графиков, таблиц и аналитики ИИ с автоматической подсветкой на карте;
    5. Аккуратный аккордеон «Анализ» под каждым графиком и таблицей с профессиональной интерпретацией в 3-5 предложениях.
    \"\"\"
    render_standalone_html_report(
        facts=facts,
        chart_payload=chart_payload,
        ai_markdown=ai_markdown,
        output_html_path=out_path,
        generation_meta={"model_name": "Qwen3.5-4B GGUF", "inference_sec": gen_sec}
    )
    return out_path
"""
    c3["source"] = [line + "\n" for line in new_source_c3.splitlines()]

    nb_path.write_text(json.dumps(nb, ensure_ascii=False, indent=1), encoding="utf-8")
    print("Updated EPANET_AI_V3.ipynb successfully.")

def update_pipeline_notebook():
    nb_path = Path("EPANET_AI_Pipeline.ipynb")
    nb = json.loads(nb_path.read_text(encoding="utf-8"))

    # Update cell 1 markdown if needed
    for cell in nb["cells"]:
        if cell["cell_type"] == "code":
            s = "".join(cell["source"])
            if "ONTOLOGY_PATH = RAW_DATA_DIR / 'aquabit_hydraulic_ontology_v3.json'" in s or 'ONTOLOGY_PATH = RAW_DATA_DIR / "aquabit_hydraulic_ontology_v3.json"' in s:
                s = s.replace(
                    'ONTOLOGY_PATH = RAW_DATA_DIR / "aquabit_hydraulic_ontology_v3.json"',
                    'ONTOLOGY_PATH = RAW_DATA_DIR / "aquabit_hydraulic_ontology_v3.json"\nONTOLOGY_TTL_PATH = RAW_DATA_DIR / "aquabit_hydraulic_ontology_v3.ttl"'
                )
                cell["source"] = [line + "\n" for line in s.splitlines()]
            if "assemble_adaptive_dashboard_html" in s and "PREVIEW_CITY" in s:
                # Update preview cell to use render_standalone_html_report
                new_s = """import datetime as dt
import html
import json
from pathlib import Path
from typing import Any
from IPython.display import display, HTML

from aquabit_ai.ontology_engine import (
    load_hydraulic_ontology,
    evaluate_ontology_rules,
    analyze_problem_domains,
    extract_ai_dashboards_and_content,
)
from aquabit_ai.v2_report_and_align import generate_reference_pure_facts_report, render_standalone_html_report

PREVIEW_CITY = next(iter(ALL_PROCESSED_FACTS.keys()), None)

if PREVIEW_CITY is not None:
    facts = ALL_PROCESSED_FACTS[PREVIEW_CITY]
    charts = ALL_CHART_PAYLOADS[PREVIEW_CITY]
    facts["chart_payload"] = charts

    print(f"📊 [1/3] Оценка онтологии (JSON/TTL) и правил для «{PREVIEW_CITY}»...")
    ontology = load_hydraulic_ontology()
    rules = evaluate_ontology_rules(facts, ontology)
    domains = analyze_problem_domains(facts, rules)
    print(f"   • Выявлена доминирующая проблемная зона: {domains['primary_domain']}")

    print(f"🧠 [2/3] Формирование адаптивного инженерного заключения...")
    ref_report_text = generate_reference_pure_facts_report(facts)
    clean_md, parsed_dashboards = extract_ai_dashboards_and_content(ref_report_text)

    # Формируем современный автономный интерактивный HTML-отчет (Canvas + интерактивная карта сети)
    print(f"📄 [3/3] Генерация интерактивного HTML-отчета ПК «Аквабит»...")
    preview_path = PREVIEW_REPORTS_DIR / f"EPANET_AI_DataPreview_{PREVIEW_CITY}.html"
    render_standalone_html_report(
        facts=facts,
        chart_payload=charts,
        ai_markdown=clean_md,
        output_html_path=preview_path,
        generation_meta={"model_name": "EPANET-AI Engine", "inference_sec": 0.05}
    )
    print(f"✅ Интерактивный отчет сохранен: {preview_path.resolve()}")
"""
                cell["source"] = [line + "\n" for line in new_s.splitlines()]

    nb_path.write_text(json.dumps(nb, ensure_ascii=False, indent=1), encoding="utf-8")
    print("Updated EPANET_AI_Pipeline.ipynb successfully.")

def update_training_notebook():
    nb_path = Path("EPANET_AI_Training.ipynb")
    nb = json.loads(nb_path.read_text(encoding="utf-8"))

    for cell in nb["cells"]:
        if cell["cell_type"] == "code":
            s = "".join(cell["source"])
            if "ONTOLOGY_PATH = RAW_DATA_DIR / 'aquabit_hydraulic_ontology_v3.json'" in s:
                s = s.replace(
                    "ONTOLOGY_PATH = RAW_DATA_DIR / 'aquabit_hydraulic_ontology_v3.json'",
                    "ONTOLOGY_PATH = RAW_DATA_DIR / 'aquabit_hydraulic_ontology_v3.json'\nONTOLOGY_TTL_PATH = RAW_DATA_DIR / 'aquabit_hydraulic_ontology_v3.ttl'"
                )
                cell["source"] = [line + "\n" for line in s.splitlines()]

            if "def render_standalone_html_report(" in s and "Compiles AI analysis + AquaBit hydraulic KPIs" in s:
                # Replace the giant inline duplicate with clean delegation
                new_s = """from aquabit_ai.v2_report_and_align import render_standalone_html_report, _markdown_to_clean_html
print("✅ Модуль интерактивной визуализации render_standalone_html_report подключен из aquabit_ai.v2_report_and_align.")
"""
                cell["source"] = [line + "\n" for line in new_s.splitlines()]

    nb_path.write_text(json.dumps(nb, ensure_ascii=False, indent=1), encoding="utf-8")
    print("Updated EPANET_AI_Training.ipynb successfully.")

if __name__ == "__main__":
    update_v3_notebook()
    update_pipeline_notebook()
    update_training_notebook()
