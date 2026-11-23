"""Чтение CSV-выгрузок AquaBit и поиск файлов."""
from __future__ import annotations
import io
import csv
import warnings
import unicodedata
from pathlib import Path
import pandas as pd

KNOWN_UNIT_TOKENS: set[str] = {
    "м", "мм", "км", "м2", "м3", "м3/час", "м3/ч", "м³/час", "м³/ч",
    "м3/сут", "м³/сут", "л/с", "л/мин", "бар", "атм", "мпа", "кпа", "па",
    "%", "ч", "час", "мин", "с", "сек", "квт", "квт*ч", "квт·ч", "мг/л", "г/м3", "балл", "баллов",
}

def _decode_file_text(fpath: Path) -> str:
    raw = fpath.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")

def repair_aquabit_header_tokens(raw_header_line: str, sample_data_line: str | None = None) -> list[str]:
    tokens = [t.strip() for t in raw_header_line.lstrip("\ufeff").rstrip("\r\n").split(",")]
    if len(tokens) <= 1:
        return tokens
    target_cols: int | None = None
    if sample_data_line and sample_data_line.strip():
        target_cols = len(sample_data_line.rstrip("\r\n").split(","))
    if target_cols is not None and len(tokens) == target_cols:
        return tokens

    merged: list[str] = []
    for tok in tokens:
        tok_norm = tok.lower().strip()
        if merged and tok_norm in KNOWN_UNIT_TOKENS:
            merged[-1] = f"{merged[-1]} ({tok})"
            continue
        merged.append(tok)

    if target_cols is not None and len(merged) != target_cols:
        if len(tokens) == target_cols:
            return tokens
        if len(merged) > target_cols:
            merged = merged[:target_cols]
        else:
            while len(merged) < target_cols:
                merged.append(f"col_{len(merged)}")
    return merged

def _repair_inline_quoted_commas(line: str, expected: int) -> list[str] | None:
    """Исправляет запятую внутри кавычек, начатых в середине незаключённого поля.

    Исправление допускается только если все кавычки сбалансированы и результат
    содержит ровно ожидаемое число полей. Кавычки в названии сохраняются.
    """
    fields: list[str] = []
    current: list[str] = []
    in_csv_quote = False
    in_inline_quote = False
    found = False
    i = 0
    while i < len(line):
        char = line[i]
        if char == '"':
            if in_csv_quote:
                if i + 1 < len(line) and line[i + 1] == '"':
                    current.append('"')
                    i += 2
                    continue
                in_csv_quote = False
            elif in_inline_quote:
                in_inline_quote = False
                current.append(char)
            elif not current:
                in_csv_quote = True
            else:
                in_inline_quote = True
                current.append(char)
            i += 1
            continue
        if char == ',' and in_inline_quote:
            found = True
            current.append(char)
        elif char == ',' and not in_csv_quote:
            fields.append(''.join(current))
            current = []
        else:
            current.append(char)
        i += 1
    if in_csv_quote or in_inline_quote:
        return None
    fields.append(''.join(current))
    if not found or len(fields) != expected:
        return None
    return fields


def _repair_csv_body(body: str, expected: int, path: Path) -> str | None:
    """Исправляет только однозначный известный дефект, сообщая номера строк."""
    repaired_lines: list[str] = []
    repaired_numbers: list[int] = []
    for line_number, line in enumerate(body.splitlines(), start=2):
        try:
            parsed = next(csv.reader([line], strict=True))
        except csv.Error:
            return None
        if len(parsed) > expected:
            fixed = _repair_inline_quoted_commas(line, expected)
            if fixed is None:
                return None
            buffer = io.StringIO(newline='')
            csv.writer(buffer, lineterminator='').writerow(fixed)
            line = buffer.getvalue()
            repaired_numbers.append(line_number)
        repaired_lines.append(line)
    if not repaired_numbers:
        return None
    warnings.warn(
        f"{path}: исправлены запятые в названиях: {len(repaired_numbers)} строк; "
        f"номера в непустых строках: {repaired_numbers[:20]}. Исходный файл не изменён.",
        UserWarning, stacklevel=2,
    )
    return '\n'.join(repaired_lines)


def read_aquabit_csv(fpath: Path | None) -> pd.DataFrame:
    if fpath is None or not fpath.exists():
        return pd.DataFrame()
    try:
        if fpath.stat().st_size == 0:
            return pd.DataFrame()
    except OSError:
        return pd.DataFrame()

    text = _decode_file_text(fpath).lstrip("\ufeff")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return pd.DataFrame()

    # Если первая строка данных сама содержит дефект, она не должна
    # искажать определение количества столбцов по заголовку.
    sample = lines[1] if len(lines) > 1 else None
    if sample is not None:
        inferred_header = repair_aquabit_header_tokens(lines[0])
        fixed_sample = _repair_inline_quoted_commas(sample, len(inferred_header))
        if fixed_sample is not None:
            sample = ','.join('x' for _ in fixed_sample)
    header_cols = repair_aquabit_header_tokens(lines[0], sample)
    if len(lines) == 1:
        return pd.DataFrame(columns=header_cols)

    body = "\n".join(lines[1:])
    def parse(csv_body: str) -> pd.DataFrame:
        return pd.read_csv(
            io.StringIO(csv_body), header=None, names=header_cols,
            na_values=["NAN", "NaN", "nan", "NULL", "null", ""],
            keep_default_na=True, low_memory=False,
        )

    # Проверяем до вызова pandas: дефект в первой строке pandas иногда
    # интерпретирует как индекс и не сообщает об ошибке.
    repaired = _repair_csv_body(body, len(header_cols), fpath)
    try:
        df = parse(repaired if repaired is not None else body)
    except pd.errors.EmptyDataError:
        return pd.DataFrame(columns=header_cols)
    if len(df.columns) > 0:
        first_col = df.columns[0]
        df[first_col] = df[first_col].astype(str).str.strip()
    return df

def find_col(df: pd.DataFrame, *substrings: str, default_idx: int | None = None) -> str | None:
    cols_lower = {c: str(c).lower() for c in df.columns}
    for sub in substrings:
        s = sub.lower()
        for col, cl in cols_lower.items():
            if s in cl:
                return col
    if default_idx is not None and 0 <= default_idx < len(df.columns):
        return str(df.columns[default_idx])
    return None

def _resolve_file_ci(folder: Path, *candidates: str) -> Path | None:
    if not folder.exists():
        return None
    by_norm = {unicodedata.normalize("NFC", p.name).strip().lower(): p for p in folder.iterdir() if p.is_file()}
    for cand in candidates:
        key = unicodedata.normalize("NFC", cand).strip().lower()
        if key in by_norm:
            return by_norm[key]
    return None
