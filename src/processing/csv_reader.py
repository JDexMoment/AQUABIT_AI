"""Чтение CSV-выгрузок AquaBit и поиск файлов."""
from __future__ import annotations
import io
import csv
import math
import warnings
import unicodedata
from pathlib import Path
from typing import Any

try:
    import pandas as pd
except ImportError:
    pd = None  # type: ignore[assignment]

KNOWN_UNIT_TOKENS: set[str] = {
    "м", "мм", "км", "м2", "м3", "м3/час", "м3/ч", "м³/час", "м³/ч",
    "м3/сут", "м³/сут", "л/с", "л/мин", "бар", "атм", "мпа", "кпа", "па",
    "%", "ч", "час", "мин", "с", "сек", "квт", "квт*ч", "квт·ч", "мг/л", "г/м3", "балл", "баллов",
}


class _MiniSeries(list):
    """Легковесная замена pandas.Series при отсутствии pandas."""

    def fillna(self, val: Any) -> "_MiniSeries":
        return _MiniSeries([val if (x is None or (isinstance(x, float) and math.isnan(x))) else x for x in self])

    def astype(self, _t: Any) -> "_MiniSeries":
        return _MiniSeries(["" if x is None else str(x) for x in self])

    @property
    def str(self) -> "_MiniSeries":
        return self

    def strip(self) -> "_MiniSeries":
        return _MiniSeries([str(x).strip() for x in self])

    def tolist(self) -> list[Any]:
        return list(self)


class _MiniILoc:
    def __init__(self, df: "_MiniDataFrame") -> None:
        self._df = df

    def __getitem__(self, key: Any) -> _MiniSeries:
        if isinstance(key, tuple) and len(key) == 2:
            _, col_idx = key
            if isinstance(col_idx, int) and 0 <= col_idx < len(self._df.columns):
                col_name = self._df.columns[col_idx]
                return self._df[col_name]
        return _MiniSeries()


class _MiniDataFrame:
    """Легковесная таблица, совместимая с интерфейсом pandas.DataFrame в пайплайне."""

    def __init__(self, columns: list[str] | None = None, rows: list[dict[str, Any]] | None = None) -> None:
        self.columns: list[str] = list(columns or [])
        self._rows: list[dict[str, Any]] = list(rows or [])

    @property
    def empty(self) -> bool:
        return len(self._rows) == 0

    @property
    def index(self) -> range:
        return range(len(self._rows))

    @property
    def iloc(self) -> _MiniILoc:
        return _MiniILoc(self)

    def __len__(self) -> int:
        return len(self._rows)

    def __getitem__(self, col: str) -> _MiniSeries:
        return _MiniSeries([r.get(col) for r in self._rows])

    def __setitem__(self, col: str, vals: list[Any]) -> None:
        if col not in self.columns:
            self.columns.append(col)
        for r, v in zip(self._rows, vals):
            r[col] = v


def to_numeric_series(values: Any, default_len: int = 0, fillna: float | None = None) -> list[float]:
    if values is None:
        base = [float("nan")] * default_len
    elif pd is not None and hasattr(values, "index"):
        s = pd.to_numeric(values, errors="coerce")
        if fillna is not None:
            s = s.fillna(fillna)
        return [float(x) for x in s.tolist()]
    else:
        base = []
        for x in values:
            if x is None or str(x).strip() == "" or str(x).strip().lower() in ("nan", "null", "none"):
                base.append(fillna if fillna is not None else float("nan"))
            else:
                try:
                    base.append(float(str(x).strip().replace(",", ".")))
                except ValueError:
                    base.append(fillna if fillna is not None else float("nan"))
    if fillna is not None:
        return [fillna if math.isnan(v) else v for v in base]
    return base


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
    """Исправляет запятую внутри кавычек, начатых в середине незаключённого поля."""
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


def read_aquabit_csv(fpath: Path | None) -> Any:
    empty_df = pd.DataFrame() if pd is not None else _MiniDataFrame()
    if fpath is None or not fpath.exists():
        return empty_df
    try:
        if fpath.stat().st_size == 0:
            return empty_df
    except OSError:
        return empty_df

    text = _decode_file_text(fpath).lstrip("\ufeff")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return empty_df

    sample = lines[1] if len(lines) > 1 else None
    if sample is not None:
        inferred_header = repair_aquabit_header_tokens(lines[0])
        fixed_sample = _repair_inline_quoted_commas(sample, len(inferred_header))
        if fixed_sample is not None:
            sample = ','.join('x' for _ in fixed_sample)
    header_cols = repair_aquabit_header_tokens(lines[0], sample)
    if len(lines) == 1:
        return pd.DataFrame(columns=header_cols) if pd is not None else _MiniDataFrame(columns=header_cols)

    body = "\n".join(lines[1:])
    repaired = _repair_csv_body(body, len(header_cols), fpath)
    csv_text = repaired if repaired is not None else body

    if pd is not None:
        try:
            df = pd.read_csv(
                io.StringIO(csv_text), header=None, names=header_cols,
                na_values=["NAN", "NaN", "nan", "NULL", "null", ""],
                keep_default_na=True, low_memory=False,
            )
        except pd.errors.EmptyDataError:
            return pd.DataFrame(columns=header_cols)
        if len(df.columns) > 0:
            first_col = df.columns[0]
            df[first_col] = df[first_col].astype(str).str.strip()
        return df

    rows: list[dict[str, Any]] = []
    for raw_row in csv.reader(io.StringIO(csv_text)):
        if not raw_row:
            continue
        row_dict: dict[str, Any] = {}
        for idx, col_name in enumerate(header_cols):
            val = raw_row[idx].strip() if idx < len(raw_row) else ""
            if idx == 0:
                row_dict[col_name] = val
            elif val.lower() in ("", "nan", "null", "none"):
                row_dict[col_name] = None
            else:
                row_dict[col_name] = val
        rows.append(row_dict)
    return _MiniDataFrame(columns=header_cols, rows=rows)


def find_col(df: Any, *substrings: str, default_idx: int | None = None) -> str | None:
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
