from bs4 import BeautifulSoup
from pathlib import Path

html_p = Path("reports/EPANET_AI_модель семей_verified.html")
soup = BeautifulSoup(html_p.read_text(encoding="utf-8"), "html.parser")
for c in soup.select(".kpi-card"):
    title = c.select_one(".kpi-title").get_text(strip=True)
    val = c.select_one(".kpi-val").get_text(strip=True)
    sub = c.select_one(".kpi-sub").get_text(strip=True)
    print(f"{title} | {val} | {sub}")
