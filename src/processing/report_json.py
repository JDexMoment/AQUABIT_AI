"""Загрузка Report.json AquaBit с контролируемым исправлением id без кавычек и кавычек в name."""
from __future__ import annotations
import codecs
import json
import re
import warnings
from pathlib import Path
from typing import Any, Iterator

# Некорректное поле вида `"id": Va1286,` встречается в реальном экспорте.
# Ключ должен находиться на отдельной строке: не меняем текст внутри JSON-строк.
_BARE_ID = re.compile(r'(?m)^(\s*"id"\s*:\s*)([A-Za-z][A-Za-z0-9_]*)(\s*[,}])')
_ALLOWED_GROUPS = ('junc', 'pipe', 'pump', 'valv', 'reserv', 'tank')

# AquaBit иногда выводит кавычки внутри name без JSON-экранирования.
_NAME_LINE = re.compile(r'^(\s*"name"\s*:\s*")(.*)("\s*[,}]\s*)$', re.DOTALL)


def _repair_name_line(line: str) -> tuple[str, bool]:
    match = _NAME_LINE.match(line)
    if not match:
        return line, False
    prefix, content, suffix = match.groups()
    try:
        json.loads('"' + content + '"')
        return line, False
    except json.JSONDecodeError:
        pass
    chars = []
    i = 0
    while i < len(content):
        char = content[i]
        if char == '\\':
            nxt = content[i + 1] if i + 1 < len(content) else ''
            if nxt and nxt in '"\\/bfnrt':
                chars.extend((char, nxt))
                i += 2
                continue
            if nxt == 'u' and re.fullmatch(r'[0-9a-fA-F]{4}', content[i + 2:i + 6]):
                chars.append(content[i:i + 6])
                i += 6
                continue
            chars.append('\\\\')
        elif char == '"':
            chars.append('\\"')
        else:
            chars.append(char)
        i += 1
    repaired = ''.join(chars)
    try:
        json.loads('"' + repaired + '"')
    except json.JSONDecodeError:
        return line, False
    return prefix + repaired + suffix, True


def _detect_encoding(path: Path) -> str:
    """Проверяет UTF-8 потоково, затем использует Windows-1251 для выгрузок AquaBit."""
    decoder = codecs.getincrementaldecoder('utf-8-sig')(errors='strict')
    try:
        with path.open('rb') as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b''):
                decoder.decode(chunk)
            decoder.decode(b'', final=True)
        return 'utf-8-sig'
    except UnicodeDecodeError:
        return 'cp1251'


def load_report_json(path: Path | str, *, allow_large: bool = False) -> dict[str, Any]:
    """Прочитать отчёт целиком. Большие файлы требуют явного подтверждения памяти.

    Выполняет известные исправления экспорта AquaBit: заключает некавыченный id в кавычки
    и экранирует внутренние кавычки в поле name. Исходный файл не изменяется.
    """
    path = Path(path)
    if path.stat().st_size > 100 * 1024 * 1024 and not allow_large:
        raise ValueError(
            f'{path}: файл больше 100 МиБ; полная загрузка требует много памяти. '
            'Используйте iter_report_objects() или передайте allow_large=True после оценки памяти.'
        )
    text = path.read_text(encoding=_detect_encoding(path))
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        fixed, count = _BARE_ID.subn(r'\1"\2"\3', text)
        names_count = 0
        repaired_lines = []
        for line in fixed.splitlines(keepends=True):
            rep_line, was_rep = _repair_name_line(line)
            if was_rep:
                names_count += 1
            repaired_lines.append(rep_line)
        fixed_all = ''.join(repaired_lines)
        if not count and not names_count:
            raise
        data = json.loads(fixed_all)
        if count:
            warnings.warn(
                f'{path}: исправлено некавыченных id: {count}; исходный файл не изменён.',
                UserWarning,
                stacklevel=2,
            )
        if names_count:
            warnings.warn(
                f'{path}: исправлены кавычки/обратные слеши name: {names_count}; исходный файл не изменён.',
                UserWarning,
                stacklevel=2,
            )
    if not isinstance(data, dict):
        raise ValueError(f'{path}: ожидался JSON-объект с группами объектов сети')
    for key in _ALLOWED_GROUPS:
        if key in data and (
            not isinstance(data[key], list)
            or any(not isinstance(item, dict) for item in data[key])
        ):
            raise ValueError(f'{path}: группа {key} должна быть массивом объектов')
    if not any(k in data for k in _ALLOWED_GROUPS):
        raise ValueError(f'{path}: не найдены известные группы объектов AquaBit')
    return data


def iter_report_objects(path: Path | str) -> Iterator[tuple[str, dict[str, Any]]]:
    """Потоково выдаёт (группа, объект), ограничивая память одним объектом при наличии ijson,
    либо использует load_report_json как резервный загрузчик без внешних зависимостей.
    """
    import tempfile
    path = Path(path)
    try:
        import ijson
        from ijson.common import ObjectBuilder
        has_ijson = True
    except ImportError:
        has_ijson = False

    if not has_ijson:
        data = load_report_json(path, allow_large=True)
        for group in _ALLOWED_GROUPS:
            for item in data.get(group, []) or []:
                yield group, item
        return

    count = 0
    names_count = 0
    with tempfile.TemporaryFile(mode='w+b') as corrected:
        with path.open('r', encoding=_detect_encoding(path), newline='') as source:
            for line in source:
                fixed, n = _BARE_ID.subn(r'\1"\2"\3', line)
                count += n
                fixed, repaired_name = _repair_name_line(fixed)
                names_count += repaired_name
                corrected.write(fixed.encode('utf-8'))
        if count:
            warnings.warn(
                f'{path}: исправлено некавыченных id: {count}; исходный файл не изменён.',
                UserWarning,
                stacklevel=2,
            )
        if names_count:
            warnings.warn(
                f'{path}: исправлены кавычки/обратные слеши name: {names_count}; исходный файл не изменён.',
                UserWarning,
                stacklevel=2,
            )
        corrected.seek(0)
        builder = None
        current_group = None
        root_group = None
        found_groups = set()
        for prefix, event, value in ijson.parse(corrected, use_float=True):
            if prefix == '' and event == 'start_map':
                root_group = True
            if prefix in _ALLOWED_GROUPS and event == 'start_array':
                found_groups.add(prefix)
            if prefix in _ALLOWED_GROUPS and event not in ('start_array', 'end_array'):
                raise ValueError(f'{path}: группа {prefix} должна быть массивом объектов')
            if (
                prefix.endswith('.item')
                and prefix.split('.')[0] in _ALLOWED_GROUPS
                and event == 'start_map'
                and builder is None
            ):
                current_group = prefix.split('.')[0]
                builder = ObjectBuilder()
            elif (
                prefix.endswith('.item')
                and prefix.split('.')[0] in _ALLOWED_GROUPS
                and builder is None
            ):
                raise ValueError(f'{path}: элемент группы {prefix} должен быть объектом')
            if builder is not None:
                builder.event(event, value)
                if prefix == current_group + '.item' and event == 'end_map':
                    yield current_group, builder.value
                    builder = None
                    current_group = None
        if not root_group or not found_groups:
            raise ValueError(f'{path}: не найдены известные группы объектов AquaBit')


def _to_float(val: Any) -> float | None:
    """Безопасное преобразование скалярного значения из JSON/DigWater в float."""
    if val is None or isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        import math
        v = float(val)
        return v if math.isfinite(v) else None
    s = str(val).strip().replace(',', '.')
    if not s or s.lower() in ('nan', 'none', 'null'):
        return None
    try:
        import math
        v = float(s)
        return v if math.isfinite(v) else None
    except (ValueError, TypeError):
        return None


def iter_normalized_report_objects(path: Path | str) -> Iterator[dict[str, Any]]:
    """Потоково выдаёт нормализованные объекты Report.json со структурой:
    {id, clean_id, type, name, properties, time_series}.
    Поддерживает как плоский формат AquaBit (head/demand/flow на верхнем уровне объекта),
    так и вложенные словари properties/time_series.
    """
    from processing.identifiers import clean_object_id, clean_object_name

    ts_keys = ('head', 'free_pressure', 'pressure', 'demand', 'flow', 'velocity', 'headloss')
    for group, raw in iter_report_objects(path):
        if not isinstance(raw, dict):
            continue
        oid = str(raw.get('id') or raw.get('ID') or '').strip()
        if not oid:
            continue
        cid = clean_object_id(oid)
        name = clean_object_name(str(raw.get('name') or raw.get('Name') or ''))
        props: dict[str, Any] = dict(raw.get('properties') or {})
        ts: dict[str, list[Any]] = dict(raw.get('time_series') or {})

        for k, v in raw.items():
            if k in ('id', 'ID', 'name', 'Name', 'properties', 'time_series'):
                continue
            kl = k.lower()
            if isinstance(v, list) and (kl in ts_keys or all(isinstance(x, (int, float)) or x is None for x in v[:5])):
                ts[kl] = v
            else:
                props.setdefault(k, v)

        yield {
            'id': oid,
            'clean_id': cid,
            'type': group,
            'name': name,
            'properties': props,
            'time_series': ts,
        }


def find_city_report_json(city_dir: Path | str) -> Path | None:
    """Находит JSON-файл результатов расчета в папке города (приоритет Report.json, затем *.json)."""
    folder = Path(city_dir)
    if not folder.exists() or not folder.is_dir():
        return None
    json_files = sorted(
        [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == '.json'],
        key=lambda p: (0 if p.name.lower() == 'report.json' else 1, -p.stat().st_size),
    )
    for cand in json_files:
        if cand.name.lower().startswith('facts_'):
            continue
        return cand
    return None


def find_city_digwater(city_dir: Path | str) -> Path | None:
    """Находит файл модели .digWater в папке города."""
    folder = Path(city_dir)
    if not folder.exists() or not folder.is_dir():
        return None
    dw_files = sorted(
        [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == '.digwater'],
        key=lambda p: -p.stat().st_size,
    )
    return dw_files[0] if dw_files else None

