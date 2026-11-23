"""Фактический HTML-отчёт по Report.json и текстовым атрибутам digWater.

Это отдельный базовый отчёт, а не эквивалент старой гидравлической диагностики.
Данные в нём не моделируются и не передаются в ИИ.
"""
from __future__ import annotations
import html
import json
import math
import re
from pathlib import Path
from typing import Any
from processing.digwater import iter_digwater_objects
from processing.report_json import iter_report_objects

_GROUP_LAYER = {'junc':'Junction', 'pipe':'Pipe', 'pump':'Pump', 'valv':'Valve', 'reserv':'Reservoir', 'tank':'Tank'}
_PREFIX = {'junc':'Ju','pipe':'Pi','pump':'Pu','valv':'Va','reserv':'Re','tank':'Ta'}
_LABELS = {'junc':'Узлы','pipe':'Трубы','pump':'Насосы','valv':'Клапаны','reserv':'Источники','tank':'Накопители'}


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool) or value == '':
        return None
    try:
        v = float(value)
        return v if math.isfinite(v) else None
    except (ValueError, TypeError):
        return None


def _bounds(values: Any) -> tuple[float | None, float | None, int]:
    if not isinstance(values, list):
        return None, None, 0
    vals = [v for x in values if (v := _number(x)) is not None]
    return (min(vals), max(vals), len(vals)) if vals else (None, None, 0)


def collect_report(report_path: Path | str, digwater_path: Path | str) -> dict[str, Any]:
    """Сводка по объектам без хранения всех массивов расчётных результатов."""
    attrs: dict[tuple[str, str], dict[str, str]] = {}
    layer_to_group = {v:k for k,v in _GROUP_LAYER.items()}
    for layer, object_id, fields in iter_digwater_objects(digwater_path):
        if not layer.startswith('Model\x08'):
            continue
        group = layer_to_group.get(layer.split('\x08', 1)[1])
        if group is None:
            continue
        key = (group, object_id)
        if key in attrs:
            raise ValueError(f'Повторный ID {object_id} в слое {layer}')
        attrs[key] = fields
    rows = {k: [] for k in _GROUP_LAYER}
    counts = {k: 0 for k in _GROUP_LAYER}
    matched = {k: 0 for k in _GROUP_LAYER}
    seen: set[tuple[str, str]] = set()
    for group, obj in iter_report_objects(report_path):
        oid = obj.get('id')
        if not isinstance(oid, str):
            raise ValueError(f'{group}: отсутствует строковый идентификатор')
        match = re.fullmatch(re.escape(_PREFIX[group]) + r'(\d+)', oid, flags=re.IGNORECASE)
        if not match:
            raise ValueError(f'{group}: неподдерживаемый идентификатор {oid!r}')
        key = (group, match.group(1))
        if key in seen:
            raise ValueError(f'Повторный идентификатор {oid} в Report.json')
        seen.add(key)
        extra = attrs.get(key)
        counts[group] += 1
        matched[group] += extra is not None
        lo, hi, n = _bounds(obj.get('head' if group in ('junc','reserv','tank') else 'flow'))
        z = _number(obj.get('elev'))
        row = {'id':oid, 'name':str((extra or {}).get('Name') or obj.get('name') or ''),
               'start':str(obj.get('start') or ''), 'finish':str(obj.get('finish') or ''),
               'minimum':lo, 'maximum':hi, 'steps':n, 'elevation':z,
               'pressure_min':(lo-z if lo is not None and z is not None else None),
               'pressure_max':(hi-z if hi is not None and z is not None else None),
               'diameter':_number(obj.get('diam')), 'length':_number(obj.get('len')),
               'roughness':_number(obj.get('rough')), 'matched':extra is not None}
        rows[group].append(row)
    return {'rows':rows,'counts':counts,'matched':matched,
            'sources':{'json':str(report_path),'digwater':str(digwater_path)}}


def render_json_report(report_path: Path | str, digwater_path: Path | str, output_path: Path | str) -> dict[str, Any]:
    """Записывает автономный HTML и возвращает сведения о сопоставлении."""
    summary = collect_report(report_path, digwater_path)
    def esc(value):
        return html.escape('—' if value is None or value == '' else str(value), quote=True)
    def num(value):
        return '—' if value is None else f'{value:.2f}'
    parts = []
    for group, objects in summary['rows'].items():
        if not objects:
            continue
        body = []
        for r in objects:
            if group in ('junc','reserv','tank'):
                other = f"{esc(num(r['elevation']))} / {esc(num(r['pressure_min']))} / {esc(num(r['pressure_max']))}"
            else:
                other = f"{esc(r['start'])} → {esc(r['finish'])}"
            body.append('<tr>' + ''.join(f'<td>{esc(v)}</td>' for v in [r['id'],r['name']]) +
                        f'<td>{other}</td><td>{esc(num(r["minimum"]))}</td><td>{esc(num(r["maximum"]))}</td>' +
                        f'<td>{r["steps"]}</td><td>{"да" if r["matched"] else "нет"}</td></tr>')
        meaning = 'Напор, м' if group in ('junc','reserv','tank') else 'Расход (единицы экспорта)'
        extra_title = 'Отметка / мин. / макс. свободный напор, м' if group in ('junc','reserv','tank') else 'Начало → конец (из JSON)'
        parts.append(f'<section><h2>{esc(_LABELS[group])}: {len(objects)}</h2>'
                     f'<p>Сопоставлено с digWater: {summary["matched"][group]} из {len(objects)}. '
                     f'Минимум и максимум: {meaning}.</p><div class="scroll"><table><thead><tr>'
                     f'<th>ID</th><th>Название</th><th>{esc(extra_title)}</th><th>Мин.</th><th>Макс.</th>'
                     '<th>Значений</th><th>digWater</th></tr></thead><tbody>' + ''.join(body) + '</tbody></table></div></section>')
    doc = '''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Отчёт AquaBit по JSON и digWater</title><style>body{font:15px system-ui,sans-serif;background:#f3f6fa;color:#172333;margin:0;padding:24px}
main{max-width:1500px;margin:auto}section,.note{background:white;border:1px solid #d8e1ec;border-radius:10px;padding:18px;margin:16px 0}
.note{border-left:5px solid #d97706}input{font:inherit;width:min(100%,520px);padding:10px;box-sizing:border-box}.scroll{overflow:auto;max-height:620px}
table{border-collapse:collapse;width:100%;font-size:13px}th,td{padding:7px;border-bottom:1px solid #e6eaf0;text-align:left;white-space:nowrap}
th{position:sticky;top:0;background:#eef3f8}</style></head><body><main><h1>Отчёт AquaBit: JSON + digWater</h1>
<div class="note"><strong>Базовый фактический отчёт.</strong> Имена и атрибуты взяты из digWater, результаты и конечные узлы — из JSON.
Свободный напор рассчитан как напор минус отметка. Геометрия digWater не декодирована; карта и старая ИИ-диагностика отсутствуют.
Расчётное потребление узлов и водный баланс в этом отчёте не определяются. Проверьте соответствие версии проекта и результатов.</div>
<input id="filter" type="search" placeholder="Поиск по ID, названию или связанному узлу" aria-label="Поиск по таблицам">''' + ''.join(parts) + '''</main>
<script>document.getElementById('filter').addEventListener('input',function(){const q=this.value.toLocaleLowerCase();document.querySelectorAll('tbody tr').forEach(r=>r.hidden=!r.textContent.toLocaleLowerCase().includes(q))});</script></body></html>'''
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(doc, encoding='utf8')
    return {'output_path': str(output), 'counts':summary['counts'], 'matched':summary['matched']}
