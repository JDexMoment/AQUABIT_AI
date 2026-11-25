"""Потоковое чтение широких временных рядов (из JSON-массивов Report.json или CSV)."""
from __future__ import annotations
import csv
import math
from pathlib import Path
from typing import Any
from processing.identifiers import clean_object_id, format_step_to_hhmm

try:
    import numpy as np
    import pandas as pd
except ImportError:
    np = None  # type: ignore[assignment]
    pd = None  # type: ignore[assignment]


def _split_buckets(n_steps: int, n_buckets: int) -> list[list[int]]:
    if n_steps <= 0 or n_buckets <= 0:
        return []
    k, m = divmod(n_steps, n_buckets)
    return [
        list(range(i * k + min(i, m), (i + 1) * k + min(i + 1, m)))
        for i in range(n_buckets)
    ]


def _percentile_sorted(sorted_vals: list[float], pct: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    pos = (pct / 100.0) * (len(sorted_vals) - 1)
    i0 = int(math.floor(pos))
    i1 = min(len(sorted_vals) - 1, i0 + 1)
    frac = pos - i0
    return float(sorted_vals[i0] * (1.0 - frac) + sorted_vals[i1] * frac)


def build_timeseries_from_json_series(
    series_by_id: dict[str, list[float | None]],
    *,
    row_offsets: dict[str, float] | None = None,
    hourly_mode: str = "sum",
    filter_ids: set[str] | None = None,
    keep_row_hourly: bool = False,
    timeline_buckets: int = 48,
) -> dict[str, Any]:
    """Строит унифицированную структуру временных рядов напрямую из массивов Report.json
    (head, flow, demand) без чтения CSV.
    """
    if not series_by_id:
        return {
            "exists": False,
            "n_steps": 0,
            "step_names": [],
            "rows": {},
            "hourly_24": None,
            "row_hourly": {},
            "timeline_labels": [],
        }

    n_steps = max((len(arr) for arr in series_by_id.values() if arr), default=0)
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

    n_buckets_24 = min(24, n_steps)
    bucket_indices_24 = _split_buckets(n_steps, n_buckets_24)
    n_tl = min(max(24, timeline_buckets), n_steps)
    tl_indices = _split_buckets(n_steps, n_tl)
    tl_mid_cols = [int(idxs[len(idxs) // 2]) for idxs in tl_indices if idxs]
    timeline_labels = [format_step_to_hhmm(c_idx, n_steps) for c_idx in tl_mid_cols]

    row_stats: dict[str, dict[str, Any]] = {}
    row_hourly: dict[str, list[float | None]] = {}
    hourly_sum = [0.0] * n_buckets_24
    hourly_samples_p: list[list[float]] = [[] for _ in range(n_buckets_24)]
    valid_numeric_any = False

    for obj_id, raw_arr in series_by_id.items():
        if not raw_arr:
            continue
        cid = clean_object_id(obj_id)
        offset = 0.0
        has_offset = False
        if row_offsets is not None:
            off_val = row_offsets.get(obj_id, row_offsets.get(cid))
            if off_val is not None and math.isfinite(float(off_val)):
                offset = float(off_val)
                has_offset = True
            else:
                continue

        # Приводим ряд к длине n_steps
        vals: list[float | None] = []
        fin_vals: list[float] = []
        pos_sum = 0.0
        neg_steps = 0
        pos_steps = 0
        abs_max = 0.0
        prev_sign = 0
        flips = 0
        prev_active = False
        starts = 0

        for idx_s in range(n_steps):
            raw_v = raw_arr[idx_s] if idx_s < len(raw_arr) else raw_arr[-1]
            if raw_v is None or isinstance(raw_v, bool):
                vals.append(None)
                continue
            try:
                fv = float(raw_v)
            except (ValueError, TypeError):
                vals.append(None)
                continue
            if not math.isfinite(fv):
                vals.append(None)
                continue
            if has_offset:
                fv = fv - offset
            vals.append(fv)
            fin_vals.append(fv)
            if fv > 0.0:
                pos_sum += fv
            if fv < -1e-6:
                neg_steps += 1
            if fv > 1e-4:
                pos_steps += 1
            av = abs(fv)
            if av > abs_max:
                abs_max = av

            sgn = 1 if fv > 1e-4 else (-1 if fv < -1e-4 else 0)
            if sgn != 0 and prev_sign != 0 and sgn * prev_sign < 0:
                flips += 1
            if sgn != 0:
                prev_sign = sgn

            act = fv > 1e-3
            if act and not prev_active:
                starts += 1
            prev_active = act

        if not fin_vals:
            continue
        valid_numeric_any = True
        cnt_i = len(fin_vals)
        stat_entry = {
            "min": float(min(fin_vals)),
            "max": float(max(fin_vals)),
            "mean": float(sum(fin_vals) / max(cnt_i, 1)),
            "pos_mean": float(pos_sum / max(cnt_i, 1)),
            "abs_max": float(abs_max),
            "neg_steps": int(neg_steps),
            "pos_steps": int(pos_steps),
            "flips": int(flips),
            "starts": int(starts),
        }
        row_stats[obj_id] = stat_entry
        if cid:
            row_stats.setdefault(cid, stat_entry)

        if keep_row_hourly:
            sampled = [
                None if (c_idx >= len(vals) or vals[c_idx] is None) else round(float(vals[c_idx]), 2)
                for c_idx in tl_mid_cols
            ]
            row_hourly[obj_id] = sampled
            if cid:
                row_hourly.setdefault(cid, sampled)

        is_kept = True
        if filter_ids is not None:
            is_kept = (obj_id in filter_ids) or (cid in filter_ids)
        if is_kept:
            for b_idx, cols_idx in enumerate(bucket_indices_24):
                if not cols_idx:
                    continue
                if hourly_mode == "sum":
                    b_vals = [vals[c] if (c < len(vals) and vals[c] is not None) else 0.0 for c in cols_idx]
                    hourly_sum[b_idx] += sum(b_vals) / len(cols_idx)
                elif hourly_mode == "sum_positive":
                    b_vals = [vals[c] if (c < len(vals) and vals[c] is not None and vals[c] > 0.0) else 0.0 for c in cols_idx]
                    hourly_sum[b_idx] += sum(b_vals) / len(cols_idx)
                elif hourly_mode == "percentiles":
                    mid_c = cols_idx[len(cols_idx) // 2]
                    mv = vals[mid_c] if mid_c < len(vals) else None
                    if mv is not None:
                        hourly_samples_p[b_idx].append(float(mv))

    hourly_out: Any = None
    if valid_numeric_any:
        if hourly_mode in ("sum", "sum_positive"):
            hourly_out = [round(float(x), 2) for x in hourly_sum]
        elif hourly_mode == "percentiles":
            p10, p50, p90 = [], [], []
            for b_idx in range(n_buckets_24):
                if hourly_samples_p[b_idx]:
                    s_arr = sorted(hourly_samples_p[b_idx])
                    p10.append(round(_percentile_sorted(s_arr, 10.0), 2))
                    p50.append(round(_percentile_sorted(s_arr, 50.0), 2))
                    p90.append(round(_percentile_sorted(s_arr, 90.0), 2))
                else:
                    p10.append(None)
                    p50.append(None)
                    p90.append(None)
            hourly_out = {"p10": p10, "p50": p50, "p90": p90}

    return {
        "exists": True,
        "valid_numeric": valid_numeric_any,
        "n_steps": n_steps,
        "step_names": [f"step_{i}" for i in range(n_steps)],
        "bucket_step_labels": [f"{h:02d}:00" for h in range(n_buckets_24)],
        "timeline_labels": timeline_labels,
        "rows": row_stats,
        "hourly_24": hourly_out,
        "row_hourly": row_hourly,
    }


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

    # Если numpy/pandas не установлены, используем потоковый stdlib csv-парсер
    if np is None or pd is None:
        series_map: dict[str, list[float | None]] = {}
        with fpath.open("r", encoding="utf-8-sig", errors="replace") as fh:
            reader = csv.reader(fh)
            hdr = next(reader, None)
            if not hdr or len(hdr) <= 1:
                return {
                    "exists": False,
                    "n_steps": 0,
                    "step_names": [],
                    "rows": {},
                    "hourly_24": None,
                    "row_hourly": {},
                    "timeline_labels": [],
                }
            for row in reader:
                if not row:
                    continue
                oid = row[0].strip()
                vals: list[float | None] = []
                for cell in row[1:]:
                    cs = cell.strip()
                    if not cs or cs.lower() in ("nan", "null", "none"):
                        vals.append(None)
                    else:
                        try:
                            vals.append(float(cs))
                        except ValueError:
                            vals.append(None)
                series_map[oid] = vals
        return build_timeseries_from_json_series(
            series_map,
            row_offsets=row_offsets,
            hourly_mode=hourly_mode,
            filter_ids=filter_ids,
            keep_row_hourly=keep_row_hourly,
            timeline_buckets=timeline_buckets,
        )

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

    n_buckets_24 = min(24, n_steps)
    bucket_indices_24 = np.array_split(np.arange(n_steps), n_buckets_24)

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
