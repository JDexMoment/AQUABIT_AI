import json
from pathlib import Path

nb_path = Path("EPANET_AI_V3.ipynb")
nb = json.loads(nb_path.read_text(encoding="utf-8"))

print(f"Total cells: {len(nb.get('cells', []))}")
for idx, c in enumerate(nb.get("cells", [])):
    src = "".join(c.get("source", []))
    lines = src.strip().split("\n")
    first_line = lines[0] if lines else ""
    print(f"Cell {idx} ({c.get('cell_type')}): {first_line[:80]} ({len(lines)} lines)")
