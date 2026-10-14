import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telethon.errors import FloodWaitError

from config import Settings, Source
from prices import Item, ParseResult, parse_documents, physical_page_labels
from runtime import SyncService, is_open, LoginRequired, merge_lowest
from state import StateStore


class HoursTests(unittest.TestCase):
    def test_moscow_start_has_no_fixed_close(self):
        settings = Settings()
        # UTC -> Moscow (+3): 06=09 (before start), 07=10 (start),
        # 16=19 and 17=20 stay open; 23=02 next day is before the next start.
        for hour, expected in [(6, False), (7, True), (16, True), (17, True), (23, False)]:
            now = datetime(2026, 9, 14, hour, tzinfo=timezone.utc)
            self.assertEqual(is_open(settings, now), expected)


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = StateStore(Path(self.temp.name) / "state.json")
        self.settings = Settings(sources=(Source("@one"), Source("@two", mode="feed")), off_hours=False)
        self.client = SimpleNamespace(is_connected=lambda: True, connect=AsyncMock(),
                                      is_user_authorized=AsyncMock(return_value=True))
        self.service = SyncService(self.client, self.settings, self.state)
        self.service.publisher.publish = AsyncMock(return_value=0)
        self.service.publisher.hide_existing = AsyncMock(return_value=0)
        self.item = Item("iPhone 17 256 Black", Decimal(60000), "RUB", "iPhone 17")
        self.state.set("sources", {
            "@one": {"status": "open", "items": [self.item.to_dict()]},
            "@two": {"status": "open", "items": []},
        })
        self.service.readers[0].fetch = AsyncMock(return_value=ParseResult([self.item], []))
        self.service.readers[1].fetch = AsyncMock(return_value=ParseResult([], [], closed=True))

    def tearDown(self):
        self.temp.cleanup()

    async def test_one_open_supplier_after_start_keeps_prices(self):
        with patch("runtime.is_open", return_value=True):
            result = await self.service.sync()
        self.assertIn("1 позиций", result)
        pages = self.service.publisher.publish.call_args.args[0]
        self.assertIn("60 000", next(iter(pages.values())))

    async def test_before_start_one_open_supplier_hides_prices(self):
        with patch("runtime.is_open", return_value=False):
            result = await self.service.sync()
        self.assertIn("ждём открытия обоих", result)
        self.service.publisher.hide_existing.assert_awaited_once()
        self.service.readers[0].fetch.assert_awaited_once()
        self.service.readers[1].fetch.assert_awaited_once()

    async def test_before_start_both_open_publishes_early(self):
        second = Item("Dyson HS08", Decimal(40000), "RUB", "Dyson")
        self.service.readers[1].fetch.return_value = ParseResult([second], [])
        with patch("runtime.is_open", return_value=False):
            result = await self.service.sync()
        self.assertIn("2 позиций", result)
        self.service.publisher.publish.assert_awaited_once()

    async def test_both_closed_hide_prices_but_keep_blocks(self):
        self.service.readers[0].fetch.return_value = ParseResult([], [], closed=True)
        await self.service.sync()
        self.service.publisher.hide_existing.assert_not_awaited()
        pages = self.service.publisher.publish.call_args.args[0]
        text = next(iter(pages.values()))
        self.assertIn("Продажи закрыты", text)
        self.assertNotIn("60 000", text)

    async def test_source_failure_uses_fresh_open_source_after_start(self):
        newer = Item("iPhone 17 256 Black", Decimal(61000), "RUB", "iPhone 17")
        self.service.readers[0].fetch.return_value = ParseResult([newer], [])
        self.service.readers[1].fetch.side_effect = ConnectionError("disconnected")
        with patch("runtime.is_open", return_value=True):
            result = await self.service.sync()
        self.assertIn("1 позиций", result)
        pages = self.service.publisher.publish.call_args.args[0]
        self.assertIn("61 000", next(iter(pages.values())))

    async def test_lower_purchase_price_wins_before_markup(self):
        expensive = Item("iPhone 17 256 Black 🇺🇸", Decimal(61000), "RUB", "iPhone 17")
        cheap = Item("iPhone 17 256 Black 🇺🇸", Decimal(59000), "RUB", "iPhone 17")
        india = Item("iPhone 17 256 Black 🇮🇳", Decimal(58000), "RUB", "iPhone 17")
        merged = merge_lowest([[expensive, india], [cheap]])
        self.assertEqual(len(merged), 2)
        usa = next(i for i in merged if "🇺🇸" in i.title)
        self.assertEqual(usa.price, Decimal(59000))

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


    async def test_render_guard_never_publishes_a_partial_price(self):
        with patch("runtime.render_blocks", return_value={"bad:0": "<b>Пусто</b>"}):
            with self.assertRaisesRegex(RuntimeError, "рендер потерял позиции"):
                await self.service.render(items=[self.item])
        self.service.publisher.publish.assert_not_awaited()

    async def test_new_pages_stay_above_saved_order_after_refresh_and_restart(self):
        self.service.set_option("physical_order", ["Dyson", "iPhone 17", "Apple"])
        initial = parse_documents([
            "iPhone 17 256 Black — 60000\nDyson HS08 — 40000\nAirPods 4 — 10000"
        ]).items
        await self.service.render(items=initial)
        added = parse_documents(["iPhone 18 256 Blue — 150000"]).items + initial
        await self.service.render(items=added)
        expected = ["iPhone 18", "Dyson", "iPhone 17", "Apple"]
        self.assertEqual(physical_page_labels(self.service.publisher.publish.call_args.args[0]), expected)
        self.assertEqual(self.service.options()["physical_order"], expected)

        state = StateStore(self.state.path)
        restarted = SyncService(self.client, self.settings, state)
        restarted.publisher.publish = AsyncMock(return_value=0)
        await restarted.render(items=list(reversed(added)))
        self.assertEqual(physical_page_labels(restarted.publisher.publish.call_args.args[0]), expected)
        self.assertEqual(restarted.options()["physical_order"], expected)

        remaining = [item for item in added if item.block != "iPhone 17"]
        await restarted.render(items=remaining)
        self.assertEqual(restarted.options()["physical_order"], expected)
        await restarted.render(items=added)
        self.assertEqual(physical_page_labels(restarted.publisher.publish.call_args.args[0]), expected)

        restarted.set_option("disabled_blocks", ["Dyson"])
        await restarted.render(items=added)
        self.assertEqual(restarted.options()["physical_order"], expected)
        restarted.set_option("disabled_blocks", [])
        await restarted.render(items=added)
        self.assertEqual(physical_page_labels(restarted.publisher.publish.call_args.args[0]), expected)

    async def test_upgrade_adopts_last_published_order_before_adding_new_page(self):
        self.state.set("published", {"binding": self.service.publisher.binding(), "messages": {
            "iphone:0": {"id": 20, "content": "<b>iPhone 17</b>"},
            "dyson:0": {"id": 10, "content": "<b>Dyson</b>"},
        }})
        items = parse_documents([
            "iPhone 17 256 Black — 60000\nDyson HS08 — 40000\niPhone 18 256 Blue — 150000"
        ]).items
        await self.service.render(items=items)
        expected = ["iPhone 18", "Dyson", "iPhone 17"]
        self.assertEqual(self.service.options()["physical_order"], expected)
        self.assertEqual(physical_page_labels(self.service.publisher.publish.call_args.args[0]), expected)

    async def test_single_model_heading_does_not_reset_generation_position(self):
        self.service.set_option("physical_order", ["Dyson", "iPhone 17", "Apple"])
        items = parse_documents([
            "iPhone 17 Pro 256 Blue — 90000\nDyson HS08 — 40000\nAirPods 4 — 10000"
        ]).items
        await self.service.render(items=items)
        self.assertEqual(physical_page_labels(self.service.publisher.publish.call_args.args[0]),
                         ["Dyson", "iPhone 17 Pro", "Apple"])

    async def test_packed_heading_change_keeps_its_place_among_existing_pages(self):
        items = parse_documents([
            "iPhone 17 256 Black — 60000\nSamsung S26 12/256 Black — 65000\n"
            "Xiaomi 15 12/256 White — 48000\nRealme GT 7 12/256 Black — 42000"
        ]).items
        await self.service.render(items=items)
        labels = physical_page_labels(self.service.publisher.publish.call_args.args[0])
        packed = next(label for label in labels if "•" in label)
        self.service.set_option("physical_order", [packed, "Samsung", "iPhone 17"])
        added = items + parse_documents(["Vivo V70 12/256 Grey — 46000"]).items
        await self.service.render(items=added)
        new_labels = physical_page_labels(self.service.publisher.publish.call_args.args[0])
        self.assertIn("Vivo", new_labels[0])
        self.assertEqual(new_labels[1:], ["Samsung", "iPhone 17"])
        self.assertEqual(len(self.service.options()["physical_order"]), 3)

    async def test_explicit_reset_does_not_adopt_old_published_order(self):
        self.state.set("published", {"binding": self.service.publisher.binding(), "messages": {
            "dyson:0": {"id": 10, "content": "<b>Dyson</b>"},
            "iphone:0": {"id": 20, "content": "<b>iPhone 17</b>"},
        }})
        self.service.set_option("physical_order", [])
        items = parse_documents(["Dyson HS08 — 40000\niPhone 17 256 Black — 60000"]).items
        await self.service.render(items=items)
        self.assertEqual(physical_page_labels(self.service.publisher.publish.call_args.args[0]),
                         ["iPhone 17", "Dyson"])

    async def test_closed_refresh_does_not_forget_overflow_order(self):
        previous = ["Apple · 2", "Dyson", "Apple", "iPhone 17"]
        self.service.set_option("physical_order", previous)
        items = parse_documents(["AirPods 4 — 10000\nDyson HS08 — 40000"]).items
        await self.service.render(closed=True, items=items)
        self.assertEqual(self.service.options()["physical_order"], previous)


    async def test_format_change_during_initialization_is_reported(self):
        self.service.ready = False
        self.service.startup_error = "SESSION_STRING недействительна"
        with self.assertRaisesRegex(RuntimeError, "SESSION_STRING"):
            await self.service.refresh_format()
        self.service.publisher.publish.assert_not_awaited()
