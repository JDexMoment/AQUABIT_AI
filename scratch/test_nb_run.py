import json
from pathlib import Path

nb = json.loads(Path("EPANET_AI_V3.ipynb").read_text(encoding="utf-8"))
for idx, c in enumerate(nb["cells"]):
    if c["cell_type"] == "code":
        code = "".join(c["source"])
        # filter out IPython magics for compile test
        clean_code = "\n".join(l for l in code.split("\n") if not l.startswith("%"))
        compile(clean_code, f"cell_{idx}", "exec")
        print(f"Cell {idx} compiled cleanly.")

print("Весь ноутбук EPANET_AI_V3.ipynb синтаксически корректен!")
