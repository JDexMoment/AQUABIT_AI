"""AquaBit AI — модуль интерпретации гидравлических расчётов AquaBit (EPANET-AI v3)."""

from __future__ import annotations

from aquabit_ai.v2_engine import process_aquabit_city_fast, process_aquabit_city_v3, parse_inp_metadata_and_topology
from aquabit_ai.ontology_engine import (
    load_hydraulic_ontology,
    evaluate_ontology_rules,
    analyze_problem_domains,
    build_ontology_ai_system_prompt,
    extract_ai_dashboards_and_content,
)
from aquabit_ai.hydraulic_matplotlib import (
    render_all_recalculated_charts,
    assemble_adaptive_dashboard_html,
    plot_pump_qh_curves,
    plot_diurnal_hydraulic_balance,
    plot_diurnal_pressure_corridor,
    plot_pressure_deficit_distribution,
    plot_pipe_bottlenecks_and_velocity,
    plot_low_velocity_stagnation_risk,
    plot_network_spatial_topology,
)

__all__ = [
    "__version__",
    "process_aquabit_city_fast",
    "process_aquabit_city_v3",
    "parse_inp_metadata_and_topology",
    "load_hydraulic_ontology",
    "evaluate_ontology_rules",
    "analyze_problem_domains",
    "build_ontology_ai_system_prompt",
    "extract_ai_dashboards_and_content",
    "render_all_recalculated_charts",
    "assemble_adaptive_dashboard_html",
    "plot_pump_qh_curves",
    "plot_diurnal_hydraulic_balance",
    "plot_diurnal_pressure_corridor",
    "plot_pressure_deficit_distribution",
    "plot_pipe_bottlenecks_and_velocity",
    "plot_low_velocity_stagnation_risk",
    "plot_network_spatial_topology",
]

__version__ = "0.3.0"
