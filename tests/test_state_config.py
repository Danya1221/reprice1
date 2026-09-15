import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from config import Settings, peer
from state import StateStore


class StateTests(unittest.TestCase):
    def test_nested_path_and_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data" / "state.json"
            store = StateStore(path)
            store.update({"options": {"enabled": False}, "message": 123})
            loaded = StateStore(path)
            self.assertFalse(loaded.get("options")["enabled"])
            self.assertEqual(loaded.get("message"), 123)
            self.assertEqual(list(path.parent.glob("state.json.*")), [])

    def test_corrupt_state_is_not_silently_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text("{broken")
            with self.assertRaises(RuntimeError):
                StateStore(path)

    def test_failed_write_preserves_memory_and_previous_file(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(Path(directory) / "state.json")
            store.set("id", 1)
            with patch("state.os.replace", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    store.set("id", 2)
            self.assertEqual(store.get("id"), 1)
            self.assertEqual(json.loads(store.path.read_text())["id"], 1)

    def test_second_worker_is_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            one = StateStore(Path(directory) / "state.json")
            two = StateStore(one.path)
            one.acquire()
            try:
                with self.assertRaises(RuntimeError):
                    two.acquire()
            finally:
                one.close()
            two.acquire()
            two.close()


class ConfigTests(unittest.TestCase):
    def test_control_configuration_can_start_without_supplier_settings(self):
        with patch.dict(os.environ, {"API_ID": "123", "API_HASH": "test"}, clear=True):
            settings = Settings.from_env(require_sync=False)
            self.assertEqual(settings.sources, ())
            with self.assertRaisesRegex(ValueError, "SESSION_STRING"):
                settings.validate()

    def test_chat_id_is_not_accepted_as_operator_id(self):
        with patch.dict(os.environ, {"API_ID": "123", "API_HASH": "test", "ADMIN_IDS": "-100123"}, clear=True):
            with self.assertRaisesRegex(ValueError, "ADMIN_IDS"):
                Settings.from_env(require_sync=False)

    def test_numeric_and_url_peer(self):
        self.assertEqual(peer("-1001234567890"), -1001234567890)
        self.assertEqual(peer("https://t.me/channel/"), "@channel")

    def test_volume_and_legacy_variable_compatibility(self):
        with patch.dict(os.environ, {
            "API_ID": "123", "API_HASH": "test", "SESSION_STRING": "test",
            "SUPPLIER_BOT": "@supplier", "TARGET_CHANNEL": "-100123",
            "RAILWAY_VOLUME_MOUNT_PATH": "/data",
            "BUTTON_PATH": "Прайс > Актуальный прайс",
        }, clear=True):
            settings = Settings.from_env()
            self.assertEqual(settings.target, -100123)
            self.assertEqual(settings.sources[0].buttons, ("Прайс", "Актуальный прайс"))
            self.assertEqual(settings.state_file, "/data/state.json")

    def test_invalid_number_has_readable_error(self):
        with patch.dict(os.environ, {"API_ID": "oops"}, clear=True):
            with self.assertRaisesRegex(ValueError, "API_ID"):
                Settings.from_env()
