"""Нормализация идентификаторов и представления времени."""
from __future__ import annotations
import re

_ID_PREFIX_RE = re.compile(
    r"^(?:node_|pipe_|pump_|valve_|reserv_|tank_|ju|pi|pu|va|re|ta|[ceputvk])(\d+)$",
    flags=re.IGNORECASE,
)


def clean_object_id(raw_id: str | None) -> str:
    """Убирает префикс ([ceputvk] или Ju/Pi/Pu/Va/Re/Ta) из ID объекта:
    c3459771 -> 3459771, Ju3459771 -> 3459771, Pi3466703 -> 3466703, k3575278 -> 3575278.
    """
    if raw_id is None:
        return ""
    s = str(raw_id).strip().lstrip("#")
    if not s:
        return ""
    return _ID_PREFIX_RE.sub(r"\1", s)


def clean_object_name(raw_name: str | None) -> str:
    if not raw_name:
        return ""
    s = str(raw_name).strip()
    if s.lower() in ("nan", "none", "null"):
        return ""
    return re.sub(r"\s*\(\s*Google\s*\)\s*", "", s, flags=re.IGNORECASE).strip()


def format_step_to_hhmm(step_idx: int, total_steps: int) -> str:
    """Форматирует индекс шага симуляции в чистый формат ЧЧ:ММ без технических меток (R360)."""
    if total_steps <= 1:
        return "00:00"
    total_minutes = int(round((step_idx / max(total_steps, 1)) * 1440.0)) % 1440
    hh = total_minutes // 60
    mm = total_minutes % 60
    return f"{hh:02d}:{mm:02d}"
