"""Преобразование Markdown для автономного HTML-отчёта (без вложенных кнопок и ошибок экранирования)."""
from __future__ import annotations
import re
import html
from ontology.context import clean_object_id

_OBJ_ID_TOKEN_RE = r"(?:(?:node|pipe|pump|valve):)?(?:node_|pipe_|pump_|valve_|source_|tank_|Ju|Pi|Pu|Va|Re|Ta|[ceputvk])?\d{2,}"

_PREFIX_TO_KIND = {
    "node": "node",
    "node_": "node",
    "ju": "node",
    "re": "node",
    "ta": "node",
    "pipe": "pipe",
    "pipe_": "pipe",
    "pi": "pipe",
    "pump": "pump",
    "pump_": "pump",
    "pu": "pump",
    "valve": "valve",
    "valve_": "valve",
    "va": "valve",
}


def _infer_object_kind(raw_token: str, context_text: str = "") -> str:
    low = (raw_token or "").strip().lower()
    m_exp = re.match(r"^(node|pipe|pump|valve):", low)
    if m_exp:
        return m_exp.group(1)
    for pref, kind in _PREFIX_TO_KIND.items():
        if low.startswith(pref):
            return kind
    ctx = (context_text or "").lower()
    if any(w in ctx for w in ("насос", "агрегат", "скважин", "внс", "пнс")):
        return "pump"
    if any(w in ctx for w in ("труб", "участ", "водовод", "магистрал", "перемычк", "отвод")):
        return "pipe"
    if any(w in ctx for w in ("клапан", "задвижк", "prv", "psv", "fcv", "регулятор")):
        return "valve"
    return "node"


def _strip_kind_and_clean(raw_token: str) -> str:
    token = re.sub(r"^(?:node|pipe|pump|valve):", "", (raw_token or "").strip(), flags=re.IGNORECASE)
    return clean_object_id(token)


def _preprocess_raw_html_leaks(md: str) -> str:
    """Нормализует случайно утекшие из LLM сырые теги <button> и <br> до экранирования HTML."""
    text = md or ""

    def _unwrap_btn(m: re.Match[str]) -> str:
        attrs = m.group(1) or ""
        inner = re.sub(r"<[^>]+>", "", m.group(2) or "").strip()
        m_scroll = re.search(
            r"scrollToTableRow\(\s*['\"](node|pipe|pump|valve)['\"]\s*,\s*['\"]#?([A-Za-z_]*\d+)['\"]\s*\)",
            attrs,
            flags=re.IGNORECASE,
        )
        if m_scroll:
            kind = m_scroll.group(1).lower()
            cid = clean_object_id(m_scroll.group(2))
            return f"[{inner or '#' + cid}](table:{kind}:{cid})"
        m_tbl = re.search(r"data-table-id=['\"]#?([A-Za-z_]*\d+)['\"]", attrs, flags=re.IGNORECASE)
        if m_tbl:
            cid = clean_object_id(m_tbl.group(1))
            return f"[{inner or '#' + cid}](table:{cid})"
        m_map = re.search(r"data-map-id=['\"]#?([A-Za-z_]*\d+)['\"]", attrs, flags=re.IGNORECASE)
        if m_map:
            cid = clean_object_id(m_map.group(1))
            return f"[{inner or '#' + cid}](map:{cid})"
        return inner

    text = re.sub(r"<button\b([^>]*)>(.*?)</button>", _unwrap_btn, text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    return text


def _markdown_to_clean_html(md: str) -> str:
    lines = _preprocess_raw_html_leaks(md).splitlines()
    out = []
    in_ul = False
    in_ol = False

    _MAP_CONTEXT_RE = re.compile(
        r"(на\s+карт|располож|местополож|географ|район|схем[еуа]\s+сет|рядом\s+с|вблизи|маршрут|ветк[иаеу]|контур)",
        flags=re.IGNORECASE,
    )

    def close_lists():
        nonlocal in_ul, in_ol
        if in_ul:
            out.append("</ul>")
            in_ul = False
        if in_ol:
            out.append("</ol>")
            in_ol = False

    def format_inline(text_line: str) -> str:
        # Убираем обратные кавычки вокруг интерактивных markdown-ссылок: `[узел #123](table:123)`
        cleaned_line = re.sub(
            r"`(\[[^\]]+\]\((?:table|map|filter):[^)]+\))`",
            r"\1",
            text_line,
        )
        s_escaped = html.escape(cleaned_line)

        # 1) Ссылки автофильтрации таблиц: [Текст](filter:nodes:data_error)
        s_escaped = re.sub(
            r"\[([^\]]+)\]\(filter:([a-zA-Z0-9_]+):([a-zA-Z0-9_]+)\)",
            lambda m: (
                f'<button type="button" class="filter-action-link" '
                f'onclick="applyQuickFilterFromAI(\'{m.group(2)}\', \'{m.group(3)}\')" '
                f'title="Применить фильтр и сортировку в таблице">{m.group(1)} ↗</button>'
            ),
            s_escaped,
        )

        # 2) Явные ссылки на таблицу: [Текст](table:3466703), [Текст](table:node:3466703), [Текст](table:node_3466703)
        def _make_table_btn(m: re.Match[str]) -> str:
            label = m.group(1)
            raw_tok = m.group(2)
            cid = _strip_kind_and_clean(raw_tok)
            win = s_escaped[max(0, m.start() - 40): min(len(s_escaped), m.end() + 20)]
            kind = _infer_object_kind(raw_tok, label + " " + win)
            return (
                f'<button type="button" class="object-link" data-table-id="{cid}" data-table-kind="{kind}" '
                f"onclick=\"scrollToTableRow('{kind}', '{cid}')\" "
                f'title="Открыть объект #{cid} в таблице">{label}</button>'
            )

        s_escaped = re.sub(
            rf"\[([^\]]+)\]\(table:#?({_OBJ_ID_TOKEN_RE})\)",
            _make_table_btn,
            s_escaped,
            flags=re.IGNORECASE,
        )

        # 3) Явные ссылки на карту: [Текст](map:3466703) или [Текст](map:node_3466703)
        s_escaped = re.sub(
            rf"\[([^\]]+)\]\(map:#?({_OBJ_ID_TOKEN_RE})\)",
            lambda m: (
                f'<button type="button" class="object-link object-link-map" data-map-id="{_strip_kind_and_clean(m.group(2))}" '
                f'title="Показать расположение #{_strip_kind_and_clean(m.group(2))} на карте">{m.group(1)}</button>'
            ),
            s_escaped,
            flags=re.IGNORECASE,
        )
        s_escaped = re.sub(r"\*\*(.*?)\*\*", r"<strong>\1</strong>", s_escaped)
        s_escaped = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", s_escaped)
        s_escaped = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", s_escaped)

        # Контекстный роутинг ID: по умолчанию -> таблица, если рядом контекст местоположения/карты -> карта
        def _make_ctx_btn(raw_id: str, full_str: str, start_idx: int, end_idx: int) -> str:
            clean = _strip_kind_and_clean(raw_id)
            window = full_str[max(0, start_idx - 70): min(len(full_str), end_idx + 70)]
            if _MAP_CONTEXT_RE.search(window):
                return (
                    f'<button type="button" class="object-link object-link-map" data-map-id="{clean}" '
                    f'title="Показать расположение #{clean} на карте">#{clean}</button>'
                )
            kind = _infer_object_kind(raw_id, window)
            return (
                f'<button type="button" class="object-link" data-table-id="{clean}" data-table-kind="{kind}" '
                f"onclick=\"scrollToTableRow('{kind}', '{clean}')\" "
                f'title="Открыть объект #{clean} в таблице (из таблицы — на карту)">#{clean}</button>'
            )

        s_escaped = re.sub(
            rf"<code>#?({_OBJ_ID_TOKEN_RE})</code>",
            lambda m: _make_ctx_btn(m.group(1), s_escaped, m.start(), m.end()),
            s_escaped,
            flags=re.IGNORECASE,
        )
        # Преобразуем голые #ID ТОЛЬКО вне уже созданных <button>...</button>, <code>...</code> и HTML-тегов
        parts = re.split(r"(<button\b[^>]*>.*?</button>|<code\b[^>]*>.*?</code>|<[^>]+>)", s_escaped, flags=re.IGNORECASE)
        for idx_p in range(0, len(parts), 2):
            seg = parts[idx_p]
            if "#" in seg:
                parts[idx_p] = re.sub(
                    rf"(?<![A-Za-z0-9_])#({_OBJ_ID_TOKEN_RE})\b",
                    lambda m, _seg=seg: _make_ctx_btn(m.group(1), _seg, m.start(), m.end()),
                    seg,
                    flags=re.IGNORECASE,
                )
        return "".join(parts)

    for line in lines:
        s = line.strip()
        if not s:
            close_lists()
            continue
        if s.startswith("# "):
            close_lists()
            out.append(f"<h1 class='ai-h1'>{format_inline(s[2:].strip())}</h1>")
        elif s.startswith("## "):
            close_lists()
            out.append(f"<h2 class='ai-h2'>{format_inline(s[3:].strip())}</h2>")
        elif s.startswith("### "):
            close_lists()
            out.append(f"<h3 class='ai-h3'>{format_inline(s[4:].strip())}</h3>")
        elif s.startswith("- ") or s.startswith("* ") or s.startswith("• "):
            if not in_ul:
                close_lists()
                out.append("<ul>")
                in_ul = True
            out.append(f"<li>{format_inline(s[2:].strip())}</li>")
        elif re.match(r"^\d+\.\s", s):
            if not in_ol:
                close_lists()
                out.append("<ol>")
                in_ol = True
            content = re.sub(r"^\d+\.\s*", "", s).strip()
            out.append(f"<li>{format_inline(content)}</li>")
        else:
            close_lists()
            out.append(f"<p>{format_inline(s)}</p>")

    close_lists()
    return "\n".join(out)
