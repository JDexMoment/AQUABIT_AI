import json
from pathlib import Path

nb_path = Path("EPANET_AI_V3.ipynb")
nb = json.loads(nb_path.read_text(encoding="utf-8"))

for idx, c in enumerate(nb.get("cells", [])):
    print(f"\n{'='*30} CELL {idx} ({c.get('cell_type')}) {'='*30}")
    src = "".join(c.get("source", []))
    print(src)
