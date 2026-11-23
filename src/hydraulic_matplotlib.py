"""Модуль пересчёта и визуализации всех гидравлических графиков через Matplotlib для ПК «Аквабит».

Обеспечивает математически точное построение:
1. Характеристик насосов Q-H с номинальной точкой, фактической рабочей точкой и зоной BEP (75-115%);
2. Суточного гидрографа подачи и потребления (24 ч) с разрывом установленной мощности;
3. Суточного коридора свободных напоров P = H - Z с опорными горизонтами (0 м, -3 м, 60 м);
4. Распределения дефицита требуемого напора абонентов относительно этажности (СП 31.13330);
5. Распределения скоростей потока и рейтинга лимитирующих участков по удельным потерям напора i (м/км);
6. Зон пониженных скоростей (< 0.10 м/с) и рисков застоя;
7. Пространственной схемы топологии сети с подсветкой проблемных объектов (при наличии координат INP).
"""

from __future__ import annotations

import base64
import io
import math
import os
import tempfile
from typing import Any

# Настраиваем окружение Matplotlib для стабильной работы без дисплея и в песочнице Windows
if "MPLCONFIGDIR" not in os.environ:
    os.environ["MPLCONFIGDIR"] = os.path.join(tempfile.gettempdir(), "mpl_epanet_ai")

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import numpy as np

# Настройка шрифтов для корректного отображения кириллицы
plt.rcParams["font.sans-serif"] = ["DejaVu Sans", "Segoe UI", "Arial", "Calibri", "sans-serif"]
plt.rcParams["axes.unicode_minus"] = False
plt.rcParams["figure.autolayout"] = False

# Цветовая палитра ПК «Аквабит»
COLORS = {
    "primary": "#1d4ed8",
    "primary_light": "#3b82f6",
    "success": "#15803d",
    "warning": "#d97706",
    "danger": "#dc2626",
    "danger_light": "#fee2e2",
    "dark": "#0f172a",
    "gray": "#64748b",
    "gray_light": "#f1f5f9",
    "border": "#cbd5e1",
    "bep_fill": "#dcfce7",
    "bep_border": "#22c55e",
    "deficit_red": "#ef4444",
    "surplus_green": "#22c55e"
}


def figure_to_base64_png(fig: plt.Figure, dpi: int = 130) -> str:
    """Конвертирует фигуру Matplotlib в base64 PNG строку для встраивания в HTML."""
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight", facecolor=fig.get_facecolor(), edgecolor="none")
    buf.seek(0)
    data = base64.b64encode(buf.read()).decode("ascii")
    plt.close(fig)
    return f"data:image/png;base64,{data}"


def plot_pump_qh_curves(facts: dict[str, Any], max_pumps: int = 4) -> tuple[plt.Figure, str]:
    """Пересчитывает и строит напорно-расходные характеристики насосов Q-H через Matplotlib."""
    pma = facts.get("pumps_analysis", {})
    all_pumps = pma.get("all_pumps_table", [])
    active_pumps = [p for p in all_pumps if p.get("q_working_m3_h") is not None and p.get("q_working_m3_h", 0) > 0]
    if not active_pumps:
        active_pumps = all_pumps[:max_pumps]
    else:
        active_pumps = active_pumps[:max_pumps]

    n_pumps = len(active_pumps)
    if n_pumps == 0:
        fig, ax = plt.subplots(figsize=(8, 4), facecolor="white")
        ax.text(0.5, 0.5, "Данные по насосам и кривым Q-H отсутствуют в модели", ha="center", va="center", color=COLORS["gray"])
        ax.axis("off")
        return fig, figure_to_base64_png(fig)

    cols = min(2, n_pumps)
    rows = math.ceil(n_pumps / cols)
    fig, axes = plt.subplots(rows, cols, figsize=(7.2 * cols, 5.0 * rows), facecolor="#f8fafc")
    if n_pumps == 1:
        axes_list = [axes]
    else:
        axes_list = axes.flatten() if hasattr(axes, "flatten") else [axes]

    for idx, pump in enumerate(active_pumps):
        ax = axes_list[idx]
        ax.set_facecolor("white")
        ax.grid(True, linestyle="--", alpha=0.5, color=COLORS["border"])

        q_work = pump.get("q_working_m3_h") or 0.0
        h_work = pump.get("h_working_m") or 0.0
        q_nom = pump.get("q_nominal_m3_h")
        h_nom = pump.get("h_nominal_m")
        curve_pts = pump.get("curve_points", [])

        # Построение теоретической кривой Q-H
        q_curve_max = max(q_work * 1.3, (q_nom or 0) * 1.3, 10.0)
        if len(curve_pts) >= 2:
            pts_sorted = sorted(curve_pts, key=lambda x: x[0])
            qx = np.array([p[0] for p in pts_sorted])
            hy = np.array([p[1] for p in pts_sorted])
            q_curve_max = max(q_curve_max, qx.max() * 1.15)
            # Полиномиальная или квадратичная аппроксимация (стандарт EPANET: H = H0 - a*Q^b)
            if len(qx) >= 3:
                poly = np.polyfit(qx, hy, 2)
            else:
                poly = np.polyfit(qx, hy, 1)
            q_fine = np.linspace(0, q_curve_max, 100)
            h_fine = np.polyval(poly, q_fine)
            h_fine = np.clip(h_fine, 0, None)
            ax.plot(q_fine, h_fine, color=COLORS["primary"], lw=2.5, label="Паспортная кривая Q-H")
            ax.scatter(qx, hy, color=COLORS["primary"], s=35, zorder=4, label="Точки паспорта")
        else:
            # Если точных точек нет, строим ориентировочную кривую по номиналу
            ref_q = q_nom or q_work or 100.0
            ref_h = h_nom or h_work or 50.0
            h0 = ref_h * 1.33
            q_fine = np.linspace(0, ref_q * 1.4, 80)
            h_fine = h0 - 0.33 * ref_h * (q_fine / ref_q) ** 2
            ax.plot(q_fine, h_fine, color=COLORS["primary"], lw=2.2, linestyle="-", label="Кривая Q-H (аппроксимация)")

        # Зона оптимального КПД (BEP: 75% - 115% от Q_ном)
        if q_nom and q_nom > 0:
            bep_low = 0.75 * q_nom
            bep_high = 1.15 * q_nom
            ax.axvspan(bep_low, bep_high, color=COLORS["bep_fill"], alpha=0.6, label="Оптимальная зона BEP (75–115%)")
            ax.scatter([q_nom], [h_nom or 0], color=COLORS["warning"], marker="D", s=70, zorder=5, label=f"Номинал: {q_nom:.1f} м³/ч")

        # Фактическая рабочая точка
        pt_color = COLORS["danger"] if ("Перегрузка" in str(pump.get("regime", "")) or "Недогрузка" in str(pump.get("regime", ""))) else COLORS["success"]
        ax.scatter([q_work], [h_work], color=pt_color, s=90, edgecolors="black", linewidths=1.5, zorder=6,
                   label=f"Факт: {q_work:.1f} м³/ч, {h_work:.1f} м")

        # Аннотация рабочей точки
        regime_label = pump.get("regime", "Рабочий режим")
        p_type = "Скважинный (1-й подъем)" if pump.get("is_borehole") else "Сетевой (повысительный)"
        ax.set_title(f"{pump.get('display_id', pump.get('pump_id'))} [{p_type}]\nРежим: {regime_label}", fontsize=11, fontweight="bold")
        ax.set_xlabel("Подача Q, м³/ч", fontsize=10)
        ax.set_ylabel("Напор H, м", fontsize=10)
        ax.legend(loc="upper right", fontsize=8.5, framealpha=0.9)

    for i in range(idx + 1, len(axes_list)):
        axes_list[i].axis("off")

    fig.suptitle(f"Напорно-расходные характеристики насосных агрегатов (ПК «Аквабит») — {facts.get('city_name', '')}",
                 fontsize=13, fontweight="bold", y=1.02)
    fig.tight_layout()
    return fig, figure_to_base64_png(fig)


def plot_diurnal_hydraulic_balance(facts: dict[str, Any]) -> tuple[plt.Figure, str]:
    """Строит суточный гидрограф подачи насосов, потребления сети и разрыва баланса (24 ч)."""
    charts = facts.get("chart_payload", {})
    flow_data = charts.get("diurnal_flow", {})
    hours = list(range(24))

    q_pump = flow_data.get("pump_supply_m3_h", [0.0] * 24)
    q_dem = flow_data.get("consumer_demand_m3_h", [0.0] * 24)
    wb = facts.get("water_balance", {})

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 6.5), sharex=True, facecolor="#f8fafc",
                                   gridspec_kw={"height_ratios": [2.2, 1.0]})

    # Верхний график: подача насосов vs водоразбор сети
    ax1.set_facecolor("white")
    ax1.grid(True, linestyle="--", alpha=0.5, color=COLORS["border"])

    ax1.plot(hours, q_pump, color=COLORS["primary"], lw=2.8, marker="o", markersize=4, label="Суммарная подача насосов (м³/ч)")
    ax1.fill_between(hours, q_pump, alpha=0.15, color=COLORS["primary_light"])

    ax1.plot(hours, q_dem, color=COLORS["danger"], lw=2.4, linestyle="--", marker="s", markersize=4, label="Водопотребление сети (м³/ч)")

    # Линия установленной теоретической мощности
    cap_val = wb.get("total_installed_pump_capacity_m3_h")
    if cap_val and cap_val > 0:
        ax1.axhline(cap_val, color=COLORS["gray"], linestyle=":", lw=1.8, label=f"Установленная мощность: {cap_val:.0f} м³/ч")

    # Отметка часа максимального водоразбора
    if q_dem and max(q_dem) > 0:
        peak_hr = int(np.argmax(q_dem))
        peak_val = q_dem[peak_hr]
        ax1.annotate(f"Пик: {peak_val:.1f} м³/ч\n({peak_hr:02d}:00)",
                     xy=(peak_hr, peak_val), xytext=(peak_hr + 0.8, peak_val * 1.05),
                     arrowprops=dict(facecolor=COLORS["danger"], shrink=0.08, width=1.5, headwidth=6),
                     fontsize=9, fontweight="bold", color=COLORS["danger"])

    ax1.set_ylabel("Расход, м³/ч", fontsize=11, fontweight="bold")
    ax1.set_title(f"Суточный баланс подачи и водоразбора (24 ч) — {facts.get('city_name', '')}", fontsize=12, fontweight="bold")
    ax1.legend(loc="upper right", framealpha=0.9, fontsize=9.5)

    # Нижний график: разность подачи и потребления (заполнение/опорожнение РЧВ)
    ax2.set_facecolor("white")
    ax2.grid(True, linestyle="--", alpha=0.5, color=COLORS["border"])
    delta = [p - d for p, d in zip(q_pump, q_dem)]
    pos_delta = [max(0.0, d) for d in delta]
    neg_delta = [min(0.0, d) for d in delta]

    ax2.bar(hours, pos_delta, color=COLORS["primary_light"], alpha=0.7, label="Аккумуляция в РЧВ (+)")
    ax2.bar(hours, neg_delta, color=COLORS["warning"], alpha=0.7, label="Сработка РЧВ / Дефицит (-)")
    ax2.axhline(0, color="black", lw=1.0)
    ax2.set_xlabel("Часы суток (00:00 – 23:00)", fontsize=11)
    ax2.set_ylabel("ΔQ, м³/ч", fontsize=10)
    ax2.set_xticks(hours)
    ax2.legend(loc="lower right", framealpha=0.9, fontsize=8.5)

    fig.tight_layout()
    return fig, figure_to_base64_png(fig)


def plot_diurnal_pressure_corridor(facts: dict[str, Any]) -> tuple[plt.Figure, str]:
    """Строит суточный коридор свободных напоров P = H - Z с опорными горизонтами (0 м, -3 м, 60 м)."""
    charts = facts.get("chart_payload", {})
    press_data = charts.get("pressure_envelope", {})
    hours = list(range(24))

    p_min = press_data.get("p_min", [0.0] * 24)
    p_med = press_data.get("p_median", [0.0] * 24)
    p_max = press_data.get("p_max", [0.0] * 24)

    fig, ax = plt.subplots(figsize=(10, 5.2), facecolor="#f8fafc")
    ax.set_facecolor("white")
    ax.grid(True, linestyle="--", alpha=0.5, color=COLORS["border"])

    # Заполнение коридора напоров
    ax.fill_between(hours, p_min, p_max, color="#bae6fd", alpha=0.45, label="Коридор свободных напоров (min..max)")
    ax.plot(hours, p_med, color=COLORS["primary"], lw=2.8, marker="o", markersize=4, label="Медианный свободный напор P = H - Z")
    ax.plot(hours, p_min, color=COLORS["danger"], lw=1.8, linestyle="--", label="Минимальный напор сети")
    ax.plot(hours, p_max, color=COLORS["success"], lw=1.8, linestyle=":", label="Максимальный напор сети")

    # Нормативные и опорные горизонты (из онтологии)
    ax.axhline(0.0, color="#1e293b", linestyle="-", lw=1.5, label="0.0 м — Уровень земли (манометрический 0)")
    ax.axhline(-3.0, color="#b91c1c", linestyle="--", lw=1.5, label="-3.0 м — Предел физ. вакуума / ошибка данных")
    ax.axhline(60.0, color="#7c3aed", linestyle=":", lw=1.8, label="60.0 м — Макс. допустимый напор (СП 31.13330)")

    # Зона комфортного гарантированного напора (20–50 м)
    ax.axhspan(20.0, 50.0, color="#dcfce7", alpha=0.25, label="Комфортный диапазон напора (20–50 м)")

    ax.set_xlabel("Часы суток (00:00 – 23:00)", fontsize=11)
    ax.set_ylabel("Свободный напор P, м вод. ст.", fontsize=11, fontweight="bold")
    ax.set_title(f"Суточный коридор свободных напоров P = H - Z — {facts.get('city_name', '')}", fontsize=12, fontweight="bold")
    ax.set_xticks(hours)
    ax.legend(loc="upper right", framealpha=0.9, fontsize=8.5)

    fig.tight_layout()
    return fig, figure_to_base64_png(fig)


def plot_pressure_deficit_distribution(facts: dict[str, Any], top_k: int = 12) -> tuple[plt.Figure, str]:
    """Строит детальный анализ дефицита свободного напора у абонентов относительно этажности (СП 31.13330)."""
    pa = facts.get("pressure_analysis", {})
    deficit_nodes = pa.get("worst_deficit_nodes", [])[:top_k]

    if not deficit_nodes:
        fig, ax = plt.subplots(figsize=(8, 3.5), facecolor="white")
        ax.text(0.5, 0.5, "[OK] Узлов с дефицитом требуемого напора не выявлено (все потребители обеспечены)",
                ha="center", va="center", color=COLORS["success"], fontsize=11, fontweight="bold")
        ax.axis("off")
        return fig, figure_to_base64_png(fig)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5.2), facecolor="#f8fafc", gridspec_kw={"width_ratios": [1.4, 1.0]})

    # Левый график: Топ абонентов с дефицитом напора (P_своб vs P_треб)
    ax1.set_facecolor("white")
    ax1.grid(True, linestyle="--", alpha=0.5, color=COLORS["border"], axis="x")

    labels = [f"{r.get('display_id', r.get('node_id'))} ({r.get('floors', 1)} эт.)" for r in reversed(deficit_nodes)]
    p_free = [r.get("free_pressure_min_m", 0.0) for r in reversed(deficit_nodes)]
    p_req = [r.get("required_pressure_m", 0.0) for r in reversed(deficit_nodes)]
    y_pos = np.arange(len(labels))

    ax1.barh(y_pos - 0.18, p_free, height=0.35, color=COLORS["deficit_red"], label="Свободный напор P_своб (м)")
    ax1.barh(y_pos + 0.18, p_req, height=0.35, color=COLORS["gray"], alpha=0.6, label="Требуемый напор P_треб (м)")

    ax1.set_yticks(y_pos)
    ax1.set_yticklabels(labels, fontsize=9)
    ax1.set_xlabel("Напор, м", fontsize=10, fontweight="bold")
    ax1.set_title("Топ абонентов с дефицитом напора", fontsize=11, fontweight="bold")
    ax1.legend(loc="lower right", fontsize=8.5)

    # Правый график: Дефицит ΔP = P_своб - P_треб
    ax2.set_facecolor("white")
    ax2.grid(True, linestyle="--", alpha=0.5, color=COLORS["border"])
    deltas = [r.get("pressure_surplus_deficit_m", 0.0) for r in reversed(deficit_nodes)]
    bar_colors = [COLORS["danger"] if d < -5.0 else COLORS["warning"] for d in deltas]

    bars = ax2.barh(y_pos, deltas, color=bar_colors, height=0.6)
    ax2.axvline(0, color="black", lw=1.2)
    ax2.set_yticks(y_pos)
    ax2.set_yticklabels([])
    ax2.set_xlabel("Дефицит ΔP (м)", fontsize=10, fontweight="bold")
    ax2.set_title("Величина недостатка давления ΔP", fontsize=11, fontweight="bold")

    for bar, d in zip(bars, deltas):
        ax2.text(d - 0.4 if d < 0 else d + 0.2, bar.get_y() + bar.get_height() / 2,
                 f"{d:.1f} м", va="center", ha="right" if d < 0 else "left",
                 fontsize=8.5, fontweight="bold", color=COLORS["danger"] if d < 0 else COLORS["success"])

    fig.suptitle(f"Дефицит требуемого напора потребителей относительно этажности — {facts.get('city_name', '')}",
                 fontsize=12, fontweight="bold", y=0.98)
    fig.tight_layout()
    return fig, figure_to_base64_png(fig)


def plot_pipe_bottlenecks_and_velocity(facts: dict[str, Any], top_k: int = 8) -> tuple[plt.Figure, str]:
    """Строит распределение скоростей потока и рейтинг лимитирующих участков по удельным потерям i (м/км)."""
    ppa = facts.get("pipes_analysis", {})
    charts = facts.get("chart_payload", {})
    v_bins = charts.get("velocity_bins", ppa.get("velocity_bins", {}))
    top_pipes = ppa.get("top_high_loss_pipes", [])[:top_k]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5.0), facecolor="#f8fafc", gridspec_kw={"width_ratios": [1.0, 1.3]})

    # Левый график: Распределение труб по скоростям
    ax1.set_facecolor("white")
    ax1.grid(True, linestyle="--", alpha=0.5, color=COLORS["border"], axis="y")

    bin_keys = ["under_0_1", "0_1_to_0_5", "0_5_to_1_5", "1_5_to_3_0", "3_0_to_5_0", "over_5_0"]
    bin_labels = ["< 0.1\n(низкая)", "0.1–0.5\n(малая)", "0.5–1.5\n(норма)", "1.5–3.0\n(повыш)", "3.0–5.0\n(высокая)", ">= 5.0\n(критич)"]
    bin_counts = [v_bins.get(k, 0) for k in bin_keys]
    bar_colors = ["#94a3b8", "#38bdf8", "#22c55e", "#f59e0b", "#f97316", "#dc2626"]

    ax1.bar(range(len(bin_keys)), bin_counts, color=bar_colors, edgecolor="black", linewidth=0.5)
    ax1.set_xticks(range(len(bin_keys)))
    ax1.set_xticklabels(bin_labels, fontsize=8.5)
    ax1.set_ylabel("Количество участков", fontsize=10, fontweight="bold")
    ax1.set_title("Распределение скоростей потока (м/с)", fontsize=11, fontweight="bold")

    # Правый график: Топ труб по удельным потерям напора i (м/км)
    ax2.set_facecolor("white")
    ax2.grid(True, linestyle="--", alpha=0.5, color=COLORS["border"], axis="x")

    if top_pipes:
        labels = [f"{p.get('display_id', p.get('pipe_id'))}\nD={p.get('diameter_mm', 0):.0f} C={p.get('hazen_williams_c', 100):.0f}" for p in reversed(top_pipes)]
        losses = [p.get("headloss_gradient_m_km", 0.0) for p in reversed(top_pipes)]
        vels = [p.get("velocity_max_m_s", 0.0) for p in reversed(top_pipes)]
        y_pos = np.arange(len(labels))

        p_colors = [COLORS["danger"] if loss >= 50.0 else (COLORS["warning"] if loss >= 20.0 else COLORS["primary_light"]) for loss in losses]
        bars = ax2.barh(y_pos, losses, color=p_colors, height=0.6)
        ax2.set_yticks(y_pos)
        ax2.set_yticklabels(labels, fontsize=8.5)
        ax2.set_xlabel("Удельная потеря напора i, м/км", fontsize=10, fontweight="bold")
        ax2.set_title("Узкие места сети (потери i, м/км по Хейзену-Вильямсу)", fontsize=11, fontweight="bold")

        for bar, loss, v in zip(bars, losses, vels):
            ax2.text(bar.get_width() + 0.8, bar.get_y() + bar.get_height() / 2,
                     f"{loss:.1f} м/км (V={v:.2f} м/с)", va="center", fontsize=8, fontweight="bold")
    else:
        ax2.text(0.5, 0.5, "Труб с критическими потерями напора не выявлено", ha="center", va="center", color=COLORS["gray"])
        ax2.axis("off")

    fig.suptitle(f"Гидравлические сопротивления и скорости труб — {facts.get('city_name', '')}",
                 fontsize=12, fontweight="bold", y=0.98)
    fig.tight_layout()
    return fig, figure_to_base64_png(fig)


def plot_low_velocity_stagnation_risk(facts: dict[str, Any]) -> tuple[plt.Figure, str]:
    """Строит анализ зон с пониженными скоростями (< 0.1 м/с) и риска ухудшения качества воды."""
    ppa = facts.get("pipes_analysis", {})
    low_count = ppa.get("low_velocity_under_01ms_count", 0)
    total_pipes = ppa.get("total_pipes", 1)
    low_pct = (low_count / max(1, total_pipes)) * 100

    fig, ax = plt.subplots(figsize=(8, 4.5), facecolor="#f8fafc")
    ax.set_facecolor("white")

    sizes = [low_count, max(0, total_pipes - low_count)]
    labels = [f"V < 0.10 м/с (риск застоя)\n{low_count} участков ({low_pct:.1f}%)",
              f"V >= 0.10 м/с (активный поток)\n{total_pipes - low_count} участков ({100 - low_pct:.1f}%)"]
    colors = ["#f59e0b", "#3b82f6"]

    wedges, texts, autotexts = ax.pie(sizes, labels=labels, autopct="%1.1f%%", startangle=140,
                                      colors=colors, textprops=dict(color="black", fontsize=10))
    for at in autotexts:
        at.set_fontsize(11)
        at.set_weight("bold")

    ax.set_title(f"Доля участков сети с низкими скоростями (< 0.10 м/с) — {facts.get('city_name', '')}\n(Критерий СП 31.13330 и методологии Walski)",
                 fontsize=11, fontweight="bold")
    fig.tight_layout()
    return fig, figure_to_base64_png(fig)


def plot_network_spatial_topology(facts: dict[str, Any]) -> tuple[plt.Figure, str] | None:
    """Строит 2D схему топологии сети с цветовой индикацией проблемных узлов (при наличии координат)."""
    charts = facts.get("chart_payload", {})
    geo = charts.get("geo", {})
    nodes_geo = geo.get("nodes", {})
    links_geo = geo.get("links", [])

    if not nodes_geo or len(nodes_geo) < 5:
        return None

    fig, ax = plt.subplots(figsize=(10, 8), facecolor="#f8fafc")
    ax.set_facecolor("#0f172a")  # Тёмная тема для контраста гидравлической карты
    ax.grid(True, linestyle=":", alpha=0.25, color="white")

    # Отрисовка труб
    bad_pipes = set(geo.get("bad_pipes", []))
    for link in links_geo:
        pts = link[1] if len(link) > 1 else []
        if len(pts) >= 2:
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            is_bad = link[0] in bad_pipes
            ax.plot(xs, ys, color="#ef4444" if is_bad else "#38bdf8", lw=2.2 if is_bad else 1.0, alpha=0.85 if is_bad else 0.5)

    # Отрисовка узлов
    bad_nodes = set(geo.get("bad_nodes", []))
    norm_x, norm_y = [], []
    bad_x, bad_y = [], []

    for nid, pt in nodes_geo.items():
        if nid in bad_nodes:
            bad_x.append(pt[0])
            bad_y.append(pt[1])
        else:
            norm_x.append(pt[0])
            norm_y.append(pt[1])

    if norm_x:
        ax.scatter(norm_x, norm_y, color="#94a3b8", s=12, alpha=0.6, label="Штатные узлы сети")
    if bad_x:
        ax.scatter(bad_x, bad_y, color="#ef4444", s=50, edgecolors="white", linewidths=1.2, zorder=5,
                   label=f"Проблемные узлы (дефицит/ошибки, {len(bad_x)} шт.)")

    ax.set_title(f"Пространственная схема сети и проблемные зоны — {facts.get('city_name', '')}",
                 fontsize=12, fontweight="bold", color="white")
    ax.legend(loc="upper right", framealpha=0.9, facecolor="#1e293b", labelcolor="white", fontsize=9)
    ax.set_aspect("equal", adjustable="datalim")
    fig.tight_layout()
    return fig, figure_to_base64_png(fig)


def render_all_recalculated_charts(facts: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Пересчитывает ВСЕ доступные графики через Matplotlib и возвращает словарь с base64-кодами."""
    charts: dict[str, dict[str, Any]] = {}

    try:
        f_qh, b64_qh = plot_pump_qh_curves(facts)
        charts["pump_qh"] = {
            "id": "pump_qh",
            "title": "Кривые Q-H насосов и рабочие точки",
            "b64": b64_qh,
            "category": "Насосные агрегаты"
        }
    except Exception as e:
        logger.warning("Ошибка генерации графика pump_qh: %s", e)

    try:
        f_bal, b64_bal = plot_diurnal_hydraulic_balance(facts)
        charts["diurnal_balance"] = {
            "id": "diurnal_balance",
            "title": "Суточный баланс подачи и потребления (24 ч)",
            "b64": b64_bal,
            "category": "Баланс водоснабжения"
        }
    except Exception as e:
        logger.warning("Ошибка генерации графика diurnal_balance: %s", e)

    try:
        f_cor, b64_cor = plot_diurnal_pressure_corridor(facts)
        charts["pressure_corridor"] = {
            "id": "pressure_corridor",
            "title": "Суточный коридор свободных напоров P = H - Z",
            "b64": b64_cor,
            "category": "Давление сети"
        }
    except Exception as e:
        logger.warning("Ошибка генерации графика pressure_corridor: %s", e)

    try:
        f_def, b64_def = plot_pressure_deficit_distribution(facts)
        charts["pressure_deficit"] = {
            "id": "pressure_deficit",
            "title": "Дефицит требуемого напора у абонентов (СП 31.13330)",
            "b64": b64_def,
            "category": "Надежность подачи"
        }
    except Exception as e:
        logger.warning("Ошибка генерации графика pressure_deficit: %s", e)

    try:
        f_bot, b64_bot = plot_pipe_bottlenecks_and_velocity(facts)
        charts["pipe_bottlenecks"] = {
            "id": "pipe_bottlenecks",
            "title": "Удельные потери напора i (м/км) и скорости потока",
            "b64": b64_bot,
            "category": "Трубопроводы"
        }
    except Exception as e:
        logger.warning("Ошибка генерации графика pipe_bottlenecks: %s", e)

    try:
        f_stag, b64_stag = plot_low_velocity_stagnation_risk(facts)
        charts["low_velocity_stagnation"] = {
            "id": "low_velocity_stagnation",
            "title": "Зоны пониженных скоростей (< 0.1 м/с)",
            "b64": b64_stag,
            "category": "Качество воды"
        }
    except Exception as e:
        logger.warning("Ошибка генерации графика low_velocity_stagnation: %s", e)

    try:
        geo_res = plot_network_spatial_topology(facts)
        if geo_res:
            charts["spatial_topology"] = {
                "id": "spatial_topology",
                "title": "Пространственная схема сети и проблемные зоны",
                "b64": geo_res[1],
                "category": "Топология сети"
            }
    except Exception as e:
        logger.warning("Ошибка генерации схемы spatial_topology: %s", e)

    return charts


def assemble_adaptive_dashboard_html(all_charts: dict[str, dict[str, Any]],
                                    selected_spec: dict[str, Any]) -> str:
    """Собирает адаптивный HTML-блок дашбордов в порядке приоритета, выбранном ИИ."""
    chosen = selected_spec.get("dashboards", [])
    notes = selected_spec.get("chart_notes", {})

    # Если ИИ выбрал дашборды, упорядочиваем по его приоритету
    cards_html = []
    seen = set()

    for item in sorted(chosen, key=lambda x: x.get("priority", 99)):
        did = item.get("id")
        if did in all_charts and did not in seen:
            seen.add(did)
            c = all_charts[did]
            rationale = item.get("rationale", "")
            note = notes.get(did, "")
            cards_html.append(_render_dashboard_card(c, rationale, note, priority=item.get("priority", 1)))

    # Добавляем остальные графики, если они не были выбраны в основной приоритет
    for did, c in all_charts.items():
        if did not in seen:
            cards_html.append(_render_dashboard_card(c, "Дополнительный диагностический график", notes.get(did, ""), is_secondary=True))

    return "\n".join(cards_html)


def _render_dashboard_card(chart_meta: dict[str, Any], rationale: str, ai_note: str,
                           priority: int = 1, is_secondary: bool = False) -> str:
    badge_style = "background:#dbeafe;color:#1e40af;" if not is_secondary else "background:#f1f5f9;color:#64748b;"
    badge_text = f"Приоритет {priority} - {chart_meta.get('category', 'Диагностика')}" if not is_secondary else "Справочный график"

    note_block = ""
    if ai_note:
        note_block = (
            f"<div style='margin-top:10px;padding:10px 14px;background:#f0fdf4;border-left:4px solid #22c55e;"
            f"border-radius:6px;font-size:13px;color:#14532d;line-height:1.5;'>"
            f"<strong>Заключение ИИ по графику:</strong> {ai_note}</div>"
        )
    elif rationale:
        note_block = (
            f"<div style='margin-top:10px;padding:8px 12px;background:#f8fafc;border-left:3px solid #94a3b8;"
            f"border-radius:4px;font-size:12.5px;color:#475569;'>"
            f"<strong>Обоснование показа:</strong> {rationale}</div>"
        )

    return f"""
    <div class="chart-card" id="card_{chart_meta['id']}" style="background:white;border-radius:12px;border:1px solid #e2e8f0;padding:18px;margin-bottom:24px;box-shadow:0 4px 6px -1px rgba(0,0,0,0.05);">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
            <div>
                <span style="font-size:11px;font-weight:700;padding:3px 8px;border-radius:12px;text-transform:uppercase;{badge_style}">{badge_text}</span>
                <span style="font-size:12px;color:#64748b;margin-left:8px;">{chart_meta.get('category', '')}</span>
                <h3 style="margin:6px 0 0 0;font-size:16px;font-weight:700;color:#0f172a;">{chart_meta['title']}</h3>
            </div>
            <button onclick="openModal('{chart_meta['id']}')" style="background:#f8fafc;border:1px solid #cbd5e1;padding:6px 12px;border-radius:6px;font-size:12px;cursor:pointer;color:#334155;">🔍 Увеличить</button>
        </div>
        <div style="text-align:center;background:#f8fafc;padding:8px;border-radius:8px;">
            <img src="{chart_meta['b64']}" alt="{chart_meta['title']}" style="max-width:100%;height:auto;border-radius:6px;" id="img_{chart_meta['id']}">
        </div>
        {note_block}
    </div>
    """
