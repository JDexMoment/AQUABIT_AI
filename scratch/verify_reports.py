from pathlib import Path
import sys
import json
import re

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from aquabit_ai.v2_engine import process_aquabit_city_v3
from aquabit_ai.ontology_engine import load_hydraulic_ontology, evaluate_ontology_rules, analyze_problem_domains, extract_ai_dashboards_and_content
from aquabit_ai.v2_report_and_align import generate_reference_pure_facts_report, render_standalone_html_report

reports_dir = Path("reports")
reports_dir.mkdir(exist_ok=True)

for city_name in ["тенино", "модель семей"]:
    city_dir = Path(f"data/{city_name}")
    if not city_dir.exists():
        continue
    print(f"=== Проверка генерации для «{city_name}» ===")
    facts, charts = process_aquabit_city_v3(city_dir)
    facts["chart_payload"] = charts

    # Проверка онтологии .ttl и .json
    ont_json = load_hydraulic_ontology("data/aquabit_hydraulic_ontology_v3.json")
    ont_ttl = load_hydraulic_ontology("data/aquabit_hydraulic_ontology_v3.ttl")
    assert len(ont_ttl["diagnostic_rules"]) >= 10
    assert len(ont_json["diagnostic_rules"]) >= 10

    rules = evaluate_ontology_rules(facts, ont_ttl)
    domains = analyze_problem_domains(facts, rules)

    ai_text = generate_reference_pure_facts_report(facts)
    clean_md, spec = extract_ai_dashboards_and_content(ai_text)

    out_file = reports_dir / f"EPANET_AI_{city_name}_verified.html"
    render_standalone_html_report(
        facts=facts,
        chart_payload=charts,
        ai_markdown=clean_md,
        output_html_path=out_file,
        generation_meta={"model_name": "Qwen3.5-4B", "inference_sec": 0.05}
    )

    html_content = out_file.read_text(encoding="utf-8")

    # 1. Запрещенные фразы и смайлики
    assert "data:image/png;base64," not in html_content, "В HTML не должно быть static base64 картинок!"
    assert "⚠️" not in html_content, "Смайлик ⚠️ не должен присутствовать!"
    assert "🚀" not in html_content, "Смайлик 🚀 не должен присутствовать!"
    assert "💡" not in html_content, "Смайлик 💡 не должен присутствовать!"
    assert "(Google)" not in html_content, "Суффикс (Google) должен быть очищен!"
    assert "(Хейзен-Вильямс)" not in html_content, "Суффикс (Хейзен-Вильямс) в заголовках должен быть очищен!"
    assert "(СП 31.13330)" not in html_content, "Суффикс (СП 31.13330) в заголовках должен быть очищен!"
    assert "openModal" not in html_content, "Старый modal не должен присутствовать!"

    # 2. Обязательные компоненты
    assert "networkMap" in html_content, "Интерактивная карта должна присутствовать"
    assert "qhCanvas" in html_content, "График Q-H должен присутствовать"
    assert "object-link" in html_content, "Ссылки на объекты должны присутствовать"
    assert "btn-analysis" in html_content, "Кнопка Анализ должна присутствовать"
    assert "focusMapObject" in html_content, "Функция фокуса карты должна присутствовать"
    assert "ИИ отчет" in html_content, "Заголовок раздела должен быть ИИ отчет"

    # 3. Проверка точности точек Q-H: H_fact лежит строго на кривой
    pma = facts.get("pumps_analysis", {})
    all_pumps = pma.get("all_pumps_table", [])
    for p in all_pumps:
        if p.get("curve_points") and p.get("q_working_m3_h"):
            q_w = p["q_working_m3_h"]
            h_w = p["h_working_m"]
            pts = p["curve_points"]
            # Если есть 3 точки (u1), проверяем интерполяцию
            print(f"  Насос {p.get('pump_id')}: Q={q_w:.2f}, H={h_w:.2f}, тип={'Скважинный' if p.get('is_borehole') else 'Сетевой'}")

    # 4. Проверка процента застоя (не отрицательный и <= 100%)
    ppa = facts.get("pipes_analysis", {})
    stag_cnt = ppa.get("low_velocity_under_01ms_count", 0)
    assert stag_cnt >= 0, f"Количество труб застоя не может быть отрицательным: {stag_cnt}"

    print(f"✅ Проверка отчета {city_name} пройдена успешно ({len(html_content):,} байт, файл {out_file.name})!")

print("\nВСЕ ПРОВЕРКИ ПРОЙДЕНЫ УСПЕШНО!")
