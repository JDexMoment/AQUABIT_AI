import json
from pathlib import Path

nb = json.loads(Path("EPANET_AI_V3.ipynb").read_text(encoding="utf-8"))
for i, cell in enumerate(nb["cells"]):
    print(f"\n{'='*20} CELL {i} ({cell['cell_type']}) {'='*20}")
    for line_idx, line in enumerate(cell["source"]):
        print(f"{line_idx+1:3d}: {line}", end="")
