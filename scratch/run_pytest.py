import site
from pathlib import Path
import sys

root = Path(__file__).resolve().parent.parent
site.addsitedir(str(root / ".venv" / "Lib" / "site-packages"))
sys.path.insert(0, str(root / "src"))

import pytest
sys.exit(pytest.main(["tests/test_real_aquabit_exports.py", "-v", "-p", "no:platformdirs"]))
