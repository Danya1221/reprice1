import json
from pathlib import Path


class StateStore:
    def __init__(self, path: str):
        self.path = Path(path)
        self.data = {
            "target_message_id": None,
            "last_supplier_message_id": None,
            "last_content": None,
        }
        self.load()

    def load(self):
        if not self.path.exists():
            return

        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self.data.update(raw)
        except Exception:
            pass

    def save(self):
        self.path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value
        self.save()
