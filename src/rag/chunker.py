"""Разбиение нормативного документа СП 31.13330 на пункты (чанки) с метаданными."""
from __future__ import annotations
import json
import re
from pathlib import Path
from typing import Any

_BUILTIN_CLAUSES_PATH = Path(__file__).resolve().parent / "sp31_clauses.json"

# Разбиение текста СП 31.13330 по номерам пунктов вида "5.11 ", "5.11. ", "10.3 ", "11.5.1 "
_CLAUSE_SPLIT_RE = re.compile(r"(?m)^(?=\d+\.\d+(?:\.\d+)?\.?\s+[А-ЯЁA-Z])")
_SECTION_HEADER_RE = re.compile(r"(?m)^(\d+)\.?\s+([А-ЯЁ][А-ЯЁа-яё\s,\-()]{4,120})$")
_CLAUSE_NUM_NORM_RE = re.compile(r"(\d+\.\d+(?:\.\d+)?)")


def _clean_sp31_pdf_text(text: str) -> str:
    """Очищает извлеченный текст PDF СП 31.13330 от переносов слов, колонтитулов и номеров страниц."""
    cleaned = text.replace("\r\n", "\n").replace("\r", "\n")
    # Убираем мягкие переносы и разрывы русских слов по дефису в конце строки
    cleaned = cleaned.replace("\u00ad", "")
    cleaned = re.sub(r"([а-яёА-ЯЁ])-\s*\n\s*([а-яё])", r"\1\2", cleaned)
    # Убираем колонтитулы вида "СП 31.13330.2012" и отдельно стоящие номера страниц
    cleaned = re.sub(r"(?m)^\s*СП\s*31\.13330\.\d{4}\s*$", "", cleaned)
    cleaned = re.sub(r"(?m)^\s*\d{1,3}\s*$", "", cleaned)
    return cleaned


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
    if any(k in t for k in ("резервуар", "рчв", "емкост", "башн", "объем", "потер")):
        applies.append("tank")
    if any(k in t for k in ("скважин", "водозабор", "источник", "подземн")):
        applies.append("source")
    if not applies:
        applies.append("network")
    return applies


def _detect_document_edition(text: str, path_hint: str = "") -> str:
    head = (path_hint + " " + text[:1200]).lower()
    if "31.13330.2021" in head:
        return "СП 31.13330.2021"
    return "СП 31.13330.2012"


def parse_sp31_text_to_chunks(text: str, document_name: str = "СП 31.13330.2012") -> list[dict[str, Any]]:
    """Разбивает полный текст СП 31.13330 по пунктам (п. X.Y) без разрыва таблиц и примечаний."""
    if not text or not text.strip():
        return []
    cleaned = _clean_sp31_pdf_text(text)
    sections_by_num: dict[str, str] = {}
    for m_sec in _SECTION_HEADER_RE.finditer(cleaned):
        sections_by_num[m_sec.group(1)] = f"{m_sec.group(1)}. {m_sec.group(2).strip()}"

    parts = _CLAUSE_SPLIT_RE.split(cleaned)
    chunks: list[dict[str, Any]] = []
    for part in parts:
        p_str = part.strip()
        if not p_str:
            continue
        m_num = re.match(r"^(\d+(?:\.\d+)+)\.?\s+(.*)$", p_str, flags=re.DOTALL)
        if not m_num:
            continue
        c_num = m_num.group(1)
        body = re.sub(r"\s+", " ", m_num.group(2)).strip()
        if len(body) < 25:
            continue
        sec_num = c_num.split(".")[0]
        sec_title = sections_by_num.get(sec_num, f"Раздел {sec_num}")
        first_sent = body.split(".", 1)[0][:110].strip()
        has_table = bool(re.search(r"\bтаблиц[аеуы]\s+\d+", body, flags=re.IGNORECASE))
        keywords = [c_num]
        if has_table:
            keywords.extend(re.findall(r"таблиц[аеуы]\s+\d+", body, flags=re.IGNORECASE)[:2])
        chunks.append({
            "clause_id": f"п. {c_num}",
            "document": document_name,
            "section": sec_title,
            "title": first_sent,
            "text": body[:2400],
            "Applies_to": _infer_applies_to(body),
            "diag_codes": [],
            "norm_threshold": "",
            "keywords": keywords,
        })
    return chunks


def extract_sp31_pdf_chunks(pdf_path: Path | str | None = None) -> list[dict[str, Any]]:
    """Извлекает пункты СП 31.13330 из PDF/TXT/MD-файла (при наличии pypdf / PyMuPDF)
    и объединяет со встроенной верифицированной базой ключевых пунктов СП 31.13330.
    """
    builtin = load_builtin_sp31_clauses()
    by_id: dict[str, dict[str, Any]] = {c["clause_id"]: dict(c) for c in builtin}
    builtin_by_num: dict[str, str] = {}
    for cid in by_id:
        for num in _CLAUSE_NUM_NORM_RE.findall(cid):
            builtin_by_num.setdefault(num, cid)

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
                doc_name = _detect_document_edition(extracted_text, path.name)
                for ch in parse_sp31_text_to_chunks(extracted_text, document_name=doc_name):
                    cid = ch["clause_id"]
                    c_nums = _CLAUSE_NUM_NORM_RE.findall(cid)
                    matched_builtin_id = next((builtin_by_num[n] for n in c_nums if n in builtin_by_num), None)
                    if cid in by_id:
                        by_id[cid]["pdf_excerpt"] = ch["text"][:1200]
                    elif matched_builtin_id and matched_builtin_id in by_id:
                        by_id[matched_builtin_id]["pdf_excerpt"] = ch["text"][:1200]
                    else:
                        by_id[cid] = ch

    return list(by_id.values())
