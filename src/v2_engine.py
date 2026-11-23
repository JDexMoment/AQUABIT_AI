"""Совместимый интерфейс старого модуля. Реализация находится в processing/."""
from processing.csv_reader import (KNOWN_UNIT_TOKENS, _decode_file_text, repair_aquabit_header_tokens,
                                   read_aquabit_csv, find_col, _resolve_file_ci)
from processing.identifiers import clean_object_id, clean_object_name, format_step_to_hhmm
from processing.curves import evaluate_qh_curve_head, build_qh_curve_plot_points
from processing.timeseries import _stream_wide_timeseries
from processing.topology import parse_inp_metadata_and_topology
from processing.pipeline import VALVE_TYPE_DESC, process_aquabit_city_v3, process_aquabit_city_fast
