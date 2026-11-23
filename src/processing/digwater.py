"""Чтение атрибутов слоёв текстового проекта AquaBit .digWater.

Геометрию и топологические связи не декодирует: их бинарный формат в доступной
спецификации не описан. Значения исходных файлов не изменяются.
"""
from __future__ import annotations
from pathlib import Path
from typing import Iterator


class DigWaterFormatError(ValueError):
    """Файл проекта не соответствует поддерживаемому текстовому формату."""


def iter_digwater_objects(path: Path | str) -> Iterator[tuple[str, str, dict[str, str]]]:
    path = Path(path)
    # splitlines() нельзя: он считает спецсимвол 0x1a концом строки.
    with path.open('r', encoding='cp1251', newline='') as stream:
        layer: str | None = None
        columns: list[str] = []
        for line_no, raw in enumerate(stream, 1):
            line = raw.rstrip('\r\n')
            if not line:
                layer, columns = None, []
                continue
            parts = line.split('\t')
            descriptor = parts[0].split('\x1a', 1)[0]
            if '\x08' in descriptor and not parts[0].lstrip().isdigit():
                layer = descriptor.split('=', 1)[0]
                columns = [part.split('\x1a', 1)[0].split('=', 1)[0] for part in parts[1:]]
                continue
            if layer is None or not parts[0].isdigit():
                continue
            fields = dict(zip(columns, parts[1:1 + len(columns)]))
            yield layer, parts[0], fields


def read_object_names(path: Path | str) -> dict[str, str]:
    """Имена по точному числовому ID. Не объединяет объекты разных проектов."""
    names: dict[str, str] = {}
    for layer, object_id, fields in iter_digwater_objects(path):
        if layer not in {f'Model\x08{name}' for name in ('Junction', 'Pipe', 'Pump', 'Valve', 'Reservoir', 'Tank')}:
            continue
        name = fields.get('Name', '').strip()
        if name:
            if object_id in names and names[object_id] != name:
                raise DigWaterFormatError(f'Повторяющийся ID {object_id} с разными именами: {path}')
            names[object_id] = name
    return names
