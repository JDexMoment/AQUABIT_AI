"""Чтение атрибутов, метаданных, топологии и геометрии текстового проекта AquaBit .digWater.

Реализует полную спецификацию формата .DigWater:
- Кодировка ANSI (Windows-1251) с автоопределением UTF-8;
- Разделитель блоков данных — одиночная пустая строка;
- Разделитель столбцов — табуляция (\\t);
- Спецсимвол ASCII 26 (\\x1a) — разделитель строк внутри свернутого текстового списка;
- Спецсимвол ASCII 8 (\\x08) — уникальность имен слоев (Model\\x08Junction и др.) и вложенные таблицы;
- Извлечение названий объектов (Name), атрибутов (отметки, диаметры, шероховатости, статусы, кривые Q-H),
  топологии (начальный/конечный узлы) и координат (из полей X/Y или hex-геометрии).
"""
from __future__ import annotations
import codecs
import math
import re
import struct
from pathlib import Path
from typing import Any, Iterator
from processing.identifiers import clean_object_id, clean_object_name


class DigWaterFormatError(ValueError):
    """Файл проекта не соответствует поддерживаемому текстовому формату."""


_MODEL_LAYERS = ("Junction", "Pipe", "Pump", "Valve", "Reservoir", "Tank", "Curve", "Customer", "Pattern")


def _detect_digwater_encoding(path: Path) -> str:
    """По умолчанию .DigWater использует ANSI (cp1251), но поддерживает и UTF-8."""
    decoder = codecs.getincrementaldecoder("utf-8-sig")(errors="strict")
    try:
        with path.open("rb") as source:
            chunk = source.read(256 * 1024)
            decoder.decode(chunk, final=len(chunk) < 256 * 1024)
        return "utf-8-sig"
    except UnicodeDecodeError:
        return "cp1251"


def parse_collapsed_list(raw_cell: str) -> dict[str, str]:
    """Разбирает свернутый список пар Ключ=Значение, разделенный спецсимволом ASCII 26 (\\x1a)."""
    if not raw_cell:
        return {}
    result: dict[str, str] = {}
    for idx, item in enumerate(raw_cell.split("\x1a")):
        item_s = item.strip()
        if not item_s:
            continue
        if "=" in item_s:
            k, v = item_s.split("=", 1)
            result[k.strip()] = v.strip()
            if idx == 0 and "_primary_key" not in result:
                result["_primary_key"] = k.strip()
                result["_primary_label"] = v.strip()
        elif idx == 0:
            result["_primary_key"] = item_s
            result["_primary_label"] = item_s
    return result


def _normalize_layer_name(raw_layer_key: str) -> str:
    """Приводит имя слоя вида 'Model\\Junction' или 'Model\\x08Junction' к каноническому 'Model\\x08Junction'."""
    s = raw_layer_key.strip()
    if "\\" in s and "\x08" not in s:
        grp, rest = s.split("\\", 1)
        return f"{grp}\x08{rest}"
    return s


def _is_layer_header_token(part0: str) -> bool:
    if not part0 or part0.lstrip().isdigit():
        return False
    first_item = part0.split("\x1a", 1)[0]
    if "\x08" in first_item:
        return True
    info = parse_collapsed_list(part0)
    name_val = info.get("Name", info.get("_primary_key", ""))
    if "\x08" in name_val or "\\" in name_val:
        return True
    if any(name_val.endswith(lyr) for lyr in _MODEL_LAYERS) and ("Type" in info or "\x1a" in part0):
        return True
    return False


def _extract_layer_name_from_token(part0: str) -> str:
    first_item = part0.split("\x1a", 1)[0]
    if "\x08" in first_item:
        return _normalize_layer_name(first_item.split("=", 1)[0])
    info = parse_collapsed_list(part0)
    if "Name" in info:
        return _normalize_layer_name(info["Name"])
    pk = info.get("_primary_key", "")
    return _normalize_layer_name(pk)


def _decode_wkb_or_hex_geometry(hex_token: str) -> list[list[float]] | None:
    """Декодирует шестнадцатеричную геометрию (WKB / EWKB Point или LineString) из последнего столбца .digWater."""
    if not hex_token:
        return None
    s = hex_token.strip()
    if s.startswith(("0x", "0X")):
        s = s[2:]
    if len(s) < 18 or len(s) % 2 != 0:
        return None
    if not re.fullmatch(r"[0-9a-fA-F]+", s):
        return None
    try:
        buf = bytes.fromhex(s)
    except ValueError:
        return None

    # Попытка 1: стандартный WKB / EWKB (первый байт 0x00 = big-endian, 0x01 = little-endian)
    if buf[0] in (0, 1) and len(buf) >= 21:
        endian = "<" if buf[0] == 1 else ">"
        try:
            raw_type = struct.unpack(endian + "I", buf[1:5])[0]
            has_srid = bool(raw_type & 0x20000000)
            has_z = bool(raw_type & 0x80000000) or (1000 <= (raw_type & 0xFFFF) < 2000) or (3000 <= (raw_type & 0xFFFF) < 4000)
            has_m = bool(raw_type & 0x40000000) or (2000 <= (raw_type & 0xFFFF) < 4000)
            base_type = (raw_type & 0xFFFF) % 1000
            offset = 5
            if has_srid:
                offset += 4
            dims = 2 + (1 if has_z else 0) + (1 if has_m else 0)
            step = 8 * dims

            if base_type == 1 and len(buf) >= offset + step:  # Point
                coords = struct.unpack(endian + ("d" * dims), buf[offset : offset + step])
                x, y = float(coords[0]), float(coords[1])
                if math.isfinite(x) and math.isfinite(y) and (abs(x) > 1e-9 or abs(y) > 1e-9):
                    return [[round(x, 4), round(y, 4)]]
            elif base_type == 2 and len(buf) >= offset + 4:  # LineString
                n_pts = struct.unpack(endian + "I", buf[offset : offset + 4])[0]
                offset += 4
                if 1 <= n_pts <= 10000 and len(buf) >= offset + n_pts * step:
                    pts: list[list[float]] = []
                    for _ in range(n_pts):
                        coords = struct.unpack(endian + ("d" * dims), buf[offset : offset + step])
                        offset += step
                        x, y = float(coords[0]), float(coords[1])
                        if math.isfinite(x) and math.isfinite(y):
                            pts.append([round(x, 4), round(y, 4)])
                    if pts:
                        return pts
        except struct.error:
            pass

    return None


def iter_digwater_objects(path: Path | str) -> Iterator[tuple[str, str, dict[str, str]]]:
    """Потоково обходит объекты всех слоёв файла .digWater, выдавая (layer, object_id, fields).

    Поддерживает как однострочный заголовок слоя (дескриптор слоя и колонки через табуляцию),
    так и двухстрочный заголовок по спецификации CGLayer (строка 1 — слой, строка 2 — колонки).
    """
    path = Path(path)
    if not path.exists() or not path.is_file():
        return
    enc = _detect_digwater_encoding(path)
    # splitlines() нельзя: он считает спецсимвол 0x1a концом строки.
    with path.open("r", encoding=enc, errors="replace", newline="") as stream:
        layer: str | None = None
        columns: list[str] = []
        awaiting_columns_line = False
        for _line_no, raw in enumerate(stream, 1):
            line = raw.rstrip("\r\n")
            if not line:
                layer, columns = None, []
                awaiting_columns_line = False
                continue
            parts = line.split("\t")
            if _is_layer_header_token(parts[0]):
                layer = _extract_layer_name_from_token(parts[0])
                col_tokens = [p for p in parts[1:] if p.strip()]
                if col_tokens:
                    columns = [part.split("\x1a", 1)[0].split("=", 1)[0].strip() for part in col_tokens]
                    awaiting_columns_line = False
                else:
                    columns = []
                    awaiting_columns_line = True
                continue

            if awaiting_columns_line and layer is not None and not parts[0].lstrip().isdigit():
                columns = [part.split("\x1a", 1)[0].split("=", 1)[0].strip() for part in parts if part.strip()]
                awaiting_columns_line = False
                continue

            if layer is None or not parts[0].strip().isdigit():
                continue

            obj_id = parts[0].strip()
            fields = dict(zip(columns, parts[1 : 1 + len(columns)]))
            # Если после колонок идет шестнадцатеричная геометрия, сохраняем её под служебным ключом _hex_geom
            if len(parts) > 1 + len(columns):
                tail_geom = parts[1 + len(columns)].strip()
                if tail_geom:
                    fields["_hex_geom"] = tail_geom
            yield layer, obj_id, fields


def read_object_names(path: Path | str) -> dict[str, str]:
    """Имена по точному числовому ID. Не объединяет объекты разных проектов."""
    names: dict[str, str] = {}
    valid_layers = {f"Model\x08{name}" for name in ("Junction", "Pipe", "Pump", "Valve", "Reservoir", "Tank")}
    for layer, object_id, fields in iter_digwater_objects(path):
        if layer not in valid_layers:
            continue
        name = (fields.get("Name") or fields.get("Наименование") or fields.get("Имя") or "").strip()
        if name:
            if object_id in names and names[object_id] != name:
                raise DigWaterFormatError(f"Повторяющийся ID {object_id} с разными именами: {path}")
            names[object_id] = name
    return names


def _safe_float(val: Any) -> float | None:
    if val is None or isinstance(val, bool):
        return None
    s = str(val).strip().replace(",", ".")
    if not s or s.lower() in ("nan", "none", "null"):
        return None
    try:
        v = float(s)
        return v if math.isfinite(v) else None
    except (ValueError, TypeError):
        return None


def _get_field_ci(fields: dict[str, str], *candidates: str) -> str:
    """Регистронезависимый поиск значения поля в словаре атрибутов .digWater."""
    for c in candidates:
        if c in fields and fields[c] != "":
            return fields[c]
    lower_map = {k.lower(): v for k, v in fields.items()}
    for c in candidates:
        cl = c.lower()
        if cl in lower_map and lower_map[cl] != "":
            return lower_map[cl]
    for c in candidates:
        cl = c.lower()
        for k, v in lower_map.items():
            if cl in k and v != "":
                return v
    return ""


def parse_digwater_metadata_and_topology(
    digwater_path: Path | str | None,
    node_ids: set[str] | None = None,
    pipe_ids: set[str] | None = None,
    pump_ids: set[str] | None = None,
    extra_node_ids: set[str] | None = None,
    *,
    node_ids_set: set[str] | None = None,
    pipe_ids_set: set[str] | None = None,
    pump_ids_set: set[str] | None = None,
) -> dict[str, Any]:
    """Полный разбор .DigWater: метаданные модели, имена объектов, дополнительные свойства,
    топология связей (Начальный/Конечный узел), статусы перекрытых участков и координаты.
    """
    meta: dict[str, Any] = {
        "model_info": {},
        "layer_groups": {},
        "names": {},
        "links": [],
        "nodes": {},
        "link_ends": {},
        "closed_links": set(),
        "reservoir_clean_ids": set(),
        "tank_clean_ids": set(),
        "node_attrs": {},
        "pipe_attrs": {},
        "pump_attrs": {},
        "valve_attrs": {},
        "reservoir_attrs": {},
        "tank_attrs": {},
        "qh_curves": {},
    }
    if not digwater_path:
        return meta
    path = Path(digwater_path)
    if not path.exists() or not path.is_file():
        return meta

    # Читаем блок 1 (Общая информация) и блок 2 (Группы слоев)
    enc = _detect_digwater_encoding(path)
    with path.open("r", encoding=enc, errors="replace", newline="") as stream:
        block_idx = 0
        in_block = False
        for raw in stream:
            line = raw.rstrip("\r\n")
            if not line:
                if in_block:
                    block_idx += 1
                    in_block = False
                    if block_idx >= 2:
                        break
                continue
            in_block = True
            if "\t" in line or "\x08" in line or "\x1a" in line:
                break
            if "=" in line:
                k, v = line.split("=", 1)
                if block_idx == 0:
                    meta["model_info"][k.strip()] = v.strip()
                elif block_idx == 1:
                    meta["layer_groups"][k.strip()] = v.strip()

    raw_links: list[tuple[str, str, str, list[list[float]] | None]] = []

    for layer, object_id, fields in iter_digwater_objects(path):
        cid = clean_object_id(object_id)
        if not cid:
            continue
        short_layer = layer.split("\x08", 1)[1] if "\x08" in layer else layer

        raw_name = _get_field_ci(fields, "Name", "Наименование", "Имя", "Caption", "Title").strip()
        clean_nm = clean_object_name(raw_name)
        if clean_nm:
            meta["names"][cid] = clean_nm
            meta["names"][object_id] = clean_nm
            for pref in ("c", "e", "p", "v", "k", "u", "Ju", "Pi", "Pu", "Va", "Re", "Ta"):
                meta["names"][f"{pref}{cid}"] = clean_nm

        # Извлечение координат из X/Y или hex-геометрии
        xv = _safe_float(_get_field_ci(fields, "X", "CoordX", "Lon"))
        yv = _safe_float(_get_field_ci(fields, "Y", "CoordY", "Lat"))
        geom_pts = _decode_wkb_or_hex_geometry(fields.get("_hex_geom", ""))

        if short_layer in ("Junction", "Reservoir", "Tank"):
            pt: list[float] | None = None
            if xv is not None and yv is not None:
                pt = [xv, yv]
            elif geom_pts and len(geom_pts) >= 1:
                pt = geom_pts[0]
            if pt is not None:
                meta["nodes"][cid] = pt
                meta["nodes"][f"c{cid}"] = pt
                meta["nodes"][f"Ju{cid}"] = pt

            elev_v = _safe_float(_get_field_ci(fields, "Elev", "Elevation", "Z", "Отметка", "Высота"))
            req_p = _safe_float(_get_field_ci(fields, "ReqPress", "RequiredPressure", "FreeHead", "ReqHead", "Требуемый"))
            floors_v = _safe_float(_get_field_ci(fields, "Floors", "Stage", "Этажность", "Этажи"))
            demand_v = _safe_float(_get_field_ci(fields, "Demand", "BaseDemand", "Потребление", "Расход"))
            pattern_v = _get_field_ci(fields, "Pattern", "Schedule", "График").strip()
            head_v = _safe_float(_get_field_ci(fields, "Head", "H", "Напор", "Level"))

            attr_dict = {
                "id": cid,
                "name": clean_nm,
                "elev": elev_v,
                "elevation_m": elev_v,
                "req_press": req_p,
                "required_pressure_m": req_p,
                "floors": int(round(floors_v)) if floors_v and floors_v > 0 else None,
                "demand": demand_v,
                "base_demand_m3_h": demand_v,
                "pattern": pattern_v,
                "schedule": pattern_v,
                "head": head_v,
                "head_m": head_v if head_v is not None else elev_v,
                "x": pt[0] if pt else None,
                "y": pt[1] if pt else None,
                "raw_fields": {k: v for k, v in fields.items() if k != "_hex_geom" and v != ""},
            }
            if short_layer == "Junction":
                meta["node_attrs"][cid] = attr_dict
            elif short_layer == "Reservoir":
                meta["reservoir_clean_ids"].add(cid)
                meta["reservoir_attrs"][cid] = attr_dict
                if pt is not None:
                    meta["nodes"][f"e{cid}"] = pt
                    meta["nodes"][f"Re{cid}"] = pt
            elif short_layer == "Tank":
                meta["tank_clean_ids"].add(cid)
                meta["tank_attrs"][cid] = attr_dict
                if pt is not None:
                    meta["nodes"][f"k{cid}"] = pt
                    meta["nodes"][f"Ta{cid}"] = pt

        elif short_layer in ("Pipe", "Pump", "Valve"):
            sn_raw = _get_field_ci(fields, "Start", "Node1", "StartNode", "FromNode", "Начало").strip()
            en_raw = _get_field_ci(fields, "Finish", "End", "Node2", "EndNode", "ToNode", "Конец").strip()
            sn_cid = clean_object_id(sn_raw)
            en_cid = clean_object_id(en_raw)
            ltype = short_layer.upper()
            if sn_cid and en_cid:
                for key_id in (cid, object_id, f"e{cid}", f"p{cid}", f"v{cid}", f"Pi{cid}", f"Pu{cid}", f"Va{cid}"):
                    meta["link_ends"][key_id] = (sn_cid, en_cid, ltype)

            status_raw = _get_field_ci(fields, "Status", "State", "Статус", "IsClosed", "Closed").strip().upper()
            is_closed = status_raw in ("CLOSED", "ЗАКРЫТ", "ЗАКРЫТА", "ВЫКЛ", "OFF")

            if short_layer == "Pipe":
                if is_closed:
                    meta["closed_links"].add(cid)
                    meta["closed_links"].add(f"e{cid}")
                    meta["closed_links"].add(f"Pi{cid}")
                d_val = _safe_float(_get_field_ci(fields, "Diam", "Diameter", "D", "Диаметр"))
                l_val = _safe_float(_get_field_ci(fields, "Len", "Length", "L", "Длина"))
                c_val = _safe_float(_get_field_ci(fields, "Rough", "Roughness", "C", "Шероховатость"))
                meta["pipe_attrs"][cid] = {
                    "id": cid,
                    "name": clean_nm,
                    "start": sn_cid,
                    "finish": en_cid,
                    "start_node": sn_cid,
                    "end_node": en_cid,
                    "diam": d_val,
                    "diameter_mm": d_val,
                    "len": l_val,
                    "length_m": l_val,
                    "rough": c_val,
                    "roughness_c": c_val,
                    "material": _get_field_ci(fields, "Material", "Материал").strip(),
                    "year": _get_field_ci(fields, "Year", "Год").strip(),
                    "status": "CLOSED" if is_closed else "OPEN",
                    "is_closed": is_closed,
                    "raw_fields": {k: v for k, v in fields.items() if k != "_hex_geom" and v != ""},
                }
                raw_links.append((cid, sn_cid, en_cid, geom_pts))
            elif short_layer == "Pump":
                curve_id = clean_object_id(_get_field_ci(fields, "Curve", "HCurve", "QH", "Кривая").strip())
                q_nom_f = _safe_float(_get_field_ci(fields, "Q", "Qnom", "Flow", "Подача"))
                h_nom_f = _safe_float(_get_field_ci(fields, "H", "Hnom", "Head", "Напор"))
                meta["pump_attrs"][cid] = {
                    "id": cid,
                    "name": clean_nm,
                    "start": sn_cid,
                    "finish": en_cid,
                    "start_node": sn_cid,
                    "end_node": en_cid,
                    "curve_id": curve_id,
                    "q_nom": q_nom_f,
                    "h_nom": h_nom_f,
                    "status": "CLOSED" if is_closed else "OPEN",
                    "raw_fields": {k: v for k, v in fields.items() if k != "_hex_geom" and v != ""},
                }
                if q_nom_f and h_nom_f and q_nom_f > 0 and h_nom_f > 0:
                    meta["qh_curves"].setdefault(curve_id or cid, []).append((q_nom_f, h_nom_f))
                raw_links.append((cid, sn_cid, en_cid, geom_pts))
            elif short_layer == "Valve":
                vtype = _get_field_ci(fields, "Type", "ValveType", "Тип").strip() or "PRV"
                vset = _safe_float(_get_field_ci(fields, "Setting", "Set", "Уставка", "Настройка"))
                meta["valve_attrs"][cid] = {
                    "id": cid,
                    "name": clean_nm,
                    "start": sn_cid,
                    "finish": en_cid,
                    "start_node": sn_cid,
                    "end_node": en_cid,
                    "type": vtype,
                    "type_code": vtype,
                    "setting": vset,
                    "status": "CLOSED" if is_closed else "OPEN",
                    "raw_fields": {k: v for k, v in fields.items() if k != "_hex_geom" and v != ""},
                }
                raw_links.append((cid, sn_cid, en_cid, geom_pts))

        elif short_layer == "Curve":
            # Разбор кривых Q-H из слоя Model\x08Curve (включая вложенные таблицы через \x08 или \x1a)
            pts: list[tuple[float, float]] = []
            for v in fields.values():
                if "\x08" in v or "\x1a" in v:
                    for token in re.split(r"[\x08\x1a;]+", v):
                        nums = re.findall(r"[-+]?\d+(?:[.,]\d+)?", token)
                        if len(nums) >= 2:
                            qv = _safe_float(nums[0])
                            hv = _safe_float(nums[1])
                            if qv is not None and hv is not None:
                                pts.append((qv, hv))
            qv_direct = _safe_float(_get_field_ci(fields, "Q", "Flow", "X", "Подача"))
            hv_direct = _safe_float(_get_field_ci(fields, "H", "Head", "Y", "Напор"))
            if qv_direct is not None and hv_direct is not None:
                pts.append((qv_direct, hv_direct))
            if pts:
                meta["qh_curves"].setdefault(cid, []).extend(pts)
                meta["qh_curves"].setdefault(f"u{cid}", []).extend(pts)

    for lid, sn_cid, en_cid, geom_pts in raw_links:
        if geom_pts and len(geom_pts) >= 2:
            meta["links"].append([lid, geom_pts])
            if sn_cid and sn_cid not in meta["nodes"]:
                meta["nodes"][sn_cid] = geom_pts[0]
            if en_cid and en_cid not in meta["nodes"]:
                meta["nodes"][en_cid] = geom_pts[-1]
        elif sn_cid and en_cid:
            p1 = meta["nodes"].get(sn_cid)
            p2 = meta["nodes"].get(en_cid)
            if p1 and p2:
                meta["links"].append([lid, [p1, p2]])

    return meta
