"""Комплексные тесты пайплайна JSON + .DigWater, RAG-слоя СП 31.13330 в Qdrant,
анти-галлюцинационного пост-валидатора и рендеринга HTML-отчёта."""
from __future__ import annotations
import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import ontology_engine as ontology
import v2_engine as engine
import v2_report_and_align as report
from eval.metrics import evaluate_report_quality_metrics
from llm.post_validator import sanitize_ai_report_text, validate_ai_report_against_facts
from rag.retriever import QdrantNormativeRetriever, retrieve_norms_for_facts


def test_report_json_repairs_bare_id_and_unescaped_quotes(tmp_path: Path) -> None:
    raw_json = (
        '{\n'
        '  "junc": [\n'
        '    {\n'
        '      "id": "Ju3459771",\n'
        '      "name": "Узел ТОО "Водоканал" Центральный",\n'
        '      "head": [214.65, 215.0]\n'
        '    }\n'
        '  ],\n'
        '  "valv": [\n'
        '    {\n'
        '      "id": Va1286,\n'
        '      "name": "Задвижка №1",\n'
        '      "flow": [0.0, 0.0]\n'
        '    }\n'
        '  ]\n'
        '}\n'
    )
    json_path = tmp_path / "Report.json"
    json_path.write_text(raw_json, encoding="utf-8")

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        data = engine.load_report_json(json_path)
        assert len(caught) >= 2

    assert data["junc"][0]["id"] == "Ju3459771"
    assert 'ТОО "Водоканал"' in data["junc"][0]["name"]
    assert data["valv"][0]["id"] == "Va1286"

    norm_objs = list(engine.iter_normalized_report_objects(json_path))
    assert len(norm_objs) == 2
    assert norm_objs[0]["clean_id"] == "3459771"
    assert norm_objs[0]["time_series"]["head"] == [214.65, 215.0]
    assert norm_objs[1]["clean_id"] == "1286"


def test_digwater_parsing_and_json_end_to_end_pipeline(tmp_path: Path) -> None:
    city_dir = tmp_path / "модель семей"
    city_dir.mkdir()

    # Формируем файл .digWater по спецификации (\t, \x1a, \x08, кодировка cp1251)
    dw_text = (
        "Version=1.0\n"
        "ModelName=Семей Тест\n"
        "\n"
        "Model=Гидравлическая модель\x1aVisible=1\n"
        "\n"
        "Model\x08Reservoir\x1aType=Point\tName\tHead\tX\tY\n"
        "9001\tВодозабор 1-й подъем\t230.0\t100.0\t200.0\n"
        "\n"
        "Model\x08Tank\x1aType=Point\tName\tHead\tX\tY\n"
        "9002\tРЧВ Центральный\t225.0\t120.0\t210.0\n"
        "\n"
        "Model\x08Junction\x1aType=Point\tName\tElev\tReqPress\tFloors\tDemand\tX\tY\n"
        "3459771\tмкр. Южный д.12\t210.0\t22.0\t4\t15.0\t150.0\t250.0\n"
        "3460120\tУзел DDA изоляция\t260.0\t14.0\t2\t5.0\t160.0\t260.0\n"
        "3460500\tКоллектор ВНС\t200.0\t10.0\t1\t0.0\t110.0\t205.0\n"
        "\n"
        "Model\x08Pipe\x1aType=Line\tName\tStart\tFinish\tDiam\tLen\tRough\tStatus\n"
        "3466703\tВставка у камеры\t3460500\t3459771\t100\t8.4\t2.0\tOPEN\n"
        "3465902\tМагистраль Южная\t3460500\t3459771\t150\t800.0\t110.0\tOPEN\n"
        "3465999\tСекционная перемычка\t3459771\t3460120\t200\t250.0\t120.0\tCLOSED\n"
        "3466000\tТупиковый отвод\t3459771\t3460120\t150\t180.0\t120.0\tOPEN\n"
        "\n"
        "Model\x08Pump\x1aType=Line\tName\tStart\tFinish\tCurve\tQnom\tHnom\tStatus\n"
        "3469091\tНасос ВНС-1 Рабочий\t9001\t3460500\t801\t500.0\t42.0\tOPEN\n"
        "3469087\tНасос ВНС-1 Резерв\t9001\t3460500\t801\t500.0\t42.0\tCLOSED\n"
        "\n"
    )
    dw_path = city_dir / "Semey.digWater"
    dw_path.write_bytes(dw_text.encode("cp1251"))

    # Проверим прямое чтение имен из .digWater
    names = engine.read_object_names(dw_path)
    assert names["3459771"] == "мкр. Южный д.12"
    assert names["3466703"] == "Вставка у камеры"

    # Формируем Report.json с 24 часовыми шагами
    # Узел 3459771: Z=210, H=214.65 -> P_своб = +4.65 м > 0 (дефицит до P_треб=22.0 м равен 17.35 м, НЕ вакуум!)
    # Узел 3460120: Z=260, H=206.5 -> P_своб = -53.5 м (артефакт DDA!)
    report_json = (
        '{\n'
        '  "reserv": [\n'
        '    {"id": "Re9001", "head": ' + str([230.0] * 24) + '}\n'
        '  ],\n'
        '  "tank": [\n'
        '    {"id": "Ta9002", "head": ' + str([225.0] * 24) + '}\n'
        '  ],\n'
        '  "junc": [\n'
        '    {"id": "Ju3459771", "head": ' + str([214.65] * 24) + ', "demand": ' + str([120.0] * 24) + '},\n'
        '    {"id": "Ju3460120", "head": ' + str([206.5] * 24) + ', "demand": ' + str([10.0] * 24) + '},\n'
        '    {"id": "Ju3460500", "head": ' + str([238.0] * 24) + ', "demand": ' + str([0.0] * 24) + '}\n'
        '  ],\n'
        '  "pipe": [\n'
        '    {"id": "Pi3466703", "flow": ' + str([45.0] * 24) + '},\n'
        '    {"id": "Pi3465902", "flow": ' + str([180.0] * 24) + '},\n'
        '    {"id": "Pi3465999", "flow": ' + str([0.0] * 24) + '},\n'
        '    {"id": "Pi3466000", "flow": ' + str([0.0] * 24) + '}\n'
        '  ],\n'
        '  "pump": [\n'
        '    {"id": "Pu3469091", "flow": ' + str([500.0] * 24) + '},\n'
        '    {"id": "Pu3469087", "flow": ' + str([0.0] * 24) + '}\n'
        '  ]\n'
        '}\n'
    )
    (city_dir / "Report.json").write_text(report_json, encoding="utf-8")

    facts, charts = engine.process_aquabit_city_v3(city_dir, timeline_buckets=24)

    # 1. Проверяем, что данные прочитаны из JSON и .DigWater
    assert facts["data_source_type"] == "json_digwater"
    assert facts["has_inp_names"] is True

    # 2. Проверяем разделение дефицита этажности (P_своб > 0) и вакуума / артефакта DDA (P_своб < -3 м)
    pa = facts["pressure_analysis"]
    node_3459771 = next(n for n in pa["worst_deficit_nodes"] if n["clean_id"] == "3459771")
    assert node_3459771["free_pressure_min_m"] == 4.65
    assert node_3459771["required_pressure_m"] == 22.0
    assert node_3459771["pressure_deficit_abs_m"] == 17.35
    assert node_3459771["is_vacuum"] is False
    assert "положительном" in node_3459771["diagnostic_advice"]
    assert "СП 31.13330 п. 5.11" in node_3459771["diagnostic_advice"]

    node_3460120 = next(n for n in pa["worst_data_error_nodes"] if n["clean_id"] == "3460120")
    assert node_3460120["free_pressure_min_m"] == -53.5
    assert node_3460120["is_vacuum"] is True
    assert "артефакт" in node_3460120["diagnostic_advice"].lower()

    # 3. Проверяем трубы: абсолютная потеря Δh = i * L / 1000, аномальный C=2.0, задвижка vs тупик Q=0
    ppa = facts["pipes_analysis"]
    assert ppa["anomalous_low_c_pipes_count"] == 1
    assert ppa["closed_by_valve_pipes_count"] == 1
    assert ppa["zero_flow_open_pipes_count"] == 1

    pipe_low_c = next(p for p in ppa["top_high_loss_pipes"] if p["clean_id"] == "3466703")
    assert pipe_low_c["is_anomalous_low_c"] is True
    assert pipe_low_c["total_headloss_m"] == round(pipe_low_c["headloss_gradient_m_km"] * 8.4 / 1000.0, 2)
    assert "опечатка ввода данных" in pipe_low_c["diagnostic_advice"]

    # 4. Проверяем насосы: холодный резерв по СП 31.13330 п. 10.3 (Таблица 23)
    pma = facts["pumps_analysis"]
    pump_reserve = next(p for p in pma["all_pumps_table"] if p["clean_id"] == "3469087")
    assert "резерв" in pump_reserve["regime"].lower()
    assert "Таблица 23" in pump_reserve["diagnostic_advice"]

    # 5. Проверяем суточный баланс и потери воды (физические + коммерческие потери)
    wb = facts["water_balance"]
    assert wb["first_lift_daily_supply_m3_day"] > wb["total_daily_consumption_m3_day"]
    assert wb["estimated_water_losses_and_unbilled_m3_day"] > 0

    # 6. Проверяем RAG-слой Qdrant по СП 31.13330
    onto = ontology.load_hydraulic_ontology(tmp_path / "missing.ttl")
    rules = ontology.evaluate_ontology_rules(facts, onto)
    rag_clauses = retrieve_norms_for_facts(facts, rules)
    clause_ids = {c["clause_id"] for c in rag_clauses}
    assert "п. 5.11" in clause_ids
    assert "п. 10.1–10.3 (Таблица 23)" in clause_ids

    # 7. Проверяем генерацию референсного отчёта, санитизацию и HTML-рендеринг
    ref_text = report.generate_reference_pure_facts_report(facts, ontology=onto)
    assert "Нормативная база (RAG-слой Qdrant по СП 31.13330)" in ref_text
    assert "физические утечки" in ref_text.lower() or "потери воды" in ref_text.lower()

    html_out = report.render_standalone_html_report(facts, charts, ref_text, tmp_path / "audit.html")
    assert "<button><button>" not in html_out
    assert "&lt;br&gt;" not in html_out
    assert "физические потери (утечки, аварии) и коммерческие потери" in html_out
    assert "exportCorrectionsForSFT" in html_out


def test_post_validator_and_markdown_link_rendering() -> None:
    raw_llm_text = (
        "Узел `[3467991](table:node_3467991)` и `Ju3459771` имеют Безопасная Эксплуатационная Подача "
        "и давление 46.5 кПа (4.65 бар) в файле `Node.cs`.\n"
        "Врезка <button class=\"obj-link\" onclick=\"scrollToTableRow('node','3459771')\">#3459771</button>."
    )
    sanitized = sanitize_ai_report_text(raw_llm_text)
    assert "Безопасная Эксплуатационная Подача" not in sanitized
    assert "Best Efficiency Point" in sanitized
    assert "Node.cs" not in sanitized

    rendered = report._markdown_to_clean_html(sanitized)
    assert "<button><button" not in rendered
    assert "node_3467991" not in rendered
    assert "scrollToTableRow('node', '3467991')" in rendered

    dummy_facts = {
        "pressure_analysis": {
            "top_critical_nodes": [
                {"clean_id": "3459771", "pressure_min_m": 4.65, "required_pressure_m": 22.0}
            ]
        },
        "pipes_analysis": {"top_high_velocity_pipes": []},
        "pumps_analysis": {"pumps": []},
    }
    bad_vacuum_claim = "В узле #3459771 наблюдается вакуум и кавитация в магистрали (P = 4.65 м)."
    val_res = validate_ai_report_against_facts(bad_vacuum_claim, dummy_facts)
    assert any(v["type"] == "FALSE_VACUUM_ON_POSITIVE_PRESSURE" for v in val_res["violations"])
    assert "Примечание верификатора" in val_res["corrected_text"]

    metrics = evaluate_report_quality_metrics(val_res["corrected_text"], dummy_facts, rendered)
    assert metrics["ui_link_integrity"] == 1.0


def test_real_aquabit_short_json_keys_and_no_csv_read(tmp_path: Path) -> None:
    """Проверяет чтение коротких ключей Report.json (elev, start, finish, diam, len, rough),
    нулевую отметку Z=0.0, отсутствие demand, и что CSV/.inp не читаются при наличии JSON + .digWater."""
    city_dir = tmp_path / "short_keys_city"
    city_dir.mkdir()

    # Подкладываем битый CSV и битый INP, чтобы убедиться, что пайплайн их игнорирует
    (city_dir / "Напор Узел.csv").write_bytes(b"\xff\xfeBROKEN_CSV_SHOULD_NOT_BE_READ")
    (city_dir / "legacy.inp").write_text("[JUNCTIONS]\n99999 999\n", encoding="utf-8")

    # Двухстрочный CGLayer + слой Customer + коллизия числовых ID (узел 100 и труба 100) + WKB в столбце Geom
    import struct
    wkb_point = struct.pack("<BIdd", 1, 1, 55.5, 66.5).hex()
    dw_text = (
        "Version=1.0\n\n"
        "Model\x08Junction\x1aType=Point\n"
        "ID\tName\tElev\tGeom\n"
        f"100\tУзел Низинный\t0.0\t{wkb_point}\n"
        "101\tУзел Высотный\t25.0\t\n\n"
        "Model\x08Customer\x1aType=Point\tNodeID\tName\tFloors\tDemand\n"
        "501\t101\tШкола №7\t5\t18.0\n\n"
        "Model\x08Pipe\x1aType=Line\n"
        "Код\tName\tStart\tFinish\tDiam\tLen\tRough\n"
        "100\tМагистраль Восточная\t100\t101\t150\t400\t100\n\n"
    )
    (city_dir / "model.digWater").write_bytes(dw_text.encode("cp1251"))

    # Report.json с короткими ключами, инлайн bare ID и без поля demand
    report_json = (
        '{\n'
        '  "junc": [\n'
        '    {"id": Ju100, "elev": 0.0, "req_press": 10.0, "head": [72.0, 70.0, 68.0]},\n'
        '    {"id": "Ju101", "elev": 25.0, "head": [42.0, 41.0, 40.0]}\n'
        '  ],\n'
        '  "pipe": [\n'
        '    {"id": "Pi100", "start": "Ju100", "finish": "Ju101", "diam": 150, "len": 400, "rough": 100, "flow": [95.0, 90.0, 85.0]}\n'
        '  ]\n'
        '}\n'
    )
    (city_dir / "Report.json").write_text(report_json, encoding="utf-8")

    dw_meta = engine.parse_digwater_metadata_and_topology(city_dir / "model.digWater")
    assert dw_meta["nodes"]["100"] == [55.5, 66.5]
    assert dw_meta["node_attrs"]["100"]["elevation_m"] == 0.0
    # Customer слился в узел 101 (этажность 5, расход 18.0)
    assert dw_meta["node_attrs"]["101"]["floors"] == 5
    assert dw_meta["node_attrs"]["101"]["base_demand_m3_h"] == 18.0
    # Коллизия ID=100 между Junction и Pipe изолирована префиксами Ju100 / Pi100
    assert dw_meta["names"]["Ju100"] == "Узел Низинный"
    assert dw_meta["names"]["Pi100"] == "Магистраль Восточная"

    facts, charts = engine.process_aquabit_city_v3(city_dir, timeline_buckets=24)
    assert facts["data_source_type"] == "json_digwater"
    # Узел 100: elev=0.0, head=72 -> P_max=72 > 60 м (избыток по СП 31.13330 п. 5.13)
    assert facts["pressure_analysis"]["excess_over_60m_count"] == 1
    # Узел 101: elev=25, floors=5 (P_req=26), head=40 -> P_min=15 -> дефицит 11 м
    assert facts["pressure_analysis"]["real_required_pressure_deficit_count"] == 1
    # Битый legacy.inp не подмешал узел 99999
    assert "99999" not in charts["node_props"]


def test_rag_chunker_dehyphenation_and_retriever_filtering(tmp_path: Path) -> None:
    from rag.chunker import extract_sp31_pdf_chunks, parse_sp31_text_to_chunks

    raw_doc = (
        "СП 31.13330.2012\n"
        "42\n"
        "5 Расчетные расходы воды и свободные напоры\n"
        "5.11. Минимальный свободный напор в сети водо-\n"
        "снабжения населенного пункта при максимальном хозяйственно-питьевом водопотреблении на вводе в здание над поверхностью земли должен приниматься при одноэтажной застройке не менее 10 м.\n"
        "Таблица 15 Нормативы напоров по этажности.\n"
        "10.99. Специальный тестовый пункт по насосным станциям и регулированию подачи агрегатов.\n"
    )
    chunks = parse_sp31_text_to_chunks(raw_doc)
    by_id = {c["clause_id"]: c for c in chunks}
    assert "п. 5.11" in by_id
    assert "водоснабжения" in by_id["п. 5.11"]["text"]
    assert "Таблица 15" in by_id["п. 5.11"]["text"]
    assert "п. 10.99" in by_id

    txt_path = tmp_path / "SP_31.13330.2012.txt"
    txt_path.write_text(raw_doc, encoding="utf-8")
    merged = extract_sp31_pdf_chunks(txt_path)
    merged_ids = {c["clause_id"] for c in merged}
    assert "п. 5.11" in merged_ids
    assert "п. 10.99" in merged_ids

    ret = QdrantNormativeRetriever(pdf_path=txt_path)
    pump_hits = ret.search("резервные насосы Таблица 23", applies_to="pump", top_k=2)
    assert pump_hits and all("pump" in h["Applies_to"] for h in pump_hits)
    ret.close()


def test_post_validator_physical_invariants_and_self_correction() -> None:
    from llm.post_validator import build_self_correction_prompt

    facts = {
        "pressure_analysis": {
            "worst_deficit_nodes": [
                {"clean_id": "3459771", "free_pressure_min_m": 4.65, "required_pressure_m": 22.0}
            ]
        },
        "pumps_analysis": {
            "all_pumps_table": [
                {"clean_id": "3469087", "active": False, "q_working_m3_h": 0.0}
            ]
        },
    }
    bad_report = (
        "В узле #3459771 возник вакуум. "
        "В узле #999888 зафиксирован физический вакуум -53.5 м вод. ст. "
        "Для снижения напора рекомендуется прикрыть задвижку на всасывании насоса и установить PRV перед насосом. "
        "Насос #3469087 неисправен и вышел из строя. "
        "Разница между подачей насосов и потреблением сети аккумулируется в резервуарах чистой воды (РЧВ) и компенсирует часы максимального водоразбора."
    )
    res = validate_ai_report_against_facts(bad_report, facts)
    v_types = {v["type"] for v in res["violations"]}
    assert "FALSE_VACUUM_ON_POSITIVE_PRESSURE" in v_types
    assert "IMPOSSIBLE_PHYSICAL_VACUUM" in v_types
    assert "SUCTION_THROTTLING_VIOLATION" in v_types
    assert "FALSE_RESERVE_PUMP_FAULT" in v_types
    assert "RCHV_WATER_LOSS_ERROR" in v_types
    assert "HALLUCINATED_OBJECT_ID" in v_types
    assert "Примечание верификатора" in res["corrected_text"]
    assert "потери воды в сети" in res["corrected_text"]

    fix_prompt = build_self_correction_prompt(bad_report, res)
    assert "Исправь ТОЛЬКО" in fix_prompt

    # Метрика должна штрафовать битый HTML с вложенными кнопками и &lt;br&gt;
    bad_html_metrics = evaluate_report_quality_metrics(
        res["corrected_text"],
        facts,
        rendered_html="<button><button>#3459771</button></button> &lt;br&gt; node_3459771",
    )
    assert bad_html_metrics["ui_link_integrity"] < 1.0


def test_hydraulic_matplotlib_logger_and_diurnal_wording() -> None:
    src_text = (Path(__file__).resolve().parents[1] / "src" / "hydraulic_matplotlib.py").read_text(encoding="utf-8")
    assert "logger = logging.getLogger(__name__)" in src_text
    assert "потери воды" in src_text
    assert "Аккумуляция в РЧВ (+)" not in src_text

