from bs4 import BeautifulSoup
from pathlib import Path
import re

html_path = Path("reports/EPANET_AI_модель семей_verified.html")
assert html_path.exists(), "HTML report does not exist"

soup = BeautifulSoup(html_path.read_text(encoding="utf-8"), "html.parser")
html_text = html_path.read_text(encoding="utf-8")

# 1. KPI cards check
kpis = {}
for c in soup.select(".kpi-card"):
    title = c.select_one(".kpi-title").get_text(strip=True)
    val = c.select_one(".kpi-val").get_text(strip=True)
    sub = c.select_one(".kpi-sub").get_text(strip=True)
    kpis[title] = (val, sub)
    print(f"[KPI] {title}: {val} | {sub}")

assert len(kpis) == 6, f"Expected 6 KPI cards, got {len(kpis)}"
assert "— .. —" not in kpis["Свободный напор P = H − Z"][0], "Свободный напор не должен быть пустым!"
assert "0.00 км" not in kpis["Трубопроводная сеть"][0], "Трубопроводная сеть не должна быть 0.00 км!"
assert "0 /" not in kpis["Насосы и энергия"][0], "Насосы не должны быть 0 / ...!"

# 2. Canvas & Tooltips
assert 'id="chartTooltip"' in html_text, "Tooltip container missing"
assert "showChartTooltip" in html_text, "showChartTooltip helper missing"
assert "Подача Q, м³/ч" in html_text, "Q axis title missing"
assert "Напор H, м" in html_text, "H axis title missing"
assert "Свободный напор P, м" in html_text, "Pressure axis title missing"
assert "Расход, м³/ч" in html_text, "Flow axis title missing"
assert "Время суток (часы)" in html_text, "Time axis title missing"

# 3. Donut chart
assert "innerR" in html_text and "outerR" in html_text and "stagnationMeta" in html_text, "Donut chart logic missing"

# 4. node_props & pipe_props
assert "node_props" in html_text and "pipe_props" in html_text, "node_props or pipe_props missing in payload"

# 5. Multi-highlight
assert "highlightMultipleObjects" in html_text, "highlightMultipleObjects missing"
assert "multiHighlights" in html_text, "multiHighlights missing"

# 6. Check clean IDs and no emojis
assert "⚠️" not in html_text, "Emoji found!"
assert "(Google)" not in html_text, "(Google) found!"
assert "(Хейзен-Вильямс)" not in html_text, "(Хейзен-Вильямс) found!"

print("\nВСЕ ТРЕБОВАНИЯ ПОЛЬЗОВАТЕЛЯ ПОЛНОСТЬЮ ПОДТВЕРЖДЕНЫ!")
