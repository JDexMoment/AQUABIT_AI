"""Интерполяция и подготовка характеристик насосов."""
from __future__ import annotations
from typing import Any


def _linspace(start: float, stop: float, num: int) -> list[float]:
    if num <= 1:
        return [start]
    step = (stop - start) / (num - 1)
    return [start + i * step for i in range(num)]


def _interp_linear(x: float, xs: list[float], ys: list[float]) -> float:
    if x <= xs[0]:
        return float(ys[0])
    if x >= xs[-1]:
        return float(ys[-1])
    for i in range(len(xs) - 1):
        x0, x1 = xs[i], xs[i + 1]
        if x0 <= x <= x1:
            if abs(x1 - x0) < 1e-12:
                return float(ys[i])
            t = (x - x0) / (x1 - x0)
            return float(ys[i] + t * (ys[i + 1] - ys[i]))
    return float(ys[-1])


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
    return round(_interp_linear(q_val, qs, hs), 2)


def build_qh_curve_plot_points(pts: list[tuple[float, float]], n_grid: int = 25) -> dict[str, Any]:
    if not pts:
        return {"mode": "empty", "design_points": [], "curve_points": []}
    pts_sorted = sorted(pts, key=lambda x: x[0])
    if len(pts_sorted) == 1:
        q_nom, h_nom = pts_sorted[0]
        q_max = max(q_nom * 2.0, 1.0)
        grid_q = _linspace(0.0, q_max, n_grid)
        curve_pts = [
            [
                round(float(q), 2),
                round(max(0.0, (4.0 / 3.0) * h_nom * (1.0 - 0.25 * ((q / max(q_nom, 1e-6)) ** 2))), 2),
            ]
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
