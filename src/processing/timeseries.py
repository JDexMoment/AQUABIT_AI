"""Потоковое чтение широких временных рядов."""
from __future__ import annotations
from pathlib import Path
from typing import Any
import numpy as np
import pandas as pd
from processing.identifiers import clean_object_id, format_step_to_hhmm

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
