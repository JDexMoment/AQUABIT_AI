"""EPANET-AI v2 Ultra-Fast Processing, Facts & Standalone HTML Report Engine for ПК «Аквабит» (AquaBit).

Designed for 1–3 minute end-to-end runtime (raw CSV -> fast processing -> local LLM streaming -> HTML report)
even on client PCs weaker than an RTX 3060 Laptop GPU.

Supports both AquaBit CSV export dialects discovered across real city datasets:
  - Dialect A (compact headers): Узел(ID,Высота,Требуемое давление), Кривая QH(ID,X,Y), Труба(ID,Начало,Конец,...)
  - Dialect B (headers with unquoted commas before units):
      Узел: ID,Высотная отметка, м,Требуемое давление, м
      Кривая QH: ID,Поток, м3/час,Давление, м
      Труба: ID,Начальный узел,Конечный узел,Диаметр, мм,Длина, м,Шероховатость,Малые потери
      Потребители Узел: ID,Потребление, м3/час,График потребления
      Источник: ID,Расчетный напор, м
"""

from __future__ import annotations

import html
import io
import json
import math
import re
import time
import unicodedata
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

KNOWN_UNIT_TOKENS: set[str] = {
    "м",
    "мм",
    "км",
    "м2",
    "м3",
    "м3/час",
    "м3/ч",
    "м³/час",
    "м³/ч",
    "м3/сут",
    "м³/сут",
    "л/с",
    "л/мин",
    "бар",
    "атм",
    "мпа",
    "кпа",
    "па",
    "%",
    "ч",
    "час",
    "мин",
    "с",
    "сек",
    "квт",
    "квт*ч",
    "квт·ч",
    "мг/л",
    "г/м3",
    "балл",
    "баллов",
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
    """Merges unquoted comma-separated unit tokens (e.g. 'Поток, м3/час' -> 'Поток (м3/час)')."""
    tokens = [t.strip() for t in raw_header_line.lstrip("\ufeff").rstrip("\r\n").split(",")]
    if len(tokens) <= 1:
        return tokens

    target_cols: int | None = None
    if sample_data_line:
        s_clean = sample_data_line.rstrip("\r\n")
        if s_clean.strip():
            target_cols = len(s_clean.split(","))

    # If header already matches data column count, double-check no shift happened
    if target_cols is not None and len(tokens) == target_cols:
        return tokens

    merged: list[str] = []
    for tok in tokens:
        tok_norm = tok.lower().strip()
        if merged and tok_norm in KNOWN_UNIT_TOKENS:
            if target_cols is None or (len(merged) + 1) > target_cols or True:
                merged[-1] = f"{merged[-1]} ({tok})"
                continue
        merged.append(tok)

    # Safety fallback if counts still differ
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
    """Fast, Windows-Cyrillic-safe, comma-unit-header-aware CSV reader for reference tables."""
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
    cols_lower = {c: c.lower() for c in df.columns}
    for sub in substrings:
        s = sub.lower()
        for col, cl in cols_lower.items():
            if s in cl:
                return col
    if default_idx is not None and 0 <= default_idx < len(df.columns):
        return str(df.columns[default_idx])
    return None


def safe_float(val: Any, decimals: int = 3) -> float | None:
    if val is None:
        return None
    try:
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return None
        return round(f, decimals)
    except (ValueError, TypeError):
        return None


def classify_roughness_score(score: float | None) -> str:
    """AquaBit relative pipe roughness scale: lower = worse, 80 = normal, 100 = good."""
    if score is None:
        return "не указано"
    if score >= 95.0:
        return "хорошо (~100 баллов)"
    if score >= 78.0:
        return "норма (~80 баллов)"
    if score >= 60.0:
        return "ниже нормы (<80 баллов, умеренный износ)"
    return "ухудшенное (<60 баллов, сильное зарастание/износ)"


def clean_object_id(raw_id: str | None) -> str:
    """Strips leading prefix letter [ceputv] from object ID: c345977 -> 345977, e1216 -> 1216."""
    if not raw_id:
        return ""
    s = str(raw_id).strip()
    return re.sub(r"^[ceputv](\d+)$", r"\1", s, flags=re.IGNORECASE)


def clean_object_name(raw_name: str | None) -> str:
    """Strips '(Google)' or similar artifact from object name."""
    if not raw_name:
        return ""
    return re.sub(r"\s*\(\s*Google\s*\)\s*", "", str(raw_name), flags=re.IGNORECASE).strip()


def evaluate_qh_curve_head(pts: list[tuple[float, float]], q_val: float) -> float | None:
    """Evaluates pump head H(Q) for multi-point curve or 1-point Walski/EPANET nominal curve."""
    if not pts:
        return None
    pts_sorted = sorted(pts, key=lambda x: x[0])
    if len(pts_sorted) == 1:
        q_nom, h_nom = pts_sorted[0]
        if q_nom <= 0 or h_nom <= 0:
            return max(0.0, h_nom)
        # Standard EPANET / AquaBit 1-point pump curve: H(0)=1.333*H_nom, H(Q_nom)=H_nom, H(2*Q_nom)=0
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
    """Builds smooth plot coordinates + design points for interactive HTML Q-H chart."""
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
) -> dict[str, Any]:
    """Ultra-fast chunked NumPy reader for R0..R1439 wide AquaBit tables (no disk rewrites!).

    - row_offsets: optional dict {id: Z_m} subtracted from each row (computes P_free = H - Z on the fly!)
    - hourly_mode:
        'sum' -> sums across rows per hour (for demand / pump flow / valve flow)
        'percentiles' -> computes p10 / p50 / p90 across rows per hour (for free pressure!)
    """
    if fpath is None or not fpath.exists() or fpath.stat().st_size == 0:
        return {"exists": False, "n_steps": 0, "rows": {}, "hourly_24": None}

    with fpath.open("r", encoding="utf-8-sig", errors="replace") as fh:
        first_line = fh.readline().strip()
    if not first_line:
        return {"exists": False, "n_steps": 0, "rows": {}, "hourly_24": None}

    hdr = [c.strip() for c in first_line.split(",")]
    step_cols = [c for c in hdr[1:] if c]
    n_steps = len(step_cols)
    if n_steps == 0:
        return {"exists": True, "n_steps": 0, "rows": {}, "hourly_24": None}

    # Map R0..R(n_steps-1) into 24 hourly buckets
    n_buckets = min(24, n_steps)
    bucket_indices = np.array_split(np.arange(n_steps), n_buckets)

    row_stats: dict[str, dict[str, Any]] = {}
    hourly_sum = np.zeros(n_buckets, dtype=np.float64)
    hourly_samples_p: list[list[np.ndarray]] = [[] for _ in range(n_buckets)]
    valid_numeric_any = False

    # Open via Python text stream to avoid Windows Cyrillic C-engine path bug while keeping C-parser speed!
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
                offs = np.array([row_offsets.get(i, np.nan) for i in ids], dtype=np.float32)[:, None]
                mat = mat - offs

            if filter_ids is not None:
                keep_mask = np.array([i in filter_ids for i in ids], dtype=bool)
            else:
                keep_mask = np.ones(len(ids), dtype=bool)

            finite_mask = np.isfinite(mat)
            if not finite_mask.any():
                continue
            valid_numeric_any = True

            # Hourly aggregation across active/filtered rows
            mat_kept = mat[keep_mask]
            if mat_kept.shape[0] > 0:
                for b_idx, cols_idx in enumerate(bucket_indices):
                    sub = mat_kept[:, cols_idx]
                    if hourly_mode == "sum":
                        # Mean within the hour across minute steps, summed over objects -> m3/h
                        row_hour_means = np.nanmean(np.where(np.isfinite(sub), sub, 0.0), axis=1)
                        hourly_sum[b_idx] += float(np.nansum(row_hour_means))
                    elif hourly_mode == "sum_positive":
                        # Positive consumer delivery only (prevents negative vacuum backflow from canceling demand!)
                        pos_sub = np.where(np.isfinite(sub) & (sub > 0.0), sub, 0.0)
                        row_hour_means = np.mean(pos_sub, axis=1)
                        hourly_sum[b_idx] += float(np.sum(row_hour_means))
                    elif hourly_mode == "percentiles":
                        col_mid = sub[:, len(cols_idx) // 2]
                        fin_vals = col_mid[np.isfinite(col_mid)]
                        if fin_vals.size > 0:
                            hourly_samples_p[b_idx].append(fin_vals)

            # Per-row vectorized stats
            row_has_fin = finite_mask.any(axis=1)
            safe_min = np.where(finite_mask, mat, np.inf).min(axis=1)
            safe_max = np.where(finite_mask, mat, -np.inf).max(axis=1)
            counts = finite_mask.sum(axis=1)
            sums = np.where(finite_mask, mat, 0.0).sum(axis=1)
            pos_sums = np.where(finite_mask & (mat > 0.0), mat, 0.0).sum(axis=1)
            neg_steps = (np.where(finite_mask, mat, 0.0) < -1e-6).sum(axis=1)
            pos_steps = (np.where(finite_mask, mat, 0.0) > 1e-4).sum(axis=1)
            abs_max = np.where(finite_mask, np.abs(mat), 0.0).max(axis=1)

            # Sign flips / pump starts
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
            for b_idx in range(n_buckets):
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
        "rows": row_stats,
        "hourly_24": hourly_out,
    }


def process_aquabit_city_fast(
    raw_city_dir: Path,
    cleaned_root_dir: Path | None = None,
    top_k: int = 10,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Minimal ultra-fast AquaBit city processor (runs in ~0.4–6s on raw CSV exports).

    Returns:
      (facts_dict, chart_payload_dict)
    And optionally saves lightweight cleaned tables + JSON passports to `cleaned_root_dir/<city>/`.
    """
    t0 = time.perf_counter()
    city_name = unicodedata.normalize("NFC", raw_city_dir.name)

    # 1. Read compact topological & reference tables
    f_nodes = _resolve_file_ci(raw_city_dir, "Узел.csv")
    f_pipes = _resolve_file_ci(raw_city_dir, "Труба.csv")
    f_pumps = _resolve_file_ci(raw_city_dir, "Насос.csv")
    f_valves = _resolve_file_ci(raw_city_dir, "Клапан.csv")
    f_qh = _resolve_file_ci(raw_city_dir, "Кривая QH.csv")
    f_cons = _resolve_file_ci(raw_city_dir, "Потребители Узел.csv")
    f_sched = _resolve_file_ci(raw_city_dir, "График потребления.csv")
    f_src = _resolve_file_ci(raw_city_dir, "Источник.csv")

    df_nodes = read_aquabit_csv(f_nodes) if f_nodes else pd.DataFrame()
    df_pipes = read_aquabit_csv(f_pipes) if f_pipes else pd.DataFrame()
    df_pumps = read_aquabit_csv(f_pumps) if f_pumps else pd.DataFrame()
    df_valves = read_aquabit_csv(f_valves) if f_valves else pd.DataFrame()
    df_qh = read_aquabit_csv(f_qh) if f_qh else pd.DataFrame()
    df_cons = read_aquabit_csv(f_cons) if f_cons else pd.DataFrame()
    df_sched = read_aquabit_csv(f_sched) if f_sched else pd.DataFrame()
    df_src = read_aquabit_csv(f_src) if f_src else pd.DataFrame()

    present_tables = sorted([p.name for p in raw_city_dir.iterdir() if p.is_file() and p.suffix.lower() == ".csv"])

    # --- Parse Nodes (Узел.csv) & Preserve ALL elevations Z before zero-row pruning ---
    total_nodes_raw = len(df_nodes)
    elev_map: dict[str, float] = {}
    req_p_map: dict[str, float] = {}
    floors_map: dict[str, int] = {}
    pruned_transit_nodes = 0

    if not df_nodes.empty:
        c_id = df_nodes.columns[0]
        c_z = find_col(df_nodes, "высот", "отмет", default_idx=1)
        c_req = find_col(df_nodes, "требуем", "давлен", default_idx=2 if len(df_nodes.columns) > 2 else None)

        ids_arr = df_nodes[c_id].astype(str).str.strip().tolist()
        z_arr = pd.to_numeric(df_nodes[c_z], errors="coerce").to_numpy() if c_z else np.full(len(df_nodes), np.nan)
        req_arr = pd.to_numeric(df_nodes[c_req], errors="coerce").fillna(0.0).to_numpy() if c_req else np.zeros(len(df_nodes))

        for nid, zv, rv in zip(ids_arr, z_arr, req_arr):
            if not math.isnan(float(zv)):
                elev_map[nid] = float(zv)
            if rv > 0.0:
                req_p_map[nid] = float(rv)
                # Domain formula: 10 m for 1-story + 4 m per extra floor -> N = 1 + (P_req - 10)/4
                floors_map[nid] = max(1, int(round(1.0 + max(0.0, float(rv) - 10.0) / 4.0)))
            else:
                pruned_transit_nodes += 1

    # --- Parse Consumers (Потребители Узел.csv) & Zero Pruning ---
    pruned_zero_consumers = 0
    base_demand_by_node: dict[str, float] = {}
    schedules_used_by_consumers: set[str] = set()
    if not df_cons.empty:
        c_id = df_cons.columns[0]
        c_q = find_col(df_cons, "потреблен", default_idx=1)
        c_sch = find_col(df_cons, "график", default_idx=2 if len(df_cons.columns) > 2 else None)
        q_vals = pd.to_numeric(df_cons[c_q], errors="coerce").fillna(0.0).to_numpy() if c_q else np.zeros(len(df_cons))
        sch_vals = df_cons[c_sch].astype(str).str.strip().tolist() if c_sch else [""] * len(df_cons)
        for nid, qv, sv in zip(df_cons[c_id].astype(str).str.strip().tolist(), q_vals, sch_vals):
            if abs(float(qv)) <= 1e-9:
                pruned_zero_consumers += 1
            else:
                base_demand_by_node[nid] = base_demand_by_node.get(nid, 0.0) + float(qv)
                if sv and sv.lower() not in ("nan", "t", "none"):
                    schedules_used_by_consumers.add(sv)

    active_consumer_node_ids = set(req_p_map.keys()) | set(base_demand_by_node.keys())

    # --- Stream Node Head / Free Pressure (Напор Узел.csv or Свободный напор Узел.csv) ---
    f_free_direct = _resolve_file_ci(raw_city_dir, "Свободный напор Узел.csv")
    f_head_total = _resolve_file_ci(raw_city_dir, "Напор Узел.csv")
    if f_free_direct is not None:
        head_source_name = f_free_direct.name
        head_ts = _stream_wide_timeseries(
            f_free_direct,
            row_offsets=None,
            hourly_mode="percentiles",
            filter_ids=active_consumer_node_ids if active_consumer_node_ids else None,
        )
    else:
        head_source_name = f_head_total.name if f_head_total else "Напор Узел.csv"
        head_ts = _stream_wide_timeseries(
            f_head_total,
            row_offsets=elev_map if elev_map else None,
            hourly_mode="percentiles",
            filter_ids=active_consumer_node_ids if active_consumer_node_ids else None,
        )

    # --- Stream Node Actual Demand (Потребление Узел.csv) ---
    f_dem_ts = _resolve_file_ci(raw_city_dir, "Потребление Узел.csv")
    dem_ts = _stream_wide_timeseries(f_dem_ts, hourly_mode="sum_positive")
    pruned_zero_demand_ts_rows = 0
    negative_demand_nodes_cnt = 0
    if dem_ts["exists"]:
        for nid, st in dem_ts["rows"].items():
            if st["abs_max"] <= 1e-6:
                pruned_zero_demand_ts_rows += 1
            elif st["min"] < -1e-4:
                negative_demand_nodes_cnt += 1
                active_consumer_node_ids.add(nid)
            elif st["mean"] > 0:
                active_consumer_node_ids.add(nid)

    # --- Build Node Analytics Table & Pressure Zones ---
    n_steps_sim = head_ts["n_steps"] or dem_ts["n_steps"] or 1440
    step_minutes = 1440.0 / max(n_steps_sim, 1)

    node_records: list[dict[str, Any]] = []
    pressure_bins = {
        "Вакуум (< 0 м)": 0,
        "Дефицит (0 м .. < P_треб)": 0,
        "Норма (P_треб .. 60 м)": 0,
        "Избыток (> 60 м, СП 31.13330)": 0,
    }

    target_nodes_iter = sorted(active_consumer_node_ids) if active_consumer_node_ids else sorted(elev_map.keys())
    for nid in target_nodes_iter:
        z_m = elev_map.get(nid)
        req_m = req_p_map.get(nid, 0.0)
        fl = floors_map.get(nid, 0)
        hst = head_ts["rows"].get(nid)
        dst = dem_ts["rows"].get(nid)

        q_mean = dst["pos_mean"] if (dst and dst["pos_mean"] > 0) else base_demand_by_node.get(nid, 0.0)
        q_max = dst["max"] if (dst and dst["max"] > 0) else base_demand_by_node.get(nid, 0.0)
        daily_m3 = q_mean * 24.0

        p_min = round(hst["min"], 2) if hst else None
        p_mean = round(hst["mean"], 2) if hst else None
        p_max = round(hst["max"], 2) if hst else None
        margin_min = round(p_min - req_m, 2) if (p_min is not None and req_m > 0) else None

        if p_min is not None:
            if p_min < 0.0:
                pressure_bins["Вакуум (< 0 м)"] += 1
            elif req_m > 0 and p_min < req_m:
                pressure_bins["Дефицит (0 м .. < P_треб)"] += 1
            elif p_max is not None and p_max > 60.0:
                pressure_bins["Избыток (> 60 м, СП 31.13330)"] += 1
            else:
                pressure_bins["Норма (P_треб .. 60 м)"] += 1

        node_records.append(
            {
                "node_id": nid,
                "elevation_m": safe_float(z_m, 2),
                "required_pressure_m": safe_float(req_m, 2) if req_m > 0 else None,
                "floors": fl,
                "free_pressure_min_m": p_min,
                "free_pressure_mean_m": p_mean,
                "free_pressure_max_m": p_max,
                "margin_min_m": margin_min,
                "demand_mean_m3_h": safe_float(q_mean, 3),
                "demand_peak_m3_h": safe_float(q_max, 3),
                "daily_volume_m3_day": safe_float(daily_m3, 2),
            }
        )

    p_mins_all = [r["free_pressure_min_m"] for r in node_records if r["free_pressure_min_m"] is not None]
    p_maxs_all = [r["free_pressure_max_m"] for r in node_records if r["free_pressure_max_m"] is not None]
    deficit_nodes = sorted(
        [
            r
            for r in node_records
            if (r["margin_min_m"] is not None and r["margin_min_m"] < 0.0)
            or (r["free_pressure_min_m"] is not None and r["free_pressure_min_m"] < 0.0)
        ],
        key=lambda x: (x["margin_min_m"] if x["margin_min_m"] is not None else x["free_pressure_min_m"]),
    )
    vacuum_nodes_cnt = sum(1 for r in node_records if r["free_pressure_min_m"] is not None and r["free_pressure_min_m"] < 0.0)
    excess_nodes = sorted(
        [r for r in node_records if r["free_pressure_max_m"] is not None and r["free_pressure_max_m"] > 60.0],
        key=lambda x: x["free_pressure_max_m"],
        reverse=True,
    )

    floors_dist: dict[str, int] = {}
    for fl_v in sorted(set(floors_map.values())):
        if fl_v > 0:
            floors_dist[f"{fl_v}_эт"] = sum(1 for v in floors_map.values() if v == fl_v)

    total_daily_demand_m3 = round(sum((r["daily_volume_m3_day"] or 0.0) for r in node_records), 2)
    peak_network_demand_m3_h = (
        round(max(dem_ts["hourly_24"]), 2)
        if (dem_ts["hourly_24"] and max(dem_ts["hourly_24"]) > 0)
        else round(sum(base_demand_by_node.values()), 2)
    )

    # --- Parse Pipes (Труба.csv) + Stream Pipe Flows (Поток Труба.csv) ---
    f_pipe_ts = _resolve_file_ci(raw_city_dir, "Поток Труба.csv")
    pipe_ts = _stream_wide_timeseries(f_pipe_ts, hourly_mode="none")

    pipe_records: list[dict[str, Any]] = []
    roughness_bins = {
        "Хорошо (95–100+ баллов)": 0.0,
        "Норма (~80 баллов: 78–94)": 0.0,
        "Ниже нормы (60–77 баллов)": 0.0,
        "Износ / зарастание (< 60 баллов)": 0.0,
    }
    velocity_bins = {
        "Застой (< 0.30 м/с)": 0,
        "Оптимум (0.30–1.50 м/с)": 0,
        "Повышенная (1.50–3.00 м/с)": 0,
        "Риск гидроудара (> 3.00 м/с)": 0,
    }
    diameter_km: dict[str, float] = {}
    total_len_m = 0.0
    total_vol_m3 = 0.0
    weighted_rough_num = 0.0
    weighted_rough_den = 0.0
    degraded_pipes_cnt = 0
    degraded_pipes_km = 0.0

    if not df_pipes.empty:
        c_id = df_pipes.columns[0]
        c_s = find_col(df_pipes, "начал", default_idx=1)
        c_e = find_col(df_pipes, "конец", "конечн", default_idx=2)
        c_d = find_col(df_pipes, "диаметр", default_idx=3)
        c_l = find_col(df_pipes, "длин", default_idx=4)
        c_r = find_col(df_pipes, "шероховат", default_idx=5)

        p_ids = df_pipes[c_id].astype(str).str.strip().tolist()
        s_nodes = df_pipes[c_s].astype(str).str.strip().tolist() if c_s else [""] * len(df_pipes)
        e_nodes = df_pipes[c_e].astype(str).str.strip().tolist() if c_e else [""] * len(df_pipes)
        d_mm_arr = pd.to_numeric(df_pipes[c_d], errors="coerce").fillna(0.0).to_numpy() if c_d else np.zeros(len(df_pipes))
        l_m_arr = pd.to_numeric(df_pipes[c_l], errors="coerce").fillna(0.0).to_numpy() if c_l else np.zeros(len(df_pipes))
        r_pts_arr = pd.to_numeric(df_pipes[c_r], errors="coerce").to_numpy() if c_r else np.full(len(df_pipes), np.nan)

        for pid, sn, en, d_mm, l_m, r_pt in zip(p_ids, s_nodes, e_nodes, d_mm_arr, l_m_arr, r_pts_arr):
            d_val = float(d_mm)
            l_val = max(0.0, float(l_m))
            r_val = None if math.isnan(float(r_pt)) else round(float(r_pt), 2)
            total_len_m += l_val
            if d_val > 0:
                area = math.pi * ((d_val / 1000.0) ** 2) / 4.0
                total_vol_m3 += area * l_val
            else:
                area = 0.0

            # Diameter bucket in km
            if d_val <= 150:
                d_key = "≤150 мм"
            elif d_val <= 300:
                d_key = "151–300 мм"
            elif d_val <= 600:
                d_key = "301–600 мм"
            else:
                d_key = ">600 мм"
            diameter_km[d_key] = round(diameter_km.get(d_key, 0.0) + l_val / 1000.0, 3)

            # Relative roughness score in points (<80 worse, 80 normal, 100 good)
            if r_val is not None:
                w = max(l_val, 1.0)
                weighted_rough_num += r_val * w
                weighted_rough_den += w
                km_seg = l_val / 1000.0
                if r_val >= 95.0:
                    roughness_bins["Хорошо (95–100+ баллов)"] += km_seg
                elif r_val >= 78.0:
                    roughness_bins["Норма (~80 баллов: 78–94)"] += km_seg
                elif r_val >= 60.0:
                    roughness_bins["Ниже нормы (60–77 баллов)"] += km_seg
                    degraded_pipes_cnt += 1
                    degraded_pipes_km += km_seg
                else:
                    roughness_bins["Износ / зарастание (< 60 баллов)"] += km_seg
                    degraded_pipes_cnt += 1
                    degraded_pipes_km += km_seg

            pst = pipe_ts["rows"].get(pid)
            v_max = None
            q_abs_max = None
            neg_steps = 0
            flips = 0
            if pst is not None:
                q_abs_max = round(pst["abs_max"], 2)
                neg_steps = pst["neg_steps"]
                flips = pst["flips"]
                if area > 0:
                    v_max = round((pst["abs_max"] / 3600.0) / area, 3)
                    if v_max < 0.30:
                        velocity_bins["Застой (< 0.30 м/с)"] += 1
                    elif v_max <= 1.50:
                        velocity_bins["Оптимум (0.30–1.50 м/с)"] += 1
                    elif v_max <= 3.00:
                        velocity_bins["Повышенная (1.50–3.00 м/с)"] += 1
                    else:
                        velocity_bins["Риск гидроудара (> 3.00 м/с)"] += 1

            pipe_records.append(
                {
                    "pipe_id": pid,
                    "start_node": sn,
                    "end_node": en,
                    "diameter_mm": round(d_val, 1),
                    "length_m": round(l_val, 2),
                    "roughness_score_pts": r_val,
                    "roughness_status": classify_roughness_score(r_val),
                    "flow_abs_max_m3_h": q_abs_max,
                    "velocity_max_m_s": v_max,
                    "reverse_flow_steps": neg_steps,
                    "direction_flips_per_day": flips,
                }
            )

    roughness_bins = {k: round(v, 3) for k, v in roughness_bins.items()}
    rough_scores_present = [p["roughness_score_pts"] for p in pipe_records if p["roughness_score_pts"] is not None]
    rough_weighted_mean = round(weighted_rough_num / weighted_rough_den, 2) if weighted_rough_den > 0 else None

    v_vals_present = [p["velocity_max_m_s"] for p in pipe_records if p["velocity_max_m_s"] is not None]
    high_vel_pipes = sorted(
        [p for p in pipe_records if p["velocity_max_m_s"] is not None and p["velocity_max_m_s"] > 1.50],
        key=lambda x: x["velocity_max_m_s"],
        reverse=True,
    )
    stagnant_pipes = sorted(
        [p for p in pipe_records if p["velocity_max_m_s"] is not None and p["velocity_max_m_s"] < 0.30],
        key=lambda x: x["length_m"],
        reverse=True,
    )
    worst_rough_pipes = sorted(
        [p for p in pipe_records if p["roughness_score_pts"] is not None and p["roughness_score_pts"] < 78.0],
        key=lambda x: (x["roughness_score_pts"], -x["length_m"]),
    )
    reverse_pipes = sorted(
        [p for p in pipe_records if p["reverse_flow_steps"] > 0],
        key=lambda x: (x["direction_flips_per_day"], x["reverse_flow_steps"]),
        reverse=True,
    )

    # --- Parse Кривая QH.csv & Насос.csv + Поток Насос.csv ---
    qh_curves_raw: dict[str, list[tuple[float, float]]] = {}
    if not df_qh.empty and len(df_qh.columns) >= 3:
        c_id = df_qh.columns[0]
        c_q = find_col(df_qh, "поток", "x", default_idx=1)
        c_h = find_col(df_qh, "давлен", "напор", "y", default_idx=2)
        for uid, qv, hv in zip(
            df_qh[c_id].astype(str).str.strip().tolist(),
            pd.to_numeric(df_qh[c_q], errors="coerce").to_numpy(),
            pd.to_numeric(df_qh[c_h], errors="coerce").to_numpy(),
        ):
            if not (math.isnan(float(qv)) or math.isnan(float(hv))):
                qh_curves_raw.setdefault(uid, []).append((float(qv), float(hv)))

    f_pump_ts = _resolve_file_ci(raw_city_dir, "Поток Насос.csv")
    pump_ts = _stream_wide_timeseries(f_pump_ts, hourly_mode="sum")

    active_pumps_list: list[dict[str, Any]] = []
    standby_pumps_cnt = 0
    total_pumped_m3_day = 0.0
    total_pump_energy_kwh_day = 0.0
    qh_chart_items: list[dict[str, Any]] = []
    plotted_curves: set[str] = set()

    if not df_pumps.empty:
        c_id = df_pumps.columns[0]
        c_curve = find_col(df_pumps, "кривая", default_idx=len(df_pumps.columns) - 1)
        for pid, cid in zip(
            df_pumps[c_id].astype(str).str.strip().tolist(),
            df_pumps[c_curve].astype(str).str.strip().tolist() if c_curve else [""] * len(df_pumps),
        ):
            pst = pump_ts["rows"].get(pid)
            pts = qh_curves_raw.get(cid, [])
            plot_info = build_qh_curve_plot_points(pts)
            q_nom = plot_info.get("q_nominal_m3_h")
            h_nom = plot_info.get("h_nominal_m")

            if pst is None or pst["max"] <= 1e-3:
                standby_pumps_cnt += 1
                # Still include up to 12 curves in chart if no active pump timeseries (e.g. Алматы)
                if not pump_ts["exists"] and pts and cid not in plotted_curves and len(qh_chart_items) < 16:
                    plotted_curves.add(cid)
                    qh_chart_items.append(
                        {
                            "label": f"{pid} → {cid}",
                            "pump_id": pid,
                            "curve_id": cid,
                            "active": False,
                            "operating_q_mean": None,
                            "operating_q_max": None,
                            "operating_h_est": None,
                            **plot_info,
                        }
                    )
                continue

            run_hours = round(pst["pos_steps"] * step_minutes / 60.0, 2)
            # Mean flow while running vs 24h mean
            q_24h_mean = pst["mean"]
            q_running_mean = pst["mean"] * (max(pump_ts["n_steps"], 1) / max(pst["pos_steps"], 1))
            q_max_p = pst["max"]
            daily_vol = q_24h_mean * 24.0
            total_pumped_m3_day += daily_vol

            h_op = evaluate_qh_curve_head(pts, q_running_mean)
            kw_hyd = round((9.81 * q_running_mean * h_op) / 3600.0, 2) if h_op is not None else None
            kwh_day = round(kw_hyd * run_hours, 2) if kw_hyd is not None else 0.0
            total_pump_energy_kwh_day += kwh_day

            bep_ratio = round(q_running_mean / q_nom, 2) if (q_nom and q_nom > 0) else None
            if bep_ratio is None:
                regime_note = "кривая не найдена"
            elif 0.75 <= bep_ratio <= 1.15:
                regime_note = "вблизи номинала BEP (оптимум)"
            elif bep_ratio < 0.75:
                regime_note = "левее номинала (недогрузка / дросселирование)"
            else:
                regime_note = "правее номинала (>1.15 Q_ном, перегрузка / риск кавитации)"

            pump_entry = {
                "pump_id": pid,
                "curve_id": cid,
                "run_hours_per_day": run_hours,
                "starts_per_day": pst["starts"],
                "flow_working_mean_m3_h": round(q_running_mean, 2),
                "flow_max_m3_h": round(q_max_p, 2),
                "nominal_curve_q_m3_h": q_nom,
                "nominal_curve_h_m": h_nom,
                "estimated_head_m": h_op,
                "q_to_nominal_ratio": bep_ratio,
                "operating_zone": regime_note,
                "hydraulic_power_kw": kw_hyd,
                "hydraulic_energy_kwh_day": kwh_day,
            }
            active_pumps_list.append(pump_entry)

            if pts and len(qh_chart_items) < 20:
                plotted_curves.add(cid)
                qh_chart_items.append(
                    {
                        "label": f"{pid} ({cid})",
                        "pump_id": pid,
                        "curve_id": cid,
                        "active": True,
                        "operating_q_mean": round(q_running_mean, 2),
                        "operating_q_max": round(q_max_p, 2),
                        "operating_h_est": h_op,
                        **plot_info,
                    }
                )

    # Supplement chart with remaining Кривая QH curves if fewer than 12 active pump curves plotted
    for cid, pts in qh_curves_raw.items():
        if cid not in plotted_curves and len(qh_chart_items) < 16:
            plotted_curves.add(cid)
            plot_info = build_qh_curve_plot_points(pts)
            qh_chart_items.append(
                {
                    "label": f"Кривая {cid}",
                    "pump_id": None,
                    "curve_id": cid,
                    "active": False,
                    "operating_q_mean": None,
                    "operating_q_max": None,
                    "operating_h_est": None,
                    **plot_info,
                }
            )

    active_pumps_list.sort(key=lambda x: x["hydraulic_energy_kwh_day"] or 0.0, reverse=True)

    # --- Parse Valves (Клапан.csv + Поток Клапан.csv) & Sources (Источник.csv) ---
    f_valve_ts = _resolve_file_ci(raw_city_dir, "Поток Клапан.csv")
    valve_ts = _stream_wide_timeseries(f_valve_ts, hourly_mode="sum")
    valves_details: list[dict[str, Any]] = []
    if not df_valves.empty:
        c_id = df_valves.columns[0]
        c_type = find_col(df_valves, "тип", default_idx=3)
        c_set = find_col(df_valves, "настройк", "уставк", default_idx=4)
        for vid, vtype, vset in zip(
            df_valves[c_id].astype(str).str.strip().tolist(),
            df_valves[c_type].astype(str).str.strip().tolist() if c_type else [""] * len(df_valves),
            pd.to_numeric(df_valves[c_set], errors="coerce").to_numpy() if c_set else np.full(len(df_valves), np.nan),
        ):
            vst = valve_ts["rows"].get(vid)
            valves_details.append(
                {
                    "valve_id": vid,
                    "type": vtype,
                    "setting_m": safe_float(vset, 2),
                    "flow_mean_m3_h": round(vst["mean"], 2) if vst else None,
                    "flow_max_m3_h": round(vst["max"], 2) if vst else None,
                }
            )

    sources_details: list[dict[str, Any]] = []
    if not df_src.empty:
        c_id = df_src.columns[0]
        c_h = find_col(df_src, "напор", default_idx=1)
        for sid, sh in zip(
            df_src[c_id].astype(str).str.strip().tolist(),
            pd.to_numeric(df_src[c_h], errors="coerce").to_numpy() if c_h else np.full(len(df_src), np.nan),
        ):
            sources_details.append({"source_id": sid, "design_head_m": safe_float(sh, 2)})

    # --- Parse Demand Schedules (График потребления.csv) ---
    sched_curves_24h: dict[str, list[float]] = {}
    if not df_sched.empty and len(df_sched.columns) >= 2:
        c_id = df_sched.columns[0]
        c_val = df_sched.columns[1]
        for tid, grp in df_sched.groupby(df_sched[c_id].astype(str).str.strip(), sort=False):
            vals = pd.to_numeric(grp[c_val], errors="coerce").dropna().tolist()
            if vals:
                sched_curves_24h[str(tid)] = [round(float(x), 4) for x in vals[:24]]

    k_max_all = max((max(v) for v in sched_curves_24h.values()), default=None)
    k_min_all = min((min(v) for v in sched_curves_24h.values()), default=None)

    # --- Water Quality quick check (Качество Узел.csv) ---
    f_qual = _resolve_file_ci(raw_city_dir, "Качество Узел.csv")
    qual_mode = "отсутствует"
    if f_qual and f_qual.exists() and f_qual.stat().st_size > 0:
        with f_qual.open("r", encoding="utf-8-sig", errors="replace") as fq:
            _ = fq.readline()
            sample_q = fq.readline()
        if "NAN" in sample_q.upper():
            qual_mode = "не рассчитывалось в сценарии Аквабит (значения NAN)"
        else:
            qual_mode = "заполнено числовыми значениями"

    elapsed_sec = round(time.perf_counter() - t0, 3)

    # --- Assemble compact high-signal `facts` for LLM + Distillation ---
    facts: dict[str, Any] = {
        "city_name": city_name,
        "schema_version": "epanet_ai_v2_fast",
        "processing_time_sec": elapsed_sec,
        "aquabit_tables_present": present_tables,
        "simulation_steps_count": n_steps_sim,
        "zero_cleaning_summary": {
            "total_nodes_in_uzel_csv": total_nodes_raw,
            "pruned_transit_nodes_req_pressure_zero": pruned_transit_nodes,
            "pruned_zero_consumer_rows": pruned_zero_consumers,
            "pruned_zero_demand_timeseries_rows": pruned_zero_demand_ts_rows,
            "pruned_standby_pumps_zero_flow": standby_pumps_cnt,
        },
        "sources_and_valves_summary": {
            "sources_count": len(sources_details),
            "sources_head_min_m": min((s["design_head_m"] for s in sources_details if s["design_head_m"] is not None), default=None),
            "sources_head_max_m": max((s["design_head_m"] for s in sources_details if s["design_head_m"] is not None), default=None),
            "top_sources": sources_details[:6],
            "valves_count": len(valves_details),
            "valves_details": valves_details[:8],
            "water_quality_table_status": qual_mode,
        },
        "nodes_summary": {
            "head_table_used": head_source_name,
            "active_consumer_nodes_count": len(node_records),
            "total_daily_consumption_m3_day": total_daily_demand_m3,
            "peak_hourly_network_demand_m3_h": peak_network_demand_m3_h,
            "free_pressure_min_m": round(min(p_mins_all), 2) if p_mins_all else None,
            "free_pressure_max_m": round(max(p_maxs_all), 2) if p_maxs_all else None,
            "vacuum_nodes_below_0m_count": vacuum_nodes_cnt,
            "deficit_nodes_below_required_count": len(deficit_nodes),
            "excess_pressure_over_60m_nodes_count": len(excess_nodes),
            "pressure_zones_distribution": pressure_bins,
            "floors_distribution": floors_dist,
            "worst_deficit_nodes": deficit_nodes[:top_k],
            "highest_excess_pressure_nodes": excess_nodes[:top_k],
        },
        "pipes_summary": {
            "flow_timeseries_available": bool(pipe_ts["exists"] and pipe_ts["valid_numeric"]),
            "pipes_count": len(pipe_records),
            "total_length_km": round(total_len_m / 1000.0, 3),
            "total_internal_water_volume_m3": round(total_vol_m3, 2),
            "diameter_length_km_breakdown": diameter_km,
            "roughness_scale_rule": "Относительный балл ПК «Аквабит»: чем меньше балл, тем хуже гидравлическое состояние; 80 = норма, 100 = хорошо",
            "roughness_length_weighted_score_pts": rough_weighted_mean,
            "roughness_min_score_pts": min(rough_scores_present) if rough_scores_present else None,
            "roughness_max_score_pts": max(rough_scores_present) if rough_scores_present else None,
            "degraded_roughness_below_78_pipes_count": degraded_pipes_cnt,
            "degraded_roughness_below_78_length_km": round(degraded_pipes_km, 3),
            "roughness_length_km_by_condition": roughness_bins,
            "network_max_velocity_m_s": max(v_vals_present) if v_vals_present else None,
            "pipes_over_1_5_m_s_count": velocity_bins["Повышенная (1.50–3.00 м/с)"] + velocity_bins["Риск гидроудара (> 3.00 м/с)"],
            "pipes_over_3_0_m_s_water_hammer_count": velocity_bins["Риск гидроудара (> 3.00 м/с)"],
            "stagnant_pipes_under_0_3_m_s_count": velocity_bins["Застой (< 0.30 м/с)"],
            "reverse_flow_pipes_count": len(reverse_pipes),
            "velocity_regime_pipes_breakdown": velocity_bins if pipe_ts["exists"] else {},
            "top_high_velocity_pipes": high_vel_pipes[:top_k],
            "top_degraded_roughness_pipes": worst_rough_pipes[:top_k],
            "top_stagnant_pipes": stagnant_pipes[:top_k],
            "top_reverse_flow_pipes": reverse_pipes[:top_k],
        },
        "pumps_summary": {
            "flow_timeseries_available": bool(pump_ts["exists"] and pump_ts["valid_numeric"]),
            "total_pumps_declared": len(df_pumps),
            "active_pumps_count": len(active_pumps_list),
            "standby_off_pumps_count": standby_pumps_cnt,
            "qh_curves_declared_count": len(qh_curves_raw),
            "total_pumped_volume_m3_day": round(total_pumped_m3_day, 2),
            "total_hydraulic_energy_kwh_day": round(total_pump_energy_kwh_day, 2),
            "active_pumps_details": active_pumps_list[:12],
        },
        "schedules_summary": {
            "schedules_count": len(sched_curves_24h),
            "consumer_linked_schedules_count": len(schedules_used_by_consumers),
            "max_hourly_multiplier_k_max": k_max_all,
            "min_hourly_multiplier_k_min": k_min_all,
        },
    }

    # --- Assemble Rich Interactive Chart Payload for Standalone HTML Report ---
    selected_scheds = {
        k: sched_curves_24h[k]
        for k in list(schedules_used_by_consumers)[:5]
        if k in sched_curves_24h
    }
    if not selected_scheds:
        for k in list(sched_curves_24h.keys())[:4]:
            selected_scheds[k] = sched_curves_24h[k]

    chart_payload: dict[str, Any] = {
        "city_name": city_name,
        "hours_labels": [f"{h:02d}:00" for h in range(24)],
        "diurnal_demand_m3_h": dem_ts["hourly_24"],
        "diurnal_pump_flow_m3_h": pump_ts["hourly_24"],
        "diurnal_valve_flow_m3_h": valve_ts["hourly_24"],
        "diurnal_free_pressure_percentiles_m": head_ts["hourly_24"],
        "qh_curves": qh_chart_items,
        "pressure_bins": pressure_bins,
        "floors_distribution": floors_dist,
        "velocity_bins": velocity_bins,
        "roughness_km_bins": roughness_bins,
        "diameter_km_bins": diameter_km,
        "demand_schedules_24h": selected_scheds,
    }

    # --- Optional Minimal Save (< 1 MB per city, no giant R0..R1439 rewrites!) ---
    if cleaned_root_dir is not None:
        out_city = cleaned_root_dir / city_name
        out_city.mkdir(parents=True, exist_ok=True)
        (out_city / f"facts_{city_name}.json").write_text(
            json.dumps(facts, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (out_city / f"chart_payload_{city_name}.json").write_text(
            json.dumps(chart_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        if node_records:
            pd.DataFrame(node_records).to_csv(out_city / "Узел_абоненты_сводка.csv", index=False, encoding="utf-8-sig")
        if pipe_records:
            pd.DataFrame(pipe_records).to_csv(out_city / "Труба_аналитика_сводка.csv", index=False, encoding="utf-8-sig")
        if active_pumps_list:
            pd.DataFrame(active_pumps_list).to_csv(out_city / "Насос_активные_сводка.csv", index=False, encoding="utf-8-sig")
        if qh_curves_raw:
            qh_rows = [{"ID": cid, "Поток_м3_ч": q, "Напор_м": h} for cid, pts in qh_curves_raw.items() for q, h in pts]
            pd.DataFrame(qh_rows).to_csv(out_city / "Кривая_QH_чистая.csv", index=False, encoding="utf-8-sig")

    return facts, chart_payload



# ==============================================================================
# EPANET-AI v3: Топология скважин, парсер INP и расчет по Хейзену-Вильямсу
# ==============================================================================

_decode_text = _decode_file_text
evaluate_qh_head = evaluate_qh_curve_head

def parse_inp_metadata_and_topology(inp_path: Path | None, node_ids: set[str], pipe_ids: set[str], pump_ids: set[str]) -> dict[str, Any]:
    """Извлекает из .inp координаты, человекочитаемые названия (после ';') и топологию связей."""
    meta = {
        "names": {},        # id -> читаемое название из комментария .inp
        "links": [],        # [id, [[x,y], ...]]
        "nodes": {},        # id -> [x, y]
        "link_ends": {},    # id -> (start_node, end_node, link_type)
        "closed_links": set()
    }
    if not inp_path or not inp_path.exists():
        return meta

    text = _decode_file_text(inp_path)
    section = ""
    verts = {}
    raw_links = []

    def resolve_id(raw_id: str, prefix: str, known: set[str]) -> str:
        if raw_id in known:
            return raw_id
        if (prefix + raw_id) in known:
            return prefix + raw_id
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
        if len(parts) < 2:
            continue

        raw_id = parts[0]
        if section in ("JUNCTIONS", "RESERVOIRS", "TANKS"):
            oid = resolve_id(raw_id, "c", node_ids)
            if comment and not comment.lower().startswith("id"):
                meta["names"][oid] = comment
        elif section == "PIPES" and len(parts) >= 3:
            oid = resolve_id(raw_id, "e", pipe_ids)
            n1 = resolve_id(parts[1], "c", node_ids)
            n2 = resolve_id(parts[2], "c", node_ids)
            meta["link_ends"][oid] = (n1, n2, "PIPE")
            if comment:
                meta["names"][oid] = comment
            if len(parts) >= 8 and parts[7].upper() == "CLOSED":
                meta["closed_links"].add(oid)
            raw_links.append(("e", oid, n1, n2, raw_id))
        elif section == "PUMPS" and len(parts) >= 3:
            oid = resolve_id(raw_id, "p", pump_ids)
            n1 = resolve_id(parts[1], "c", node_ids)
            n2 = resolve_id(parts[2], "c", node_ids)
            meta["link_ends"][oid] = (n1, n2, "PUMP")
            if comment:
                meta["names"][oid] = comment
            raw_links.append(("p", oid, n1, n2, raw_id))
        elif section == "VALVES" and len(parts) >= 3:
            oid = resolve_id(raw_id, "v", set())
            n1 = resolve_id(parts[1], "c", node_ids)
            n2 = resolve_id(parts[2], "c", node_ids)
            meta["link_ends"][oid] = (n1, n2, "VALVE")
            if comment:
                meta["names"][oid] = comment
            raw_links.append(("v", oid, n1, n2, raw_id))
        elif section == "COORDINATES" and len(parts) >= 3:
            try:
                oid = resolve_id(raw_id, "c", node_ids)
                meta["nodes"][oid] = [float(parts[1]), float(parts[2])]
            except ValueError:
                pass
        elif section == "VERTICES" and len(parts) >= 3:
            try:
                verts.setdefault(raw_id, []).append([float(parts[1]), float(parts[2])])
            except ValueError:
                pass

    for pref, oid, n1, n2, raw_id in raw_links:
        p1 = meta["nodes"].get(n1)
        p2 = meta["nodes"].get(n2)
        if p1 and p2:
            meta["links"].append([oid, [p1] + verts.get(raw_id, []) + [p2]])

    return meta


def process_aquabit_city_v3(raw_city_dir: Path | str, inp_path: Path | str | None = None, top_k: int = 10) -> tuple[dict[str, Any], dict[str, Any]]:
    """Процессор ПК «Аквабит» v3: физика Хейзена-Вильямса, м/км, топология скважин, названия из .inp и все насосы."""
    t0 = time.perf_counter()
    raw_city_dir = Path(raw_city_dir)
    inp_path = Path(inp_path) if inp_path else None
    city_name = unicodedata.normalize("NFC", raw_city_dir.name)

    df_nodes = read_aquabit_csv(_resolve_file_ci(raw_city_dir, "Узел.csv"))
    df_pipes = read_aquabit_csv(_resolve_file_ci(raw_city_dir, "Труба.csv"))
    df_pumps = read_aquabit_csv(_resolve_file_ci(raw_city_dir, "Насос.csv"))
    df_valves = read_aquabit_csv(_resolve_file_ci(raw_city_dir, "Клапан.csv"))
    df_qh = read_aquabit_csv(_resolve_file_ci(raw_city_dir, "Кривая QH.csv"))
    df_cons = read_aquabit_csv(_resolve_file_ci(raw_city_dir, "Потребители Узел.csv"))
    df_src = read_aquabit_csv(_resolve_file_ci(raw_city_dir, "Источник.csv"))

    node_ids_set = set(df_nodes[df_nodes.columns[0]]) if not df_nodes.empty else set()
    pipe_ids_set = set(df_pipes[df_pipes.columns[0]]) if not df_pipes.empty else set()
    pump_ids_set = set(df_pumps[df_pumps.columns[0]]) if not df_pumps.empty else set()
    source_ids_set = set(df_src[df_src.columns[0]]) if not df_src.empty else set()

    if inp_path is None:
        inps = sorted([p for p in raw_city_dir.iterdir() if p.is_file() and p.suffix.lower() == ".inp"])
        if len(inps) == 1:
            inp_path = inps[0]

    inp_meta = parse_inp_metadata_and_topology(inp_path, node_ids_set, pipe_ids_set, pump_ids_set)
    obj_names = inp_meta["names"]

    def label_with_name(oid: str) -> str:
        cid = clean_object_id(oid)
        raw_nm = obj_names.get(oid)
        nm = clean_object_name(raw_nm)
        return f"{cid} ({nm})" if nm else cid

    # 1. Узлы и требуемое давление
    elev_map, req_p_map, floors_map = {}, {}, {}
    if not df_nodes.empty:
        c_id = df_nodes.columns[0]
        c_z = find_col(df_nodes, "высот", "отмет", default_idx=1)
        c_req = find_col(df_nodes, "требуем", "давлен", default_idx=2 if len(df_nodes.columns) > 2 else None)
        for nid, zv, rv in zip(df_nodes[c_id], pd.to_numeric(df_nodes[c_z], errors="coerce"),
                               pd.to_numeric(df_nodes[c_req], errors="coerce").fillna(0.0)):
            if not math.isnan(float(zv)):
                elev_map[nid] = float(zv)
            if rv > 0:
                req_p_map[nid] = float(rv)
                floors_map[nid] = max(1, int(round(1.0 + max(0.0, float(rv) - 10.0) / 4.0)))

    # 2. Потребители
    base_demand = {}
    if not df_cons.empty:
        c_id = df_cons.columns[0]
        c_q = find_col(df_cons, "потреблен", default_idx=1)
        for nid, qv in zip(df_cons[c_id], pd.to_numeric(df_cons[c_q], errors="coerce").fillna(0.0)):
            if abs(float(qv)) > 1e-9:
                base_demand[nid] = base_demand.get(nid, 0.0) + float(qv)

    active_nodes = set(req_p_map.keys()) | set(base_demand.keys())

    # 3. Временные ряды напоров (избыточное давление P = H - Z, где 0 = уровень земли)
    f_free = _resolve_file_ci(raw_city_dir, "Свободный напор Узел.csv")
    f_head = _resolve_file_ci(raw_city_dir, "Напор Узел.csv")
    head_ts = _stream_wide_timeseries(f_free or f_head, row_offsets=None if f_free else elev_map,
                                      hourly_mode="percentiles", filter_ids=active_nodes or None)
    dem_ts = _stream_wide_timeseries(_resolve_file_ci(raw_city_dir, "Потребление Узел.csv"), hourly_mode="sum_positive")
    pump_ts = _stream_wide_timeseries(_resolve_file_ci(raw_city_dir, "Поток Насос.csv"), hourly_mode="sum")
    pipe_ts = _stream_wide_timeseries(_resolve_file_ci(raw_city_dir, "Поток Труба.csv"), hourly_mode="none")

    n_steps = head_ts["n_steps"] or dem_ts["n_steps"] or 1440
    step_min = 1440.0 / max(n_steps, 1)

    # Определяем выключенные скважины (насос с start_node in source_ids и подачей 0)
    inactive_well_nodes = set()
    pump_start_nodes = {}
    if not df_pumps.empty:
        c_id = df_pumps.columns[0]
        c_s = find_col(df_pumps, "начал", default_idx=1)
        for pid, sn in zip(df_pumps[c_id], df_pumps[c_s] if c_s else [""] * len(df_pumps)):
            sn_clean = str(sn).strip()
            if not sn_clean and pid in inp_meta["link_ends"]:
                sn_clean = inp_meta["link_ends"][pid][0]
            pump_start_nodes[pid] = sn_clean
            pst = pump_ts["rows"].get(pid)
            if (pst is None or pst["max"] <= 1e-3) and sn_clean in source_ids_set:
                inactive_well_nodes.add(sn_clean)

    # 4. Анализ узлов (фокус: недостаток требуемого давления ΔP = P_своб - P_треб и отделение ошибок < -3 м)
    node_records = []
    pressure_bins = {
        "Ошибка / отсечение (< -3.0 м)": 0,
        "Отрицат. давление (-3.0 .. 0 м)": 0,
        "Недостаток давления (< P_треб)": 0,
        "Норма (P_треб .. 60 м)": 0,
        "Избыток (> 60 м)": 0,
    }

    for nid in sorted(active_nodes or elev_map.keys()):
        hst = head_ts["rows"].get(nid)
        dst = dem_ts["rows"].get(nid)
        req_m = req_p_map.get(nid, 0.0)
        q_mean = dst["pos_mean"] if (dst and dst["pos_mean"] > 0) else base_demand.get(nid, 0.0)
        p_min = round(hst["min"], 2) if hst else None
        p_max = round(hst["max"], 2) if hst else None
        margin_m = round(p_min - req_m, 2) if (p_min is not None and req_m > 0) else None
        is_shutoff_well = (nid in inactive_well_nodes) or (p_min is not None and p_min < 0 and q_mean <= 1e-6)

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
            "clean_id": clean_object_id(nid),
            "name": clean_object_name(obj_names.get(nid, "")),
            "display_id": label_with_name(nid),
            "elevation_m": round(elev_map[nid], 2) if nid in elev_map else None,
            "floors": floors_map.get(nid, 0),
            "required_pressure_m": round(req_m, 2) if req_m > 0 else None,
            "free_pressure_min_m": p_min,
            "free_pressure_max_m": p_max,
            "pressure_surplus_deficit_m": margin_m,
            "demand_mean_m3_h": round(q_mean, 3),
            "is_shutoff_well_or_zero_demand": is_shutoff_well
        })

    # Реальный дефицит требуемого давления (исключая выключенные скважины и выбросы < -3м, которые идут в ошибки данных)
    real_deficit_nodes = sorted(
        [r for r in node_records if not r["is_shutoff_well_or_zero_demand"]
         and r["free_pressure_min_m"] is not None and r["free_pressure_min_m"] >= -3.0
         and r["pressure_surplus_deficit_m"] is not None and r["pressure_surplus_deficit_m"] < 0],
        key=lambda x: x["pressure_surplus_deficit_m"]
    )
    data_error_nodes = sorted(
        [r for r in node_records if not r["is_shutoff_well_or_zero_demand"]
         and r["free_pressure_min_m"] is not None and r["free_pressure_min_m"] < -3.0],
        key=lambda x: x["free_pressure_min_m"]
    )
    shutoff_well_nodes = [r for r in node_records if r["is_shutoff_well_or_zero_demand"]
                          and r["free_pressure_min_m"] is not None and r["free_pressure_min_m"] < 0]
    excess_nodes = sorted(
        [r for r in node_records if r["free_pressure_max_m"] is not None and r["free_pressure_max_m"] > 60.0],
        key=lambda x: x["free_pressure_max_m"], reverse=True
    )

    # 5. Трубы: коэффициент Хейзена-Вильямса C и удельные потери напора i (м/км), БЕЗ средней шероховатости!
    pipe_records = []
    velocity_bins = {
        "Низкие скорости (< 0.10 м/с)": 0,
        "Умеренные (0.10–0.50 м/с)": 0,
        "Оптимум (0.50–1.50 м/с)": 0,
        "Повышенные (1.50–5.00 м/с)": 0,
        "Большая скорость (>= 5.00 м/с)": 0,
    }
    total_len_km = 0.0
    if not df_pipes.empty:
        c_id = df_pipes.columns[0]
        c_s = find_col(df_pipes, "начал", default_idx=1)
        c_e = find_col(df_pipes, "конец", "конечн", default_idx=2)
        c_d = find_col(df_pipes, "диаметр", default_idx=3)
        c_l = find_col(df_pipes, "длин", default_idx=4)
        c_c = find_col(df_pipes, "шероховат", default_idx=5)

        for pid, sn, en, d_mm, l_m, c_hw in zip(
            df_pipes[c_id],
            df_pipes[c_s] if c_s else [""] * len(df_pipes),
            df_pipes[c_e] if c_e else [""] * len(df_pipes),
            pd.to_numeric(df_pipes[c_d], errors="coerce").fillna(0.0),
            pd.to_numeric(df_pipes[c_l], errors="coerce").fillna(0.0),
            pd.to_numeric(df_pipes[c_c], errors="coerce")
        ):
            d_val = float(d_mm); l_val = max(0.0, float(l_m))
            total_len_km += l_val / 1000.0
            c_val = None if math.isnan(float(c_hw)) else round(float(c_hw), 1)
            sn_s = str(sn).strip() or inp_meta["link_ends"].get(pid, ("", ""))[0]
            en_s = str(en).strip() or inp_meta["link_ends"].get(pid, ("", ""))[1]

            pst = pipe_ts["rows"].get(pid)
            v_max, q_max, headloss_m_km = None, None, None
            if pst is not None and d_val > 0:
                q_max = round(pst["abs_max"], 2)
                area = math.pi * ((d_val / 1000.0) ** 2) / 4.0
                v_max = round((q_max / 3600.0) / area, 3)

                # Удельная потеря напора i (м/км) по формуле Хейзена-Вильямса:
                # i = 1000 * 10.67 * (Q_m3s^1.852) / (C^1.852 * D_m^4.87)
                if c_val and c_val > 0 and q_max > 0:
                    q_m3s = q_max / 3600.0
                    d_m = d_val / 1000.0
                    i_grad = 1000.0 * 10.67 * (q_m3s ** 1.852) / ((c_val ** 1.852) * (d_m ** 4.87))
                    headloss_m_km = round(i_grad, 2)

                if v_max < 0.10:
                    velocity_bins["Низкие скорости (< 0.10 м/с)"] += 1
                elif v_max < 0.50:
                    velocity_bins["Умеренные (0.10–0.50 м/с)"] += 1
                elif v_max <= 1.50:
                    velocity_bins["Оптимум (0.50–1.50 м/с)"] += 1
                elif v_max < 5.00:
                    velocity_bins["Повышенные (1.50–5.00 м/с)"] += 1
                else:
                    velocity_bins["Большая скорость (>= 5.00 м/с)"] += 1

            cid = clean_object_id(pid)
            clean_nm = clean_object_name(obj_names.get(pid, ""))
            pipe_records.append({
                "pipe_id": pid,
                "clean_id": cid,
                "name": clean_nm,
                "display_id": label_with_name(pid),
                "start_node": sn_s,
                "end_node": en_s,
                "diameter_mm": round(d_val, 1),
                "length_m": round(l_val, 1),
                "hazen_williams_c": c_val,
                "flow_max_m3_h": q_max,
                "velocity_max_m_s": v_max,
                "headloss_gradient_m_km": headloss_m_km,
                "reverse_steps": pst["neg_steps"] if pst else 0
            })

    high_loss_pipes = sorted(
        [p for p in pipe_records if (p["velocity_max_m_s"] or 0) >= 5.0 or (p["headloss_gradient_m_km"] or 0) > 20.0],
        key=lambda x: (x["headloss_gradient_m_km"] or 0), reverse=True
    )
    low_c_pipes = sorted(
        [p for p in pipe_records if p["hazen_williams_c"] is not None and p["hazen_williams_c"] < 80.0 and (p["flow_max_m3_h"] or 0) > 1.0],
        key=lambda x: (x["hazen_williams_c"], -(x["headloss_gradient_m_km"] or 0))
    )

    # 6. Насосы: ВСЕ насосы без обрезки + разделение на скважинные и сетевые + теоретическая vs пиковая подача
    qh_raw = {}
    if not df_qh.empty and len(df_qh.columns) >= 3:
        for uid, qv, hv in zip(df_qh[df_qh.columns[0]], pd.to_numeric(df_qh.iloc[:, 1], errors="coerce"),
                               pd.to_numeric(df_qh.iloc[:, 2], errors="coerce")):
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
        for pid, cid in zip(df_pumps[c_id], df_pumps[c_curve] if c_curve else [""] * len(df_pumps)):
            cid_s = str(cid).strip()
            pts = qh_raw.get(cid_s, [])
            plot_info = build_qh_curve_plot_points(pts)
            q_nom = plot_info.get("q_nominal_m3_h")
            h_nom = plot_info.get("h_nominal_m")
            if q_nom:
                total_theoretical_q_m3_h += q_nom

            sn_node = pump_start_nodes.get(pid, "")
            is_borehole = sn_node in source_ids_set
            pump_kind = "Скважинный (от источника)" if is_borehole else "Сетевой / повысительный"

            pst = pump_ts["rows"].get(pid)
            is_active = bool(pst and pst["max"] > 1e-3)
            q_work, q_max_p, h_est, ratio, run_h = None, None, None, None, 0.0
            zone_label = "Резерв / отключен"

            if is_active:
                run_h = round(pst["pos_steps"] * step_min / 60.0, 2)
                q_work = round(pst["mean"] * (max(pump_ts["n_steps"], 1) / max(pst["pos_steps"], 1)), 2)
                q_max_p = round(pst["max"], 2)
                total_pumped_m3_day += pst["mean"] * 24.0
                h_est = evaluate_qh_head(pts, q_work)
                if h_est is not None and q_work > 0:
                    kw_hyd = (9.81 * q_work * h_est) / 3600.0
                    total_pump_energy_kwh_day += kw_hyd * run_h
                ratio = round(q_work / q_nom, 2) if (q_nom and q_nom > 0) else None
                if ratio is None:
                    zone_label = "Кривая Q-H не задана"
                elif ratio < 0.75:
                    zone_label = "Недогрузка (левее Q_ном)"
                elif ratio <= 1.15:
                    zone_label = "Рабочая зона (около Q_ном)"
                else:
                    zone_label = "Перегрузка (правее Q_ном)"

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
                "start_node": sn_node,
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
                "regime": zone_label
            }
            all_pumps_list.append(p_entry)

            if pts:
                qh_chart_items.append({
                    "label": f"{disp} → {cid_s}" + (" [РАБ]" if is_active else " [ВЫКЛ]"),
                    "pump_id": pid,
                    "clean_id": cid,
                    "name": clean_nm,
                    "curve_id": cid_s,
                    "active": is_active,
                    "is_borehole": is_borehole,
                    "pump_kind": pump_kind,
                    "operating_q_mean": q_work,
                    "operating_q_max": q_max_p,
                    "operating_h_est": h_est,
                    **plot_info
                })

    active_pumps = [p for p in all_pumps_list if p["active"]]
    peak_pump_flow_m3_h = round(max(pump_ts["hourly_24"]), 2) if pump_ts["hourly_24"] else 0.0
    total_daily_cons = round(sum(r["demand_mean_m3_h"] * 24.0 for r in node_records), 2)
    peak_network_cons = round(max(dem_ts["hourly_24"]), 2) if dem_ts["hourly_24"] else round(sum(base_demand.values()), 2)

    # 7. Детектор индивидуальных аномалий (чтобы отчет по каждому городу был уникальным)
    anomalies = []
    if head_ts["hourly_24"] and head_ts["hourly_24"]["p50"]:
        p50_arr = [v for v in head_ts["hourly_24"]["p50"] if v is not None]
        if len(p50_arr) >= 24:
            diffs = [(i, round(p50_arr[i] - p50_arr[i-1], 2)) for i in range(1, len(p50_arr))]
            max_jump = max(diffs, key=lambda x: abs(x[1]))
            if abs(max_jump[1]) >= 4.0:
                anomalies.append(f"Резкий скачок медианного избыточного давления по сети на {max_jump[1]:+.1f} м в {max_jump[0]:02d}:00 (вероятно, переключение насосной группы по расписанию).")
    if shutoff_well_nodes:
        anomalies.append(f"Обнаружено {len(shutoff_well_nodes)} узлов с отрицательным давлением на остановленных скважинах/линиях без отбора (не являются дефицитом абонентов).")
    if high_loss_pipes:
        top_p = high_loss_pipes[0]
        anomalies.append(f"Лимитирующий участок {top_p['display_id']} (D={top_p['diameter_mm']} мм, C={top_p['hazen_williams_c']}): скорость {top_p['velocity_max_m_s']} м/с создаёт удельную потерю напора {top_p['headloss_gradient_m_km']} м/км.")

    # Глобальные агрегаты по сети
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
    rev_cnt = sum(1 for p in pipe_records if p.get("reverse_steps", 0) > 0)
    p_min_network = round(min(p_mins_all), 2) if p_mins_all else None
    p_max_network = round(max(p_maxs_all), 2) if p_maxs_all else None
    valves_count = len(df_valves) if not df_valves.empty else 0

    facts = {
        "city_name": city_name,
        "has_inp_names": bool(obj_names),
        "simulation_steps": n_steps,
        "water_balance": {
            "active_consumer_nodes": len(node_records),
            "total_daily_consumption_m3_day": total_daily_cons,
            "peak_network_demand_m3_h": peak_network_cons,
            "pumps_theoretical_capacity_m3_h": round(total_theoretical_q_m3_h, 2),
            "pumps_theoretical_capacity_m3_day": round(total_theoretical_q_m3_h * 24.0, 2),
            "pumps_peak_actual_flow_m3_h": peak_pump_flow_m3_h,
            "pumps_total_actual_m3_day": round(total_pumped_m3_day, 2),
            "capacity_vs_demand_gap_m3_day": round(total_theoretical_q_m3_h * 24.0 - total_daily_cons, 2)
        },
        "pressure_analysis": {
            "pressure_bins": pressure_bins,
            "free_pressure_min_m": p_min_network,
            "free_pressure_max_m": p_max_network,
            "real_required_pressure_deficit_count": len(real_deficit_nodes),
            "shutoff_wells_negative_count": len(shutoff_well_nodes),
            "data_error_below_minus_3m_count": len(data_error_nodes),
            "excess_over_60m_count": len(excess_nodes),
            "worst_deficit_nodes": real_deficit_nodes[:top_k],
            "worst_data_error_nodes": data_error_nodes[:15],
            "top_excess_nodes": excess_nodes[:10]
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
            "reverse_flow_count": rev_cnt,
            "velocity_bins": velocity_bins,
            "high_velocity_over_5ms_count": velocity_bins["Большая скорость (>= 5.00 м/с)"],
            "low_velocity_under_01ms_count": velocity_bins["Низкие скорости (< 0.10 м/с)"],
            "top_high_loss_pipes": high_loss_pipes[:top_k],
            "top_low_hazen_williams_c_pipes": low_c_pipes[:top_k]
        },
        "pumps_analysis": {
            "total_pumps": len(all_pumps_list),
            "active_pumps_count": len(active_pumps),
            "total_energy_kwh_day": round(total_pump_energy_kwh_day, 1),
            "borehole_active_count": sum(1 for p in active_pumps if p["is_borehole"]),
            "booster_active_count": sum(1 for p in active_pumps if not p["is_borehole"]),
            "underloaded_pumps": [p for p in active_pumps if p["q_ratio"] is not None and p["q_ratio"] < 0.75],
            "overloaded_pumps": [p for p in active_pumps if p["q_ratio"] is not None and p["q_ratio"] > 1.15],
            "all_pumps_table": all_pumps_list
        },
        # Кросс-совместимость со сводками v2_fast
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
            "active_pumps_details": active_pumps[:12],
        },
        "sources_and_valves_summary": {
            "valves_count": valves_count,
        },
        "unique_model_anomalies": anomalies,
        "processing_time_sec": round(time.perf_counter() - t0, 2)
    }

    # Компактные словари атрибутов для каждого узла и трубы (поддержка клика по любому объекту карты)
    node_props = {
        r["node_id"]: {
            "z": r.get("elevation_m"),
            "p_min": r.get("free_pressure_min_m"),
            "p_max": r.get("free_pressure_max_m"),
            "req": r.get("required_pressure_m"),
            "floors": r.get("floors"),
            "margin": r.get("pressure_surplus_deficit_m"),
            "demand": r.get("demand_mean_m3_h"),
            "name": r.get("name")
        }
        for r in node_records
    }
    pipe_props = {
        p["pipe_id"]: {
            "d": p.get("diameter_mm"),
            "l": p.get("length_m"),
            "c": p.get("hazen_williams_c"),
            "v": p.get("velocity_max_m_s"),
            "i": p.get("headloss_gradient_m_km"),
            "q": p.get("flow_max_m3_h"),
            "name": p.get("name")
        }
        for p in pipe_records
    }

    p50_vals = [v for v in (head_ts["hourly_24"].get("p50") or []) if v is not None] if head_ts.get("hourly_24") else []
    p50_is_dynamic = bool(len(p50_vals) >= 4 and (max(p50_vals) - min(p50_vals)) >= 0.2)
    dem_24 = [v for v in (dem_ts.get("hourly_24") or []) if v is not None]
    pmp_24 = [v for v in (pump_ts.get("hourly_24") or []) if v is not None]
    dem_is_dynamic = bool(len(dem_24) >= 4 and (max(dem_24) - min(dem_24)) >= 1.0)
    pmp_is_dynamic = bool(len(pmp_24) >= 4 and (max(pmp_24) - min(pmp_24)) >= 1.0)

    chart_payload = {
        "city_name": city_name,
        "hours_labels": [f"{h:02d}:00" for h in range(24)],
        "diurnal_demand_m3_h": dem_ts["hourly_24"],
        "diurnal_pump_flow_m3_h": pump_ts["hourly_24"],
        "diurnal_free_pressure_percentiles_m": head_ts["hourly_24"],
        "qh_curves": qh_chart_items,
        "pressure_bins": pressure_bins,
        "velocity_bins": velocity_bins,
        "node_props": node_props,
        "pipe_props": pipe_props,
        "geo": {"nodes": inp_meta["nodes"], "links": inp_meta["links"],
                "bad_nodes": [r["node_id"] for r in real_deficit_nodes + data_error_nodes],
                "bad_pipes": [p["pipe_id"] for p in high_loss_pipes + low_c_pipes]},
        "informative_charts": {
            "pump_qh": len(qh_chart_items) > 0 and any(c.get("curve_points") for c in qh_chart_items),
            "pressure_corridor": p50_is_dynamic,
            "diurnal_balance": dem_is_dynamic or pmp_is_dynamic,
            "low_velocity_stagnation": len(pipe_records) > 0 and bool(pipe_ts.get("rows")),
            "pipe_bottlenecks": bool(velocity_bins["Большая скорость (>= 5.00 м/с)"] > 0 or (high_loss_pipes and (high_loss_pipes[0].get("headloss_gradient_m_km") or 0) >= 10.0)),
            "pressure_deficit": len(real_deficit_nodes) > 0
        }
    }
    return facts, chart_payload

