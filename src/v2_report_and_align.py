"""Совместимый интерфейс отчётов. Реализация находится в reporting/."""
from reporting.facts_report import generate_reference_pure_facts_report
from reporting.markdown import _markdown_to_clean_html
from reporting.html_report import render_standalone_html_report
