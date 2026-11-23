"""EPANET-AI v3.2 Processing, Topology Repair, Graph Neighborhood Diagnostics & Hydraulic Facts Engine for ПК «Аквабит»."""

from __future__ import annotations

import io
import math
import re
import time
import unicodedata
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

KNOWN_UNIT_TOKENS: set[str] = {
    "м", "мм", "км", "м2", "м3", "м3/час", "м3/ч", "м³/час", "м³/ч",
    "м3/сут", "м³/сут", "л/с", "л/мин", "бар", "атм", "мпа", "кпа", "па",
    "%", "ч", "час", "мин", "с", "сек", "квт", "квт*ч", "квт·ч", "мг/л", "г/м3", "балл", "баллов",
}

VALVE_TYPE_DESC: dict[str, str] = {
    "PRV": "Редукционный клапан (снижения давления «после себя»)",
    "PSV": "Клапан поддержания давления («до себя»)",
    "PBV": "Клапан гашения перепада давления",
    "FCV": "Регулятор-ограничитель расхода",
    "TCV": "Дросселирующий клапан",
    "GPV": "Клапан общего назначения",
}


def _decode_file_text(fpath: Path) -> str:
    raw = fpath.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def repair_aquabit_header_tokens(raw_header_line: str, sample_data_line: str | None = None) -> list[str]:
    tokens = [t.strip() for t in raw_header_line.lstrip("\ufeff").rstrip("\r\n").split(",")]
    if len(tokens) <= 1:
        return tokens
    target_cols: int | None = None
    if sample_data_line and sample_data_line.strip():
        target_cols = len(sample_data_line.rstrip("\r\n").split(","))
    if target_cols is not None and len(tokens) == target_cols:
        return tokens

    merged: list[str] = []
    for tok in tokens:
        tok_norm = tok.lower().strip()
        if merged and tok_norm in KNOWN_UNIT_TOKENS:
            merged[-1] = f"{merged[-1]} ({tok})"
            continue
        merged.append(tok)

    if target_cols is not None and len(merged) != target_cols:
        if len(tokens) == target_cols:
            return tokens
        if len(merged) > target_cols:
            merged = merged[:target_cols]
        else:
            while len(merged) < target_cols:
                merged.append(f"col_{len(merged)}")
    return merged


def read_aquabit_csv(fpath: Path | None) -> pd.DataFrame:
    if fpath is None or not fpath.exists():
        return pd.DataFrame()
    try:
        if fpath.stat().st_size == 0:
            return pd.DataFrame()
    except OSError:
        return pd.DataFrame()

    text = _decode_file_text(fpath).lstrip("\ufeff")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return pd.DataFrame()

    header_cols = repair_aquabit_header_tokens(lines[0], lines[1] if len(lines) > 1 else None)
    if len(lines) == 1:
        return pd.DataFrame(columns=header_cols)

    body = "\n".join(lines[1:])
    try:
        df = pd.read_csv(
            io.StringIO(body),
            header=None,
            names=header_cols,
            na_values=["NAN", "NaN", "nan", "NULL", "null", ""],
            keep_default_na=True,
            low_memory=False,
        )
        if len(df.columns) > 0:
            first_col = df.columns[0]
            df[first_col] = df[first_col].astype(str).str.strip()
        return df
    except pd.errors.EmptyDataError:
        return pd.DataFrame(columns=header_cols)


def find_col(df: pd.DataFrame, *substrings: str, default_idx: int | None = None) -> str | None:
    cols_lower = {c: str(c).lower() for c in df.columns}
    for sub in substrings:
        s = sub.lower()
        for col, cl in cols_lower.items():
            if s in cl:
                return col
    if default_idx is not None and 0 <= default_idx < len(df.columns):
        return str(df.columns[default_idx])
    return None


def clean_object_id(raw_id: str | None) -> str:
    """Убирает префиксную букву [ceputvk] из ID объекта: c3459771 -> 3459771, k3575278 -> 3575278."""
    if not raw_id:
        return ""
    s = str(raw_id).strip()
    return re.sub(r"^[ceputvk](\d+)$", r"\1", s, flags=re.IGNORECASE)


def clean_object_name(raw_name: str | None) -> str:
    if not raw_name:
        return ""
    return re.sub(r"\s*\(\s*Google\s*\)\s*", "", str(raw_name), flags=re.IGNORECASE).strip()


def format_step_to_hhmm(step_idx: int, total_steps: int) -> str:
    """Форматирует индекс шага симуляции в чистый формат ЧЧ:ММ без технических меток (R360)."""
    if total_steps <= 1:
        return "00:00"
    total_minutes = int(round((step_idx / max(total_steps, 1)) * 1440.0)) % 1440
    hh = total_minutes // 60
    mm = total_minutes % 60
    return f"{hh:02d}:{mm:02d}"


def evaluate_qh_curve_head(pts: list[tuple[float, float]], q_val: float) -> float | None:
    if not pts:
        return None
    pts_sorted = sorted(pts, key=lambda x: x[0])
    if len(pts_sorted) == 1:
        q_nom, h_nom = pts_sorted[0]
        if q_nom <= 0 or h_nom <= 0:
            return max(0.0, h_nom)
        h_calc = (4.0 / 3.0) * h_nom * (1.0 - 0.25 * ((max(0.0, q_val) / q_nom) ** 2))
        return round(max(0.0, h_calc), 2)
    qs = [p[0] for p in pts_sorted]
    hs = [p[1] for p in pts_sorted]
    if q_val <= qs[0]:
        return round(float(hs[0]), 2)
    if q_val >= qs[-1]:
        return round(float(hs[-1]), 2)
    return round(float(np.interp(q_val, qs, hs)), 2)


def build_qh_curve_plot_points(pts: list[tuple[float, float]], n_grid: int = 25) -> dict[str, Any]:
    if not pts:
        return {"mode": "empty", "design_points": [], "curve_points": []}
    pts_sorted = sorted(pts, key=lambda x: x[0])
    if len(pts_sorted) == 1:
        q_nom, h_nom = pts_sorted[0]
        q_max = max(q_nom * 2.0, 1.0)
        grid_q = np.linspace(0.0, q_max, n_grid)
        curve_pts = [
            [round(float(q), 2), round(max(0.0, (4.0 / 3.0) * h_nom * (1.0 - 0.25 * ((q / max(q_nom, 1e-6)) ** 2))), 2)]
            for q in grid_q
        ]
        return {
            "mode": "single_point_parabola",
            "q_nominal_m3_h": round(q_nom, 2),
            "h_nominal_m": round(h_nom, 2),
            "h_shutoff_m": round((4.0 / 3.0) * h_nom, 2),
            "q_max_m3_h": round(q_max, 2),
            "design_points": [[round(q_nom, 2), round(h_nom, 2)]],
            "curve_points": curve_pts,
        }
    return {
        "mode": "multi_point_table",
        "q_nominal_m3_h": round(pts_sorted[len(pts_sorted) // 2][0], 2),
        "h_nominal_m": round(pts_sorted[len(pts_sorted) // 2][1], 2),
        "h_shutoff_m": round(pts_sorted[0][1], 2),
        "q_max_m3_h": round(pts_sorted[-1][0], 2),
        "design_points": [[round(q, 2), round(h, 2)] for q, h in pts_sorted],
        "curve_points": [[round(q, 2), round(h, 2)] for q, h in pts_sorted],
    }


def _resolve_file_ci(folder: Path, *candidates: str) -> Path | None:
    if not folder.exists():
        return None
    by_norm = {unicodedata.normalize("NFC", p.name).strip().lower(): p for p in folder.iterdir() if p.is_file()}
    for cand in candidates:
        key = unicodedata.normalize("NFC", cand).strip().lower()
        if key in by_norm:
            return by_norm[key]
    return None


def _stream_wide_timeseries(
    fpath: Path | None,
    chunksize: int = 4000,
    row_offsets: dict[str, float] | None = None,
    hourly_mode: str = "sum",
    filter_ids: set[str] | None = None,
    keep_row_hourly: bool = False,
    timeline_buckets: int = 48,
) -> dict[str, Any]:
    if fpath is None or not fpath.exists() or fpath.stat().st_size == 0:
        return {
            "exists": False,
            "n_steps": 0,
            "step_names": [],
            "rows": {},
            "hourly_24": None,
            "row_hourly": {},
            "timeline_labels": [],
        }

    with fpath.open("r", encoding="utf-8-sig", errors="replace") as fh:
        first_line = fh.readline().strip()
    if not first_line:
        return {
            "exists": False,
            "n_steps": 0,
            "step_names": [],
            "rows": {},
            "hourly_24": None,
            "row_hourly": {},
            "timeline_labels": [],
        }

    hdr = [c.strip() for c in first_line.split(",")]
    step_cols = [c for c in hdr[1:] if c]
    n_steps = len(step_cols)
    if n_steps == 0:
        return {
            "exists": True,
            "n_steps": 0,
            "step_names": [],
            "rows": {},
            "hourly_24": None,
            "row_hourly": {},
            "timeline_labels": [],
        }

    # 24 часовых корзины для суточных графиков
    n_buckets_24 = min(24, n_steps)
    bucket_indices_24 = np.array_split(np.arange(n_steps), n_buckets_24)

    # Детальные корзины для временной шкалы карты (например, 48 шагов по 30 мин или 96 по 15 мин)
    n_tl = min(max(24, timeline_buckets), n_steps)
    tl_indices = np.array_split(np.arange(n_steps), n_tl)
    tl_mid_cols = [int(idxs[len(idxs) // 2]) for idxs in tl_indices]
    timeline_labels = [format_step_to_hhmm(c_idx, n_steps) for c_idx in tl_mid_cols]

    row_stats: dict[str, dict[str, Any]] = {}
    row_hourly: dict[str, list[float | None]] = {}
    hourly_sum = np.zeros(n_buckets_24, dtype=np.float64)
    hourly_samples_p: list[list[np.ndarray]] = [[] for _ in range(n_buckets_24)]
    valid_numeric_any = False

    with fpath.open("r", encoding="utf-8-sig", errors="replace") as stream:
        reader = pd.read_csv(
            stream,
            chunksize=chunksize,
            na_values=["NAN", "NaN", "nan", "NULL", "null", ""],
            keep_default_na=True,
            low_memory=False,
        )
        for chunk in reader:
            if chunk.empty:
                continue
            ids = chunk.iloc[:, 0].astype(str).str.strip().tolist()
            mat = chunk.iloc[:, 1 : 1 + n_steps].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=np.float32, copy=True)

            if row_offsets is not None:
                offs = np.array([row_offsets.get(i, row_offsets.get(clean_object_id(i), np.nan)) for i in ids], dtype=np.float32)[:, None]
                mat = mat - offs

            if filter_ids is not None:
                keep_mask = np.array([(i in filter_ids or clean_object_id(i) in filter_ids) for i in ids], dtype=bool)
            else:
                keep_mask = np.ones(len(ids), dtype=bool)

            finite_mask = np.isfinite(mat)
            if not finite_mask.any():
                continue
            valid_numeric_any = True

            mat_kept = mat[keep_mask]
            if mat_kept.shape[0] > 0:
                for b_idx, cols_idx in enumerate(bucket_indices_24):
                    sub = mat_kept[:, cols_idx]
                    if hourly_mode == "sum":
                        row_hour_means = np.nanmean(np.where(np.isfinite(sub), sub, 0.0), axis=1)
                        hourly_sum[b_idx] += float(np.nansum(row_hour_means))
                    elif hourly_mode == "sum_positive":
                        pos_sub = np.where(np.isfinite(sub) & (sub > 0.0), sub, 0.0)
                        row_hour_means = np.mean(pos_sub, axis=1)
                        hourly_sum[b_idx] += float(np.sum(row_hour_means))
                    elif hourly_mode == "percentiles":
                        col_mid = sub[:, len(cols_idx) // 2]
                        fin_vals = col_mid[np.isfinite(col_mid)]
                        if fin_vals.size > 0:
                            hourly_samples_p[b_idx].append(fin_vals)

            if keep_row_hourly:
                sub_tl = np.round(mat[:, tl_mid_cols], 2)
                for idx_r, obj_id in enumerate(ids):
                    arr_r = sub_tl[idx_r]
                    if np.isfinite(arr_r).any():
                        row_hourly[obj_id] = [None if np.isnan(float(v)) else round(float(v), 2) for v in arr_r]

            row_has_fin = finite_mask.any(axis=1)
            safe_min = np.where(finite_mask, mat, np.inf).min(axis=1)
            safe_max = np.where(finite_mask, mat, -np.inf).max(axis=1)
            counts = finite_mask.sum(axis=1)
            sums = np.where(finite_mask, mat, 0.0).sum(axis=1)
            pos_sums = np.where(finite_mask & (mat > 0.0), mat, 0.0).sum(axis=1)
            neg_steps = (np.where(finite_mask, mat, 0.0) < -1e-6).sum(axis=1)
            pos_steps = (np.where(finite_mask, mat, 0.0) > 1e-4).sum(axis=1)
            abs_max = np.where(finite_mask, np.abs(mat), 0.0).max(axis=1)

            signs = np.sign(np.where(np.abs(np.where(finite_mask, mat, 0.0)) > 1e-4, mat, 0.0))
            flips = ((signs[:, 1:] * signs[:, :-1]) < 0).sum(axis=1) if n_steps > 1 else np.zeros(len(ids), dtype=int)
            active_bool = np.where(finite_mask, mat, 0.0) > 1e-3
            starts = ((~active_bool[:, :-1]) & active_bool[:, 1:]).sum(axis=1) + active_bool[:, 0].astype(int) if n_steps > 1 else active_bool[:, 0].astype(int)

            for idx_r, obj_id in enumerate(ids):
                if not row_has_fin[idx_r]:
                    continue
                cnt_i = int(counts[idx_r])
                row_stats[obj_id] = {
                    "min": float(safe_min[idx_r]),
                    "max": float(safe_max[idx_r]),
                    "mean": float(sums[idx_r] / max(cnt_i, 1)),
                    "pos_mean": float(pos_sums[idx_r] / max(cnt_i, 1)),
                    "abs_max": float(abs_max[idx_r]),
                    "neg_steps": int(neg_steps[idx_r]),
                    "pos_steps": int(pos_steps[idx_r]),
                    "flips": int(flips[idx_r]),
                    "starts": int(starts[idx_r]),
                }

    hourly_out: Any = None
    if valid_numeric_any:
        if hourly_mode in ("sum", "sum_positive"):
            hourly_out = [round(float(x), 2) for x in hourly_sum]
        elif hourly_mode == "percentiles":
            p10, p50, p90 = [], [], []
            for b_idx in range(n_buckets_24):
                if hourly_samples_p[b_idx]:
                    arr = np.concatenate(hourly_samples_p[b_idx])
                    p10.append(round(float(np.percentile(arr, 10)), 2))
                    p50.append(round(float(np.percentile(arr, 50)), 2))
                    p90.append(round(float(np.percentile(arr, 90)), 2))
                else:
                    p10.append(None)
                    p50.append(None)
                    p90.append(None)
            hourly_out = {"p10": p10, "p50": p50, "p90": p90}

    return {
        "exists": True,
        "valid_numeric": valid_numeric_any,
        "n_steps": n_steps,
        "step_names": step_cols,
        "bucket_step_labels": [f"{h:02d}:00" for h in range(n_buckets_24)],
        "timeline_labels": timeline_labels,
        "rows": row_stats,
        "hourly_24": hourly_out,
        "row_hourly": row_hourly,
    }


def parse_inp_metadata_and_topology(
    inp_path: Path | None,
    node_ids: set[str],
    pipe_ids: set[str],
    pump_ids: set[str],
    extra_node_ids: set[str] | None = None,
) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "names": {},
        "links": [],
        "nodes": {},
        "link_ends": {},
        "closed_links": set(),
        "reservoir_clean_ids": set(),
        "tank_clean_ids": set(),
    }
    if not inp_path or not inp_path.exists():
        return meta

    all_known_nodes = set(node_ids) | set(extra_node_ids or set())
    text = _decode_file_text(inp_path)
    section = ""
    verts: dict[str, list[list[float]]] = {}
    raw_links = []

    def resolve_id(raw_id: str, prefix: str, known: set[str]) -> str:
        if raw_id in known:
            return raw_id
        if (prefix + raw_id) in known:
            return prefix + raw_id
        for cand_pref in ("c", "e", "k"):
            if (cand_pref + raw_id) in known:
                return cand_pref + raw_id
        return raw_id if raw_id.startswith(prefix) else prefix + raw_id

    for line in text.splitlines():
        comment = ""
        if ";" in line:
            code_part, comment_part = line.split(";", 1)
            comment = comment_part.strip()
        else:
            code_part = line
        code_part = code_part.strip()
        if not code_part:
            continue
        if code_part.startswith("[") and "]" in code_part:
            section = code_part[1:code_part.index("]")].upper()
            continue

        parts = code_part.split()
        if len(parts) < 1:
            continue

        raw_id = parts[0]
        if section in ("JUNCTIONS", "RESERVOIRS", "TANKS"):
            pref = "e" if section == "RESERVOIRS" else ("k" if section == "TANKS" else "c")
            oid = resolve_id(raw_id, pref, all_known_nodes)
            if section == "RESERVOIRS":
                meta["reservoir_clean_ids"].add(clean_object_id(raw_id))
            elif section == "TANKS":
                meta["tank_clean_ids"].add(clean_object_id(raw_id))
            if comment and not comment.lower().startswith("id"):
                meta["names"][oid] = comment
                meta["names"][clean_object_id(raw_id)] = comment
        elif section == "PIPES" and len(parts) >= 3:
            oid = resolve_id(raw_id, "e", pipe_ids)
            n1 = resolve_id(parts[1], "c", all_known_nodes)
            n2 = resolve_id(parts[2], "c", all_known_nodes)
            meta["link_ends"][oid] = (n1, n2, "PIPE")
            meta["link_ends"][clean_object_id(raw_id)] = (n1, n2, "PIPE")
            if comment:
                meta["names"][oid] = comment
            if len(parts) >= 8 and parts[7].upper() == "CLOSED":
                meta["closed_links"].add(oid)
            raw_links.append(("e", oid, n1, n2, raw_id))
        elif section == "PUMPS" and len(parts) >= 3:
            oid = resolve_id(raw_id, "p", pump_ids)
            n1 = resolve_id(parts[1], "c", all_known_nodes)
            n2 = resolve_id(parts[2], "c", all_known_nodes)
            meta["link_ends"][oid] = (n1, n2, "PUMP")
            meta["link_ends"][clean_object_id(raw_id)] = (n1, n2, "PUMP")
            if comment:
                meta["names"][oid] = comment
            raw_links.append(("p", oid, n1, n2, raw_id))
        elif section == "VALVES" and len(parts) >= 3:
            oid = resolve_id(raw_id, "v", set())
            n1 = resolve_id(parts[1], "c", all_known_nodes)
            n2 = resolve_id(parts[2], "c", all_known_nodes)
            meta["link_ends"][oid] = (n1, n2, "VALVE")
            meta["link_ends"][clean_object_id(raw_id)] = (n1, n2, "VALVE")
            if comment:
                meta["names"][oid] = comment
            raw_links.append(("v", oid, n1, n2, raw_id))
        elif section == "COORDINATES" and len(parts) >= 3:
            try:
                oid = resolve_id(raw_id, "c", all_known_nodes)
                pt = [float(parts[1]), float(parts[2])]
                meta["nodes"][oid] = pt
                meta["nodes"][clean_object_id(raw_id)] = pt
            except ValueError:
                pass
        elif section == "VERTICES" and len(parts) >= 3:
            try:
                verts.setdefault(raw_id, []).append([float(parts[1]), float(parts[2])])
            except ValueError:
                pass

    for _pref, oid, n1, n2, raw_id in raw_links:
        p1 = meta["nodes"].get(n1) or meta["nodes"].get(clean_object_id(n1))
        p2 = meta["nodes"].get(n2) or meta["nodes"].get(clean_object_id(n2))
        if p1 and p2:
            meta["links"].append([oid, [p1] + verts.get(raw_id, []) + [p2]])

    return meta


def process_aquabit_city_v3(
    raw_city_dir: Path | str,
    inp_path: Path | str | None = None,
    top_k: int = 15,
    timeline_buckets: int = 48,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Процессор ПК «Аквабит» v3.2:
    - Детальная временная шкала (48 шагов по 30 мин / ЧЧ:ММ без меток R360)
    - Графовая причинно-следственная диагностика: Источник <-> Насос <-> Коллектор <-> Труба
    - Полная выборка таблиц по категориям давлений и секторам скоростей потока."""
    t0 = time.perf_counter()
    raw_city_dir = Path(raw_city_dir)
    inp_path = Path(inp_path) if inp_path else None
    city_name = unicodedata.normalize("NFC", raw_city_dir.name)

    df_nodes = read_aquabit_csv(_resolve_file_ci(raw_city_dir, "Узел.csv"))
    df_pipes = read_aquabit_csv(_resolve_file_ci(raw_city_dir, "Труба.csv"))
    df_pumps = read_aquabit_csv(_resolve_file_ci(raw_city_dir, "Насос.csv"))
    df_valves = read_aquabit_csv(_resolve_file_ci(raw_city_dir, "Клапан.csv"))
    df_valve_types = read_aquabit_csv(_resolve_file_ci(raw_city_dir, "Типы Клапанов.csv"))
    df_tanks = read_aquabit_csv(_resolve_file_ci(raw_city_dir, "Резервуар.csv"))
    df_src = read_aquabit_csv(_resolve_file_ci(raw_city_dir, "Источник.csv"))
    df_qh = read_aquabit_csv(_resolve_file_ci(raw_city_dir, "Кривая QH.csv"))
    df_cons = read_aquabit_csv(_resolve_file_ci(raw_city_dir, "Потребители Узел.csv"))

    node_ids_set = set(df_nodes[df_nodes.columns[0]]) if not df_nodes.empty else set()
    pipe_ids_set = set(df_pipes[df_pipes.columns[0]]) if not df_pipes.empty else set()
    pump_ids_set = set(df_pumps[df_pumps.columns[0]]) if not df_pumps.empty else set()
    source_ids_set = set(df_src[df_src.columns[0]]) if not df_src.empty else set()
    tank_ids_set = set(df_tanks[df_tanks.columns[0]]) if not df_tanks.empty else set()

    if inp_path is None and raw_city_dir.exists():
        inps = sorted([p for p in raw_city_dir.iterdir() if p.is_file() and p.suffix.lower() == ".inp"])
        if inps:
            inp_path = inps[0]

    inp_meta = parse_inp_metadata_and_topology(
        inp_path,
        node_ids_set,
        pipe_ids_set,
        pump_ids_set,
        extra_node_ids=source_ids_set | tank_ids_set,
    )
    obj_names = inp_meta["names"]

    source_clean_ids = {clean_object_id(s) for s in source_ids_set if s} | inp_meta["reservoir_clean_ids"]
    tank_clean_ids = {clean_object_id(t) for t in tank_ids_set if t} | inp_meta["tank_clean_ids"]

    def label_with_name(oid: str) -> str:
        cid = clean_object_id(oid)
        raw_nm = obj_names.get(oid) or obj_names.get(cid)
        nm = clean_object_name(raw_nm)
        return f"{cid} ({nm})" if nm else cid

    data_export_warnings: list[str] = []

    # 1. Узлы (Узел.csv) + координаты X, Y
    elev_map: dict[str, float] = {}
    req_p_map: dict[str, float] = {}
    floors_map: dict[str, int] = {}
    csv_coords: dict[str, list[float]] = dict(inp_meta["nodes"])

    if not df_nodes.empty:
        c_id = df_nodes.columns[0]
        c_z = find_col(df_nodes, "высот", "отмет", default_idx=1 if len(df_nodes.columns) > 1 else None)
        c_req = find_col(df_nodes, "требуем", "давлен", default_idx=2 if len(df_nodes.columns) > 2 else None)
        c_x = find_col(df_nodes, "x", default_idx=3 if len(df_nodes.columns) > 3 else None)
        c_y = find_col(df_nodes, "y", default_idx=4 if len(df_nodes.columns) > 4 else None)

        zs = pd.to_numeric(df_nodes[c_z], errors="coerce") if c_z else pd.Series(np.nan, index=df_nodes.index)
        reqs = pd.to_numeric(df_nodes[c_req], errors="coerce").fillna(0.0) if c_req else pd.Series(0.0, index=df_nodes.index)
        xs = pd.to_numeric(df_nodes[c_x], errors="coerce") if c_x else pd.Series(np.nan, index=df_nodes.index)
        ys = pd.to_numeric(df_nodes[c_y], errors="coerce") if c_y else pd.Series(np.nan, index=df_nodes.index)

        for nid, zv, rv, xv, yv in zip(df_nodes[c_id], zs, reqs, xs, ys):
            nid_s = str(nid).strip()
            if not math.isnan(float(zv)):
                elev_map[nid_s] = float(zv)
                elev_map[clean_object_id(nid_s)] = float(zv)
            if rv > 0:
                req_p_map[nid_s] = float(rv)
                floors_map[nid_s] = max(1, int(round(1.0 + max(0.0, float(rv) - 10.0) / 4.0)))
            if not (math.isnan(float(xv)) or math.isnan(float(yv))):
                csv_coords[nid_s] = [float(xv), float(yv)]
                csv_coords[clean_object_id(nid_s)] = [float(xv), float(yv)]

    # 1b. Источники и РЧВ
    sources_list: list[dict[str, Any]] = []
    source_head_by_cid: dict[str, float] = {}
    if not df_src.empty:
        c_id = df_src.columns[0]
        c_h = find_col(df_src, "напор", default_idx=1 if len(df_src.columns) > 1 else None)
        c_x = find_col(df_src, "x", default_idx=2 if len(df_src.columns) > 2 else None)
        c_y = find_col(df_src, "y", default_idx=3 if len(df_src.columns) > 3 else None)
        hs = pd.to_numeric(df_src[c_h], errors="coerce") if c_h else pd.Series(np.nan, index=df_src.index)
        xs = pd.to_numeric(df_src[c_x], errors="coerce") if c_x else pd.Series(np.nan, index=df_src.index)
        ys = pd.to_numeric(df_src[c_y], errors="coerce") if c_y else pd.Series(np.nan, index=df_src.index)
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
            sources_list.append({
                "source_id": sid_s,
                "clean_id": s_cid,
                "name": clean_object_name(obj_names.get(sid_s, "Источник водоснабжения")),
                "design_head_m": h_val,
                "x": None if math.isnan(float(xv)) else round(float(xv), 2),
                "y": None if math.isnan(float(yv)) else round(float(yv), 2),
            })

    tanks_list: list[dict[str, Any]] = []
    if not df_tanks.empty:
        c_id = df_tanks.columns[0]
        c_h = find_col(df_tanks, "напор", default_idx=1 if len(df_tanks.columns) > 1 else None)
        c_x = find_col(df_tanks, "x", default_idx=2 if len(df_tanks.columns) > 2 else None)
        c_y = find_col(df_tanks, "y", default_idx=3 if len(df_tanks.columns) > 3 else None)
        hs = pd.to_numeric(df_tanks[c_h], errors="coerce") if c_h else pd.Series(np.nan, index=df_tanks.index)
        xs = pd.to_numeric(df_tanks[c_x], errors="coerce") if c_x else pd.Series(np.nan, index=df_tanks.index)
        ys = pd.to_numeric(df_tanks[c_y], errors="coerce") if c_y else pd.Series(np.nan, index=df_tanks.index)
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
            tanks_list.append({
                "tank_id": tid_s,
                "clean_id": t_cid,
                "name": clean_object_name(obj_names.get(tid_s, "Резервуар чистой воды (РЧВ)")),
                "design_head_m": h_val,
                "x": None if math.isnan(float(xv)) else round(float(xv), 2),
                "y": None if math.isnan(float(yv)) else round(float(yv), 2),
            })

    # 2. Потребители
    base_demand: dict[str, float] = {}
    node_schedules: dict[str, list[str]] = {}
    if not df_cons.empty:
        c_id = df_cons.columns[0]
        c_q = find_col(df_cons, "потреблен", default_idx=1 if len(df_cons.columns) > 1 else None)
        c_sch = find_col(df_cons, "график", default_idx=2 if len(df_cons.columns) > 2 else None)
        q_col = pd.to_numeric(df_cons[c_q], errors="coerce").fillna(0.0) if c_q else pd.Series(0.0, index=df_cons.index)
        sch_col = df_cons[c_sch].astype(str).str.strip().tolist() if c_sch else [""] * len(df_cons)
        for nid, qv, sv in zip(df_cons[c_id], q_col, sch_col):
            nid_s = str(nid).strip()
            if abs(float(qv)) > 1e-9:
                base_demand[nid_s] = base_demand.get(nid_s, 0.0) + float(qv)
                if sv and sv.lower() not in ("t", "nan", "none"):
                    node_schedules.setdefault(nid_s, []).append(sv)

    active_nodes = set(req_p_map.keys()) | set(base_demand.keys())

    # 3. Временные ряды с высокой детализацией для временной шкалы
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

    n_steps = head_ts["n_steps"] or dem_ts["n_steps"] or valve_ts["n_steps"] or 1440
    step_min = 1440.0 / max(n_steps, 1)

    # 4. Проверка и починка топологии связей
    def resolve_link_endpoints(lid: str, sn_raw: str, en_raw: str) -> tuple[str, str, bool]:
        sn_s, en_s = str(sn_raw).strip(), str(en_raw).strip()
        is_self_loop_bug = bool(sn_s and sn_s == en_s) or (clean_object_id(sn_s) == clean_object_id(lid))
        if not sn_s or not en_s or is_self_loop_bug:
            ended = inp_meta["link_ends"].get(lid) or inp_meta["link_ends"].get(clean_object_id(lid))
            if ended:
                return ended[0], ended[1], is_self_loop_bug
        return sn_s, en_s, is_self_loop_bug

    inactive_well_nodes = set()
    pump_endpoints: dict[str, tuple[str, str]] = {}
    if not df_pumps.empty:
        c_id = df_pumps.columns[0]
        c_s = find_col(df_pumps, "начал", default_idx=1 if len(df_pumps.columns) > 1 else None)
        c_e = find_col(df_pumps, "конец", "конечн", default_idx=2 if len(df_pumps.columns) > 2 else None)
        for pid, sn, en in zip(
            df_pumps[c_id],
            df_pumps[c_s] if c_s else [""] * len(df_pumps),
            df_pumps[c_e] if c_e else [""] * len(df_pumps),
        ):
            sn_clean, en_clean, _had_bug = resolve_link_endpoints(pid, sn, en)
            pump_endpoints[pid] = (sn_clean, en_clean)
            pst = pump_ts["rows"].get(pid)
            if (pst is None or pst["max"] <= 1e-3) and (clean_object_id(sn_clean) in source_clean_ids or sn_clean in source_ids_set):
                inactive_well_nodes.add(sn_clean)

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

    if not df_pipes.empty:
        c_id = df_pipes.columns[0]
        c_s = find_col(df_pipes, "начал", default_idx=1 if len(df_pipes.columns) > 1 else None)
        c_e = find_col(df_pipes, "конец", "конечн", default_idx=2 if len(df_pipes.columns) > 2 else None)
        c_d = find_col(df_pipes, "диаметр", default_idx=3 if len(df_pipes.columns) > 3 else None)
        c_l = find_col(df_pipes, "длин", default_idx=4 if len(df_pipes.columns) > 4 else None)
        c_c = find_col(df_pipes, "шероховат", default_idx=5 if len(df_pipes.columns) > 5 else None)

        ds = pd.to_numeric(df_pipes[c_d], errors="coerce").fillna(0.0) if c_d else pd.Series(0.0, index=df_pipes.index)
        ls = pd.to_numeric(df_pipes[c_l], errors="coerce").fillna(0.0) if c_l else pd.Series(np.nan, index=df_pipes.index)
        cs = pd.to_numeric(df_pipes[c_c], errors="coerce") if c_c else pd.Series(np.nan, index=df_pipes.index)

        for pid, sn, en, d_mm, l_m, c_hw in zip(
            df_pipes[c_id],
            df_pipes[c_s] if c_s else [""] * len(df_pipes),
            df_pipes[c_e] if c_e else [""] * len(df_pipes),
            ds, ls, cs,
        ):
            d_val = float(d_mm)
            l_val = max(0.0, float(l_m) if not math.isnan(float(l_m)) else 0.0)
            total_len_km += l_val / 1000.0
            c_val = None if math.isnan(float(c_hw)) else round(float(c_hw), 1)
            sn_s, en_s, had_bug = resolve_link_endpoints(pid, sn, en)
            if had_bug:
                self_loop_pipes += 1

            sn_cid = clean_object_id(sn_s)
            en_cid = clean_object_id(en_s)
            for u_node in (sn_s, en_s, sn_cid, en_cid):
                if u_node:
                    node_degree[u_node] = node_degree.get(u_node, 0) + 1

            pst = pipe_ts["rows"].get(pid)
            v_max, q_max, headloss_m_km = None, None, None
            is_closed_pipe = (pid in inp_meta["closed_links"])
            v_bin_name = "Не определен"
            if pst is not None and d_val > 0:
                q_max = round(pst["abs_max"], 2)
                if q_max <= 1e-4:
                    is_closed_pipe = True
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
                else:
                    headloss_m_km = 0.0

                if is_closed_pipe or v_max <= 0.001:
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

            cid = clean_object_id(pid)
            clean_nm = clean_object_name(obj_names.get(pid, ""))

            # Формируем конкретный инженерный вывод по участку без шаблонов
            if is_closed_pipe:
                pipe_diag = f"Поток Q = 0 м³/ч (перекрыт задвижкой между узлами #{sn_cid} и #{en_cid}). Проверить целесообразность открытия для кольцевания."
            elif (headloss_m_km or 0) >= 15.0 or (v_max or 0) >= 5.0:
                if c_val is not None and c_val < 70:
                    pipe_diag = f"Критическое сопротивление (i={headloss_m_km} м/км) между узлами #{sn_cid} и #{en_cid} вызвано заниженным коэфф. шероховатости C={c_val} при D={round(d_val)} мм. В Аквабит: проверить C={c_val} или запланировать санацию/перекладку."
                else:
                    pipe_diag = f"Гидравлическое узкое место (i={headloss_m_km} м/км, V={v_max} м/с) на участке #{sn_cid} → #{en_cid}. Диаметр D={round(d_val)} мм недостаточен для пропуска {q_max} м³/ч; требуется дублирование или перекладка."
            elif v_max is not None and v_max < 0.10:
                pipe_diag = f"Малопроточная зона (V={v_max} м/с, Q={q_max} м³/ч) на ветке #{sn_cid}–#{en_cid} (D={round(d_val)} мм). Риск застоя воды."
            else:
                pipe_diag = f"Рабочий режим (V={v_max or 0} м/с, i={headloss_m_km or 0} м/км) между узлами #{sn_cid} и #{en_cid}."

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
                "velocity_bin": v_bin_name,
                "is_closed": is_closed_pipe,
                "reverse_steps": pst["neg_steps"] if pst else 0,
                "diagnostic_advice": pipe_diag,
            }
            pipe_records.append(p_rec)
            for k_node in {sn_s, en_s, sn_cid, en_cid}:
                if k_node:
                    adj_pipes_by_node.setdefault(k_node, []).append(p_rec)

    if self_loop_pipes > 0:
        data_export_warnings.append(
            f"В Труба.csv обнаружено {self_loop_pipes} участков с ошибочным совпадением Начальный узел == Конечный узел (связи восстановлены из .inp)."
        )

    # 5.5 Граф гидравлической связности (BFS от источников и РЧВ по открытым участкам с учетом починенных ID начала/конца)
    from collections import deque
    open_adj: dict[str, list[str]] = {}
    closed_edges: list[tuple[str, str, str]] = []
    for p_rec in pipe_records:
        u = clean_object_id(p_rec.get("start_node"))
        v = clean_object_id(p_rec.get("end_node"))
        if not u or not v or u == v:
            continue
        if p_rec.get("is_closed"):
            closed_edges.append((u, v, p_rec.get("clean_id", "")))
        else:
            open_adj.setdefault(u, []).append(v)
            open_adj.setdefault(v, []).append(u)
    for _pid, (_sn, _en) in pump_endpoints.items():
        u, v = clean_object_id(_sn), clean_object_id(_en)
        if u and v and u != v:
            open_adj.setdefault(u, []).append(v)
            open_adj.setdefault(v, []).append(u)
    if not df_valves.empty:
        _vc_s = find_col(df_valves, "начал", default_idx=1 if len(df_valves.columns) > 1 else None)
        _vc_e = find_col(df_valves, "конец", "конечн", default_idx=2 if len(df_valves.columns) > 2 else None)
        if _vc_s and _vc_e:
            for _sn, _en in zip(df_valves[_vc_s], df_valves[_vc_e]):
                u, v = clean_object_id(str(_sn)), clean_object_id(str(_en))
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

    # Для отсеченных узлов находим конкретную перекрытую трубу на границе с живой сетью
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
    node_records = []
    all_node_props: dict[str, dict[str, Any]] = {}
    isolated_consumer_nodes_count = 0
    pressure_bins = {
        "Ошибка / отсечение (< -3.0 м)": 0,
        "Отрицат. давление (-3.0 .. 0 м)": 0,
        "Недостаток давления (< P_треб)": 0,
        "Норма (P_треб .. 60 м)": 0,
        "Избыток (> 60 м)": 0,
    }

    all_known_nodes = sorted(set(elev_map.keys()) | set(head_ts["rows"].keys()) | active_nodes)
    for nid in all_known_nodes:
        hst = head_ts["rows"].get(nid)
        dst = dem_ts["rows"].get(nid)
        req_m = req_p_map.get(nid, 0.0)
        q_mean = dst["pos_mean"] if (dst and dst["pos_mean"] > 0) else base_demand.get(nid, 0.0)
        p_min = round(hst["min"], 2) if hst else None
        p_max = round(hst["max"], 2) if hst else None
        p_mean = round(hst["mean"], 2) if hst else None
        margin_m = round(p_min - req_m, 2) if (p_min is not None and req_m > 0) else None
        is_active_consumer = nid in active_nodes
        is_shutoff_well = (nid in inactive_well_nodes) or (p_min is not None and p_min < 0 and q_mean <= 1e-6)

        cid = clean_object_id(nid)
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

        # Определяем категорию давления и точный инженерный диагноз узла с привязкой к топологии графа
        p_category = "normal"
        node_diag = "Нормативный гидравлический режим."
        if p_min is not None:
            if p_min < -3.0:
                p_category = "data_error"
                closed_nb = [p["clean_id"] for p in conn_pipes if p.get("is_closed")]
                high_loss_nb = sorted(conn_pipes, key=lambda x: x.get("headloss_gradient_m_km") or 0, reverse=True)
                if is_isolated_zone and (cutoff_pipe_id or closed_nb):
                    cut_id = cutoff_pipe_id or closed_nb[0]
                    node_diag = f"Отсеченная зона (Pмин={p_min} м): граф подтверждает отсутствие гидравлической связи с источниками из-за перекрытого участка #{cut_id} (Q=0) при активном отборе {round(q_mean, 2)} м³/ч. В Аквабит: открыть участок #{cut_id}."
                elif closed_nb:
                    node_diag = f"Критический вакуум Pмин={p_min} м: рядом перекрытый участок #{closed_nb[0]} (Q=0) при активном отборе {round(q_mean, 2)} м³/ч. В Аквабит: проверить статус #{closed_nb[0]} и отметку Z={elev_map.get(nid, '—')} м."
                elif high_loss_nb and (high_loss_nb[0].get("headloss_gradient_m_km") or 0) >= 15.0:
                    nb0 = high_loss_nb[0]
                    node_diag = f"Критический вакуум Pмин={p_min} м из-за падения напора на подводящем участке #{nb0['clean_id']} (i={nb0['headloss_gradient_m_km']} м/км, D={nb0['diameter_mm']} мм) и отметки Z={elev_map.get(nid, '—')} м."
                elif has_valid_topology and not is_isolated_zone:
                    node_diag = f"Ошибка исходной отметки Z={elev_map.get(nid, '—')} м (Pмин={p_min} м): узел гидравлически связан с источниками (отсечения нет, смежные трубы: {', '.join('#'+x for x in conn_pipe_ids) or 'нет'}). В Аквабит: исправить Z в Узел.csv."
                else:
                    node_diag = f"Критический вакуум Pмин={p_min} м при Z={elev_map.get(nid, '—')} м (смежные участки: {', '.join('#'+x for x in conn_pipe_ids) or 'нет'}). Проверить отметку Z и связность ветки в ПК «Аквабит»."
            elif p_min < 0.0:
                p_category = "negative"
                node_diag = f"Субатмосферное давление Pмин={p_min} м при отметке Z={elev_map.get(nid, '—')} м (подводящие трубы: {', '.join('#'+x for x in conn_pipe_ids)})."
            elif req_m > 0 and p_min < req_m:
                p_category = "deficit"
                high_loss_nb = sorted(conn_pipes, key=lambda x: x.get("headloss_gradient_m_km") or 0, reverse=True)
                if high_loss_nb and (high_loss_nb[0].get("headloss_gradient_m_km") or 0) >= 10.0:
                    nb0 = high_loss_nb[0]
                    node_diag = f"Нехватка напора ΔP={margin_m} м ({floors_map.get(nid, 1)} эт., Pтреб={req_m} м): потери на подводящей трубе #{nb0['clean_id']} составляют {nb0['headloss_gradient_m_km']} м/км (D={nb0['diameter_mm']} мм)."
                else:
                    node_diag = f"Дефицит свободного напора ΔP={margin_m} м для {floors_map.get(nid, 1)}-эт. застройки (Z={elev_map.get(nid, '—')} м, трубы: {', '.join('#'+x for x in conn_pipe_ids)}). Требуется закольцовка или локальная ПНС."
            elif p_max is not None and p_max > 60.0:
                p_category = "excess"
                node_diag = f"Избыточный напор Pмакс={p_max} м (>60 м по СП 31.13330.2021) при низкой отметке Z={elev_map.get(nid, '—')} м. Рекомендуется установка PRV на вводе (участки {', '.join('#'+x for x in conn_pipe_ids)})."

        prop_obj = {
            "role": node_role,
            "z": round(elev_map[nid], 2) if nid in elev_map else None,
            "p_min": p_min,
            "p_mean": p_mean,
            "p_max": p_max,
            "req": round(req_m, 2) if req_m > 0 else "Не нормируется (транзит)",
            "floors": floors_map.get(nid, 0),
            "margin": margin_m,
            "demand": round(q_mean, 3),
            "transit_q": tr_flow,
            "connected_pipes": deg,
            "connected_pipe_ids": conn_pipe_ids,
            "pressure_category": p_category,
            "diagnostic_advice": node_diag,
            "schedules": ", ".join(sorted(set(node_schedules.get(nid, [])))) or ("Транзит" if not is_active_consumer else "Базовый"),
            "name": clean_object_name(obj_names.get(nid, obj_names.get(cid, ""))),
        }
        all_node_props[nid] = prop_obj
        all_node_props[cid] = prop_obj

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
            "name": clean_object_name(obj_names.get(nid, "")),
            "display_id": label_with_name(nid),
            "elevation_m": round(elev_map[nid], 2) if nid in elev_map else None,
            "floors": floors_map.get(nid, 0),
            "required_pressure_m": round(req_m, 2) if req_m > 0 else None,
            "free_pressure_min_m": p_min,
            "free_pressure_max_m": p_max,
            "pressure_surplus_deficit_m": margin_m,
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
    closed_pipes_list = [p for p in pipe_records if p.get("is_closed")]
    reverse_flow_pipes = sorted(
        [p for p in pipe_records if p.get("reverse_steps", 0) > 0],
        key=lambda x: x["reverse_steps"],
        reverse=True,
    )

    # Формируем сбалансированную выборку труб для клиентской таблицы, чтобы каждый сектор круговой диаграммы скоростей имел данные для фильтрации
    pipes_for_client_table: list[dict[str, Any]] = []
    seen_p_ids: set[str] = set()
    for p_list in (
        all_high_loss_pipes[:80],
        low_c_pipes[:40],
        closed_pipes_list[:50],
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
    qh_raw: dict[str, list[tuple[float, float]]] = {}
    if not df_qh.empty and len(df_qh.columns) >= 3:
        for uid, qv, hv in zip(
            df_qh[df_qh.columns[0]],
            pd.to_numeric(df_qh.iloc[:, 1], errors="coerce"),
            pd.to_numeric(df_qh.iloc[:, 2], errors="coerce"),
        ):
            if not (math.isnan(float(qv)) or math.isnan(float(hv))):
                qh_raw.setdefault(str(uid).strip(), []).append((float(qv), float(hv)))

    all_pumps_list = []
    qh_chart_items = []
    total_theoretical_q_m3_h = 0.0
    total_pumped_m3_day = 0.0
    total_pump_energy_kwh_day = 0.0

    if not df_pumps.empty:
        c_id = df_pumps.columns[0]
        c_curve = find_col(df_pumps, "кривая", default_idx=len(df_pumps.columns) - 1)
        for pid, cid_curve in zip(df_pumps[c_id], df_pumps[c_curve] if c_curve else [""] * len(df_pumps)):
            cid_s = str(cid_curve).strip()
            pts = qh_raw.get(cid_s, [])
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

            # Смежная топология вокруг насоса: входной узел/источник и выходной напорный коллектор + отходящие трубы
            sn_props = all_node_props.get(sn_node) or all_node_props.get(sn_clean_num) or {}
            en_props = all_node_props.get(en_node) or all_node_props.get(en_clean_num) or {}
            src_head = source_head_by_cid.get(sn_clean_num) or sn_props.get("z")
            col_z = en_props.get("z")
            col_p_mean = en_props.get("p_mean") if en_props.get("p_mean") is not None else en_props.get("p_max")
            col_p_max = en_props.get("p_max")
            out_pipes = adj_pipes_by_node.get(en_clean_num) or adj_pipes_by_node.get(en_node) or []
            out_pipes_sorted = sorted(out_pipes, key=lambda x: (x.get("headloss_gradient_m_km") or 0.0), reverse=True)
            top_out_pipe = out_pipes_sorted[0] if out_pipes_sorted else None
            closed_out_pipes = [p for p in out_pipes if p.get("is_closed")]

            pst = pump_ts["rows"].get(pid)
            is_active = bool(pst and pst["max"] > 1e-3)
            q_work, q_max_p, h_est, ratio, run_h = None, None, None, None, 0.0

            if not is_active:
                zone_label = "Выключен / резерв (Q = 0)"
                if closed_out_pipes:
                    cp_id = closed_out_pipes[0]["clean_id"]
                    diag_advice = (
                        f"Подача Q = 0 м³/ч: за напорным узлом #{en_clean_num or '—'} перекрыт отходящий участок #{cp_id} (Q=0). "
                        f"В ПК «Аквабит» откройте трубу #{cp_id} либо оставьте агрегат #{pid_clean_num} в резерве."
                    )
                elif col_p_max is not None and col_p_max > 10:
                    diag_advice = (
                        f"Агрегат #{pid_clean_num} находится в резерве (Q = 0 м³/ч). В приемном узле коллектора #{en_clean_num or '—'} "
                        f"поддерживается напор P = {col_p_max} м от работающих насосов."
                    )
                else:
                    diag_advice = (
                        f"Агрегат #{pid_clean_num} отключен в расчете (Q = 0 м³/ч, резерв). Связь: узел #{sn_clean_num or '—'} → коллектор #{en_clean_num or '—'}."
                    )
            else:
                run_h = round(pst["pos_steps"] * step_min / 60.0, 2)
                q_work = round(pst["mean"] * (max(pump_ts["n_steps"], 1) / max(pst["pos_steps"], 1)), 2)
                q_max_p = round(pst["max"], 2)
                total_pumped_m3_day += pst["mean"] * 24.0
                h_est = evaluate_qh_curve_head(pts, q_work)
                if h_est is not None and q_work > 0:
                    kw_hyd = (9.81 * q_work * h_est) / 3600.0
                    total_pump_energy_kwh_day += kw_hyd * run_h
                ratio = round(q_work / q_nom, 2) if (q_nom and q_nom > 0) else None
                pct_nom = int(round((ratio or 0) * 100))

                if ratio is None:
                    zone_label = "Кривая Q-H не задана"
                    diag_advice = f"Фактическая подача Q = {q_work} м³/ч в узел #{en_clean_num or '—'}, но паспортная кривая #{cid_s} не найдена в Кривая QH.csv."
                elif ratio < 0.75:
                    zone_label = "Недогрузка (< 75% Q_ном)"
                    # Однозначное определение причины по фактическим данным коллектора и отходящих труб
                    col_p_disp = col_p_mean if col_p_mean is not None else "—"
                    col_z_disp = col_z if col_z is not None else "—"
                    src_h_disp = src_head if src_head is not None else "—"
                    sn_p_disp = sn_props.get("p_mean") if sn_props.get("p_mean") is not None else "—"
                    if top_out_pipe and (top_out_pipe.get("headloss_gradient_m_km") or 0) >= 15.0:
                        tp_id = top_out_pipe["clean_id"]
                        tp_i = top_out_pipe["headloss_gradient_m_km"]
                        tp_d = top_out_pipe["diameter_mm"]
                        tp_c = top_out_pipe["hazen_williams_c"]
                        diag_advice = (
                            f"Подача зажата до {q_work} м³/ч ({pct_nom}% от Qном={q_nom} м³/ч) из-за высокого сопротивления отходящего от коллектора #{en_clean_num} "
                            f"водовода #{tp_id} (D={tp_d} мм, C={tp_c}, потери i={tp_i} м/км; давление в коллекторе #{en_clean_num} P={col_p_disp} м). "
                            f"Мероприятие в Аквабит: увеличить диаметр/коэффициент C трубы #{tp_id} или перераспределить нагрузку."
                        )
                    elif closed_out_pipes:
                        cp_id = closed_out_pipes[0]["clean_id"]
                        diag_advice = (
                            f"Подача ограничена до {q_work} м³/ч ({pct_nom}% Qном): в узле коллектора #{en_clean_num} (P={col_p_disp} м) "
                            f"перекрыта смежная линия #{cp_id} (Q=0). Мероприятие в Аквабит: открыть участок #{cp_id} для разгрузки коллектора #{en_clean_num}."
                        )
                    elif is_borehole:
                        if col_p_mean is not None and h_nom is not None and col_p_mean >= h_nom * 0.85:
                            diag_advice = (
                                f"Скважинный агрегат #{pid_clean_num} (источник #{sn_clean_num}, Hист={src_h_disp} м) выдает {q_work} м³/ч ({pct_nom}% Qном) "
                                f"вследствие высокого противодавления P={col_p_mean} м в сборном коллекторе #{en_clean_num} (Z={col_z_disp} м). "
                                f"Мероприятие: разгрузить сборный коллектор #{en_clean_num} или скорректировать кривую #{cid_s}."
                            )
                        else:
                            diag_advice = (
                                f"Скважинный агрегат #{pid_clean_num} развивает напор H={h_est} м при подаче {q_work} м³/ч ({pct_nom}% Qном) на подъеме от источника #{sn_clean_num} "
                                f"(отметка/напор {src_h_disp} м) к узлу #{en_clean_num} (Z={col_z_disp} м, P={col_p_disp} м). "
                                f"Мероприятие в Аквабит: проверить динамический уровень в источнике #{sn_clean_num} и соответствие кривой #{cid_s} геодезическому подъему."
                            )
                    else:
                        diag_advice = (
                            f"Сетевой насос #{pid_clean_num} выдает {q_work} м³/ч ({pct_nom}% от Qном={q_nom} м³/ч, H={h_est} м) на участке #{sn_clean_num} (Pвх={sn_p_disp} м) "
                            f"→ #{en_clean_num} (Pвых={col_p_disp} м). Высокое противодавление после узла #{en_clean_num}; проверить пропускную способность отходящих линий."
                        )
                elif ratio <= 1.15:
                    col_p_disp = col_p_mean if col_p_mean is not None else "—"
                    zone_label = "Оптимум BEP (75–115% Q_ном)"
                    diag_advice = (
                        f"Штатная работа в зоне BEP ({pct_nom}% Qном): подача Q={q_work} м³/ч, напор H={h_est} м "
                        f"(узел входа #{sn_clean_num or '—'} → напорный узел #{en_clean_num or '—'}, Pвых={col_p_disp} м)."
                    )
                else:
                    col_p_disp = col_p_mean if col_p_mean is not None else "—"
                    zone_label = "Перегрузка (> 115% Q_ном)"
                    tp_info = f" в водовод #{top_out_pipe['clean_id']} (D={top_out_pipe['diameter_mm']} мм)" if top_out_pipe else ""
                    diag_advice = (
                        f"Работа правее зоны BEP ({pct_nom}% Qном): расход вырос до Q={q_work} м³/ч (при Qном={q_nom} м³/ч) с просадкой напора до H={h_est} м "
                        f"из-за низкого противодавления в приемном узле #{en_clean_num or '—'} (P={col_p_disp} м){tp_info}. "
                        f"Мероприятие в Аквабит: снизить давление на выходе насоса #{pid_clean_num} (регулирование ЧРП или дросселирование после узла #{en_clean_num})."
                    )

            disp = label_with_name(pid)
            cid = clean_object_id(pid)
            clean_nm = clean_object_name(obj_names.get(pid, ""))
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
        for vtid, vtname in zip(df_valve_types.iloc[:, 0], df_valve_types.iloc[:, 1]):
            valve_type_map[str(vtid).strip()] = str(vtname).strip()

    valves_table: list[dict[str, Any]] = []
    valves_hourly_series: list[dict[str, Any]] = []
    self_loop_valves = 0
    if not df_valves.empty:
        c_id = df_valves.columns[0]
        c_s = find_col(df_valves, "начал", default_idx=1 if len(df_valves.columns) > 1 else None)
        c_e = find_col(df_valves, "конец", "конечн", default_idx=2 if len(df_valves.columns) > 2 else None)
        c_t = find_col(df_valves, "тип", default_idx=3 if len(df_valves.columns) > 3 else None)
        c_set = find_col(df_valves, "настройк", "уставк", default_idx=4 if len(df_valves.columns) > 4 else None)

        for vid, sn, en, vtype_raw, vset in zip(
            df_valves[c_id],
            df_valves[c_s] if c_s else [""] * len(df_valves),
            df_valves[c_e] if c_e else [""] * len(df_valves),
            df_valves[c_t].astype(str).str.strip() if c_t else ["V1"] * len(df_valves),
            pd.to_numeric(df_valves[c_set], errors="coerce") if c_set else pd.Series(np.nan, index=df_valves.index),
        ):
            vid_s = str(vid).strip()
            sn_s, en_s, had_bug = resolve_link_endpoints(vid_s, sn, en)
            if had_bug:
                self_loop_valves += 1
            vt_code = valve_type_map.get(vtype_raw, vtype_raw)
            vt_desc = VALVE_TYPE_DESC.get(vt_code, "Регулирующий клапан")
            vst = valve_ts["rows"].get(vid_s)
            q_min_v = round(vst["min"], 2) if vst else 0.0
            q_mean_v = round(vst["mean"], 2) if vst else 0.0
            q_max_v = round(vst["max"], 2) if vst else 0.0
            q_swing_v = round(q_max_v - q_min_v, 2)

            sn_c_v = clean_object_id(sn_s) or sn_s
            en_c_v = clean_object_id(en_s) or en_s
            _sn_p = (all_node_props.get(sn_s) or all_node_props.get(sn_c_v) or all_node_props.get(f"c{sn_c_v}") or {}).get("p_mean")
            _en_p = (all_node_props.get(en_s) or all_node_props.get(en_c_v) or all_node_props.get(f"c{en_c_v}") or {}).get("p_mean")
            _dp_v = round(float(_sn_p) - float(_en_p), 2) if (isinstance(_sn_p, (int, float)) and isinstance(_en_p, (int, float))) else None
            valves_table.append({
                "valve_id": vid_s,
                "clean_id": clean_object_id(vid_s),
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
            f"В Клапан.csv обнаружено {self_loop_valves} строк, где Начальный узел == Конечный узел."
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
            f"Лимитирующий участок {top_p['display_id']} (узлы {top_p['start_node']} → {top_p['end_node']}, D={top_p['diameter_mm']} мм, C={top_p['hazen_williams_c']}): скорость {top_p['velocity_max_m_s']} м/с создаёт удельную потерю напора {top_p['headloss_gradient_m_km']} м/км."
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
            "closed_zero_flow_pipes_count": len(closed_pipes_list),
            "closed_pipes_sample": closed_pipes_list[:50],
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
            "q": p.get("flow_max_m3_h"),
            "sn": p.get("start_node"),
            "en": p.get("end_node"),
            "v_bin": p.get("velocity_bin"),
            "advice": p.get("diagnostic_advice"),
            "closed": p.get("is_closed"),
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
            "links": inp_meta["links"],
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
