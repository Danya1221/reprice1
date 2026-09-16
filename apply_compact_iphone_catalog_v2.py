from pathlib import Path
import runpy

# Apply the actual compact-catalog migration first.
runpy.run_path("apply_compact_iphone_catalog.py", run_name="__main__")

p = Path("tests/test_catalog.py")
t = p.read_text(encoding="utf-8")n