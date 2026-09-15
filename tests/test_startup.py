import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from config import Settings, Source
from control import Controller
from main import main, run_supplier
from runtime import SyncService
from state import StateStore


class StartupTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = Settings(api_id=123, api_hash="test", bot_token="FAKE_TOKEN",
                                 admin_ids=(42,), state_file=str(Path(self.temp.name) / "state.json"))
        self.state = StateStore(self.settings.state_file)
        self.service = SyncService(None, self.settings, self.state)

    def tearDown(self):
        self.temp.cleanup()

    async def test_supplier_failure_keeps_controller_alive_and_answers_start(self):
        failed = asyncio.Event()
        controller = Controller(MagicMock(), self.service, [42])
        with patch("main.prepare_supplier", new=AsyncMock(side_effect=ValueError("Сессия отозвана"))), \
             patch("main.log.error", side_effect=lambda *args: failed.set()):
            task = asyncio.create_task(run_supplier(self.service, self.settings, controller))
            try:
                await asyncio.wait_for(failed.wait(), timeout=1)
                event = SimpleNamespace(is_private=True, sender_id=42, raw_text="/start", respond=AsyncMock())
                await controller.message(event)
                self.assertIn("Сессия отозвана", event.respond.call_args.args[0])
                self.assertTrue(event.respond.call_args.kwargs["buttons"])
                self.assertFalse(task.done())
            finally:
                self.service.stop_event.set()
                await asyncio.wait_for(task, timeout=1)

    async def test_supplier_can_recover_without_restarting_controller(self):
        client = SimpleNamespace(disconnect=AsyncMock())
        attempts = []

        async def prepare(service, settings, controller):
            attempts.append(1)
            if len(attempts) == 1:
                raise ConnectionError("Временный сбой")
            service.attach_client(client)
            service.ready = True
            service.startup_error = None

        async def run():
            self.assertTrue(self.service.ready)
            self.service.stop_event.set()

        self.service.run = run
        with patch("main.prepare_supplier", new=prepare):
            await asyncio.wait_for(run_supplier(self.service, self.settings, retry_seconds=0.001), timeout=1)
        self.assertEqual(len(attempts), 2)
        client.disconnect.assert_awaited_once()

    async def test_real_startup_registers_control_before_missing_session_error(self):
        # Exercises main's actual order: bot -> handlers -> supplier validation failure.
        bot_client = MagicMock()
        bot_client.start = AsyncMock()
        bot_client.get_me = AsyncMock(return_value=SimpleNamespace(username="reprice_test_bot"))
        bot_client.disconnect = AsyncMock()
        failed = asyncio.Event()
        with patch("main.Settings.from_env", return_value=self.settings), \
             patch("main.TelegramClient", return_value=bot_client), \
             patch("main.log.error", side_effect=lambda *args: failed.set()), \
             patch.object(asyncio.get_running_loop(), "add_signal_handler"):
            task = asyncio.create_task(main())
            try:
                await asyncio.wait_for(failed.wait(), timeout=1)
                handler = bot_client.add_event_handler.call_args_list[0].args[0]
                event = SimpleNamespace(is_private=True, sender_id=42, raw_text="/start", respond=AsyncMock())
                await handler(event)
                self.assertIn("SESSION_STRING", event.respond.call_args.args[0])
                self.assertTrue(event.respond.call_args.kwargs["buttons"])
                self.assertFalse(task.done())
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        bot_client.disconnect.assert_awaited_once()

    async def test_fallback_owner_is_never_granted_to_first_message_sender(self):
        controller = Controller(MagicMock(), self.service, [])
        event = SimpleNamespace(is_private=True, sender_id=99, raw_text="/start", respond=AsyncMock())
        await controller.message(event)
        self.assertEqual(controller.admins, set())
        self.assertIn("ADMIN_IDS", event.respond.call_args.args[0])
        self.assertIsNone(event.respond.call_args.kwargs["buttons"])
