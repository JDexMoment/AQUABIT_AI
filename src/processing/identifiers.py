"""Нормализация идентификаторов и представления времени."""
from __future__ import annotations
import re

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
