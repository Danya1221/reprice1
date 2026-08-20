import os
import subprocess
import sys

setup_mode = os.getenv("SETUP_MODE", "").strip().lower() in {"1", "true", "yes", "on"}
script = "session_web.py" if setup_mode else "main.py"
print(f"▶️ Запуск: {script}", flush=True)
raise SystemExit(subprocess.call([sys.executable, script]))
