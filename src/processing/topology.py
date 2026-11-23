"""Разбор геометрии и топологии сети из EPANET INP."""
from __future__ import annotations
from pathlib import Path
from typing import Any
from processing.csv_reader import _decode_file_text
from processing.identifiers import clean_object_id

def parse_inp_metadata_and_topology(
    inp_path: Path | None,
    node_ids: set[str],
    pipe_ids: set[str],
    pump_ids: set[str],
    extra_node_ids: set[str] | None = None,
) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "names": {},
        "links": [],
        "nodes": {},
        "link_ends": {},
        "closed_links": set(),
        "reservoir_clean_ids": set(),
        "tank_clean_ids": set(),
    }
    if not inp_path or not inp_path.exists():
        return meta

    all_known_nodes = set(node_ids) | set(extra_node_ids or set())
    text = _decode_file_text(inp_path)
    section = ""
    verts: dict[str, list[list[float]]] = {}
    raw_links = []

    def resolve_id(raw_id: str, prefix: str, known: set[str]) -> str:
        if raw_id in known:
            return raw_id
        if (prefix + raw_id) in known:
            return prefix + raw_id
        for cand_pref in ("c", "e", "k"):
            if (cand_pref + raw_id) in known:
                return cand_pref + raw_id
        return raw_id if raw_id.startswith(prefix) else prefix + raw_id

    for line in text.splitlines():
        comment = ""
        if ";" in line:
            code_part, comment_part = line.split(";", 1)
            comment = comment_part.strip()
        else:
            code_part = line
        code_part = code_part.strip()
        if not code_part:
            continue
        if code_part.startswith("[") and "]" in code_part:
            section = code_part[1:code_part.index("]")].upper()
            continue

        parts = code_part.split()
        if len(parts) < 1:
            continue

        raw_id = parts[0]
        if section in ("JUNCTIONS", "RESERVOIRS", "TANKS"):
            pref = "e" if section == "RESERVOIRS" else ("k" if section == "TANKS" else "c")
            oid = resolve_id(raw_id, pref, all_known_nodes)
            if section == "RESERVOIRS":
                meta["reservoir_clean_ids"].add(clean_object_id(raw_id))
            elif section == "TANKS":
                meta["tank_clean_ids"].add(clean_object_id(raw_id))
            if comment and not comment.lower().startswith("id"):
                meta["names"][oid] = comment
                meta["names"][clean_object_id(raw_id)] = comment
        elif section == "PIPES" and len(parts) >= 3:
            oid = resolve_id(raw_id, "e", pipe_ids)
            n1 = resolve_id(parts[1], "c", all_known_nodes)
            n2 = resolve_id(parts[2], "c", all_known_nodes)
            meta["link_ends"][oid] = (n1, n2, "PIPE")
            meta["link_ends"][clean_object_id(raw_id)] = (n1, n2, "PIPE")
            if comment:
                meta["names"][oid] = comment
            if len(parts) >= 8 and parts[7].upper() == "CLOSED":
                meta["closed_links"].add(oid)
            raw_links.append(("e", oid, n1, n2, raw_id))
        elif section == "PUMPS" and len(parts) >= 3:
            oid = resolve_id(raw_id, "p", pump_ids)
            n1 = resolve_id(parts[1], "c", all_known_nodes)
            n2 = resolve_id(parts[2], "c", all_known_nodes)
            meta["link_ends"][oid] = (n1, n2, "PUMP")
            meta["link_ends"][clean_object_id(raw_id)] = (n1, n2, "PUMP")
            if comment:
                meta["names"][oid] = comment
            raw_links.append(("p", oid, n1, n2, raw_id))
        elif section == "VALVES" and len(parts) >= 3:
            oid = resolve_id(raw_id, "v", set())
            n1 = resolve_id(parts[1], "c", all_known_nodes)
            n2 = resolve_id(parts[2], "c", all_known_nodes)
            meta["link_ends"][oid] = (n1, n2, "VALVE")
            meta["link_ends"][clean_object_id(raw_id)] = (n1, n2, "VALVE")
            if comment:
                meta["names"][oid] = comment
            raw_links.append(("v", oid, n1, n2, raw_id))
        elif section == "COORDINATES" and len(parts) >= 3:
            try:
                oid = resolve_id(raw_id, "c", all_known_nodes)
                pt = [float(parts[1]), float(parts[2])]
                meta["nodes"][oid] = pt
                meta["nodes"][clean_object_id(raw_id)] = pt
            except ValueError:
                pass
        elif section == "VERTICES" and len(parts) >= 3:
            try:
                verts.setdefault(raw_id, []).append([float(parts[1]), float(parts[2])])
            except ValueError:
                pass

    for _pref, oid, n1, n2, raw_id in raw_links:
        p1 = meta["nodes"].get(n1) or meta["nodes"].get(clean_object_id(n1))
        p2 = meta["nodes"].get(n2) or meta["nodes"].get(clean_object_id(n2))
        if p1 and p2:
            meta["links"].append([oid, [p1] + verts.get(raw_id, []) + [p2]])

    return meta
