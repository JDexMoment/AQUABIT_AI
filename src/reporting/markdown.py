"""Преобразование Markdown для автономного HTML-отчёта."""
from __future__ import annotations
import re
import html
from ontology.context import clean_object_id

def _markdown_to_clean_html(md: str) -> str:
    lines = md.splitlines()
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
        s_escaped = html.escape(text_line)
        # 1) Ссылки автофильтрации таблиц: [Текст](filter:nodes:data_error)
        s_escaped = re.sub(
            r"\[([^\]]+)\]\(filter:([a-zA-Z0-9_]+):([a-zA-Z0-9_]+)\)",
            r'<button type="button" class="filter-action-link" onclick="applyQuickFilterFromAI(\'\2\', \'\3\')" title="Применить фильтр и сортировку в таблице">\1 ↗</button>',
            s_escaped,
        )
        # 2) Явные ссылки на таблицу: [Текст](table:3466703)
        s_escaped = re.sub(
            r"\[([^\]]+)\]\(table:#?([ceputvk]?\d+)\)",
            lambda m: f'<button type="button" class="object-link" data-table-id="{clean_object_id(m.group(2))}" title="Открыть объект #{clean_object_id(m.group(2))} в таблице">{m.group(1)}</button>',
            s_escaped,
            flags=re.IGNORECASE,
        )
        # 3) Явные ссылки на карту: [Текст](map:3466703)
        s_escaped = re.sub(
            r"\[([^\]]+)\]\(map:#?([ceputvk]?\d+)\)",
            lambda m: f'<button type="button" class="object-link object-link-map" data-map-id="{clean_object_id(m.group(2))}" title="Показать расположение #{clean_object_id(m.group(2))} на карте">{m.group(1)}</button>',
            s_escaped,
            flags=re.IGNORECASE,
        )
        s_escaped = re.sub(r"\*\*(.*?)\*\*", r"<strong>\1</strong>", s_escaped)
        s_escaped = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", s_escaped)
        s_escaped = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", s_escaped)

        # Контекстный роутинг ID: по умолчанию -> таблица, если рядом контекст местоположения/карты -> карта
        def _make_ctx_btn(raw_id: str, full_str: str, start_idx: int, end_idx: int) -> str:
            clean = clean_object_id(raw_id)
            window = full_str[max(0, start_idx - 70): min(len(full_str), end_idx + 70)]
            if _MAP_CONTEXT_RE.search(window):
                return f'<button type="button" class="object-link object-link-map" data-map-id="{clean}" title="Показать расположение #{clean} на карте">#{clean}</button>'
            return f'<button type="button" class="object-link" data-table-id="{clean}" title="Открыть объект #{clean} в таблице (из таблицы — на карту)">#{clean}</button>'

        s_escaped = re.sub(
            r"<code>#?([ceputvk]\d+|\d{2,})</code>",
            lambda m: _make_ctx_btn(m.group(1), s_escaped, m.start(), m.end()),
            s_escaped,
            flags=re.IGNORECASE,
        )
        # Также преобразуем голые #ID вне HTML-тегов и атрибутов
        parts = re.split(r"(<[^>]+>)", s_escaped)
        for idx_p in range(0, len(parts), 2):
            seg = parts[idx_p]
            if "#" in seg:
                parts[idx_p] = re.sub(
                    r"(?<![A-Za-z0-9_])#([ceputvk]\d+|\d{2,})\b",
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
