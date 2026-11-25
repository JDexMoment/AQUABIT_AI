"""Разбиение нормативного документа СП 31.13330 на пункты (чанки) с метаданными."""
from __future__ import annotations
import json
import re
from pathlib import Path
from typing import Any

_BUILTIN_CLAUSES_PATH = Path(__file__).resolve().parent / "sp31_clauses.json"

# Разбиение текста СП 31.13330 по номерам пунктов вида "5.11 ", "10.3 ", "11.5 "
_CLAUSE_SPLIT_RE = re.compile(r"(?m)^(?=\d+\.\d+(?:\.\d+)?\s+[А-ЯЁA-Z])")
_SECTION_HEADER_RE = re.compile(r"(?m)^(\d+)\s+([А-ЯЁ][А-ЯЁа-яё\s,\-()]{4,120})$")


def load_builtin_sp31_clauses() -> list[dict[str, Any]]:
    """Загружает встроенную верифицированную базу пунктов СП 31.13330.2012."""
    if _BUILTIN_CLAUSES_PATH.exists():
        return json.loads(_BUILTIN_CLAUSES_PATH.read_text(encoding="utf-8"))
    return []


def _infer_applies_to(text: str) -> list[str]:
    t = text.lower()
    applies: list[str] = []
    if any(k in t for k in ("напор", "этаж", "узел", "потребител", "здани", "давлен")):
        applies.append("node")
    if any(k in t for k in ("насос", "агрегат", "резерв", "всасыва", "кпд", "bep", "станци")):
        applies.append("pump")
    if any(k in t for k in ("труб", "водовод", "диаметр", "скорост", "тупик", "кольцев", "задвижк", "шероховат")):
        applies.append("pipe")
    if any(k in t for k in ("клапан", "регулятор", "prv", "psv", "fcv", "арматур")):
        applies.append("valve")
    if any(k in t for k in ("резервуар", "рчв", "емкост", "башн", "объем")):
        applies.append("tank")
    if any(k in t for k in ("скважин", "водозабор", "источник", "подземн")):
        applies.append("source")
    if not applies:
        applies.append("network")
    return applies


def parse_sp31_text_to_chunks(text: str, document_name: str = "СП 31.13330.2012") -> list[dict[str, Any]]:
    """Разбивает полный текст СП 31.13330 по пунктам (п. X.Y) без разрыва таблиц и примечаний."""
    if not text or not text.strip():
        return []
    cleaned = text.replace("\r\n", "\n")
    sections_by_num: dict[str, str] = {}
    for m_sec in _SECTION_HEADER_RE.finditer(cleaned):
        sections_by_num[m_sec.group(1)] = f"{m_sec.group(1)}. {m_sec.group(2).strip()}"

    parts = _CLAUSE_SPLIT_RE.split(cleaned)
    chunks: list[dict[str, Any]] = []
    for part in parts:
        p_str = part.strip()
        if not p_str:
            continue
        m_num = re.match(r"^(\d+(?:\.\d+)+)\s+(.*)$", p_str, flags=re.DOTALL)
        if not m_num:
            continue
        c_num = m_num.group(1)
        body = re.sub(r"\s+", " ", m_num.group(2)).strip()
        if len(body) < 25:
            continue
        sec_num = c_num.split(".")[0]
        sec_title = sections_by_num.get(sec_num, f"Раздел {sec_num}")
        first_sent = body.split(".", 1)[0][:110].strip()
        chunks.append({
            "clause_id": f"п. {c_num}",
            "document": document_name,
            "section": sec_title,
            "title": first_sent,
            "text": body[:1800],
            "Applies_to": _infer_applies_to(body),
            "diag_codes": [],
            "norm_threshold": "",
            "keywords": [c_num],
        })
    return chunks


def extract_sp31_pdf_chunks(pdf_path: Path | str | None = None) -> list[dict[str, Any]]:
    """Извлекает пункты СП 31.13330 из PDF-файла (при наличии pypdf / PyMuPDF) и объединяет
    со встроенной верифицированной базой ключевых пунктов СП 31.13330.2012.
    """
    builtin = load_builtin_sp31_clauses()
    by_id: dict[str, dict[str, Any]] = {c["clause_id"]: c for c in builtin}

    if pdf_path is not None:
        path = Path(pdf_path)
        if path.exists() and path.is_file():
            extracted_text = ""
            if path.suffix.lower() in (".md", ".txt"):
                extracted_text = path.read_text(encoding="utf-8", errors="replace")
            elif path.suffix.lower() == ".pdf":
                try:
                    import pypdf  # type: ignore[import-not-found]
                    reader = pypdf.PdfReader(str(path))
                    extracted_text = "\n".join(page.extract_text() or "" for page in reader.pages)
                except ImportError:
                    try:
                        import fitz  # type: ignore[import-not-found]
                        doc = fitz.open(str(path))
                        extracted_text = "\n".join(page.get_text("text") for page in doc)
                    except ImportError:
                        extracted_text = ""
            if extracted_text:
                for ch in parse_sp31_text_to_chunks(extracted_text):
                    cid = ch["clause_id"]
                    if cid not in by_id:
                        by_id[cid] = ch

    return list(by_id.values())
