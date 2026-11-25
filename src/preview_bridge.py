"""CLI-мост на чистом Python 3 (stdlib) для интерактивного веб-предпросмотра AquaBit AI-модуля."""
from __future__ import annotations

import json
import struct
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import v2_engine as engine
from eval.metrics import evaluate_report_quality_metrics
from llm.post_validator import sanitize_ai_report_text, validate_ai_report_against_facts
from rag.retriever import QdrantNormativeRetriever, retrieve_norms_for_facts
from reporting.facts_report import generate_reference_pure_facts_report
from reporting.html_report import render_standalone_html_report


def _wkb_point(x: float, y: float) -> str:
    return (b"\x01" + struct.pack("<I", 1) + struct.pack("<dd", x, y)).hex().upper()


def _wkb_line(pts: list[tuple[float, float]]) -> str:
    body = b"\x01" + struct.pack("<II", 2, len(pts))
    for x, y in pts:
        body += struct.pack("<dd", x, y)
    return body.hex().upper()


def _build_demo_facts_and_charts() -> tuple[dict[str, Any], dict[str, Any]]:
    with tempfile.TemporaryDirectory() as td:
        city_dir = Path(td) / "Демо_Аквабит"
        city_dir.mkdir()

        dw_text = (
            "Model\x08Source\x1aType=Point\n"
            "ID\tName\tGeom\tHead\n"
            f"10\tВодозабор Северный\t{_wkb_point(0.0, 0.0)}\t82.0\n\n"
            "Model\x08Junction\x1aType=Point\n"
            "ID\tName\tGeom\tElevation\n"
            f"100\tУзел Низинный (ул. Набережная)\t{_wkb_point(100.0, 10.0)}\t0.0\n"
            f"101\tУзел Нагорный (ЖК Восток)\t{_wkb_point(300.0, 40.0)}\t28.0\n"
            f"102\tУзел Отсеченный (Тупик-3)\t{_wkb_point(450.0, 90.0)}\t35.0\n"
            f"103\tУзел Центральный\t{_wkb_point(200.0, 20.0)}\t15.0\n\n"
            "Model\x08Customer\x1aType=Point\n"
            "ID\tNode\tName\tFloors\tDemand\n"
            "501\t101\tЖК Восток корп. 2\t6\t42.0\n"
            "502\t102\tПромзона Тупик-3\t2\t15.0\n"
            "503\t103\tМикрорайон Центр\t5\t68.0\n\n"
            "Model\x08Pipe\x1aType=Line\n"
            "ID\tName\tGeom\tStart\tFinish\tDiam\tLen\tRough\n"
            f"201\tМагистраль Водовод-1\t{_wkb_line([(100.0, 10.0), (200.0, 20.0)])}\t100\t103\t300\t420\t110\n"
            f"202\tУзкий ввод на ЖК Восток\t{_wkb_line([(200.0, 20.0), (300.0, 40.0)])}\t103\t101\t100\t450\t2.0\n"
            f"203\tПерекрытая перемычка Тупик-3\t{_wkb_line([(300.0, 40.0), (450.0, 90.0)])}\t101\t102\t150\t300\t100\n\n"
            "Model\x08Pump\x1aType=Line\n"
            "ID\tName\tGeom\tStart\tFinish\tCurve\n"
            f"301\tНС-1 Основной агрегат №1\t{_wkb_line([(0.0, 0.0), (100.0, 10.0)])}\t10\t100\tCRV1\n"
            f"302\tНС-1 Резервный агрегат №2\t{_wkb_line([(0.0, 0.0), (100.0, 10.0)])}\t10\t100\tCRV1\n\n"
            "Model\x08Valve\x1aType=Line\n"
            "ID\tName\tGeom\tStart\tFinish\tType\tSetting\n"
            f"401\tРегулятор давления ПРД-1\t{_wkb_line([(100.0, 10.0), (200.0, 20.0)])}\t100\t103\tPRV\t45.0\n\n"
            "Model\x08Tank\x1aType=Point\n"
            "ID\tName\tGeom\tElevation\n"
            f"601\tРЧВ Западный 2000 м3\t{_wkb_point(50.0, 50.0)}\t52.0\n\n"
        )
        (city_dir / "model.digWater").write_bytes(dw_text.encode("cp1251"))

        hrs = list(range(24))
        p_supply = [round(180.0 + 45.0 * (1 if 7 <= h <= 21 else 0), 1) for h in hrs]
        dem_101 = [round(32.0 + 12.0 * (1 if 7 <= h <= 21 else 0), 1) for h in hrs]
        dem_103 = [round(55.0 + 22.0 * (1 if 7 <= h <= 21 else 0), 1) for h in hrs]

        report_json = {
            "reserv": [{"id": "Re10", "head": [82.0] * 24}],
            "tank": [{"id": "Ta601", "elev": 52.0, "level": [3.5 + 0.1 * (h % 6) for h in hrs]}],
            "junc": [
                {"id": "Ju100", "elev": 0.0, "req_press": 10.0, "head": [68.0 - 0.1 * h for h in hrs], "demand": [12.0] * 24},
                {"id": "Ju103", "elev": 15.0, "req_press": 26.0, "head": [51.0 - 0.1 * h for h in hrs], "demand": dem_103},
                {"id": "Ju101", "elev": 28.0, "req_press": 30.0, "head": [42.0 - 0.1 * h for h in hrs], "demand": dem_101},
                {"id": "Ju102", "elev": 35.0, "req_press": 14.0, "head": [-10.0] * 24, "demand": [15.0] * 24},
            ],
            "pipe": [
                {"id": "Pi201", "start": "Ju100", "finish": "Ju103", "diam": 300, "len": 420, "rough": 110, "flow": p_supply},
                {"id": "Pi202", "start": "Ju103", "finish": "Ju101", "diam": 100, "len": 450, "rough": 2.0, "flow": [round(q + 30.0, 1) for q in dem_101]},
                {"id": "Pi203", "start": "Ju101", "finish": "Ju102", "diam": 150, "len": 300, "rough": 100, "status": "Closed", "flow": [0.0] * 24},
            ],
            "pump": [
                {"id": "Pu301", "start": "Re10", "finish": "Ju100", "curve": "CRV1", "flow": p_supply},
                {"id": "Pu302", "start": "Re10", "finish": "Ju100", "curve": "CRV1", "flow": [0.0] * 24},
            ],
            "valv": [
                {"id": "Va401", "start": "Ju100", "finish": "Ju103", "type": "PRV", "set": 45.0, "flow": p_supply},
            ],
        }
        (city_dir / "Report.json").write_text(json.dumps(report_json, ensure_ascii=False), encoding="utf-8")

        facts, charts = engine.process_aquabit_city_v3(city_dir, timeline_buckets=24)
        retriever = QdrantNormativeRetriever(pdf_path=None, qdrant_path=None)
        facts["normative_rag"] = retrieve_norms_for_facts(facts, retriever=retriever, top_k=6)
        retriever.close()
        return facts, charts


def cmd_report() -> None:
    facts, charts = _build_demo_facts_and_charts()
    ai_text = sanitize_ai_report_text(generate_reference_pure_facts_report(facts))
    val = validate_ai_report_against_facts(ai_text, facts)
    with tempfile.TemporaryDirectory() as td:
        out_html = Path(td) / "report.html"
        render_standalone_html_report(facts, charts, val["corrected_text"], out_html)
        html_str = out_html.read_text(encoding="utf-8")
    metrics = evaluate_report_quality_metrics(val["corrected_text"], facts, rendered_html=html_str)
    print(json.dumps({
        "html": html_str,
        "data_source_type": facts.get("data_source_type"),
        "metrics": {
            "overall_engineering_score": metrics["overall_engineering_score"],
            "factual_id_precision": metrics["factual_id_precision"],
            "physical_invariant_compliance": metrics["physical_invariant_compliance"],
            "normative_grounding_score": metrics["normative_grounding_score"],
            "ui_link_integrity": metrics["ui_link_integrity"],
        },
        "rag_clauses": [c.get("clause_id") for c in (facts.get("normative_rag") or [])],
    }, ensure_ascii=False))


def cmd_cities() -> None:
    facts_dir = ROOT_DIR / "training" / "facts"
    items: list[dict[str, Any]] = []
    if facts_dir.exists():
        for fp in sorted(facts_dir.glob("facts_*.json")):
            try:
                data = json.loads(fp.read_text(encoding="utf-8"))
                ns = data.get("nodes_summary") or {}
                ps = data.get("pipes_summary") or {}
                pms = data.get("pumps_summary") or {}
                items.append({
                    "file": fp.name,
                    "city_name": data.get("city_name") or fp.stem.replace("facts_", ""),
                    "schema_version": data.get("schema_version", ""),
                    "consumer_nodes_count": ns.get("consumer_nodes_count", 0),
                    "total_daily_consumption_m3_day": ns.get("total_daily_consumption_m3_day", 0.0),
                    "deficit_nodes_count": ns.get("deficit_nodes_count", 0),
                    "excess_over_60m_count": ns.get("excess_pressure_over_60m_nodes_count", 0),
                    "pipes_count": ps.get("pipes_count", 0),
                    "total_length_km": ps.get("total_length_km", 0.0),
                    "total_pumps_declared": pms.get("total_pumps_declared", 0),
                    "active_pumps_count": pms.get("active_pumps_count", 0),
                    "standby_off_pumps_count": pms.get("standby_off_pumps_count", 0),
                    "data_quality_flags": data.get("data_quality_flags") or [],
                })
            except Exception:
                continue
    print(json.dumps({"cities": items}, ensure_ascii=False))


def cmd_rag(query: str, applies_to: str | None) -> None:
    retriever = QdrantNormativeRetriever(pdf_path=None, qdrant_path=None)
    hits = retriever.search(query or "свободный напор потери воды РЧВ", applies_to=(applies_to or None), top_k=5)
    retriever.close()
    print(json.dumps({"query": query, "applies_to": applies_to or "", "hits": hits}, ensure_ascii=False))


def cmd_validate(payload_json: str) -> None:
    payload = json.loads(payload_json or "{}")
    raw_text = str(payload.get("text") or "")
    facts, _charts = _build_demo_facts_and_charts()
    val = validate_ai_report_against_facts(raw_text, facts)
    metrics = evaluate_report_quality_metrics(val["corrected_text"], facts)
    print(json.dumps({
        "is_valid": val["is_valid"],
        "violations": val["violations"],
        "issues": val["issues"],
        "corrected_text": val["corrected_text"],
        "metrics": {
            "overall_engineering_score": metrics["overall_engineering_score"],
            "factual_id_precision": metrics["factual_id_precision"],
            "physical_invariant_compliance": metrics["physical_invariant_compliance"],
            "normative_grounding_score": metrics["normative_grounding_score"],
            "ui_link_integrity": metrics["ui_link_integrity"],
        },
    }, ensure_ascii=False))


def main() -> None:
    if len(sys.argv) < 2:
        print(json.dumps({"error": "missing command"}))
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "report":
        cmd_report()
    elif cmd == "cities":
        cmd_cities()
    elif cmd == "rag":
        q = sys.argv[2] if len(sys.argv) > 2 else ""
        ap = sys.argv[3] if len(sys.argv) > 3 else ""
        cmd_rag(q, ap or None)
    elif cmd == "validate":
        body = sys.stdin.read()
        cmd_validate(body)
    else:
        print(json.dumps({"error": f"unknown command: {cmd}"}))
        sys.exit(1)


if __name__ == "__main__":
    main()
