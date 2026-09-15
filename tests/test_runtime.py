import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telethon.errors import FloodWaitError

from config import Settings, Source
from prices import Item, ParseResult
from runtime import SyncService, is_open, LoginRequired
from state import StateStore


class HoursTests(unittest.TestCase):
    def test_moscow_boundaries(self):
        settings = Settings()
        for hour, expected in [(6, False), (7, True), (16, True), (17, False)]:
            now = datetime(2026, 9, 14, hour, tzinfo=timezone.utc)
            self.assertEqual(is_open(settings, now), expected)

    def test_overnight_window(self):
        settings = Settings(timezone="UTC", open_hour=20, close_hour=6)
        self.assertTrue(is_open(settings, datetime(2026, 1, 1, 23, tzinfo=timezone.utc)))
        self.assertFalse(is_open(settings, datetime(2026, 1, 1, 12, tzinfo=timezone.utc)))


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = StateStore(Path(self.temp.name) / "state.json")
        self.settings = Settings(sources=(Source("@one"), Source("@two", mode="feed")), off_hours=False)
        self.client = SimpleNamespace(is_connected=lambda: True, connect=AsyncMock(),
                                      is_user_authorized=AsyncMock(return_value=True))
        self.service = SyncService(self.client, self.settings, self.state)
        self.service.publisher.publish = AsyncMock(return_value=0)
        self.item = Item("iPhone 17 256 Black", Decimal(60000), "RUB", "iPhone 17")
        self.state.set("sources", {
            "@one": {"status": "open", "items": [self.item.to_dict()]},
            "@two": {"status": "open", "items": []},
        })
        self.service.readers[0].fetch = AsyncMock(return_value=ParseResult([self.item], []))
        self.service.readers[1].fetch = AsyncMock(return_value=ParseResult([], [], closed=True))

    def tearDown(self):
        self.temp.cleanup()

    async def test_one_open_supplier_keeps_prices(self):
        result = await self.service.sync()
        self.assertIn("1 позиций", result)
        pages = self.service.publisher.publish.call_args.args[0]
        self.assertIn("60 000", next(iter(pages.values())))

    async def test_both_closed_hide_prices_but_keep_blocks(self):
        self.service.readers[0].fetch.return_value = ParseResult([], [], closed=True)
        await self.service.sync()
        pages = self.service.publisher.publish.call_args.args[0]
        text = next(iter(pages.values()))
        self.assertIn("Продажи закрыты", text)
        self.assertNotIn("60 000", text)

    async def test_night_hides_prices_without_requesting_suppliers(self):
        with patch("runtime.is_open", return_value=False):
            await self.service.sync()
        self.service.readers[0].fetch.assert_not_awaited()
        self.service.readers[1].fetch.assert_not_awaited()
        text = next(iter(self.service.publisher.publish.call_args.args[0].values()))
        self.assertIn("Продажи закрыты", text)

    async def test_source_failure_keeps_complete_previous_snapshot(self):
        newer = Item("iPhone 17 256 Black", Decimal(61000), "RUB", "iPhone 17")
        self.service.readers[0].fetch.return_value = ParseResult([newer], [])
        self.service.readers[1].fetch.side_effect = ConnectionError("disconnected")
        await self.service.sync()
        self.service.publisher.publish.assert_not_awaited()
        self.assertEqual(self.service.cached_items()[0].price, Decimal(60000))

    async def test_pause_persists_and_skips_periodic_sync(self):
        await self.service.pause()
        await self.service.sync()
        self.service.readers[0].fetch.assert_not_awaited()
        self.assertFalse(StateStore(self.state.path).get("options")["enabled"])

    async def test_flood_wait_blocks_manual_retry(self):
        self.service.readers[0].fetch.side_effect = FloodWaitError(request=None, capture=60)
        await self.service.sync()
        await self.service.sync(force=True)
        self.assertEqual(self.service.readers[0].fetch.await_count, 1)

    async def test_revoked_session_never_prompts_interactively(self):
        self.client.is_user_authorized.return_value = False
        with self.assertRaises(LoginRequired):
            await self.service.connect()

    async def test_disconnected_client_reconnects(self):
        self.client.is_connected = lambda: False
        await self.service.connect()
        self.client.connect.assert_awaited_once()

    async def test_manual_sync_during_initialization_does_not_use_client(self):
        self.service.ready = False
        self.service.startup_error = "SESSION_STRING недействительна"
        self.assertIn("SESSION_STRING", await self.service.sync(force=True))
        self.service.readers[0].fetch.assert_not_awaited()
        self.client.is_user_authorized.assert_not_awaited()

    async def test_format_change_during_initialization_is_reported(self):
        self.service.ready = False
        self.service.startup_error = "SESSION_STRING недействительна"
        with self.assertRaisesRegex(RuntimeError, "SESSION_STRING"):
            await self.service.refresh_format()
        self.service.publisher.publish.assert_not_awaited()
