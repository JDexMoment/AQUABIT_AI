"""Основной сценарий вычисления показателей и данных графиков города (JSON + .DigWater с резервным CSV/.inp)."""
from __future__ import annotations
from collections import deque
import math
import time
import unicodedata
from pathlib import Path
from typing import Any

from processing.csv_reader import read_aquabit_csv, find_col, _resolve_file_ci, to_numeric_series
from processing.identifiers import clean_object_id, clean_object_name
from processing.curves import build_qh_curve_plot_points, evaluate_qh_curve_head
from processing.timeseries import _stream_wide_timeseries, build_timeseries_from_json_series
from processing.topology import parse_inp_metadata_and_topology
from processing.digwater import parse_digwater_metadata_and_topology
from processing.report_json import (
    iter_report_objects,
    iter_normalized_report_objects,
    find_city_report_json,
    find_city_digwater,
    _to_float,
)

VALVE_TYPE_DESC: dict[str, str] = {
    "PRV": "Редукционный клапан (снижения давления «после себя»)",
    "PSV": "Клапан поддержания давления («до себя»)",
    "PBV": "Клапан гашения перепада давления",
    "FCV": "Регулятор-ограничитель расхода",
    "TCV": "Дросселирующий клапан",
    "GPV": "Клапан общего назначения",
}


def _safe_float(val: Any, default: float = math.nan) -> float:
    try:
        if val is None or val == "":
            return default
        if isinstance(val, (int, float)):
            return float(val)
        s = str(val).strip().replace(",", ".")
        return float(s) if s else default
    except (ValueError, TypeError):
        return default


def _first_present(*vals: Any) -> Any:
    """Возвращает первое непустое значение (не пропускает 0 и 0.0 в отличие от `or`)."""
    for v in vals:
        if v is not None and v != "":
            return v
    return None


def _preserve_series(raw_arr: list[Any]) -> list[float | None]:
    out: list[float | None] = []
    for x in raw_arr:
        fv = _to_float(x)
        out.append(fv)
    return out


def _ingest_from_json_and_digwater(
    json_path: Path,
    digwater_meta: dict[str, Any],
    timeline_buckets: int = 48,
) -> dict[str, Any]:
    """Читает все объекты и временные ряды из Report.json, обогащая их метаданными из .DigWater."""
    elev_map: dict[str, float] = {}
    req_p_map: dict[str, float] = {}
    floors_map: dict[str, int] = {}
    coords_map: dict[str, list[float]] = dict(digwater_meta.get("nodes") or {})
    base_demand: dict[str, float] = {}
    node_schedules: dict[str, list[str]] = {}

    sources_list: list[dict[str, Any]] = []
    source_head_by_cid: dict[str, float] = {}
    source_ids_set: set[str] = set()
    tanks_list: list[dict[str, Any]] = []
    tank_ids_set: set[str] = set()

    raw_pipes: list[dict[str, Any]] = []
    raw_pumps: list[dict[str, Any]] = []
    raw_valves: list[dict[str, Any]] = []
    node_ids_set: set[str] = set()
    pipe_ids_set: set[str] = set()
    pump_ids_set: set[str] = set()

    node_free_head_series: dict[str, list[float | None]] = {}
    node_total_head_series: dict[str, list[float | None]] = {}
    node_demand_series: dict[str, list[float | None]] = {}
    pipe_flow_series: dict[str, list[float | None]] = {}
    pump_flow_series: dict[str, list[float | None]] = {}
    valve_flow_series: dict[str, list[float | None]] = {}

    obj_names = dict(digwater_meta.get("names") or {})
    dw_node_attrs = digwater_meta.get("node_attrs") or {}
    dw_pipe_attrs = digwater_meta.get("pipe_attrs") or {}
    dw_pump_attrs = digwater_meta.get("pump_attrs") or {}
    dw_valve_attrs = digwater_meta.get("valve_attrs") or {}
    dw_res_attrs = digwater_meta.get("reservoir_attrs") or {}
    dw_tank_attrs = digwater_meta.get("tank_attrs") or {}
    dw_link_ends = digwater_meta.get("link_ends") or {}

    for item in iter_normalized_report_objects(json_path):
        oid = item["id"]
        cid = item["clean_id"] or clean_object_id(oid)
        otype = item["type"]
        props = item["properties"] or {}
        ts = item["time_series"] or {}
        nm = clean_object_name(obj_names.get(oid) or obj_names.get(cid) or item.get("name") or "")
        if nm:
            obj_names[oid] = nm
            obj_names.setdefault(cid, nm)

        x_val = _to_float(_first_present(props.get("X"), props.get("x"), props.get("CoordX")))
        y_val = _to_float(_first_present(props.get("Y"), props.get("y"), props.get("CoordY")))
        if x_val is not None and y_val is not None:
            coords_map[oid] = [float(x_val), float(y_val)]
            coords_map[cid] = [float(x_val), float(y_val)]

        if otype == "junc":
            node_ids_set.add(oid)
            dw_n = dw_node_attrs.get(cid) or dw_node_attrs.get(oid) or {}
            z_val = _to_float(
                _first_present(
                    props.get("elev"),
                    props.get("Elev"),
                    props.get("Elevation"),
                    props.get("elevation"),
                    props.get("Z"),
                    props.get("z"),
                    dw_n.get("elevation_m"),
                )
            )
            if z_val is not None:
                elev_map[oid] = float(z_val)
                elev_map[cid] = float(z_val)

            req_val = _to_float(
                _first_present(
                    props.get("RequiredPressure"),
                    props.get("required_pressure"),
                    props.get("ReqPressure"),
                    props.get("req_press"),
                    props.get("ReqPress"),
                    dw_n.get("required_pressure_m"),
                )
            )
            floors_val = _to_float(_first_present(props.get("Floors"), props.get("floors"), dw_n.get("floors")))
            if (req_val is None or req_val <= 0) and floors_val is not None and floors_val > 0:
                # По СП 31.13330.2012 п. 5.11: 10 м на 1 этаж + 4 м на каждый последующий
                req_val = 10.0 + max(0.0, float(floors_val) - 1.0) * 4.0
            if req_val is not None and req_val > 0:
                req_p_map[oid] = float(req_val)
                fl_int = (
                    int(round(floors_val))
                    if (floors_val is not None and floors_val > 0)
                    else max(1, int(round(1.0 + max(0.0, float(req_val) - 10.0) / 4.0)))
                )
                floors_map[oid] = fl_int

            dem_base = _to_float(
                _first_present(
                    props.get("BaseDemand"),
                    props.get("base_demand"),
                    props.get("Demand"),
                    props.get("demand"),
                    dw_n.get("base_demand_m3_h"),
                )
            )
            if dem_base is not None and abs(float(dem_base)) > 1e-9:
                base_demand[oid] = float(dem_base)

            sch_str = str(_first_present(props.get("Pattern"), props.get("pattern"), props.get("Schedule"), dw_n.get("schedule")) or "").strip()
            if sch_str and sch_str.lower() not in ("t", "nan", "none"):
                node_schedules[oid] = [sch_str]

            free_arr = ts.get("free_pressure") or ts.get("pressure")
            head_arr = ts.get("head")
            dem_arr = ts.get("demand")
            if free_arr:
                node_free_head_series[oid] = _preserve_series(free_arr)
            elif head_arr:
                node_total_head_series[oid] = _preserve_series(head_arr)
            if dem_arr:
                node_demand_series[oid] = _preserve_series(dem_arr)

        elif otype == "reserv":
            source_ids_set.add(oid)
            dw_r = dw_res_attrs.get(cid) or dw_res_attrs.get(oid) or {}
            h_val = _to_float(
                _first_present(
                    props.get("Head"),
                    props.get("head"),
                    props.get("TotalHead"),
                    props.get("elev"),
                    props.get("Elevation"),
                    dw_r.get("head_m"),
                )
            )
            if h_val is None and ts.get("head"):
                h_arr = [fv for x in ts["head"] if (fv := _to_float(x)) is not None]
                if h_arr:
                    h_val = round(sum(h_arr) / len(h_arr), 2)
            if h_val is not None:
                h_val = round(float(h_val), 2)
                elev_map.setdefault(oid, h_val)
                elev_map.setdefault(cid, h_val)
                source_head_by_cid[cid] = h_val
            c_xy = coords_map.get(oid) or coords_map.get(cid)
            sources_list.append({
                "source_id": oid,
                "clean_id": cid,
                "name": nm or "Источник водоснабжения",
                "design_head_m": h_val,
                "x": round(c_xy[0], 2) if c_xy else None,
                "y": round(c_xy[1], 2) if c_xy else None,
            })

        elif otype == "tank":
            tank_ids_set.add(oid)
            dw_t = dw_tank_attrs.get(cid) or dw_tank_attrs.get(oid) or {}
            h_val = _to_float(
                _first_present(
                    props.get("Head"),
                    props.get("head"),
                    props.get("elev"),
                    props.get("Elevation"),
                    props.get("InitLevel"),
                    dw_t.get("head_m"),
                )
            )
            if h_val is None and ts.get("head"):
                h_arr = [fv for x in ts["head"] if (fv := _to_float(x)) is not None]
                if h_arr:
                    h_val = round(sum(h_arr) / len(h_arr), 2)
            if h_val is not None:
                h_val = round(float(h_val), 2)
                elev_map.setdefault(oid, h_val)
                elev_map.setdefault(cid, h_val)
            c_xy = coords_map.get(oid) or coords_map.get(cid)
            tanks_list.append({
                "tank_id": oid,
                "clean_id": cid,
                "name": nm or "Резервуар чистой воды (РЧВ)",
                "design_head_m": h_val,
                "x": round(c_xy[0], 2) if c_xy else None,
                "y": round(c_xy[1], 2) if c_xy else None,
            })

        elif otype == "pipe":
            pipe_ids_set.add(oid)
            dw_p = dw_pipe_attrs.get(cid) or dw_pipe_attrs.get(oid) or {}
            ends = dw_link_ends.get(oid) or dw_link_ends.get(cid) or ("", "")
            sn = str(_first_present(props.get("start"), props.get("Start"), props.get("StartNode"), props.get("Node1"), props.get("start_node"), dw_p.get("start_node"), ends[0]) or "").strip()
            en = str(_first_present(props.get("finish"), props.get("Finish"), props.get("EndNode"), props.get("Node2"), props.get("end_node"), dw_p.get("end_node"), ends[1]) or "").strip()
            d_mm = _to_float(_first_present(props.get("diam"), props.get("Diam"), props.get("Diameter"), props.get("diameter"), props.get("D"), dw_p.get("diameter_mm")))
            l_m = _to_float(_first_present(props.get("len"), props.get("Len"), props.get("Length"), props.get("length"), props.get("L"), dw_p.get("length_m")))
            c_hw = _to_float(_first_present(props.get("rough"), props.get("Rough"), props.get("Roughness"), props.get("roughness"), props.get("C"), dw_p.get("roughness_c")))
            st_raw = str(_first_present(props.get("status"), props.get("Status"), dw_p.get("status")) or "").strip().lower()
            is_closed_prop = st_raw in ("closed", "закрыта", "закрыт", "0", "false", "выкл")
            raw_pipes.append({
                "pipe_id": oid,
                "clean_id": cid,
                "start_node": sn,
                "end_node": en,
                "diameter_mm": float(d_mm) if d_mm is not None else 0.0,
                "length_m": float(l_m) if l_m is not None else 0.0,
                "hazen_williams_c": float(c_hw) if c_hw is not None else math.nan,
                "material": str(_first_present(props.get("material"), props.get("Material"), dw_p.get("material")) or "").strip(),
                "year": str(_first_present(props.get("year"), props.get("Year"), dw_p.get("year")) or "").strip(),
                "is_closed_prop": is_closed_prop,
            })
            if ts.get("flow"):
                pipe_flow_series[oid] = _preserve_series(ts["flow"])

        elif otype == "pump":
            pump_ids_set.add(oid)
            dw_pu = dw_pump_attrs.get(cid) or dw_pump_attrs.get(oid) or {}
            ends = dw_link_ends.get(oid) or dw_link_ends.get(cid) or ("", "")
            sn = str(_first_present(props.get("start"), props.get("Start"), props.get("StartNode"), props.get("Node1"), props.get("start_node"), dw_pu.get("start_node"), ends[0]) or "").strip()
            en = str(_first_present(props.get("finish"), props.get("Finish"), props.get("EndNode"), props.get("Node2"), props.get("end_node"), dw_pu.get("end_node"), ends[1]) or "").strip()
            curve_id = str(_first_present(props.get("curve"), props.get("Curve"), props.get("HeadCurve"), props.get("curve_id"), dw_pu.get("curve_id")) or "").strip()
            raw_pumps.append({
                "pump_id": oid,
                "clean_id": cid,
                "start_node": sn,
                "end_node": en,
                "curve_id": curve_id,
            })
            if ts.get("flow"):
                pump_flow_series[oid] = _preserve_series(ts["flow"])

        elif otype == "valv":
            dw_v = dw_valve_attrs.get(cid) or dw_valve_attrs.get(oid) or {}
            ends = dw_link_ends.get(oid) or dw_link_ends.get(cid) or ("", "")
            sn = str(_first_present(props.get("start"), props.get("Start"), props.get("StartNode"), props.get("Node1"), props.get("start_node"), dw_v.get("start_node"), ends[0]) or "").strip()
            en = str(_first_present(props.get("finish"), props.get("Finish"), props.get("EndNode"), props.get("Node2"), props.get("end_node"), dw_v.get("end_node"), ends[1]) or "").strip()
            vtype = str(_first_present(props.get("type"), props.get("Type"), props.get("ValveType"), dw_v.get("type_code")) or "PRV").strip()
            vset = _to_float(_first_present(props.get("setting"), props.get("Setting"), props.get("set"), props.get("Set"), dw_v.get("setting")))
            raw_valves.append({
                "valve_id": oid,
                "clean_id": cid,
                "start_node": sn,
                "end_node": en,
                "type_raw": vtype,
                "setting": float(vset) if vset is not None else math.nan,
            })
            if ts.get("flow"):
                valve_flow_series[oid] = _preserve_series(ts["flow"])

    # Дополняем параметрами из .DigWater для узлов, которые в Report.json содержали только временные ряды
    for nid in list(node_ids_set):
        cid = clean_object_id(nid)
        dw_n = dw_node_attrs.get(cid) or dw_node_attrs.get(nid)
        if not dw_n:
            continue
        if nid not in elev_map and dw_n.get("elevation_m") is not None:
            elev_map[nid] = float(dw_n["elevation_m"])
            elev_map[cid] = float(dw_n["elevation_m"])
        if nid not in req_p_map:
            rq = dw_n.get("required_pressure_m")
            fl = dw_n.get("floors")
            if (rq is None or rq <= 0) and fl is not None and fl > 0:
                rq = 10.0 + max(0.0, float(fl) - 1.0) * 4.0
            if rq is not None and rq > 0:
                req_p_map[nid] = float(rq)
                floors_map[nid] = int(round(fl)) if (fl is not None and fl > 0) else max(1, int(round(1.0 + max(0.0, float(rq) - 10.0) / 4.0)))
        if nid not in base_demand and dw_n.get("base_demand_m3_h") is not None:
            bd = float(dw_n["base_demand_m3_h"])
            if abs(bd) > 1e-9:
                base_demand[nid] = bd

    active_nodes = set(req_p_map.keys()) | set(base_demand.keys())
    for nid, d_arr in node_demand_series.items():
        if any(x is not None and float(x) > 1e-6 for x in d_arr):
            active_nodes.add(nid)
    # Если в Report.json и .DigWater не заданы ни demand, ни ReqPress, включаем все узлы с рассчитанным напором
    if not active_nodes:
        active_nodes = set(node_free_head_series.keys()) | set(node_total_head_series.keys())

    if node_free_head_series:
        head_ts = build_timeseries_from_json_series(
            node_free_head_series,
            row_offsets=None,
            hourly_mode="percentiles",
            filter_ids=active_nodes or None,
            keep_row_hourly=True,
            timeline_buckets=timeline_buckets,
        )
    else:
        head_ts = build_timeseries_from_json_series(
            node_total_head_series,
            row_offsets=elev_map,
            hourly_mode="percentiles",
            filter_ids=active_nodes or None,
            keep_row_hourly=True,
            timeline_buckets=timeline_buckets,
        )

    dem_ts = build_timeseries_from_json_series(
        node_demand_series,
        hourly_mode="sum_positive",
        timeline_buckets=timeline_buckets,
    )
    pump_ts = build_timeseries_from_json_series(
        pump_flow_series,
        hourly_mode="sum",
        keep_row_hourly=True,
        timeline_buckets=timeline_buckets,
    )
    pipe_ts = build_timeseries_from_json_series(
        pipe_flow_series,
        hourly_mode="none",
        keep_row_hourly=True,
        timeline_buckets=timeline_buckets,
    )
    valve_ts = build_timeseries_from_json_series(
        valve_flow_series,
        hourly_mode="sum",
        keep_row_hourly=True,
        timeline_buckets=timeline_buckets,
    )

    return {
        "elev_map": elev_map,
        "req_p_map": req_p_map,
        "floors_map": floors_map,
        "coords_map": coords_map,
        "base_demand": base_demand,
        "node_schedules": node_schedules,
        "active_nodes": active_nodes,
        "sources_list": sources_list,
        "source_head_by_cid": source_head_by_cid,
        "source_ids_set": source_ids_set,
        "tanks_list": tanks_list,
        "tank_ids_set": tank_ids_set,
        "raw_pipes": raw_pipes,
        "raw_pumps": raw_pumps,
        "raw_valves": raw_valves,
        "node_ids_set": node_ids_set,
        "pipe_ids_set": pipe_ids_set,
        "pump_ids_set": pump_ids_set,
        "head_ts": head_ts,
        "dem_ts": dem_ts,
        "pump_ts": pump_ts,
        "pipe_ts": pipe_ts,
        "valve_ts": valve_ts,
    }


def process_aquabit_city_v3(
    raw_city_dir: Path | str,
    inp_path: Path | str | None = None,
    top_k: int = 15,
    timeline_buckets: int = 48,
    report_json_path: Path | str | None = None,
    digwater_path: Path | str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Процессор ПК «Аквабит» v3.5:
    - Чтение результатов расчета из JSON (Report.json) и паспортов/названий/топологии из .DigWater
      (с автоматическим резервным чтением CSV + .inp при отсутствии JSON/.DigWater)
    - Детальная временная шкала (48 шагов по 30 мин / ЧЧ:ММ)
    - Графовая причинно-следственная диагностика: Источник <-> Насос <-> Коллектор <-> Труба
    - Физически корректное разделение 1-го подъема, перекачки и потерь воды."""
    t0 = time.perf_counter()
    raw_city_dir = Path(raw_city_dir)
    city_name = unicodedata.normalize("NFC", raw_city_dir.name)
    data_export_warnings: list[str] = []

    resolved_json = Path(report_json_path) if report_json_path else find_city_report_json(raw_city_dir)
    resolved_dw = Path(digwater_path) if digwater_path else find_city_digwater(raw_city_dir)
    if inp_path is not None and Path(inp_path).suffix.lower() == ".digwater" and resolved_dw is None:
        resolved_dw = Path(inp_path)
        inp_path = None
    else:
        inp_path = Path(inp_path) if inp_path else None

    # 0. Приоритет источников: Report.json + .DigWater (CSV и .inp НЕ читаются при наличии JSON + .DigWater)
    has_json = bool(resolved_json is not None and resolved_json.exists())
    has_digwater = bool(resolved_dw is not None and resolved_dw.exists())

    use_json_primary = has_json
    json_bundle: dict[str, Any] | None = None
    topo_meta: dict[str, Any]

    if has_digwater and resolved_dw is not None:
        topo_meta = parse_digwater_metadata_and_topology(resolved_dw)
        topology_source = "digwater"
    else:
        topo_meta = {
            "names": {},
            "links": [],
            "nodes": {},
            "link_ends": {},
            "closed_links": set(),
            "reservoir_clean_ids": set(),
            "tank_clean_ids": set(),
            "qh_curves": {},
        }
        topology_source = "none"

    if use_json_primary and resolved_json is not None:
        try:
            json_bundle = _ingest_from_json_and_digwater(
                resolved_json,
                topo_meta,
                timeline_buckets=timeline_buckets,
            )
        except Exception as exc:
            data_export_warnings.append(
                f"Не удалось прочитать JSON {resolved_json.name} ({exc}); выполнен переход на резервное чтение CSV."
            )
            json_bundle = None
            use_json_primary = False

    # Читаем CSV и .inp ТОЛЬКО в резервном режиме (когда отсутствует Report.json или .DigWater)
    need_csv_fallback = not use_json_primary
    need_inp_fallback = not has_digwater

    if need_csv_fallback:
        df_nodes = read_aquabit_csv(_resolve_file_ci(raw_city_dir, "Узел.csv"))
        df_pipes = read_aquabit_csv(_resolve_file_ci(raw_city_dir, "Труба.csv"))
        df_pumps = read_aquabit_csv(_resolve_file_ci(raw_city_dir, "Насос.csv"))
        df_valves = read_aquabit_csv(_resolve_file_ci(raw_city_dir, "Клапан.csv"))
        df_valve_types = read_aquabit_csv(_resolve_file_ci(raw_city_dir, "Типы Клапанов.csv"))
        df_tanks = read_aquabit_csv(_resolve_file_ci(raw_city_dir, "Резервуар.csv"))
        df_src = read_aquabit_csv(_resolve_file_ci(raw_city_dir, "Источник.csv"))
        df_qh = read_aquabit_csv(_resolve_file_ci(raw_city_dir, "Кривая QH.csv"))
        df_cons = read_aquabit_csv(_resolve_file_ci(raw_city_dir, "Потребители Узел.csv"))
    else:
        df_nodes = read_aquabit_csv(None)
        df_pipes = read_aquabit_csv(None)
        df_pumps = read_aquabit_csv(None)
        df_valves = read_aquabit_csv(None)
        df_valve_types = read_aquabit_csv(None)
        df_tanks = read_aquabit_csv(None)
        df_src = read_aquabit_csv(None)
        # Если в .DigWater нет кривых Q-H, но в папке лежит Кривая QH.csv, аккуратно подгрузим только кривые
        df_qh = read_aquabit_csv(_resolve_file_ci(raw_city_dir, "Кривая QH.csv")) if not (topo_meta.get("qh_curves")) else read_aquabit_csv(None)
        df_cons = read_aquabit_csv(None)

    csv_node_ids = set(df_nodes[df_nodes.columns[0]]) if not df_nodes.empty else set()
    csv_pipe_ids = set(df_pipes[df_pipes.columns[0]]) if not df_pipes.empty else set()
    csv_pump_ids = set(df_pumps[df_pumps.columns[0]]) if not df_pumps.empty else set()
    csv_source_ids = set(df_src[df_src.columns[0]]) if not df_src.empty else set()
    csv_tank_ids = set(df_tanks[df_tanks.columns[0]]) if not df_tanks.empty else set()

    if need_inp_fallback:
        if inp_path is None and raw_city_dir.exists():
            inps = sorted([p for p in raw_city_dir.iterdir() if p.is_file() and p.suffix.lower() == ".inp"])
            if inps:
                inp_path = inps[0]
        if inp_path and inp_path.exists():
            topo_meta = parse_inp_metadata_and_topology(
                inp_path,
                csv_node_ids | (json_bundle["node_ids_set"] if json_bundle else set()),
                csv_pipe_ids | (json_bundle["pipe_ids_set"] if json_bundle else set()),
                csv_pump_ids | (json_bundle["pump_ids_set"] if json_bundle else set()),
                extra_node_ids=csv_source_ids | csv_tank_ids,
            )
            topology_source = "inp"

    obj_names: dict[str, str] = dict(topo_meta.get("names") or {})

    elev_map: dict[str, float] = {}
    req_p_map: dict[str, float] = {}
    floors_map: dict[str, int] = {}
    csv_coords: dict[str, list[float]] = dict(topo_meta.get("nodes") or {})
    sources_list: list[dict[str, Any]] = []
    source_head_by_cid: dict[str, float] = {}
    tanks_list: list[dict[str, Any]] = []
    base_demand: dict[str, float] = {}
    node_schedules: dict[str, list[str]] = {}

    if not df_nodes.empty:
        c_id = df_nodes.columns[0]
        c_z = find_col(df_nodes, "высот", "отмет", default_idx=1 if len(df_nodes.columns) > 1 else None)
        c_req = find_col(df_nodes, "требуем", "давлен", default_idx=2 if len(df_nodes.columns) > 2 else None)
        c_x = find_col(df_nodes, "x", default_idx=3 if len(df_nodes.columns) > 3 else None)
        c_y = find_col(df_nodes, "y", default_idx=4 if len(df_nodes.columns) > 4 else None)

        zs = to_numeric_series(df_nodes[c_z]) if c_z else [math.nan] * len(df_nodes)
        reqs = to_numeric_series(df_nodes[c_req], fillna=0.0) if c_req else [0.0] * len(df_nodes)
        xs = to_numeric_series(df_nodes[c_x]) if c_x else [math.nan] * len(df_nodes)
        ys = to_numeric_series(df_nodes[c_y]) if c_y else [math.nan] * len(df_nodes)

        for nid, zv, rv, xv, yv in zip(df_nodes[c_id], zs, reqs, xs, ys):
            nid_s = str(nid).strip()
            cid_s = clean_object_id(nid_s)
            if not math.isnan(float(zv)):
                elev_map[nid_s] = float(zv)
                elev_map[cid_s] = float(zv)
            if float(rv) > 0:
                req_p_map[nid_s] = float(rv)
                req_p_map[cid_s] = float(rv)
                fl = max(1, int(round(1.0 + max(0.0, float(rv) - 10.0) / 4.0)))
                floors_map[nid_s] = fl
                floors_map[cid_s] = fl
            if not (math.isnan(float(xv)) or math.isnan(float(yv))):
                csv_coords[nid_s] = [float(xv), float(yv)]
                csv_coords[cid_s] = [float(xv), float(yv)]

    if not df_src.empty:
        c_id = df_src.columns[0]
        c_h = find_col(df_src, "напор", default_idx=1 if len(df_src.columns) > 1 else None)
        c_x = find_col(df_src, "x", default_idx=2 if len(df_src.columns) > 2 else None)
        c_y = find_col(df_src, "y", default_idx=3 if len(df_src.columns) > 3 else None)
        hs = to_numeric_series(df_src[c_h]) if c_h else [math.nan] * len(df_src)
        xs = to_numeric_series(df_src[c_x]) if c_x else [math.nan] * len(df_src)
        ys = to_numeric_series(df_src[c_y]) if c_y else [math.nan] * len(df_src)
        for sid, hv, xv, yv in zip(df_src[c_id], hs, xs, ys):
            sid_s = str(sid).strip()
            s_cid = clean_object_id(sid_s)
            h_val = None if math.isnan(float(hv)) else round(float(hv), 2)
            if h_val is not None:
                elev_map.setdefault(sid_s, h_val)
                elev_map.setdefault(s_cid, h_val)
                source_head_by_cid[s_cid] = h_val
            if not (math.isnan(float(xv)) or math.isnan(float(yv))):
                csv_coords[sid_s] = [float(xv), float(yv)]
                csv_coords[s_cid] = [float(xv), float(yv)]
            if not use_json_primary:
                sources_list.append({
                    "source_id": sid_s,
                    "clean_id": s_cid,
                    "name": clean_object_name(obj_names.get(sid_s) or obj_names.get(s_cid) or "Источник водоснабжения"),
                    "design_head_m": h_val,
                    "x": None if math.isnan(float(xv)) else round(float(xv), 2),
                    "y": None if math.isnan(float(yv)) else round(float(yv), 2),
                })

    if not df_tanks.empty:
        c_id = df_tanks.columns[0]
        c_h = find_col(df_tanks, "напор", default_idx=1 if len(df_tanks.columns) > 1 else None)
        c_x = find_col(df_tanks, "x", default_idx=2 if len(df_tanks.columns) > 2 else None)
        c_y = find_col(df_tanks, "y", default_idx=3 if len(df_tanks.columns) > 3 else None)
        hs = to_numeric_series(df_tanks[c_h]) if c_h else [math.nan] * len(df_tanks)
        xs = to_numeric_series(df_tanks[c_x]) if c_x else [math.nan] * len(df_tanks)
        ys = to_numeric_series(df_tanks[c_y]) if c_y else [math.nan] * len(df_tanks)
        for tid, hv, xv, yv in zip(df_tanks[c_id], hs, xs, ys):
            tid_s = str(tid).strip()
            t_cid = clean_object_id(tid_s)
            h_val = None if math.isnan(float(hv)) else round(float(hv), 2)
            if h_val is not None:
                elev_map.setdefault(tid_s, h_val)
                elev_map.setdefault(t_cid, h_val)
            if not (math.isnan(float(xv)) or math.isnan(float(yv))):
                csv_coords[tid_s] = [float(xv), float(yv)]
                csv_coords[t_cid] = [float(xv), float(yv)]
            if not use_json_primary:
                tanks_list.append({
                    "tank_id": tid_s,
                    "clean_id": t_cid,
                    "name": clean_object_name(obj_names.get(tid_s) or obj_names.get(t_cid) or "Резервуар чистой воды (РЧВ)"),
                    "design_head_m": h_val,
                    "x": None if math.isnan(float(xv)) else round(float(xv), 2),
                    "y": None if math.isnan(float(yv)) else round(float(yv), 2),
                })

    if not df_cons.empty:
        c_id = df_cons.columns[0]
        c_q = find_col(df_cons, "потреблен", default_idx=1 if len(df_cons.columns) > 1 else None)
        c_sch = find_col(df_cons, "график", default_idx=2 if len(df_cons.columns) > 2 else None)
        q_col = to_numeric_series(df_cons[c_q], fillna=0.0) if c_q else [0.0] * len(df_cons)
        sch_col = [str(x).strip() for x in df_cons[c_sch]] if c_sch else [""] * len(df_cons)
        for nid, qv, sv in zip(df_cons[c_id], q_col, sch_col):
            nid_s = str(nid).strip()
            cid_s = clean_object_id(nid_s)
            if abs(float(qv)) > 1e-9:
                base_demand[nid_s] = base_demand.get(nid_s, 0.0) + float(qv)
                base_demand.setdefault(f"Ju{cid_s}", base_demand[nid_s])
                if sv and sv.lower() not in ("t", "nan", "none"):
                    node_schedules.setdefault(nid_s, []).append(sv)
                    node_schedules.setdefault(f"Ju{cid_s}", []).append(sv)

    if use_json_primary and json_bundle is not None:
        elev_map.update(json_bundle["elev_map"])
        for k, v in json_bundle["req_p_map"].items():
            req_p_map[k] = v
            req_p_map[clean_object_id(k)] = v
        for k, v in json_bundle["floors_map"].items():
            floors_map[k] = v
            floors_map[clean_object_id(k)] = v
        # Переносим требуемые напоры и этажность на ключи Ju...
        for nid in json_bundle["node_ids_set"]:
            cid = clean_object_id(nid)
            if nid not in req_p_map and cid in req_p_map:
                req_p_map[nid] = req_p_map[cid]
                floors_map[nid] = floors_map.get(cid, 1)
            if nid not in elev_map and cid in elev_map:
                elev_map[nid] = elev_map[cid]
        csv_coords.update(json_bundle["coords_map"])
        base_demand.update(json_bundle["base_demand"])
        node_schedules.update(json_bundle["node_schedules"])
        if json_bundle["sources_list"]:
            sources_list = json_bundle["sources_list"]
        source_head_by_cid.update(json_bundle["source_head_by_cid"])
        if json_bundle["tanks_list"]:
            tanks_list = json_bundle["tanks_list"]
        node_ids_set = json_bundle["node_ids_set"] | csv_node_ids
        pipe_ids_set = json_bundle["pipe_ids_set"] | csv_pipe_ids
        pump_ids_set = json_bundle["pump_ids_set"] | csv_pump_ids
        source_ids_set = json_bundle["source_ids_set"] | csv_source_ids
        tank_ids_set = json_bundle["tank_ids_set"] | csv_tank_ids
        head_ts = json_bundle["head_ts"]
        dem_ts = json_bundle["dem_ts"]
        pump_ts = json_bundle["pump_ts"]
        pipe_ts = json_bundle["pipe_ts"]
        valve_ts = json_bundle["valve_ts"]
        active_nodes = json_bundle["active_nodes"] | {
            nid for nid in json_bundle["node_ids_set"]
            if clean_object_id(nid) in {clean_object_id(k) for k in (set(req_p_map.keys()) | set(base_demand.keys()))}
        }
    else:
        node_ids_set = csv_node_ids
        pipe_ids_set = csv_pipe_ids
        pump_ids_set = csv_pump_ids
        source_ids_set = csv_source_ids
        tank_ids_set = csv_tank_ids
        active_nodes = set(req_p_map.keys()) | set(base_demand.keys())
        f_free = _resolve_file_ci(raw_city_dir, "Свободный напор Узел.csv")
        f_head = _resolve_file_ci(raw_city_dir, "Напор Узел.csv")
        head_ts = _stream_wide_timeseries(
            f_free or f_head,
            row_offsets=None if f_free else elev_map,
            hourly_mode="percentiles",
            filter_ids=active_nodes or None,
            keep_row_hourly=True,
            timeline_buckets=timeline_buckets,
        )
        dem_ts = _stream_wide_timeseries(
            _resolve_file_ci(raw_city_dir, "Потребление Узел.csv"),
            hourly_mode="sum_positive",
            timeline_buckets=timeline_buckets,
        )
        pump_ts = _stream_wide_timeseries(
            _resolve_file_ci(raw_city_dir, "Поток Насос.csv"),
            hourly_mode="sum",
            keep_row_hourly=True,
            timeline_buckets=timeline_buckets,
        )
        pipe_ts = _stream_wide_timeseries(
            _resolve_file_ci(raw_city_dir, "Поток Труба.csv"),
            hourly_mode="none",
            keep_row_hourly=True,
            timeline_buckets=timeline_buckets,
        )
        valve_ts = _stream_wide_timeseries(
            _resolve_file_ci(raw_city_dir, "Поток Клапан.csv"),
            hourly_mode="sum",
            keep_row_hourly=True,
            timeline_buckets=timeline_buckets,
        )

    source_clean_ids = {clean_object_id(s) for s in source_ids_set if s} | set(topo_meta.get("reservoir_clean_ids") or set())
    tank_clean_ids = {clean_object_id(t) for t in tank_ids_set if t} | set(topo_meta.get("tank_clean_ids") or set())

    def label_with_name(oid: str) -> str:
        cid = clean_object_id(oid)
        raw_nm = obj_names.get(oid) or obj_names.get(cid)
        nm = clean_object_name(raw_nm)
        return f"{cid} ({nm})" if nm else cid

    n_steps = head_ts["n_steps"] or dem_ts["n_steps"] or valve_ts["n_steps"] or 1440
    step_min = 1440.0 / max(n_steps, 1)

    # 4. Проверка и починка топологии связей
    def resolve_link_endpoints(lid: str, sn_raw: str, en_raw: str) -> tuple[str, str, bool]:
        sn_s, en_s = str(sn_raw).strip(), str(en_raw).strip()
        is_self_loop_bug = bool(sn_s and sn_s == en_s) or (bool(sn_s) and clean_object_id(sn_s) == clean_object_id(lid))
        if not sn_s or not en_s or is_self_loop_bug:
            ended = (
                topo_meta["link_ends"].get(lid)
                or topo_meta["link_ends"].get(clean_object_id(lid))
                or topo_meta["link_ends"].get(f"e{clean_object_id(lid)}")
                or topo_meta["link_ends"].get(f"p{clean_object_id(lid)}")
            )
            if ended:
                return ended[0], ended[1], is_self_loop_bug
        return sn_s, en_s, is_self_loop_bug

    # Справочники атрибутов из CSV (на случай, если в Report.json нет статических полей)
    csv_pipe_attr_by_cid: dict[str, dict[str, Any]] = {}
    if not df_pipes.empty:
        c_id = df_pipes.columns[0]
        c_s = find_col(df_pipes, "начал", default_idx=1 if len(df_pipes.columns) > 1 else None)
        c_e = find_col(df_pipes, "конец", "конечн", default_idx=2 if len(df_pipes.columns) > 2 else None)
        c_d = find_col(df_pipes, "диаметр", default_idx=3 if len(df_pipes.columns) > 3 else None)
        c_l = find_col(df_pipes, "длин", default_idx=4 if len(df_pipes.columns) > 4 else None)
        c_c = find_col(df_pipes, "шероховат", default_idx=5 if len(df_pipes.columns) > 5 else None)
        ds = to_numeric_series(df_pipes[c_d], fillna=0.0) if c_d else [0.0] * len(df_pipes)
        ls = to_numeric_series(df_pipes[c_l], fillna=0.0) if c_l else [math.nan] * len(df_pipes)
        cs = to_numeric_series(df_pipes[c_c]) if c_c else [math.nan] * len(df_pipes)
        for pid, sn, en, d_mm, l_m, c_hw in zip(
            df_pipes[c_id],
            df_pipes[c_s] if c_s else [""] * len(df_pipes),
            df_pipes[c_e] if c_e else [""] * len(df_pipes),
            ds, ls, cs,
        ):
            cid_p = clean_object_id(str(pid))
            csv_pipe_attr_by_cid[cid_p] = {
                "pipe_id": str(pid).strip(),
                "start_node": str(sn).strip(),
                "end_node": str(en).strip(),
                "diameter_mm": float(d_mm),
                "length_m": float(l_m),
                "hazen_williams_c": float(c_hw),
            }

    csv_pump_attr_by_cid: dict[str, dict[str, Any]] = {}
    if not df_pumps.empty:
        c_id = df_pumps.columns[0]
        c_s = find_col(df_pumps, "начал", default_idx=1 if len(df_pumps.columns) > 1 else None)
        c_e = find_col(df_pumps, "конец", "конечн", default_idx=2 if len(df_pumps.columns) > 2 else None)
        c_curve = find_col(df_pumps, "кривая", default_idx=len(df_pumps.columns) - 1)
        for pid, sn, en, crv in zip(
            df_pumps[c_id],
            df_pumps[c_s] if c_s else [""] * len(df_pumps),
            df_pumps[c_e] if c_e else [""] * len(df_pumps),
            df_pumps[c_curve] if c_curve else [""] * len(df_pumps),
        ):
            cid_pu = clean_object_id(str(pid))
            csv_pump_attr_by_cid[cid_pu] = {
                "pump_id": str(pid).strip(),
                "start_node": str(sn).strip(),
                "end_node": str(en).strip(),
                "curve_id": str(crv).strip(),
            }

    inactive_well_nodes: set[str] = set()
    pump_endpoints: dict[str, tuple[str, str]] = {}
    pump_iter_items: list[dict[str, Any]] = []
    if use_json_primary and json_bundle is not None and json_bundle["raw_pumps"]:
        for rp in json_bundle["raw_pumps"]:
            cid_pu = rp["clean_id"]
            fb = csv_pump_attr_by_cid.get(cid_pu, {})
            pump_iter_items.append({
                "pump_id": rp["pump_id"],
                "start_node": rp["start_node"] or fb.get("start_node", ""),
                "end_node": rp["end_node"] or fb.get("end_node", ""),
                "curve_id": rp["curve_id"] or fb.get("curve_id", ""),
            })
    else:
        for _cid_pu, fb in csv_pump_attr_by_cid.items():
            pump_iter_items.append(fb)

    for p_item in pump_iter_items:
        pid = p_item["pump_id"]
        sn_clean, en_clean, _had_bug = resolve_link_endpoints(pid, p_item["start_node"], p_item["end_node"])
        pump_endpoints[pid] = (sn_clean, en_clean)
        pst = pump_ts["rows"].get(pid)
        if (pst is None or pst["max"] <= 1e-3) and (clean_object_id(sn_clean) in source_clean_ids or sn_clean in source_ids_set):
            inactive_well_nodes.add(sn_clean)
            inactive_well_nodes.add(clean_object_id(sn_clean))

    # 5. Трубы и индексация графа смежности узлов и участков
    pipe_records: list[dict[str, Any]] = []
    adj_pipes_by_node: dict[str, list[dict[str, Any]]] = {}
    velocity_bins = {
        "Перекрыты / Поток = 0 (задвижка)": 0,
        "Низкие скорости (< 0.10 м/с)": 0,
        "Умеренные (0.10–0.50 м/с)": 0,
        "Оптимум (0.50–1.50 м/с)": 0,
        "Повышенные (1.50–5.00 м/с)": 0,
        "Большая скорость (>= 5.00 м/с)": 0,
    }
    total_len_km = 0.0
    self_loop_pipes = 0
    node_degree: dict[str, int] = {}
    node_transit_flow: dict[str, float] = {}

    pipe_iter_items: list[dict[str, Any]] = []
    if use_json_primary and json_bundle is not None and json_bundle["raw_pipes"]:
        for rp in json_bundle["raw_pipes"]:
            cid_p = rp["clean_id"]
            fb = csv_pipe_attr_by_cid.get(cid_p, {})
            d_mm = rp["diameter_mm"] if rp["diameter_mm"] > 0 else fb.get("diameter_mm", 0.0)
            l_m = rp["length_m"] if rp["length_m"] > 0 else fb.get("length_m", 0.0)
            c_hw = rp["hazen_williams_c"] if not math.isnan(rp["hazen_williams_c"]) else fb.get("hazen_williams_c", math.nan)
            pipe_iter_items.append({
                "pipe_id": rp["pipe_id"],
                "start_node": rp["start_node"] or fb.get("start_node", ""),
                "end_node": rp["end_node"] or fb.get("end_node", ""),
                "diameter_mm": d_mm,
                "length_m": l_m,
                "hazen_williams_c": c_hw,
                "is_closed_prop": rp.get("is_closed_prop", False),
            })
    else:
        for _cid_p, fb in csv_pipe_attr_by_cid.items():
            pipe_iter_items.append({**fb, "is_closed_prop": False})

    closed_links_set = set(topo_meta.get("closed_links") or set())
    for p_item in pipe_iter_items:
        pid = p_item["pipe_id"]
        cid = clean_object_id(pid)
        d_val = float(p_item["diameter_mm"] or 0.0)
        l_raw = float(p_item["length_m"] if p_item["length_m"] is not None else 0.0)
        l_val = max(0.0, l_raw if not math.isnan(l_raw) else 0.0)
        total_len_km += l_val / 1000.0
        c_raw = float(p_item["hazen_williams_c"]) if p_item["hazen_williams_c"] is not None else math.nan
        c_val = None if math.isnan(c_raw) else round(c_raw, 1)

        sn_s, en_s, had_bug = resolve_link_endpoints(pid, p_item["start_node"], p_item["end_node"])
        if had_bug:
            self_loop_pipes += 1

        sn_cid = clean_object_id(sn_s)
        en_cid = clean_object_id(en_s)
        for u_node in (sn_s, en_s, sn_cid, en_cid):
            if u_node:
                node_degree[u_node] = node_degree.get(u_node, 0) + 1

        pst = pipe_ts["rows"].get(pid)
        v_max, q_max, headloss_m_km, total_headloss_m = None, None, None, None
        is_closed_by_valve = bool(
            p_item.get("is_closed_prop")
            or (pid in closed_links_set)
            or (cid in closed_links_set)
            or (f"e{cid}" in closed_links_set)
        )
        is_zero_flow = False
        v_bin_name = "Не определен"

        if pst is not None and d_val > 0:
            q_max = round(pst["abs_max"], 2)
            if q_max <= 1e-4:
                is_zero_flow = True
            for u_node in (sn_s, en_s, sn_cid, en_cid):
                if u_node:
                    node_transit_flow[u_node] = node_transit_flow.get(u_node, 0.0) + q_max * 0.5
            area = math.pi * ((d_val / 1000.0) ** 2) / 4.0
            v_max = round((q_max / 3600.0) / area, 3)

            if c_val and c_val > 0 and q_max > 0:
                q_m3s = q_max / 3600.0
                d_m = d_val / 1000.0
                i_grad = 1000.0 * 10.67 * (q_m3s ** 1.852) / ((c_val ** 1.852) * (d_m ** 4.87))
                headloss_m_km = round(i_grad, 2)
                total_headloss_m = round(i_grad * l_val / 1000.0, 2)
            else:
                headloss_m_km = 0.0
                total_headloss_m = 0.0

            if is_closed_by_valve or is_zero_flow or v_max <= 0.001:
                v_bin_name = "Перекрыты / Поток = 0 (задвижка)"
            elif v_max < 0.10:
                v_bin_name = "Низкие скорости (< 0.10 м/с)"
            elif v_max < 0.50:
                v_bin_name = "Умеренные (0.10–0.50 м/с)"
            elif v_max <= 1.50:
                v_bin_name = "Оптимум (0.50–1.50 м/с)"
            elif v_max < 5.00:
                v_bin_name = "Повышенные (1.50–5.00 м/с)"
            else:
                v_bin_name = "Большая скорость (>= 5.00 м/с)"
            velocity_bins[v_bin_name] += 1

        clean_nm = clean_object_name(obj_names.get(pid) or obj_names.get(cid) or "")
        is_anomalous_low_c = bool(c_val is not None and c_val < 30.0)

        # Формируем точный инженерный диагноз участка с разделением i (м/км) и абсолютной потери Δh (м)
        if is_closed_by_valve:
            pipe_diag = (
                f"Участок перекрыт задвижкой в модели (статус CLOSED, Q = 0 м³/ч между узлами #{sn_cid} и #{en_cid}). "
                f"Проверить целесообразность открытия для кольцевания по СП 31.13330 п. 11.5."
            )
        elif is_zero_flow:
            pipe_diag = (
                f"Нулевой расход Q = 0 м³/ч (участок открыт, но находится на тупиковой/резервной ветке между узлами #{sn_cid} и #{en_cid} без активного водоразбора)."
            )
        elif is_anomalous_low_c:
            pipe_diag = (
                f"Аномальный коэффициент Хейзена-Уильямса C={c_val} на участке L={round(l_val, 1)} м (D={round(d_val)} мм, #{sn_cid} → #{en_cid}): "
                f"фактический перепад напора на длине {round(l_val, 1)} м составляет Δh={total_headloss_m} м (при удельном градиенте i={headloss_m_km} м/км). "
                f"Для стальных и чугунных труб физический минимум C составляет 60–80 ед.; значение C={c_val} — опечатка ввода данных или искусственная имитация прикрытой задвижки."
            )
        elif (headloss_m_km or 0) >= 15.0 or (v_max or 0) >= 5.0:
            if c_val is not None and c_val < 70:
                pipe_diag = (
                    f"Повышенное гидравлическое сопротивление (удельный уклон i={headloss_m_km} м/км, фактическая потеря на участке L={round(l_val, 1)} м составляет Δh={total_headloss_m} м) "
                    f"между узлами #{sn_cid} и #{en_cid} вызвано сниженным коэфф. C={c_val} при D={round(d_val)} мм. В Аквабит: верифицировать C={c_val} или запланировать санацию."
                )
            else:
                pipe_diag = (
                    f"Гидравлическое узкое место (i={headloss_m_km} м/км, фактическая потеря Δh={total_headloss_m} м на длине L={round(l_val, 1)} м, скорость V={v_max} м/с > доп. 1.5–2.0 м/с) "
                    f"на участке #{sn_cid} → #{en_cid}. Диаметр D={round(d_val)} мм недостаточен для пропуска Q={q_max} м³/ч; требуется дублирование или перекладка."
                )
        elif v_max is not None and v_max < 0.10:
            pipe_diag = f"Малопроточная зона (V={v_max} м/с, Q={q_max} м³/ч) на ветке #{sn_cid}–#{en_cid} (D={round(d_val)} мм). Риск застоя воды и вторичного ухудшения качества."
        else:
            pipe_diag = f"Рабочий режим (V={v_max or 0} м/с, i={headloss_m_km or 0} м/км, Δh={total_headloss_m or 0} м) между узлами #{sn_cid} и #{en_cid}."

        p_rec = {
            "pipe_id": pid,
            "clean_id": cid,
            "name": clean_nm,
            "display_id": label_with_name(pid),
            "start_node": sn_cid or sn_s,
            "end_node": en_cid or en_s,
            "diameter_mm": round(d_val, 1),
            "length_m": round(l_val, 1),
            "hazen_williams_c": c_val,
            "flow_max_m3_h": q_max,
            "velocity_max_m_s": v_max,
            "headloss_gradient_m_km": headloss_m_km,
            "total_headloss_m": total_headloss_m,
            "velocity_bin": v_bin_name,
            "is_closed": is_closed_by_valve or is_zero_flow,
            "is_closed_by_valve": is_closed_by_valve,
            "is_zero_flow": is_zero_flow,
            "is_anomalous_low_c": is_anomalous_low_c,
            "reverse_steps": pst["neg_steps"] if pst else 0,
            "diagnostic_advice": pipe_diag,
        }
        pipe_records.append(p_rec)
        for k_node in {sn_s, en_s, sn_cid, en_cid}:
            if k_node:
                adj_pipes_by_node.setdefault(k_node, []).append(p_rec)

    if self_loop_pipes > 0:
        src_label = ".DigWater" if topology_source == "digwater" else ".inp"
        data_export_warnings.append(
            f"Обнаружено {self_loop_pipes} участков с ошибочным совпадением Начальный узел == Конечный узел в табличной выгрузке (связи автоматически восстановлены из {src_label})."
        )

    # 5.5 Граф гидравлической связности (BFS от источников и РЧВ по открытым участкам)
    open_adj: dict[str, list[str]] = {}
    closed_edges: list[tuple[str, str, str]] = []
    for p_rec in pipe_records:
        u = clean_object_id(p_rec.get("start_node"))
        v = clean_object_id(p_rec.get("end_node"))
        if not u or not v or u == v:
            continue
        if p_rec.get("is_closed_by_valve") or p_rec.get("is_closed"):
            closed_edges.append((u, v, p_rec.get("clean_id", "")))
        else:
            open_adj.setdefault(u, []).append(v)
            open_adj.setdefault(v, []).append(u)
    for _pid, (_sn, _en) in pump_endpoints.items():
        u, v = clean_object_id(_sn), clean_object_id(_en)
        if u and v and u != v:
            open_adj.setdefault(u, []).append(v)
            open_adj.setdefault(v, []).append(u)

    seed_clean = {clean_object_id(x) for x in (source_ids_set | tank_ids_set | source_clean_ids | tank_clean_ids) if x}
    reachable_from_sources: set[str] = set()
    has_valid_topology = bool(seed_clean and len(open_adj) > 10)
    if has_valid_topology:
        q_bfs = deque(seed_clean)
        reachable_from_sources.update(seed_clean)
        while q_bfs:
            cur = q_bfs.popleft()
            for nb in open_adj.get(cur, ()):
                if nb not in reachable_from_sources:
                    reachable_from_sources.add(nb)
                    q_bfs.append(nb)

    cutoff_pipe_by_node: dict[str, str] = {}
    if has_valid_topology and closed_edges:
        boundary_seeds: deque[tuple[str, str]] = deque()
        for u, v, pcid in closed_edges:
            if u in reachable_from_sources and v not in reachable_from_sources:
                cutoff_pipe_by_node.setdefault(v, pcid)
                boundary_seeds.append((v, pcid))
            elif v in reachable_from_sources and u not in reachable_from_sources:
                cutoff_pipe_by_node.setdefault(u, pcid)
                boundary_seeds.append((u, pcid))
        while boundary_seeds:
            cur, pcid = boundary_seeds.popleft()
            for nb in open_adj.get(cur, ()):
                if nb not in reachable_from_sources and nb not in cutoff_pipe_by_node:
                    cutoff_pipe_by_node[nb] = pcid
                    boundary_seeds.append((nb, pcid))

    # 6. Узлы (включая транзитные, источники и РЧВ) + привязка к смежным трубам
    node_records: list[dict[str, Any]] = []
    all_node_props: dict[str, dict[str, Any]] = {}
    isolated_consumer_nodes_count = 0
    pressure_bins = {
        "Ошибка / отсечение (< -3.0 м)": 0,
        "Отрицат. давление (-3.0 .. 0 м)": 0,
        "Недостаток давления (< P_треб)": 0,
        "Норма (P_треб .. 60 м)": 0,
        "Избыток (> 60 м)": 0,
    }

    # Собираем канонический список узлов (без дублирования c123 / Ju123 / 123)
    primary_node_keys = set(head_ts["rows"].keys()) | set(dem_ts["rows"].keys()) | set(node_ids_set)
    if not primary_node_keys:
        primary_node_keys = set(elev_map.keys()) | active_nodes
    seen_node_cids: set[str] = set()
    all_known_nodes: list[str] = []
    for k in sorted(primary_node_keys):
        cid_k = clean_object_id(k)
        if cid_k and cid_k in seen_node_cids:
            continue
        if cid_k:
            seen_node_cids.add(cid_k)
        all_known_nodes.append(k)

    active_clean_ids = {clean_object_id(a) for a in active_nodes if a}

    for nid in all_known_nodes:
        cid = clean_object_id(nid)
        hst = head_ts["rows"].get(nid) or head_ts["rows"].get(cid) or head_ts["rows"].get(f"c{cid}") or head_ts["rows"].get(f"Ju{cid}")
        dst = dem_ts["rows"].get(nid) or dem_ts["rows"].get(cid) or dem_ts["rows"].get(f"c{cid}") or dem_ts["rows"].get(f"Ju{cid}")
        req_m = req_p_map.get(nid, req_p_map.get(cid, req_p_map.get(f"c{cid}", 0.0)))
        fl_cnt = floors_map.get(nid, floors_map.get(cid, floors_map.get(f"c{cid}", 0)))
        z_node = elev_map.get(nid, elev_map.get(cid, elev_map.get(f"c{cid}")))
        q_mean = dst["pos_mean"] if (dst and dst["pos_mean"] > 0) else base_demand.get(nid, base_demand.get(cid, base_demand.get(f"c{cid}", 0.0)))
        p_min = round(hst["min"], 2) if hst else None
        p_max = round(hst["max"], 2) if hst else None
        p_mean = round(hst["mean"], 2) if hst else None
        margin_m = round(p_min - req_m, 2) if (p_min is not None and req_m > 0) else None
        is_active_consumer = (nid in active_nodes) or (cid in active_clean_ids) or (req_m > 0) or (q_mean > 1e-6)
        is_shutoff_well = (nid in inactive_well_nodes) or (cid in inactive_well_nodes) or (p_min is not None and p_min < 0 and q_mean <= 1e-6)
        is_vacuum = bool(p_min is not None and p_min < 0.0)
        deficit_abs_m = round(abs(margin_m), 2) if (margin_m is not None and margin_m < 0) else 0.0

        if cid in source_clean_ids or nid in source_ids_set:
            node_role = "Источник водоснабжения (скважина / водозабор)"
        elif cid in tank_clean_ids or nid in tank_ids_set:
            node_role = "Резервуар чистой воды (РЧВ)"
        elif is_active_consumer:
            node_role = "Абонентский узел с водоразбором"
        else:
            node_role = "Транзитный соединительный узел сети"

        deg = node_degree.get(nid, node_degree.get(cid, 1))
        tr_flow = round(node_transit_flow.get(nid, node_transit_flow.get(cid, 0.0)), 2)
        conn_pipes = adj_pipes_by_node.get(cid) or adj_pipes_by_node.get(nid) or []
        conn_pipe_ids = [p["clean_id"] for p in conn_pipes[:4]]

        is_isolated_zone = bool(has_valid_topology and cid not in reachable_from_sources)
        if is_isolated_zone and is_active_consumer:
            isolated_consumer_nodes_count += 1
        cutoff_pipe_id = cutoff_pipe_by_node.get(cid)

        p_category = "normal"
        node_diag = "Нормативный гидравлический режим (СП 31.13330.2012 п. 5.11–5.13)."
        z_disp = round(z_node, 2) if z_node is not None else "—"
        if p_min is not None:
            if p_min < -3.0:
                p_category = "data_error"
                closed_nb = [p["clean_id"] for p in conn_pipes if p.get("is_closed")]
                high_loss_nb = sorted(conn_pipes, key=lambda x: x.get("headloss_gradient_m_km") or 0, reverse=True)
                if is_isolated_zone and (cutoff_pipe_id or closed_nb):
                    cut_id = cutoff_pipe_id or closed_nb[0]
                    node_diag = (
                        f"Расчетный артефакт DDA (Pмин={p_min} м < -10 м физ. вакуума): граф подтверждает отсечение от источников "
                        f"из-за перекрытого участка #{cut_id} (Q=0) при заданном отборе {round(q_mean, 2)} м³/ч. В Аквабит: открыть участок #{cut_id}."
                    )
                elif closed_nb:
                    node_diag = (
                        f"Нефизическое давление Pмин={p_min} м (артефакт DDA-расчета): рядом перекрытый участок #{closed_nb[0]} (Q=0) "
                        f"при активном отборе {round(q_mean, 2)} м³/ч. В Аквабит: проверить статус #{closed_nb[0]} и отметку Z={z_disp} м."
                    )
                elif high_loss_nb and (high_loss_nb[0].get("headloss_gradient_m_km") or 0) >= 15.0:
                    nb0 = high_loss_nb[0]
                    node_diag = (
                        f"Расчетное падение напора до Pмин={p_min} м из-за потерь на подводящем участке #{nb0['clean_id']} "
                        f"(i={nb0['headloss_gradient_m_km']} м/км, Δh={nb0.get('total_headloss_m', 0)} м, D={nb0['diameter_mm']} мм) при отметке Z={z_disp} м."
                    )
                elif has_valid_topology and not is_isolated_zone:
                    node_diag = (
                        f"Ошибка исходной геодезической отметки Z={z_disp} м (расчетный артефакт Pмин={p_min} м): "
                        f"узел гидравлически связан с источниками (смежные трубы: {', '.join('#'+x for x in conn_pipe_ids) or 'нет'}). В Аквабит: исправить Z."
                    )
                else:
                    node_diag = (
                        f"Нефизическое отрицательное давление Pмин={p_min} м (артефакт DDA при Z={z_disp} м, смежные участки: "
                        f"{', '.join('#'+x for x in conn_pipe_ids) or 'нет'}). Проверить отметку Z и связность ветки в ПК «Аквабит»."
                    )
            elif p_min < 0.0:
                p_category = "negative"
                node_diag = (
                    f"Субатмосферное давление (вакуум в сети) Pмин={p_min} м при отметке Z={z_disp} м "
                    f"(подводящие трубы: {', '.join('#'+x for x in conn_pipe_ids)})."
                )
            elif req_m > 0 and p_min < req_m:
                p_category = "deficit"
                high_loss_nb = sorted(conn_pipes, key=lambda x: x.get("headloss_gradient_m_km") or 0, reverse=True)
                if high_loss_nb and (high_loss_nb[0].get("headloss_gradient_m_km") or 0) >= 10.0:
                    nb0 = high_loss_nb[0]
                    node_diag = (
                        f"Нехватка напора для верхних этажей на {deficit_abs_m} м (ΔP={margin_m} м при положительном давлении в сети Pмин={p_min} м > 0, вакуума нет; "
                        f"{fl_cnt or 1} эт., Pтреб={req_m} м по СП 31.13330 п. 5.11): потери на подводящей трубе #{nb0['clean_id']} составляют {nb0['headloss_gradient_m_km']} м/км (Δh={nb0.get('total_headloss_m', 0)} м, D={nb0['diameter_mm']} мм)."
                    )
                else:
                    node_diag = (
                        f"Дефицит свободного напора {deficit_abs_m} м (ΔP={margin_m} м) для {fl_cnt or 1}-эт. застройки при положительном напоре в магистрали Pмин={p_min} м "
                        f"(вакуума нет; Pтреб={req_m} м по СП 31.13330 п. 5.11, Z={z_disp} м, трубы: {', '.join('#'+x for x in conn_pipe_ids)}). Требуется локальная ПНС (СП 31.13330 п. 5.11 Примечание 2) или закольцовка (п. 11.5)."
                    )
            elif p_max is not None and p_max > 60.0:
                p_category = "excess"
                node_diag = (
                    f"Избыточный свободный напор Pмакс={p_max} м (превышает предел 60 м по СП 31.13330.2012 п. 5.13) при низкой отметке Z={z_disp} м. "
                    f"Рекомендуется зонирование или установка PRV на вводе (участки {', '.join('#'+x for x in conn_pipe_ids)})."
                )

        sched_list = node_schedules.get(nid) or node_schedules.get(cid) or node_schedules.get(f"c{cid}") or []
        clean_nm = clean_object_name(obj_names.get(nid) or obj_names.get(cid) or "")
        prop_obj = {
            "role": node_role,
            "z": round(z_node, 2) if z_node is not None else None,
            "p_min": p_min,
            "p_mean": p_mean,
            "p_max": p_max,
            "req": round(req_m, 2) if req_m > 0 else "Не нормируется (транзит)",
            "floors": fl_cnt,
            "margin": margin_m,
            "pressure_deficit_abs_m": deficit_abs_m,
            "is_vacuum": is_vacuum,
            "demand": round(q_mean, 3),
            "transit_q": tr_flow,
            "connected_pipes": deg,
            "connected_pipe_ids": conn_pipe_ids,
            "pressure_category": p_category,
            "diagnostic_advice": node_diag,
            "schedules": ", ".join(sorted(set(sched_list))) or ("Транзит" if not is_active_consumer else "Базовый"),
            "name": clean_nm,
        }
        all_node_props[nid] = prop_obj
        all_node_props[cid] = prop_obj
        all_node_props[f"c{cid}"] = prop_obj
        all_node_props[f"Ju{cid}"] = prop_obj

        if not is_active_consumer:
            continue

        if p_min is not None:
            if p_min < -3.0:
                pressure_bins["Ошибка / отсечение (< -3.0 м)"] += 1
            elif p_min < 0.0:
                pressure_bins["Отрицат. давление (-3.0 .. 0 м)"] += 1
            elif req_m > 0 and p_min < req_m:
                pressure_bins["Недостаток давления (< P_треб)"] += 1
            elif p_max is not None and p_max > 60.0:
                pressure_bins["Избыток (> 60 м)"] += 1
            else:
                pressure_bins["Норма (P_треб .. 60 м)"] += 1

        node_records.append({
            "node_id": nid,
            "clean_id": cid,
            "name": clean_nm,
            "display_id": label_with_name(nid),
            "elevation_m": round(z_node, 2) if z_node is not None else None,
            "floors": fl_cnt,
            "required_pressure_m": round(req_m, 2) if req_m > 0 else None,
            "free_pressure_min_m": p_min,
            "free_pressure_max_m": p_max,
            "pressure_surplus_deficit_m": margin_m,
            "pressure_deficit_abs_m": deficit_abs_m,
            "is_vacuum": is_vacuum,
            "demand_mean_m3_h": round(q_mean, 3),
            "transit_flow_m3_h": tr_flow,
            "pressure_category": p_category,
            "connected_pipe_ids": conn_pipe_ids,
            "diagnostic_advice": node_diag,
            "is_shutoff_well_or_zero_demand": is_shutoff_well,
        })

    real_deficit_nodes = sorted(
        [
            r for r in node_records
            if not r["is_shutoff_well_or_zero_demand"]
            and r["free_pressure_min_m"] is not None
            and r["free_pressure_min_m"] >= -3.0
            and r["pressure_surplus_deficit_m"] is not None
            and r["pressure_surplus_deficit_m"] < 0
        ],
        key=lambda x: x["pressure_surplus_deficit_m"],
    )
    data_error_nodes = sorted(
        [
            r for r in node_records
            if not r["is_shutoff_well_or_zero_demand"]
            and r["free_pressure_min_m"] is not None
            and r["free_pressure_min_m"] < -3.0
        ],
        key=lambda x: x["free_pressure_min_m"],
    )
    shutoff_well_nodes = [
        r for r in node_records
        if r["is_shutoff_well_or_zero_demand"]
        and r["free_pressure_min_m"] is not None
        and r["free_pressure_min_m"] < 0
    ]
    excess_nodes = sorted(
        [r for r in node_records if r["free_pressure_max_m"] is not None and r["free_pressure_max_m"] > 60.0],
        key=lambda x: x["free_pressure_max_m"],
        reverse=True,
    )

    all_high_loss_pipes = sorted(
        [p for p in pipe_records if (p["velocity_max_m_s"] or 0) >= 5.0 or (p["headloss_gradient_m_km"] or 0) >= 15.0],
        key=lambda x: (x["headloss_gradient_m_km"] or 0),
        reverse=True,
    )
    bottleneck_pipes_total_count = len(all_high_loss_pipes)
    high_loss_pipes = all_high_loss_pipes if all_high_loss_pipes else sorted(
        [p for p in pipe_records if (p["headloss_gradient_m_km"] or 0) > 0],
        key=lambda x: (x["headloss_gradient_m_km"] or 0),
        reverse=True,
    )[:top_k]

    low_c_pipes = sorted(
        [p for p in pipe_records if p["hazen_williams_c"] is not None and p["hazen_williams_c"] < 80.0 and (p["flow_max_m3_h"] or 0) > 1.0],
        key=lambda x: (x["hazen_williams_c"], -(x["headloss_gradient_m_km"] or 0)),
    )
    degraded_high_loss_pipes = sorted(
        [
            p for p in pipe_records
            if p["hazen_williams_c"] is not None
            and p["hazen_williams_c"] < 80.0
            and (p["headloss_gradient_m_km"] or 0.0) >= 20.0
        ],
        key=lambda x: (x["headloss_gradient_m_km"] or 0.0),
        reverse=True,
    )
    closed_by_valve_pipes_list = [p for p in pipe_records if p.get("is_closed_by_valve")]
    zero_flow_open_pipes_list = [p for p in pipe_records if p.get("is_zero_flow") and not p.get("is_closed_by_valve")]
    closed_pipes_list = [p for p in pipe_records if p.get("is_closed")]
    reverse_flow_pipes = sorted(
        [p for p in pipe_records if p.get("reverse_steps", 0) > 0],
        key=lambda x: x["reverse_steps"],
        reverse=True,
    )

    pipes_for_client_table: list[dict[str, Any]] = []
    seen_p_ids: set[str] = set()
    for p_list in (
        all_high_loss_pipes[:80],
        low_c_pipes[:40],
        closed_by_valve_pipes_list[:50],
        closed_pipes_list[:30],
        [p for p in pipe_records if p.get("velocity_bin") == "Низкие скорости (< 0.10 м/с)"][:50],
        [p for p in pipe_records if p.get("velocity_bin") == "Умеренные (0.10–0.50 м/с)"][:40],
        [p for p in pipe_records if p.get("velocity_bin") == "Оптимум (0.50–1.50 м/с)"][:40],
        [p for p in pipe_records if p.get("velocity_bin") == "Повышенные (1.50–5.00 м/с)"][:40],
        [p for p in pipe_records if p.get("velocity_bin") == "Большая скорость (>= 5.00 м/с)"][:40],
    ):
        for pr in p_list:
            if pr["pipe_id"] not in seen_p_ids:
                seen_p_ids.add(pr["pipe_id"])
                pipes_for_client_table.append(pr)

    # 7. Насосы с графовой причинно-следственной диагностикой (Источник -> Насос -> Коллектор -> Труба)
    qh_raw: dict[str, list[tuple[float, float]]] = dict(topo_meta.get("qh_curves") or {})
    if not df_qh.empty and len(df_qh.columns) >= 3:
        c0, c1, c2 = df_qh.columns[0], df_qh.columns[1], df_qh.columns[2]
        q_vals = to_numeric_series(df_qh[c1])
        h_vals = to_numeric_series(df_qh[c2])
        for uid, qv, hv in zip(df_qh[c0], q_vals, h_vals):
            if not (math.isnan(float(qv)) or math.isnan(float(hv))):
                uid_s = str(uid).strip()
                qh_raw.setdefault(uid_s, []).append((float(qv), float(hv)))
                qh_raw.setdefault(clean_object_id(uid_s), []).append((float(qv), float(hv)))

    all_pumps_list: list[dict[str, Any]] = []
    qh_chart_items: list[dict[str, Any]] = []
    total_theoretical_q_m3_h = 0.0
    total_pumped_m3_day = 0.0
    first_lift_daily_supply_m3_day = 0.0
    booster_daily_repumping_m3_day = 0.0
    first_lift_peak_flow_m3_h = 0.0
    booster_peak_flow_m3_h = 0.0
    total_pump_energy_kwh_day = 0.0

    for p_item in pump_iter_items:
        pid = p_item["pump_id"]
        cid_s = str(p_item.get("curve_id") or "").strip()
        pts = qh_raw.get(cid_s) or qh_raw.get(clean_object_id(cid_s)) or []
        plot_info = build_qh_curve_plot_points(pts)
        q_nom = plot_info.get("q_nominal_m3_h")
        h_nom = plot_info.get("h_nominal_m")
        if q_nom:
            total_theoretical_q_m3_h += q_nom

        sn_node, en_node = pump_endpoints.get(pid, ("", ""))
        sn_clean_num = clean_object_id(sn_node)
        en_clean_num = clean_object_id(en_node)
        pid_clean_num = clean_object_id(pid)

        is_borehole = (
            (sn_node in source_ids_set)
            or (sn_clean_num in source_clean_ids and sn_clean_num != "")
            or (pid_clean_num in source_clean_ids and pid_clean_num != "")
        )
        pump_kind = "Скважинный (1-й подъем, от источника)" if is_borehole else "Сетевой / повысительный (НС)"

        sn_props = all_node_props.get(sn_node) or all_node_props.get(sn_clean_num) or {}
        en_props = all_node_props.get(en_node) or all_node_props.get(en_clean_num) or {}
        src_head = source_head_by_cid.get(sn_clean_num) or sn_props.get("z")
        col_z = en_props.get("z")
        col_p_mean = en_props.get("p_mean") if en_props.get("p_mean") is not None else en_props.get("p_max")
        col_p_max = en_props.get("p_max")
        out_pipes = adj_pipes_by_node.get(en_clean_num) or adj_pipes_by_node.get(en_node) or []
        out_pipes_sorted = sorted(out_pipes, key=lambda x: (x.get("headloss_gradient_m_km") or 0.0), reverse=True)
        top_out_pipe = out_pipes_sorted[0] if out_pipes_sorted else None
        closed_out_pipes = [p for p in out_pipes if p.get("is_closed_by_valve")]

        pst = pump_ts["rows"].get(pid)
        is_active = bool(pst and pst["max"] > 1e-3)
        q_work, q_max_p, h_est, ratio, run_h = None, None, None, None, 0.0

        if not is_active:
            zone_label = "Выключен / резерв (Q = 0)"
            if closed_out_pipes:
                cp_id = closed_out_pipes[0]["clean_id"]
                diag_advice = (
                    f"Холодный резерв (Q = 0 м³/ч): за напорным узлом #{en_clean_num or '—'} перекрыт задвижкой отходящий участок #{cp_id} (CLOSED). "
                    f"По СП 31.13330.2021 п. 10.3 (Таблица 23) наличие резервных агрегатов обязательно."
                )
            elif col_p_max is not None and col_p_max > 10:
                diag_advice = (
                    f"Агрегат #{pid_clean_num} находится в нормативном холодном резерве (Q = 0 м³/ч, СП 31.13330.2021 п. 10.3 Таблица 23). "
                    f"В приемном узле коллектора #{en_clean_num or '—'} поддерживается штатный напор P = {col_p_max} м от работающих насосов (аварии нет)."
                )
            else:
                diag_advice = (
                    f"Агрегат #{pid_clean_num} находится в резерве / отключен в расчете (Q = 0 м³/ч, СП 31.13330.2021 п. 10.3). "
                    f"Связь: узел #{sn_clean_num or '—'} → коллектор #{en_clean_num or '—'}."
                )
        else:
            run_h = round(pst["pos_steps"] * step_min / 60.0, 2)
            q_work = round(pst["mean"] * (max(pump_ts["n_steps"], 1) / max(pst["pos_steps"], 1)), 2)
            q_max_p = round(pst["max"], 2)
            daily_m3 = pst["mean"] * 24.0
            total_pumped_m3_day += daily_m3
            if is_borehole:
                first_lift_daily_supply_m3_day += daily_m3
                first_lift_peak_flow_m3_h += q_max_p
            else:
                booster_daily_repumping_m3_day += daily_m3
                booster_peak_flow_m3_h += q_max_p

            h_est = evaluate_qh_curve_head(pts, q_work)
            if h_est is not None and q_work > 0:
                kw_hyd = (9.81 * q_work * h_est) / 3600.0
                total_pump_energy_kwh_day += kw_hyd * run_h
            ratio = round(q_work / q_nom, 2) if (q_nom and q_nom > 0) else None
            pct_nom = int(round((ratio or 0) * 100))

            if ratio is None:
                zone_label = "Кривая Q-H не задана"
                diag_advice = f"Фактическая подача Q = {q_work} м³/ч в узел #{en_clean_num or '—'}, но паспортная кривая #{cid_s} не найдена."
            elif ratio < 0.75:
                zone_label = "Недогрузка (< 75% Q_ном)"
                col_p_disp = col_p_mean if col_p_mean is not None else "—"
                col_z_disp = col_z if col_z is not None else "—"
                src_h_disp = src_head if src_head is not None else "—"
                sn_p_disp = sn_props.get("p_mean") if sn_props.get("p_mean") is not None else "—"
                if top_out_pipe and (top_out_pipe.get("headloss_gradient_m_km") or 0) >= 15.0:
                    tp_id = top_out_pipe["clean_id"]
                    tp_i = top_out_pipe["headloss_gradient_m_km"]
                    tp_dh = top_out_pipe.get("total_headloss_m", 0)
                    tp_d = top_out_pipe["diameter_mm"]
                    tp_c = top_out_pipe["hazen_williams_c"]
                    diag_advice = (
                        f"Рабочая точка смещена влево от зоны BEP (подача зажата до {q_work} м³/ч, {pct_nom}% от Qном={q_nom} м³/ч) "
                        f"из-за высокого сопротивления отходящего от коллектора #{en_clean_num} водовода #{tp_id} "
                        f"(D={tp_d} мм, C={tp_c}, удельный уклон i={tp_i} м/км, Δh={tp_dh} м; давление в коллекторе #{en_clean_num} P={col_p_disp} м). "
                        f"Мероприятие: увеличить пропускную способность линии #{tp_id} или перераспределить нагрузку."
                    )
                elif closed_out_pipes:
                    cp_id = closed_out_pipes[0]["clean_id"]
                    diag_advice = (
                        f"Подача ограничена до {q_work} м³/ч ({pct_nom}% Qном): в узле коллектора #{en_clean_num} (P={col_p_disp} м) "
                        f"перекрыта задвижкой смежная линия #{cp_id} (Q=0). Мероприятие: открыть участок #{cp_id} для разгрузки коллектора #{en_clean_num}."
                    )
                elif is_borehole:
                    if col_p_mean is not None and h_nom is not None and col_p_mean >= h_nom * 0.85:
                        diag_advice = (
                            f"Скважинный агрегат #{pid_clean_num} (источник #{sn_clean_num}, Hист={src_h_disp} м) выдает {q_work} м³/ч ({pct_nom}% Qном) "
                            f"вследствие высокого противодавления P={col_p_mean} м в сборном коллекторе #{en_clean_num} (Z={col_z_disp} м). "
                            f"Мероприятие: разгрузить сборный коллектор #{en_clean_num} или подобрать агрегат с меньшим напором."
                        )
                    else:
                        diag_advice = (
                            f"Скважинный агрегат #{pid_clean_num} развивает напор H={h_est} м при подаче {q_work} м³/ч ({pct_nom}% Qном) на подъеме от источника #{sn_clean_num} "
                            f"(отметка/напор {src_h_disp} м) к узлу #{en_clean_num} (Z={col_z_disp} м, P={col_p_disp} м). "
                            f"Мероприятие: проверить динамический уровень в источнике #{sn_clean_num} и соответствие кривой #{cid_s} геодезическому подъему."
                        )
                else:
                    diag_advice = (
                        f"Сетевой насос #{pid_clean_num} работает левее зоны BEP ({q_work} м³/ч, {pct_nom}% от Qном={q_nom} м³/ч, H={h_est} м) "
                        f"на участке #{sn_clean_num} (Pвх={sn_p_disp} м) → #{en_clean_num} (Pвых={col_p_disp} м) из-за повышенного противодавления сети."
                    )
            elif ratio <= 1.15:
                col_p_disp = col_p_mean if col_p_mean is not None else "—"
                zone_label = "Оптимум BEP (75–115% Q_ном)"
                diag_advice = (
                    f"Штатная работа в зоне оптимума КПД (BEP, {pct_nom}% Qном): подача Q={q_work} м³/ч, напор H={h_est} м "
                    f"(узел входа #{sn_clean_num or '—'} → напорный узел #{en_clean_num or '—'}, Pвых={col_p_disp} м)."
                )
            else:
                col_p_disp = col_p_mean if col_p_mean is not None else "—"
                zone_label = "Перегрузка (> 115% Q_ном)"
                tp_info = f" в водовод #{top_out_pipe['clean_id']} (D={top_out_pipe['diameter_mm']} мм)" if top_out_pipe else ""
                diag_advice = (
                    f"Работа правее зоны оптимального КПД (BEP, {pct_nom}% Qном): расход вырос до Q={q_work} м³/ч (при Qном={q_nom} м³/ч) "
                    f"со снижением напора по характеристике Q-H до H={h_est} м вследствие низкого противодавления в приемном коллекторе #{en_clean_num or '—'} (P={col_p_disp} м){tp_info}. "
                    f"Мероприятие по СП 31.13330 п. 10.7: применить частотное регулирование (ЧРП) для снижения оборотов либо дросселировать напорную задвижку после узла #{en_clean_num} для возврата рабочей точки в зону BEP."
                )

        disp = label_with_name(pid)
        cid = clean_object_id(pid)
        clean_nm = clean_object_name(obj_names.get(pid) or obj_names.get(cid) or "")
        p_entry = {
            "pump_id": pid,
            "clean_id": cid,
            "name": clean_nm,
            "display_id": disp,
            "pump_type": pump_kind,
            "is_borehole": is_borehole,
            "start_node": sn_clean_num or sn_node,
            "end_node": en_clean_num or en_node,
            "source_head_m": src_head,
            "collector_pressure_m": col_p_mean,
            "collector_elevation_m": col_z,
            "connected_out_pipe_id": top_out_pipe["clean_id"] if top_out_pipe else None,
            "connected_out_pipe_loss": top_out_pipe["headloss_gradient_m_km"] if top_out_pipe else None,
            "curve_id": cid_s,
            "active": is_active,
            "run_hours_day": run_h,
            "q_nominal_m3_h": q_nom,
            "h_nominal_m": h_nom,
            "q_working_m3_h": q_work,
            "q_max_m3_h": q_max_p,
            "h_working_m": h_est,
            "h_est_m": h_est,
            "q_ratio": ratio,
            "regime": zone_label,
            "diagnostic_advice": diag_advice,
        }
        all_pumps_list.append(p_entry)

        if pts:
            qh_chart_items.append({
                "label": f"{disp} → {cid_s}" + (" [Скваж]" if is_borehole else " [Сетев]") + (" [РАБ]" if is_active else " [ВЫКЛ]"),
                "pump_id": pid,
                "clean_id": cid,
                "name": clean_nm,
                "curve_id": cid_s,
                "active": is_active,
                "is_borehole": is_borehole,
                "pump_kind": pump_kind,
                "start_node": sn_clean_num or sn_node,
                "end_node": en_clean_num or en_node,
                "source_head_m": src_head,
                "collector_pressure_m": col_p_mean,
                "collector_elevation_m": col_z,
                "connected_out_pipe_id": top_out_pipe["clean_id"] if top_out_pipe else None,
                "connected_out_pipe_loss": top_out_pipe["headloss_gradient_m_km"] if top_out_pipe else None,
                "operating_q_mean": q_work,
                "operating_q_max": q_max_p,
                "operating_h_est": h_est,
                "q_ratio": ratio,
                "regime": zone_label,
                "diagnostic_advice": diag_advice,
                **plot_info,
            })

    # 8. Клапаны
    valve_type_map = {"V1": "PRV", "V2": "PSV", "V3": "PBV", "V4": "FCV", "V5": "TCV", "V6": "GPV"}
    if not df_valve_types.empty and len(df_valve_types.columns) >= 2:
        c0, c1 = df_valve_types.columns[0], df_valve_types.columns[1]
        for vtid, vtname in zip(df_valve_types[c0], df_valve_types[c1]):
            valve_type_map[str(vtid).strip()] = str(vtname).strip()

    csv_valve_attr_by_cid: dict[str, dict[str, Any]] = {}
    if not df_valves.empty:
        c_id = df_valves.columns[0]
        c_s = find_col(df_valves, "начал", default_idx=1 if len(df_valves.columns) > 1 else None)
        c_e = find_col(df_valves, "конец", "конечн", default_idx=2 if len(df_valves.columns) > 2 else None)
        c_t = find_col(df_valves, "тип", default_idx=3 if len(df_valves.columns) > 3 else None)
        c_set = find_col(df_valves, "настройк", "уставк", default_idx=4 if len(df_valves.columns) > 4 else None)
        vsets = to_numeric_series(df_valves[c_set]) if c_set else [math.nan] * len(df_valves)
        for vid, sn, en, vtype_raw, vset in zip(
            df_valves[c_id],
            df_valves[c_s] if c_s else [""] * len(df_valves),
            df_valves[c_e] if c_e else [""] * len(df_valves),
            [str(x).strip() for x in df_valves[c_t]] if c_t else ["V1"] * len(df_valves),
            vsets,
        ):
            cid_v = clean_object_id(str(vid))
            csv_valve_attr_by_cid[cid_v] = {
                "valve_id": str(vid).strip(),
                "start_node": str(sn).strip(),
                "end_node": str(en).strip(),
                "type_raw": vtype_raw,
                "setting": float(vset),
            }

    valve_iter_items: list[dict[str, Any]] = []
    if use_json_primary and json_bundle is not None and json_bundle["raw_valves"]:
        for rv in json_bundle["raw_valves"]:
            cid_v = rv["clean_id"]
            fb = csv_valve_attr_by_cid.get(cid_v, {})
            vset = rv["setting"] if not math.isnan(rv["setting"]) else fb.get("setting", math.nan)
            valve_iter_items.append({
                "valve_id": rv["valve_id"],
                "start_node": rv["start_node"] or fb.get("start_node", ""),
                "end_node": rv["end_node"] or fb.get("end_node", ""),
                "type_raw": rv["type_raw"] if rv["type_raw"] else fb.get("type_raw", "V1"),
                "setting": vset,
            })
    else:
        for _cid_v, fb in csv_valve_attr_by_cid.items():
            valve_iter_items.append(fb)

    valves_table: list[dict[str, Any]] = []
    valves_hourly_series: list[dict[str, Any]] = []
    self_loop_valves = 0

    for v_item in valve_iter_items:
        vid_s = str(v_item["valve_id"]).strip()
        sn_s, en_s, had_bug = resolve_link_endpoints(vid_s, v_item["start_node"], v_item["end_node"])
        if had_bug:
            self_loop_valves += 1
        vtype_raw = str(v_item.get("type_raw") or "V1").strip()
        vset = float(v_item.get("setting", math.nan))
        vt_code = valve_type_map.get(vtype_raw, vtype_raw)
        vt_desc = VALVE_TYPE_DESC.get(vt_code, "Регулирующий клапан")
        vst = valve_ts["rows"].get(vid_s)
        q_min_v = round(vst["min"], 2) if vst else 0.0
        q_mean_v = round(vst["mean"], 2) if vst else 0.0
        q_max_v = round(vst["max"], 2) if vst else 0.0
        q_swing_v = round(q_max_v - q_min_v, 2)

        sn_c_v = clean_object_id(sn_s) or sn_s
        en_c_v = clean_object_id(en_s) or en_s
        if sn_c_v and en_c_v and sn_c_v != en_c_v:
            open_adj.setdefault(sn_c_v, []).append(en_c_v)
            open_adj.setdefault(en_c_v, []).append(sn_c_v)

        _sn_p = (all_node_props.get(sn_s) or all_node_props.get(sn_c_v) or all_node_props.get(f"c{sn_c_v}") or {}).get("p_mean")
        _en_p = (all_node_props.get(en_s) or all_node_props.get(en_c_v) or all_node_props.get(f"c{en_c_v}") or {}).get("p_mean")
        _dp_v = round(float(_sn_p) - float(_en_p), 2) if (isinstance(_sn_p, (int, float)) and isinstance(_en_p, (int, float))) else None
        vid_cid = clean_object_id(vid_s)
        valves_table.append({
            "valve_id": vid_s,
            "clean_id": vid_cid,
            "name": clean_object_name(obj_names.get(vid_s) or obj_names.get(vid_cid) or ""),
            "start_node": sn_c_v,
            "end_node": en_c_v,
            "type_id": vtype_raw,
            "type_code": vt_code,
            "type_desc": vt_desc,
            "setting": None if math.isnan(float(vset)) else round(float(vset), 2),
            "p_before_m": _sn_p,
            "p_after_m": _en_p,
            "delta_p_m": _dp_v,
            "flow_min_m3_h": q_min_v,
            "flow_mean_m3_h": q_mean_v,
            "flow_max_m3_h": q_max_v,
            "flow_swing_m3_h": q_swing_v,
            "status": "Закрыт (Q=0)" if q_max_v <= 0.01 else ("Активное регулирование" if q_swing_v > 25.0 else "Стабильный пропуск"),
        })
        if vid_s in valve_ts["row_hourly"]:
            valves_hourly_series.append({
                "valve_id": vid_s,
                "clean_id": clean_object_id(vid_s),
                "label": f"Клапан {clean_object_id(vid_s)} ({vt_code}, уст. {round(float(vset), 1) if not math.isnan(float(vset)) else '—'})",
                "data": valve_ts["row_hourly"][vid_s],
            })

    if self_loop_valves > 0:
        data_export_warnings.append(
            f"Обнаружено {self_loop_valves} клапанов, где Начальный узел == Конечный узел в табличной выгрузке."
        )

    active_pumps = [p for p in all_pumps_list if p["active"]]
    peak_pump_flow_m3_h = round(max(pump_ts["hourly_24"]), 2) if pump_ts["hourly_24"] else 0.0
    total_daily_cons = round(sum(r["demand_mean_m3_h"] * 24.0 for r in node_records), 2)
    dem_24_vals = [float(v) for v in (dem_ts.get("hourly_24") or []) if v is not None]
    peak_network_cons = round(max(dem_24_vals), 2) if dem_24_vals else round(sum(base_demand.values()), 2)
    dem_nonzero = [v for v in dem_24_vals if v > 0]
    mean_hourly_cons = (sum(dem_nonzero) / len(dem_nonzero)) if dem_nonzero else sum(base_demand.values())
    peak_factor_k_hour = round(peak_network_cons / mean_hourly_cons, 2) if mean_hourly_cons > 0 else 1.0

    installed_vs_actual_flow_gap_m3_h = round(total_theoretical_q_m3_h - peak_network_cons, 2)

    # Расчет баланса: 1-й подъем (подача в сеть из источников), перекачка НС 2/3-го подъема и потери воды (физические утечки + коммерческие потери)
    primary_supply_m3_day = round(first_lift_daily_supply_m3_day if first_lift_daily_supply_m3_day > 0 else total_pumped_m3_day, 2)
    water_losses_m3_day = round(max(0.0, primary_supply_m3_day - total_daily_cons), 2)
    water_losses_pct = round((water_losses_m3_day / primary_supply_m3_day) * 100.0, 1) if primary_supply_m3_day > 0 else 0.0

    anomalies = []
    if head_ts["hourly_24"] and head_ts["hourly_24"]["p50"]:
        p50_arr = [v for v in head_ts["hourly_24"]["p50"] if v is not None]
        if len(p50_arr) >= 24:
            diffs = [(i, round(p50_arr[i] - p50_arr[i - 1], 2)) for i in range(1, len(p50_arr))]
            max_jump = max(diffs, key=lambda x: abs(x[1]))
            if abs(max_jump[1]) >= 4.0:
                anomalies.append(f"Резкий скачок медианного свободного напора по сети на {max_jump[1]:+.1f} м в {max_jump[0]:02d}:00.")
    if valves_table:
        top_v = max(valves_table, key=lambda x: x["flow_swing_m3_h"])
        if top_v["flow_swing_m3_h"] > 50:
            anomalies.append(
                f"Клапан {top_v['clean_id']} ({top_v['type_code']}, уставка {top_v['setting']}) работает с глубокой модуляцией потока: от {top_v['flow_min_m3_h']} до {top_v['flow_max_m3_h']} м³/ч (амплитуда ΔQ = {top_v['flow_swing_m3_h']} м³/ч)."
            )
    if high_loss_pipes:
        top_p = high_loss_pipes[0]
        anomalies.append(
            f"Лимитирующий участок {top_p['display_id']} (узлы {top_p['start_node']} → {top_p['end_node']}, D={top_p['diameter_mm']} мм, L={top_p['length_m']} м, C={top_p['hazen_williams_c']}): скорость {top_p['velocity_max_m_s']} м/с создаёт удельную потерю {top_p['headloss_gradient_m_km']} м/км (фактический перепад Δh={top_p.get('total_headloss_m', 0)} м)."
        )

    p_mins_all = [r["free_pressure_min_m"] for r in node_records if r["free_pressure_min_m"] is not None]
    p_maxs_all = [r["free_pressure_max_m"] for r in node_records if r["free_pressure_max_m"] is not None]
    c_vals = [p["hazen_williams_c"] for p in pipe_records if p["hazen_williams_c"] is not None]
    v_vals = [p["velocity_max_m_s"] for p in pipe_records if p["velocity_max_m_s"] is not None]
    total_vol_m3 = round(sum(math.pi * ((p["diameter_mm"] / 1000.0) ** 2) / 4.0 * p["length_m"] for p in pipe_records), 2)
    rough_mean = round(sum(c_vals) / len(c_vals), 2) if c_vals else None
    low_c_km = round(sum(p["length_m"] / 1000.0 for p in pipe_records if p["hazen_williams_c"] is not None and p["hazen_williams_c"] < 78.0), 2)
    max_v = round(max(v_vals), 3) if v_vals else None
    v_over_15 = sum(1 for v in v_vals if v >= 1.5)
    v_under_03 = sum(1 for v in v_vals if v < 0.3)
    rev_cnt = len(reverse_flow_pipes)
    p_min_network = round(min(p_mins_all), 2) if p_mins_all else None
    p_max_network = round(max(p_maxs_all), 2) if p_maxs_all else None

    facts = {
        "city_name": city_name,
        "data_source_type": "json_digwater" if use_json_primary else f"csv_{topology_source}",
        "has_inp_names": bool(obj_names),
        "simulation_steps": n_steps,
        "data_export_warnings": data_export_warnings,
        "water_balance": {
            "total_nodes_in_model": len(all_known_nodes),
            "active_consumer_nodes": len(node_records),
            "transit_nodes_count": max(0, len(all_known_nodes) - len(node_records)),
            "total_daily_consumption_m3_day": total_daily_cons,
            "peak_network_demand_m3_h": peak_network_cons,
            "peak_factor_k_hour": peak_factor_k_hour,
            "pumps_installed_capacity_m3_h": round(total_theoretical_q_m3_h, 2),
            "pumps_theoretical_capacity_m3_h": round(total_theoretical_q_m3_h, 2),
            "pumps_theoretical_capacity_m3_day": round(total_theoretical_q_m3_h * 24.0, 2),
            "pumps_peak_actual_flow_m3_h": peak_pump_flow_m3_h,
            "pumps_total_actual_m3_day": round(total_pumped_m3_day, 2),
            "first_lift_daily_supply_m3_day": round(first_lift_daily_supply_m3_day, 2),
            "first_lift_peak_flow_m3_h": round(first_lift_peak_flow_m3_h, 2),
            "booster_daily_repumping_m3_day": round(booster_daily_repumping_m3_day, 2),
            "booster_peak_flow_m3_h": round(booster_peak_flow_m3_h, 2),
            "estimated_water_losses_and_unbilled_m3_day": water_losses_m3_day,
            "estimated_water_losses_pct": water_losses_pct,
            "installed_vs_demand_flow_gap_m3_h": installed_vs_actual_flow_gap_m3_h,
            "capacity_vs_demand_gap_m3_day": round(total_theoretical_q_m3_h * 24.0 - total_daily_cons, 2),
        },
        "pressure_analysis": {
            "pressure_bins": pressure_bins,
            "free_pressure_min_m": p_min_network,
            "free_pressure_max_m": p_max_network,
            "real_required_pressure_deficit_count": len(real_deficit_nodes),
            "shutoff_wells_negative_count": len(shutoff_well_nodes),
            "shutoff_wells_nodes": shutoff_well_nodes[:25],
            "data_error_below_minus_3m_count": len(data_error_nodes),
            "excess_over_60m_count": len(excess_nodes),
            "worst_deficit_nodes": real_deficit_nodes[:top_k],
            "all_deficit_nodes": real_deficit_nodes[:250],
            "worst_data_error_nodes": data_error_nodes[:50],
            "top_excess_nodes": excess_nodes[:50],
        },
        "pipes_analysis": {
            "pipes_count": len(pipe_records),
            "total_pipes": len(pipe_records),
            "total_length_km": round(total_len_km, 2),
            "total_volume_m3": total_vol_m3,
            "mean_hazen_williams_c": rough_mean,
            "low_c_under_78_km": low_c_km,
            "max_velocity_m_s": max_v,
            "high_velocity_over_15ms_count": v_over_15,
            "low_velocity_under_03ms_count": v_under_03,
            "bottleneck_pipes_count": bottleneck_pipes_total_count,
            "anomalous_low_c_pipes_count": sum(1 for p in pipe_records if p.get("is_anomalous_low_c")),
            "closed_by_valve_pipes_count": len(closed_by_valve_pipes_list),
            "zero_flow_open_pipes_count": len(zero_flow_open_pipes_list),
            "closed_zero_flow_pipes_count": len(closed_pipes_list),
            "closed_pipes_sample": (closed_by_valve_pipes_list + zero_flow_open_pipes_list)[:50],
            "reverse_flow_count": rev_cnt,
            "reverse_flow_pipes": reverse_flow_pipes[:25],
            "velocity_bins": velocity_bins,
            "high_velocity_over_5ms_count": velocity_bins["Большая скорость (>= 5.00 м/с)"],
            "low_velocity_under_01ms_count": velocity_bins["Низкие скорости (< 0.10 м/с)"],
            "top_high_loss_pipes": high_loss_pipes[:top_k],
            "all_high_loss_pipes": all_high_loss_pipes[:80],
            "top_low_hazen_williams_c_pipes": low_c_pipes[:top_k],
            "degraded_high_loss_pipes": degraded_high_loss_pipes[:top_k],
            "table_pipes_records": pipes_for_client_table,
        },
        "pumps_analysis": {
            "total_pumps": len(all_pumps_list),
            "active_pumps_count": len(active_pumps),
            "reserve_inactive_pumps_count": len(all_pumps_list) - len(active_pumps),
            "total_energy_kwh_day": round(total_pump_energy_kwh_day, 1),
            "borehole_total_count": sum(1 for p in all_pumps_list if p["is_borehole"]),
            "borehole_active_count": sum(1 for p in active_pumps if p["is_borehole"]),
            "booster_active_count": sum(1 for p in active_pumps if not p["is_borehole"]),
            "underloaded_pumps": [p for p in active_pumps if p["q_ratio"] is not None and p["q_ratio"] < 0.75],
            "overloaded_pumps": [p for p in active_pumps if p["q_ratio"] is not None and p["q_ratio"] > 1.15],
            "all_pumps_table": all_pumps_list,
        },
        "valves_and_reservoirs_analysis": {
            "valves_count": len(valves_table),
            "valves_table": valves_table,
            "sources_count": len(sources_list),
            "sources_table": sources_list,
            "tanks_count": len(tanks_list),
            "tanks_table": tanks_list,
        },
        "nodes_summary": {
            "active_consumer_nodes_count": len(node_records),
            "total_daily_consumption_m3_day": total_daily_cons,
            "peak_hourly_network_demand_m3_h": peak_network_cons,
            "free_pressure_min_m": p_min_network,
            "free_pressure_max_m": p_max_network,
            "deficit_nodes_below_required_count": len(real_deficit_nodes),
            "excess_pressure_over_60m_nodes_count": len(excess_nodes),
            "worst_deficit_nodes": real_deficit_nodes[:top_k],
        },
        "pipes_summary": {
            "pipes_count": len(pipe_records),
            "total_length_km": round(total_len_km, 2),
            "total_internal_water_volume_m3": total_vol_m3,
            "roughness_length_weighted_score_pts": rough_mean,
            "degraded_roughness_below_78_length_km": low_c_km,
            "network_max_velocity_m_s": max_v,
            "pipes_over_1_5_m_s_count": v_over_15,
            "stagnant_pipes_under_0_3_m_s_count": v_under_03,
            "reverse_flow_pipes_count": rev_cnt,
            "top_high_velocity_pipes": high_loss_pipes[:top_k],
        },
        "pumps_summary": {
            "total_pumps_declared": len(all_pumps_list),
            "active_pumps_count": len(active_pumps),
            "total_hydraulic_energy_kwh_day": round(total_pump_energy_kwh_day, 1),
            "active_pumps_details": active_pumps[:15],
        },
        "sources_and_valves_summary": {
            "valves_count": len(valves_table),
            "sources_count": len(sources_list),
            "tanks_count": len(tanks_list),
        },
        "unique_model_anomalies": anomalies,
        "processing_time_sec": round(time.perf_counter() - t0, 2),
    }

    pipe_props = {
        p["pipe_id"]: {
            "d": p.get("diameter_mm"),
            "l": p.get("length_m"),
            "c": p.get("hazen_williams_c"),
            "v": p.get("velocity_max_m_s"),
            "i": p.get("headloss_gradient_m_km"),
            "dh": p.get("total_headloss_m"),
            "q": p.get("flow_max_m3_h"),
            "sn": p.get("start_node"),
            "en": p.get("end_node"),
            "v_bin": p.get("velocity_bin"),
            "advice": p.get("diagnostic_advice"),
            "closed": p.get("is_closed"),
            "closed_by_valve": p.get("is_closed_by_valve"),
            "name": p.get("name"),
        }
        for p in pipe_records
    }

    p50_vals = [v for v in (head_ts["hourly_24"].get("p50") or []) if v is not None] if head_ts.get("hourly_24") else []
    p50_is_dynamic = bool(len(p50_vals) >= 2 and (max(p50_vals) - min(p50_vals)) >= 0.1)
    dem_24 = [v for v in (dem_ts.get("hourly_24") or []) if v is not None]
    pmp_24 = [v for v in (pump_ts.get("hourly_24") or []) if v is not None]
    dem_is_dynamic = bool(len(dem_24) >= 2 and max(dem_24, default=0) > 0)
    pmp_is_dynamic = bool(len(pmp_24) >= 2 and max(pmp_24, default=0) > 0)

    tl_labels = head_ts.get("timeline_labels") or [f"{h:02d}:00" for h in range(24)]

    chart_payload = {
        "city_name": city_name,
        "hours_labels": tl_labels,
        "step_labels": [f"{h:02d}:00" for h in range(24)],
        "diurnal_demand_m3_h": dem_ts["hourly_24"],
        "diurnal_pump_flow_m3_h": pump_ts["hourly_24"],
        "diurnal_valve_flow_m3_h": valve_ts["hourly_24"],
        "valves_hourly_series": valves_hourly_series,
        "diurnal_free_pressure_percentiles_m": head_ts["hourly_24"],
        "node_hourly_pressures": head_ts.get("row_hourly", {}),
        "pipe_hourly_flows": pipe_ts.get("row_hourly", {}),
        "pump_hourly_flows": pump_ts.get("row_hourly", {}),
        "deficit_nodes_points": [
            {
                "node_id": r["node_id"],
                "clean_id": r["clean_id"],
                "z": r["elevation_m"],
                "p_free": r["free_pressure_min_m"],
                "p_req": r["required_pressure_m"],
                "dp": r["pressure_surplus_deficit_m"],
                "dp_abs": r["pressure_deficit_abs_m"],
                "is_vacuum": r["is_vacuum"],
                "floors": r["floors"],
                "q": r["demand_mean_m3_h"],
                "advice": r.get("diagnostic_advice", ""),
            }
            for r in real_deficit_nodes[:250]
        ],
        "bottleneck_pipes_points": [
            {
                "pipe_id": p["pipe_id"],
                "clean_id": p["clean_id"],
                "sn": p["start_node"],
                "en": p["end_node"],
                "d": p["diameter_mm"],
                "l": p["length_m"],
                "v": p["velocity_max_m_s"],
                "i": p["headloss_gradient_m_km"],
                "dh": p["total_headloss_m"],
                "c": p["hazen_williams_c"],
                "q": p["flow_max_m3_h"],
                "advice": p.get("diagnostic_advice", ""),
            }
            for p in (all_high_loss_pipes[:80] if all_high_loss_pipes else high_loss_pipes[:60])
        ],
        "bottleneck_pipes_total_count": bottleneck_pipes_total_count,
        "qh_curves": qh_chart_items,
        "pressure_bins": pressure_bins,
        "velocity_bins": velocity_bins,
        "node_props": all_node_props,
        "pipe_props": pipe_props,
        "geo": {
            "nodes": csv_coords,
            "links": topo_meta["links"],
            "sources": [s["clean_id"] for s in sources_list],
            "tanks": [t["clean_id"] for t in tanks_list],
            "borehole_pumps": [p["clean_id"] for p in all_pumps_list if p["is_borehole"]],
            "bad_nodes": [r["node_id"] for r in real_deficit_nodes + data_error_nodes],
            "bad_pipes": [p["pipe_id"] for p in high_loss_pipes + low_c_pipes],
        },
        "informative_charts": {
            "pump_qh": len(qh_chart_items) > 0 and any(c.get("curve_points") for c in qh_chart_items),
            "pressure_corridor": p50_is_dynamic or len(real_deficit_nodes) > 0,
            "pressure_deficit": len(real_deficit_nodes) > 0,
            "diurnal_balance": dem_is_dynamic or pmp_is_dynamic,
            "low_velocity_stagnation": len(pipe_records) > 0 and bool(pipe_ts.get("rows")),
            "pipe_bottlenecks": len(high_loss_pipes) > 0,
            "valves_regimes": len(valves_table) > 0,
        },
    }
    return facts, chart_payload


def process_aquabit_city_fast(
    raw_city_dir: Path | str,
    inp_path: Path | str | None = None,
    top_k: int = 15,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Алиас обратной совместимости для __init__.py (делегирует в process_aquabit_city_v3)."""
    return process_aquabit_city_v3(raw_city_dir=raw_city_dir, inp_path=inp_path, top_k=top_k)
