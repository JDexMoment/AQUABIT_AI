import json
from pathlib import Path

nb_path = Path("EPANET_AI_V3.ipynb")
nb = json.loads(nb_path.read_text(encoding="utf-8"))

# Cell 0
cell0_source = """# ==============================================================================
# КЛЕТКА 1. Конфигурация и параметры инференса EPANET-AI v3
# ==============================================================================
%load_ext autoreload
%autoreload 2

import os
import sys
import json
import tempfile
from pathlib import Path

# Обеспечиваем доступ к модулям src/ и настраиваем рабочие пути
PROJECT_ROOT = Path.cwd()
sys.path.insert(0, str(PROJECT_ROOT / "src"))

RAW_DATA_DIR = PROJECT_ROOT / "data"
DATA_CLEANED_DIR = PROJECT_ROOT / "data_cleaned"
MODELS_DIR = PROJECT_ROOT / "training_epanet_ai" / "models"
REPORTS_DIR = PROJECT_ROOT / "reports"
ONTOLOGY_PATH = RAW_DATA_DIR / "aquabit_hydraulic_ontology_v3.json"
ONTOLOGY_TTL_PATH = RAW_DATA_DIR / "aquabit_hydraulic_ontology_v3.ttl"

for d in (DATA_CLEANED_DIR, MODELS_DIR, REPORTS_DIR):
    d.mkdir(parents=True, exist_ok=True)

# Настройки модели Qwen3.5-4B GGUF (оптимизировано под 6 ГБ VRAM)
GGUF_REPO = "unsloth/Qwen3.5-4B-GGUF"
GGUF_FILENAME = "Qwen3.5-4B-Q4_K_M.gguf"
LLAMA_BACKEND = "cuda"          # "cuda" или "cpu"
LLAMA_N_GPU_LAYERS = 20         # Число слоев для 6 ГБ VRAM
LLAMA_CONTEXT = 8192            # KV-кэш
INFERENCE_MAX_NEW_TOKENS = 2048 # Объем генерации отчета
LLAMA_SERVER_PATH = None

TARGET_CITY_OR_PATH = "модель семей"  # Или "тенино", "Орша", "Алматы", "куляб"
GEOMETRY_INP_PATH = None              # None = автопоиск *.inp в папке города

print("Конфигурация EPANET-AI v3 загружена. Выбранный расчетный объект:", TARGET_CITY_OR_PATH)
"""

# Cell 1
cell1_source = """# ==============================================================================
# КЛЕТКА 2. Аналитическое ядро Аквабит v3, онтология и генератор отчетов
# ==============================================================================
import html
import io
import math
import os
import re
import time
from typing import Any
import numpy as np
import pandas as pd

from aquabit_ai.v2_report_and_align import render_standalone_html_report, generate_reference_pure_facts_report
from aquabit_ai.ontology_engine import (
    load_hydraulic_ontology,
    evaluate_ontology_rules,
    analyze_problem_domains,
    build_ontology_ai_system_prompt,
    extract_ai_dashboards_and_content,
)
from aquabit_ai.v2_engine import (
    process_aquabit_city_v3,
    parse_inp_metadata_and_topology,
    read_aquabit_csv,
    find_col,
)

print("Аналитическое ядро Аквабит v3 и онтологический движок готовы к работе.")
"""

# Cell 2
cell2_source = """# ==============================================================================
# КЛЕТКА 3. Генерация современного интерактивного HTML5-отчета ПК «Аквабит»
#           (динамический подбор структуры, интерактивная карта сети,
#            векторные кривые Q-H, круговая диаграмма скоростей, табличные фильтры)
# ==============================================================================
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
    5. Аккуратный аккордеон «Анализ» под каждым графиком и таблицей с профессиональной интерпретацией в 3-5 предложениях;
    6. Круговая диаграмма скоростей (Donut Chart) и мульти-подсветка аномальных узлов.
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

# Cell 3
cell3_source = """# ==============================================================================
# КЛЕТКА 4. Запуск сквозного расчета: обработка данных, онтология и генерация отчета
# ==============================================================================
import socket
import subprocess
import tempfile
import time
import json
import re
import requests
from pathlib import Path
from typing import Any
from huggingface_hub import hf_hub_download
from IPython.display import display, HTML

def run_pipeline_v3(city_or_path: str | Path):
    t0 = time.perf_counter()
    ontology = load_hydraulic_ontology(ONTOLOGY_PATH)
    city_dir = Path(city_or_path) if Path(city_or_path).exists() else (RAW_DATA_DIR / str(city_or_path))
    inp_file = Path(GEOMETRY_INP_PATH) if GEOMETRY_INP_PATH else None

    print(f"[1/3] Обработка данных и оценка онтологических правил для «{city_dir.name}»...")
    facts, charts = process_aquabit_city_v3(city_dir, inp_path=inp_file)
    facts["chart_payload"] = charts

    triggered_rules = evaluate_ontology_rules(facts, ontology)
    problem_domains = analyze_problem_domains(facts, triggered_rules)

    print(f"Ключевая проблемная область сети: «{problem_domains['primary_domain']}»")
    for d in problem_domains["ranked_domains"]:
        print(f"   • [{d['score']:.1f} б.] {d['title']} ({d['details']})")

    # Формируем компактный контекст для ИИ без лишнего шума
    compact_context = {
        "city": facts["city_name"],
        "ranked_problem_domains": problem_domains["ranked_domains"],
        "activated_ontology_rules": triggered_rules,
        "unique_anomalies": facts.get("unique_model_anomalies", []),
        "water_balance": facts["water_balance"],
        "pressure_kpi": facts["pressure_analysis"]["pressure_bins"],
        "velocity_kpi": facts["pipes_analysis"]["velocity_bins"],
        "pumps_kpi": {
            "total_pumps": facts["pumps_analysis"]["total_pumps"],
            "active_pumps": facts["pumps_analysis"]["active_pumps_count"],
            "underloaded_count": len(facts["pumps_analysis"]["underloaded_pumps"]),
            "overloaded_count": len(facts["pumps_analysis"]["overloaded_pumps"])
        }
    }

    # 2. Инференс локальной модели или эталонный генератор фактов
    print("[2/3] Формирование аналитического заключения ИИ...")
    system_prompt = build_ontology_ai_system_prompt(ontology)
    user_prompt = f"Выполни глубокий технический аудит гидравлической модели города {facts['city_name']} на основе фактических данных:\\n" + json.dumps(compact_context, ensure_ascii=False, indent=2)

    raw_ai_text = ""
    gen_sec = 0.0

    # Проверяем локальный GGUF сервер (если скачан и доступен)
    exe_candidates = [
        Path(LLAMA_SERVER_PATH) if LLAMA_SERVER_PATH else None,
        MODELS_DIR / "llama-server.exe",
        PROJECT_ROOT / "llama-server.exe",
        PROJECT_ROOT / "tools" / "llama-server.exe"
    ]
    exe = next((p for p in exe_candidates if p and p.exists()), None)
    if exe:
        try:
            model_file = Path(
                hf_hub_download(repo_id=GGUF_REPO, filename=GGUF_FILENAME, local_dir=str(MODELS_DIR / "gguf" / GGUF_REPO.replace("/", "--")))
            )
            with socket.socket() as s:
                s.bind(("127.0.0.1", 0))
                port = s.getsockname()[1]
            base_url = f"http://127.0.0.1:{port}"
            ngl_val = LLAMA_N_GPU_LAYERS if LLAMA_BACKEND.lower() == "cuda" else 0

            with tempfile.TemporaryFile(mode="w+t", encoding="utf-8", errors="replace") as log_file:
                proc = subprocess.Popen(
                    [
                        str(exe), "-m", str(model_file),
                        "--host", "127.0.0.1", "--port", str(port),
                        "-c", str(LLAMA_CONTEXT),
                        "-ngl", str(ngl_val),
                        "--jinja"
                    ],
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                )
                try:
                    ready = False
                    for _ in range(120):
                        if proc.poll() is not None:
                            break
                        try:
                            if requests.get(base_url + "/health", timeout=1).status_code == 200:
                                ready = True
                                break
                        except requests.RequestException:
                            pass
                        time.sleep(1.0)

                    if ready:
                        t_llm = time.perf_counter()
                        http_resp = requests.post(
                            base_url + "/v1/chat/completions",
                            json={
                                "model": "local",
                                "messages": [
                                    {"role": "system", "content": system_prompt},
                                    {"role": "user", "content": user_prompt}
                                ],
                                "max_tokens": INFERENCE_MAX_NEW_TOKENS,
                                "temperature": 0.45,
                                "chat_template_kwargs": {"enable_thinking": False}
                            },
                            timeout=300
                        )
                        if http_resp.ok:
                            raw_ai_text = http_resp.json()["choices"][0]["message"]["content"]
                            gen_sec = round(time.perf_counter() - t_llm, 2)
                finally:
                    proc.terminate()
                    try: proc.wait(timeout=5)
                    except subprocess.TimeoutExpired: proc.kill()
        except Exception as e:
            print(f"Локальный llama-server недоступен ({e}), используется эталонный детерминированный генератор онтологии.")

    # Если локальный GGUF сервер не запущен, генерируем эталонный разбор на основе правил онтологии
    if not raw_ai_text:
        t_ref = time.perf_counter()
        raw_ai_text = generate_reference_pure_facts_report(facts)
        gen_sec = round(time.perf_counter() - t_ref, 2)

    # 3. Извлекаем решение ИИ по дашбордам и аналитический текст
    print("[3/3] Сборка интерактивного HTML5-паспорта сети...")
    main_md, parsed_dashboards = extract_ai_dashboards_and_content(raw_ai_text)
    selected_dashboards = [d["id"] for d in parsed_dashboards.get("dashboards", [])]
    print(f"ИИ определил ключевые приоритеты анализа: {selected_dashboards}")

    out_html = REPORTS_DIR / f"EPANET_AI_v3_{facts['city_name']}.html"
    render_aquabit_html_v3(facts, charts, main_md, parsed_dashboards, out_html, gen_sec)
    total_sec = round(time.perf_counter() - t0, 2)
    print(f"Отчет успешно сформирован за {total_sec} сек (ИИ: {gen_sec} сек).")
    print(f"Путь к HTML-отчету: {out_html.resolve()}")

    # Интерактивный предпросмотр прямо в выводе Jupyter Notebook
    rel_path = f"reports/{out_html.name}"
    display(HTML(f'''
    <div style="background:#f8fafc;border:1px solid #cbd5e1;border-radius:10px;padding:16px 20px;margin-top:14px;box-shadow:0 2px 8px rgba(0,0,0,0.04);font-family:sans-serif;">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;">
            <span style="font-weight:700;font-size:15px;color:#0f172a;">Интерактивный отчет ПК «Аквабит» ({facts['city_name']})</span>
            <a href="{rel_path}" target="_blank" style="display:inline-block;background:#0284c7;color:#fff;text-decoration:none;padding:7px 16px;border-radius:6px;font-weight:600;font-size:13px;">Открыть отчет в новой вкладке ↗</a>
        </div>
        <p style="margin:0 0 10px;font-size:13px;color:#64748b;">Ниже встроен интерактивный отчет (карта сети, векторные кривые Q-H, коридоры давлений, круговая диаграмма скоростей и таблицы). Кликните по ссылке выше, чтобы открыть на весь экран.</p>
        <iframe src="{rel_path}" style="width:100%;height:720px;border:1px solid #e2e8f0;border-radius:8px;background:#fff;" frameborder="0"></iframe>
    </div>
    '''))

# Запуск пайплайна
run_pipeline_v3(TARGET_CITY_OR_PATH)
"""

def split_lines(src):
    lines = src.split("\n")
    return [l + "\n" for l in lines[:-1]] + ([lines[-1]] if lines[-1] else [])

nb["cells"][0]["source"] = split_lines(cell0_source)
nb["cells"][1]["source"] = split_lines(cell1_source)
nb["cells"][2]["source"] = split_lines(cell2_source)
nb["cells"][3]["source"] = split_lines(cell3_source)

# Clear old execution outputs from notebook cells so it's clean and fresh
for c in nb["cells"]:
    if c.get("cell_type") == "code":
        c["outputs"] = []
        c["execution_count"] = None

nb_path.write_text(json.dumps(nb, ensure_ascii=False, indent=1), encoding="utf-8")
print("EPANET_AI_V3.ipynb успешно обновлен!")
