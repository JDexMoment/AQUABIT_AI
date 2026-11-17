import json
from pathlib import Path

nb = json.loads(Path("EPANET_AI_V3.ipynb").read_text(encoding="utf-8"))
cells = nb.get("cells", [])

print("=== CELL 2 ===")
print("".join(cells[2].get("source", [])))

print("\n=== CELL 3 ===")
print("".join(cells[3].get("source", [])))
