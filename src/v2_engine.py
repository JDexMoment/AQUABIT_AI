"""Совместимый интерфейс старого модуля. Реализация находится в processing/."""
from processing.csv_reader import (KNOWN_UNIT_TOKENS, _decode_file_text, repair_aquabit_header_tokens,
                                   read_aquabit_csv, find_col, _resolve_file_ci)
from processing.identifiers import clean_object_id, clean_object_name, format_step_to_hhmm
from processing.curves import evaluate_qh_curve_head, build_qh_curve_plot_points
from processing.timeseries import _stream_wide_timeseries, build_timeseries_from_json_series
from processing.topology import parse_inp_metadata_and_topology
from processing.digwater import (
    DigWaterFormatError,
    parse_collapsed_list,
    iter_digwater_objects,
    read_object_names,
    parse_digwater_metadata_and_topology,
)
from processing.report_json import (
    load_report_json,
    iter_report_objects,
    iter_normalized_report_objects,
    find_city_report_json,
    find_city_digwater,
)
from processing.pipeline import VALVE_TYPE_DESC, process_aquabit_city_v3, process_aquabit_city_fast

