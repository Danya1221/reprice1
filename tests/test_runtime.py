import asyncio
import re
import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telethon.errors import FloodWaitError

from config import Settings, Source
from control_catalog import CatalogController
from control_botapi import BotAPIController, moscow_time
from prices import Item, ParseResult, parse_documents, physical_page_labels
from runtime import SyncService, is_open, LoginRequired, merge_lowest
from state import StateStore


class HoursTests(unittest.TestCase):
    def test_source_time_is_converted_to_moscow(self):
        self.assertEqual(moscow_time("2026-11-11T15:44:23+00:00"), "11.11.2026 18:44 МСК")
        self.assertEqual(moscow_time(0), "01.01.1970 03:00 МСК")
        self.assertEqual(moscow_time("2026-11-11T15:44:23"), "неизвестно")
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

    async def test_published_price_source_and_search_use_successful_publication(self):
        with patch("runtime.is_open", return_value=True):
            await self.service.sync()
        snapshot = self.state.get("published_price_sources")
        row = next(row for row in snapshot["rows"] if "iPhone 17" in row["title"])
        self.assertIn("60 000", row["public"])
        self.assertEqual(row["sources"][0]["source"], self.settings.sources[0].label + " · аккаунт 1")
        controller = BotAPIController("token", self.service, [42])
        controller.send = AsyncMock()
        controller.answer_callback = AsyncMock()
        await controller.handle_callback({"id": "cb", "from": {"id": 42}, "data": "price_source",
                                          "message": {"chat": {"id": 42, "type": "private"}}})
        await controller.handle_message({"from": {"id": 42}, "chat": {"id": 42, "type": "private"},
                                         "text": "iPhone 17 256 Black"})
        response = controller.send.await_args.args[1]
        self.assertIn("60 000", response)
        self.assertIn(self.settings.sources[0].label, response)

    async def test_manual_hide_survives_sync_restart_and_restore_requires_fresh_sync(self):
        await self.service.set_availability_hidden(True)
        self.assertTrue(self.service.options()["availability_hidden"])
        self.service.publisher.hide_existing.assert_awaited_once()
        self.service.publisher.publish.assert_not_awaited()
        with patch("runtime.is_open", return_value=True):
            result = await self.service.sync(force=True)
        self.assertIn("Наличие скрыто вручную", result)
        self.service.publisher.publish.assert_not_awaited()
        self.assertTrue(StateStore(self.state.path).get("options")["availability_hidden"])
        await self.service.set_availability_hidden(False)
        self.service.publisher.publish.assert_not_awaited()
        with patch("runtime.is_open", return_value=True):
            await self.service.sync(force=True)
        pages = self.service.publisher.publish.await_args.args[0]
        self.assertIn("60 000", "\n".join(pages.values()))

    async def test_manual_hide_reopens_next_morning_and_fetches_fresh_prices(self):
        evening = datetime(2026, 11, 8, 18, tzinfo=timezone.utc)
        await self.service.set_availability_hidden(True, now=evening)
        self.assertEqual(self.service.options()["availability_reopen_at"], "2026-11-09T07:00:00+00:00")
        restored = SyncService(self.client, self.settings, StateStore(self.state.path))
        self.assertFalse(restored._auto_enable_availability(datetime(2026, 11, 9, 6, 59, tzinfo=timezone.utc)))
        self.assertTrue(restored._auto_enable_availability(datetime(2026, 11, 9, 7, tzinfo=timezone.utc)))
        self.assertFalse(restored.options()["availability_hidden"])
        self.assertEqual(restored.last_attempt, 0)
        self.service.publisher.publish.assert_not_awaited()

    async def test_hiding_before_opening_reopens_the_same_morning(self):
        await self.service.set_availability_hidden(True, now=datetime(2026, 11, 9, 5, tzinfo=timezone.utc))
        self.assertEqual(self.service.options()["availability_reopen_at"], "2026-11-09T07:00:00+00:00")

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

    async def test_closed_bot_and_empty_feed_hide_the_price(self):
        self.service.readers[0].fetch.return_value = ParseResult([], [], closed=True)
        self.service.readers[1].fetch.return_value = ParseResult([], [])
        with patch("runtime.is_open", return_value=True):
            result = await self.service.sync()
        self.assertIn("цены скрыты", result)
        self.assertEqual(self.state.get("sources")["@two"]["status"], "empty")
        content = "\n".join(self.service.publisher.publish.await_args.args[0].values())
        self.assertNotIn("60 000", content)

    async def test_failed_feed_is_not_mistaken_for_an_empty_feed(self):
        self.service.readers[0].fetch.return_value = ParseResult([], [], closed=True)
        self.service.readers[1].fetch.side_effect = ConnectionError("offline")
        with patch("runtime.is_open", return_value=True):
            result = await self.service.sync()
        self.assertIn("Прайс сохранён", result)
        self.service.publisher.hide_existing.assert_not_awaited()
        self.service.publisher.publish.assert_not_awaited()

    async def test_three_distinct_closed_suppliers_hide_all_even_with_fresh_supernova(self):
        settings = Settings(sources=(Source("@one"), Source("@two"), Source("@three"), Source("@four")), off_hours=False)
        service = SyncService(self.client, settings, self.state)
        service.publisher.publish = AsyncMock(return_value=0)
        service.publisher.hide_existing = AsyncMock(return_value=0)
        for reader in service.readers[:3]:
            reader.fetch = AsyncMock(return_value=ParseResult([], [], closed=True))
        service.readers[3].fetch = AsyncMock(return_value=ParseResult([self.item], []))
        service.supernova_items = lambda now=None: [self.item]
        with patch("runtime.is_open", return_value=True):
            result = await service.sync()
        self.assertIn("Три поставщика закрыты", result)
        content = "\n".join(service.publisher.publish.await_args.args[0].values())
        self.assertIn("Продажи закрыты", content)
        self.assertNotIn("60 000", content)
        service.publisher.publish.reset_mock()
        await service.refresh_format()
        content = "\n".join(service.publisher.publish.await_args.args[0].values())
        self.assertIn("Продажи закрыты", content)
        self.assertNotIn("60 000", content)
        service.readers[0].fetch = AsyncMock(return_value=ParseResult([self.item], []))
        with patch("runtime.is_open", return_value=True):
            result = await service.sync()
        self.assertIn("Прайс обновлён", result)
        content = "\n".join(service.publisher.publish.await_args.args[0].values())
        self.assertIn("60 000", content)

    async def test_three_suppliers_rule_does_not_count_errors_as_closed(self):
        settings = Settings(sources=(Source("@one"), Source("@two"), Source("@three")), off_hours=False)
        service = SyncService(self.client, settings, self.state)
        service.publisher.publish = AsyncMock(return_value=0)
        for reader in service.readers[:2]:
            reader.fetch = AsyncMock(return_value=ParseResult([], [], closed=True))
        service.readers[2].fetch = AsyncMock(side_effect=ConnectionError("offline"))
        service.supernova_items = lambda now=None: [self.item]
        with patch("runtime.is_open", return_value=True):
            result = await service.sync()
        self.assertNotIn("Три поставщика закрыты", result)
        self.assertEqual(service._cached_source_statuses(service.state.get("sources")), ["closed", "closed", "error"])

    async def test_source_failure_uses_fresh_open_source_after_start(self):
        newer = Item("iPhone 17 256 Black", Decimal(61000), "RUB", "iPhone 17")
        self.service.readers[0].fetch.return_value = ParseResult([newer], [])
        self.service.readers[1].fetch.side_effect = ConnectionError("disconnected")
        with patch("runtime.is_open", return_value=True):
            result = await self.service.sync()
        self.assertIn("1 позиций", result)
        pages = self.service.publisher.publish.call_args.args[0]
        self.assertIn("61 000", next(iter(pages.values())))

    async def test_timed_out_supplier_preserves_published_price(self):
        async def stuck():
            await asyncio.Event().wait()
        self.service.readers[1].fetch.side_effect = stuck
        with patch("runtime.MAX_SOURCE_SECONDS", 0.01), patch("runtime.is_open", return_value=True):
            result = await self.service.sync()
        self.assertIn("Последний опубликованный прайс сохранён", result)
        self.service.publisher.publish.assert_not_awaited()
        self.assertFalse(self.service.busy)

    async def test_same_iphone_sim_spelling_merges_but_distinct_variants_do_not(self):
        rows = parse_documents(["\n".join([
            "iPhone 17 256 Black 1Sim+eSim 🇪🇺 — 61000",
            "iPhone 17 256 Black SIM + eSIM 🇪🇺 — 59000",
            "iPhone 17 256 Black eSIM 🇪🇺 — 58000",
            "iPhone 17 256 Black SIM + eSIM 🇺🇸 — 57000",
            "iPhone 17 512 Black SIM + eSIM 🇪🇺 — 70000",
            "iPhone 17 256 Black SIM + eSIM Актив 🇪🇺 — 56000",
        ])]).items
        merged = merge_lowest([rows])
        self.assertEqual(len(merged), 4)
        self.assertEqual(sorted(item.price for item in merged),
                         list(map(Decimal, (56000, 57000, 58000, 70000))))

    async def test_all_ten_user_iphone_17_color_storage_pairs_keep_lowest_price(self):
        pairs = [
            (256, "Black", 75200, 73200), (256, "Blue", 75000, 73000),
            (256, "Lavender", 75500, 73600), (256, "Sage", 75100, 73000),
            (256, "White", 75300, 73500), (512, "Black", 91700, 90000),
            (512, "Blue", 89800, 88500), (512, "Lavender", 95000, 93900),
            (512, "Sage", 93400, 91500), (512, "White", 90800, 89600),
        ]
        first = [f"Apple iPhone 17 {storage}GB SIM+eSIM {color} — {high}" for storage, color, high, low in pairs]
        second = [f"iPhone 17 {storage} {color} (1Sim+eSim) — {low}" for storage, color, high, low in pairs]
        merged = merge_lowest([parse_documents(first).items, parse_documents(second).items])
        self.assertEqual(len(merged), len(pairs))
        for storage, color, high, low in pairs:
            matching = [item for item in merged if f"{storage} {color}" in item.title]
            self.assertEqual(len(matching), 1)
            self.assertEqual(matching[0].price, Decimal(low))

    async def test_iphone_air_regions_with_same_variant_keep_lowest_offer(self):
        rows = parse_documents(["\n".join([
            "Apple iPhone 17 Air 256GB eSIM White — 77 200",
            "iPhone 17 Air 256 White 🇨🇳 — 82 300",
            "iPhone 17 Air 256 Gold 🇨🇳 — 81 200",
            "iPhone 17 Air 256 Gold (eSim) — 74 300",
            "iPhone 17 Air 256 Gold (eSim) ASIS — 65 000",
            "iPhone 17 Air 512 Gold (eSim) — 79 000",
        ])]).items
        merged = merge_lowest([rows])
        self.assertEqual(sorted(item.price for item in merged),
                         list(map(Decimal, (65000, 74300, 77200, 79000))))
        count, _ = await self.service.render(items=merged)
        content = "\n".join(self.service.publisher.publish.await_args.args[0].values())
        self.assertEqual(count, 4)
        self.assertNotIn("82 300", content)
        self.assertNotIn("81 200", content)
        self.assertIn("74 300", content)
        self.assertIn("77 200", content)

    async def test_user_examples_publish_one_cheapest_row_and_no_lego(self):
        rows = parse_documents(["\n".join([
            "Apple iPhone 17 256GB SIM+eSIM Black — 75 200",
            "iPhone 17 256 Black (1Sim+eSim) — 73 200",
            "Samsung S26 12/256GB () Cobalt Violet 🇰🇿 — 58 500",
            "Galaxy S26 12/256 Cobalt Violet 🇰🇿 — 58 300",
            "Apple MacBook Neo 13 A18 Pro 8/512GB Silver MHFC4 — 69 000",
            "Neo 13 MHFC4 Silver (A18 Pro 8/512) — 67 500",
            "Realme 16 Pro 5G 8/256 Бежевый 🇷🇺 — 29 500",
            "Realme 16 Pro 5G 8/256GB Beige 🇷🇺 — 30 300",
            "Sony WH-1000 XM6 (Black) — 25 400",
            "Sony WH-1000XM6 Blaсk — 26 800",
            "LEGO Pokemon Eevee (72151) — 6 000",
        ])]).items
        count, _ = await self.service.render(items=merge_lowest([rows]))
        content = "\n".join(self.service.publisher.publish.await_args.args[0].values())
        self.assertEqual(count, 5)
        for price in ("73 200", "58 300", "67 500", "29 500", "25 400"):
            self.assertIn(price, content)
        for price in ("75 200", "58 500", "69 000", "30 300", "26 800"):
            self.assertNotIn(price, content)
        self.assertNotIn("LEGO", content)

    async def test_user_iphone_pairs_merge_without_mixing_versions(self):
        rows = parse_documents(["\n".join([
            "Apple iPhone 17 256GB SIM+eSIM Black — 75 200",
            "iPhone 17 256 Black (1Sim+eSim) — 73 200",
            "Apple iPhone 17 Pro Max 1TB SIM+eSIM Orange — 157 100",
            "iPhone 17 Pro Max 1TB Orange (1Sim+eSim) — 157 200",
            "Apple iPhone 17e 512GB eSIM White — 67 400",
            "iPhone 17e 512 White (eSim) — 67 000",
            "Apple iPhone 17 Air 256GB eSIM Black — 78 700",
            "iPhone 17 Air 256 Black (eSim) — 75 000",
            "iPhone 17 256 Black (1Sim+eSim) 🇮🇳 — 62 000",
            "iPhone 17 256 Black (eSim) — 70 000",
            "iPhone 17 512 Black (1Sim+eSim) — 90 000",
            "iPhone 17 256 Black (1Sim+eSim) ASIS — 65 000",
        ])]).items
        merged = merge_lowest([rows])
        self.assertEqual(len(merged), 7)
        self.assertEqual(sorted(item.price for item in merged),
                         list(map(Decimal, (62000, 65000, 67000, 70000, 75000, 90000, 157100))))

    async def test_iphone_18_same_sim_memory_and_colour_selects_cheapest(self):
        rows = parse_documents(["\n".join([
            "iPhone 18 Pro Max 512 Black (1Sim+eSim) 🇭🇰 — 142700",
            "Apple iPhone 18 Pro Max 512GB SIM+eSIM Black 🇪🇺 — 143500",
            "iPhone 18 Pro Max 512GB Black SIM — 144000",
            "iPhone 18 Pro Max 1TB Burgundy SIM+eSIM 🇭🇰 — 195500",
            "iPhone 18 Pro Max 1TB Burgundy SIM+eSIM 🇪🇺 — 194500",
            "iPhone 18 Pro 256 Black eSIM 🇯🇵 — 106200",
            "Apple iPhone 18 Pro 256GB eSIM Black 🇺🇸 — 106000",
            "iPhone 18 Pro 256 Black 1Sim+eSim 🇭🇰 — 117700",
            "iPhone 18 Pro 256 Black ACTIVE eSIM 🇺🇸 — 104500",
            "iPhone 18 Pro 256 Black eSIM ASIS 🇺🇸 — 100000",
            "iPhone 18 Pro 256 Black eSIM Уценка 🇺🇸 — 99000",
        ])]).items
        merged = merge_lowest([rows])
        self.assertEqual(sorted(item.price for item in merged),
                         list(map(Decimal, (99000, 100000, 104500, 106000, 117700, 142700, 194500))))

    async def test_user_samsung_pairs_merge_only_with_matching_specs_and_region(self):
        rows = parse_documents(["\n".join([
            "Samsung S26 12/256GB () Cobalt Violet 🇰🇿 — 58 500",
            "Galaxy S26 12/256 Cobalt Violet 🇰🇿 — 58 300",
            "Samsung S26 Ultra 12/512GB () Sky Blue 🇰🇿 — 92 800",
            "Galaxy S26 Ultra 12/512 Sky Blue 🇰🇿 — 91 300",
            "Samsung S26+ 12/512GB () Sky Blue 🇰🇿 — 77 000",
            "Galaxy S26+ 12/512 Sky Blue 🇰🇿 — 76 000",
            "Galaxy S26 Ultra 12/512 Sky Blue 🇨🇱 — 91 300",
            "Galaxy S26 Ultra 12/256 Sky Blue 🇰🇿 — 76 000",
            "Galaxy S26 Ultra 12/512 Sky Blue Уценка 🇰🇿 — 80 000",
        ])]).items
        merged = merge_lowest([rows])
        self.assertEqual(len(merged), 6)
        self.assertEqual(sorted(item.price for item in merged),
                         list(map(Decimal, (58300, 76000, 76000, 80000, 91300, 91300))))

    async def test_samsung_prefix_region_flag_position_does_not_create_duplicate(self):
        rows = parse_documents([
            "🇰🇿 Samsung S26 12/256GB Cobalt Violet — 58 500",
            "Galaxy S26 12/256 Cobalt Violet 🇰🇿 — 58 300",
            "Galaxy S26 12/256 Cobalt Violet 🇨🇱 — 58 200",
        ]).items
        merged = merge_lowest([rows])
        self.assertEqual(len(merged), 2)
        self.assertEqual(sorted(item.price for item in merged), [Decimal(58200), Decimal(58300)])

    async def test_user_realme_and_sony_pairs_merge_only_for_same_version(self):
        rows = parse_documents(["\n".join([
            "Realme 16 Pro 5G 8/256 Бежевый 🇷🇺 — 29 500",
            "Realme 16 Pro 5G 8/256GB Beige 🇷🇺 — 30 300",
            "Realme 16 Pro+ 5G 12/512 Серый 🇷🇺 — 42 000",
            "Realme 16 Pro+ 5G 12/512GB Gray 🇷🇺 — 42 300",
            "Realme 16 Pro 5G 8/256 Графитовый 🇷🇺 — 29 500",
            "Realme 16 Pro 5G 8/256GB Графит 🇷🇺 — 30 300",
            "Sony WH-1000 XM6 (Black) — 25 400",
            "Sony WH-1000XM6 Blaсk — 26 800",
            "Sony WH-1000 XM6 (Pink) — 25 800",
            "Sony WH-1000 XM5SA (Black) мягкий кейс — 18 400",
            "Sony WH-1000XM6 Black 🇪🇺 — 26 000",
        ])]).items
        merged = merge_lowest([rows])
        self.assertEqual(len(merged), 7)
        self.assertEqual(sorted(item.price for item in merged), list(map(Decimal, (
            18400, 25400, 25800, 26000, 29500, 29500, 42000))))

    async def test_user_galaxy_book_exact_sku_keeps_best_price_and_full_description(self):
        rows = parse_documents(["\n".join([
            'Samsung Galaxy Book 6 14" Core Ultra 5-325 16/512GB Gray NP740VJG-LG1IN 🇮🇳 — 110 600',
            'Galaxy Book 6 NP740VJG - LG1IN 🇮🇳 — 105 500',
            'Galaxy Book 6 NP740VJG - LG2IN 🇮🇳 — 116 500',
            'Galaxy Book 6 NP740VJG - LG1IN 🇪🇺 — 105 000',
            'Galaxy Book 6 NP740VJG - LG1IN Уценка 🇮🇳 — 99 000',
        ])]).items
        merged = merge_lowest([rows])
        self.assertEqual(len(merged), 4)
        full = next(item for item in merged if "Core Ultra" in item.title)
        self.assertEqual(full.price, Decimal(105500))
        self.assertIn("16/512", full.title)

    async def test_galaxy_book_conflicting_full_specs_are_not_merged_by_sku(self):
        rows = parse_documents(["\n".join([
            'Samsung Galaxy Book 6 14" Core Ultra 5-325 16/512GB Gray NP740VJG-LG1IN 🇮🇳 — 110 600',
            'Samsung Galaxy Book 6 14" Core Ultra 5-325 16/512GB Silver NP740VJG-LG1IN 🇮🇳 — 110 700',
            'Galaxy Book 6 NP740VJG - LG1IN 🇮🇳 — 105 500',
        ])]).items
        self.assertEqual(len(merge_lowest([rows])), 3)

    async def test_expired_galaxy_book_short_sku_is_not_duplicated_when_fresh_full_sku_exists(self):
        rows = parse_documents(["\n".join([
            'Samsung Galaxy Book 6 14" Core Ultra 5-325 16/512GB Gray NP740VJG-LG1IN 🇮🇳 — 110 600',
            'Galaxy Book 6 NP740VJG - LG1IN 🇮🇳 — 105 500',
        ])]).items
        self.service.supernova_expired_items = lambda: [rows[1]]
        self.service.publisher.publish = AsyncMock(return_value=0)
        count, _ = await self.service.render(items=[rows[0]])
        content = "\n".join(self.service.publisher.publish.await_args.args[0].values())
        self.assertEqual(count, 1)
        self.assertIn("110 600", content)
        self.assertNotIn("По запросу", content)

    async def test_expired_dyson_shorthand_shows_in_dyson_as_on_request(self):
        self.service.supernova_expired_items = lambda: [Item.from_dict({
            "title": "HS08 (Apricot Topaz) 🇬🇧", "price": "34000", "currency": "RUB",
            "block": "Товары", "sim": "unknown", "accessory": False})]
        _, _ = await self.service.render(items=[])
        pages = self.service.publisher.publish.await_args.args[0]
        content = "\n".join(pages.values())
        self.assertIn("Dyson", content)
        self.assertIn("HS08", content)
        self.assertIn("По запросу", content)
        self.assertNotIn("<b>Товары</b>", content)

    async def test_user_macbook_neo_pair_requires_all_specs_to_match(self):
        rows = parse_documents(["\n".join([
            "Apple MacBook Neo 13 A18 Pro 8/512GB Silver MHFC4 — 69 000",
            "Neo 13 MHFC4 Silver (A18 Pro 8/512) — 67 500",
            "Neo 13 MHFC4 Blush (A18 Pro 8/512) — 68 000",
            "Neo 13 MHFC4 Silver (A18 Pro 8/256) — 66 000",
            "Neo 13 MHFC4 Silver (A18 Pro 8/512) 🇺🇸 — 68 500",
        ])]).items
        merged = merge_lowest([rows])
        self.assertEqual(len(merged), 4)
        self.assertEqual(sorted(item.price for item in merged), list(map(Decimal, (66000, 67500, 68000, 68500))))

    async def test_admin_sets_category_and_base_markup_with_buttons(self):
        self.service.refresh_format = AsyncMock(return_value=(1, 1))
        controller = CatalogController("TOKEN", self.service, [42])
        controller.send = AsyncMock()
        controller.answer_callback = AsyncMock()
        def callback(data):
            return {"id": "1", "from": {"id": 42}, "message": {"chat": {"id": 42, "type": "private"}}, "data": data}
        def message(text):
            return {"from": {"id": 42}, "chat": {"id": 42, "type": "private"}, "text": text}
        controller.channel_waiting.add(42)
        controller.first_message_waiting.add(42)
        controller.order_selected[42] = "iPhone 17"
        await controller.handle_callback(callback("markup:set:iphone"))
        self.assertNotIn(42, controller.channel_waiting)
        self.assertNotIn(42, controller.first_message_waiting)
        self.assertNotIn(42, controller.order_selected)
        await controller.handle_message(message("1000"))
        self.assertEqual(self.service.options()["markup_rules"], [{"pattern": "iPhone", "amount": "1000"}])
        await controller.handle_callback(callback("markup:set:base"))
        await controller.handle_message(message("500"))
        self.assertEqual(self.service.options()["markup_base_rub"], "500")
        await controller.handle_callback(callback("markup:set:custom"))
        await controller.handle_message(message("Samsung Ultra"))
        await controller.handle_message(message("1000"))
        self.assertEqual(self.service.options()["markup_rules"][-1], {"pattern": "Samsung Ultra", "amount": "1000"})
        self.assertEqual(self.service.refresh_format.await_count, 3)

    async def test_combined_publication_applies_rules_to_lowest_supplier_price(self):
        first = Item("iPhone 17 256 Black 1Sim+eSim 🇪🇺", Decimal(60000), "RUB", "iPhone 17", "hybrid")
        cheaper = Item("iPhone 17 256 Black SIM + eSIM 🇪🇺", Decimal(59000), "RUB", "iPhone 17", "hybrid")
        ultra = Item("Samsung Galaxy S26 Ultra 12/256 Black", Decimal(90000), "RUB", "Samsung S26")
        other = Item("Dyson HS08", Decimal(40000), "RUB", "Dyson")
        self.service.readers[0].fetch.return_value = ParseResult([first], [])
        self.service.readers[1].fetch.return_value = ParseResult([cheaper, ultra, other], [])
        self.service.set_option("markup_base_rub", "500")
        self.service.set_option("markup_rules", [{"pattern": "iPhone", "amount": "1000"},
                                                 {"pattern": "Samsung Ultra", "amount": "1000"}])
        with patch("runtime.is_open", return_value=True):
            result = await self.service.sync(force=True)
        content = "\n".join(self.service.publisher.publish.await_args.args[0].values())
        self.assertIn("3 позиций", result)
        self.assertEqual(content.count("iPhone 17 256 Black"), 1)
        self.assertIn("60 000", content)
        self.assertIn("91 000", content)
        self.assertIn("40 500", content)
        self.assertNotIn("61 000", content)

    async def test_lower_purchase_price_wins_before_markup(self):
        expensive = Item("iPhone 17 256 Black 🇺🇸", Decimal(61000), "RUB", "iPhone 17")
        cheap = Item("iPhone 17 256 Black 🇺🇸", Decimal(59000), "RUB", "iPhone 17")
        india = Item("iPhone 17 256 Black 🇮🇳", Decimal(58000), "RUB", "iPhone 17")
        merged = merge_lowest([[expensive, india], [cheap]])
        self.assertEqual(len(merged), 2)
        usa = next(i for i in merged if "🇺🇸" in i.title)
        self.assertEqual(usa.price, Decimal(59000))

    async def test_samsung_publication_keeps_lowest_and_three_requested_regions(self):
        rows = parse_documents(["\n".join([
            "Galaxy S26 12/256 Cobalt Violet 🇨🇱 — 58 200",
            "Galaxy S26 12/256 Cobalt Violet 🇦🇪 — 58 400",
            "Galaxy S26 12/256 Cobalt Violet 🇭🇰 — 57 500",
            "Samsung S26 12/256GB () Cobalt Violet 🇰🇿 — 58 500",
            "Samsung S26 12/256GB () Cobalt Violet 🇨🇳 — 60 000",
            "Galaxy S26 12/512 Cobalt Violet 🇨🇱 — 67 500",
            "Galaxy S26 12/512 Cobalt Violet 🇰🇿 — 68 000",
            "Galaxy S26 12/256 Cobalt Violet Уценка 🇺🇸 — 48 500",
            "Galaxy S26 12/256 Cobalt Violet Уценка 🇰🇿 — 49 500",
        ])]).items
        count, _ = await self.service.render(items=merge_lowest([rows]))
        content = "\n".join(self.service.publisher.publish.await_args.args[0].values())
        self.assertEqual(count, 7)
        for price in ("57 500", "58 500", "60 000", "67 500", "68 000", "48 500", "49 500"):
            self.assertIn(price, content)
        for price in ("58 200", "58 400"):
            self.assertNotIn(price, content)
        self.assertIn("🇭🇰", content)
        self.assertIn("🇨🇳", content)
        self.assertIn("🇰🇿", content)

    async def test_samsung_cheapest_outside_protected_markets_is_also_shown(self):
        rows = parse_documents(["\n".join([
            "Galaxy S26 12/256 White 🇹🇭 — 58 000",
            "Galaxy S26 12/256 White 🇮🇳 — 57 000",
            "Galaxy S26 12/256 White 🇭🇰 — 59 000",
            "Galaxy S26 12/256 White 🇨🇳 — 60 000",
            "Galaxy S26 12/256 White 🇰🇿 — 61 000",
        ])]).items
        count, _ = await self.service.render(items=rows)
        content = "\n".join(self.service.publisher.publish.await_args.args[0].values())
        self.assertEqual(count, 4)
        self.assertIn("🇮🇳", content)
        self.assertNotIn("🇹🇭", content)

    async def test_samsung_preserves_russian_market_and_explicit_rst(self):
        rows = parse_documents(["\n".join([
            "Samsung S26 12/256 Black 🇦🇪 — 57 000",
            "Samsung S26 12/256 Black 🇮🇳 — 58 000",
            "Samsung S26 12/256 Black 🇷🇺 — 60 000",
            "Samsung S26 12/256 Black РСТ 🇷🇺 — 61 000",
            "Samsung S26 12/256 Black RST 🇷🇺 — 62 000",
            "Samsung S26 12/256 Black 🇰🇿 — 59 000",
            "Samsung S26 12/256 Black 🇭🇰 — 58 500",
            "Samsung S26 12/256 Black 🇨🇳 — 59 500",
        ])]).items
        count, _ = await self.service.render(items=merge_lowest([rows]))
        content = "\n".join(self.service.publisher.publish.await_args.args[0].values())
        self.assertEqual(count, 6)
        for price in ("57 000", "58 500", "59 000", "59 500", "60 000", "61 000"):
            self.assertIn(price, content)
        self.assertNotIn("58 000", content)
        self.assertNotIn("62 000", content)
        self.assertEqual(content.count("РСТ"), 1)

    async def test_samsung_a18_4g_and_unlabelled_duplicates_keep_lowest(self):
        rows = parse_documents(["\n".join([
            "Galaxy A18 6/128 Black 🇷🇺 — 17 600",
            "Samsung A18 4G 6/128GB Black 🇷🇺 — 17 800",
            "Samsung A18 5G 6/128GB Black 🇷🇺 — 21 000",
            "Galaxy A08 6/128 Синий 🇷🇺 — 15 900",
            "Samsung A08 6/128GB Blue 🇷🇺 — 16 000",
            "Samsung A18 4G 6/128GB Black Уценка 🇷🇺 — 15 000",
            "Samsung A18 4G 6/128GB Black 🇰🇿 — 18 000",
        ])]).items
        count, _ = await self.service.render(items=merge_lowest([rows]))
        content = "\n".join(self.service.publisher.publish.await_args.args[0].values())
        self.assertEqual(count, 5)
        self.assertNotIn("17 800", content)
        self.assertNotIn("16 000", content)
        for price in ("17 600", "21 000", "15 900", "15 000", "18 000"):
            self.assertIn(price, content)

    async def test_s26_rows_group_by_model_and_monarch_never_published(self):
        rows = parse_documents(["\n".join([
            "Galaxy S26 12/256 Sky Blue 🇰🇿 — 58 500",
            "Galaxy S26 FE 8/128 Graphite 🇿🇦 — 42 300",
            "Galaxy S26 Ultra 12/256 White 🇲🇾 — 77 500",
            "Samsung S26+ 12/512 Black 🇰🇿 — 76 000",
            "MONARCH для Samsung Galaxy S26 Ultra Без рамки — 900",
            "Samsung S26 12/256GB Sky Blue 🇰🇿 — 59 000",
            "Samsung S26 12/128GB Black 🇪🇺 — 56 800",
            "Samsung S26 FE 5G 8/256GB Pistachio 🇦🇪 — 46 000",
            "Samsung S26 Ultra 12/512 White 🇨🇱 — 91 300",
            "Samsung S26+ 12/512 Black 🇨🇱 — 75 800",
            "Samsung Z Fold7 12/256 Blue Shadow 🇭🇰 — 97 600",
            "Samsung Z Flip8 12/256 Graphite 🇰🇿 — 64 000",
        ])]).items
        count, _ = await self.service.render(items=merge_lowest([rows]))
        content = "\n".join(self.service.publisher.publish.await_args.args[0].values())
        self.assertNotIn("MONARCH", content)
        self.assertNotIn("59 000", content)
        self.assertIn("58 500", content)
        self.assertIn("76 000", content)  # protected Kazakhstan
        self.assertIn("75 800", content)  # lowest elsewhere
        base = content.index("Samsung S26 12/128")
        fe = content.index("Samsung S26 FE 8/128")
        plus = content.index("Samsung S26+ 12/512")
        ultra = content.index("Samsung S26 Ultra 12/256")
        self.assertLess(base, fe)
        self.assertLess(fe, plus)
        self.assertLess(plus, ultra)
        self.assertGreater(count, 0)

    async def test_s26_memory_colour_and_duplicate_regions_follow_user_example(self):
        first = parse_documents(["""Samsung S26 12/256 Cobalt Violet 🇭🇰 — 57500
Samsung S26 12/512 Black 🇰🇿 — 68500
Samsung S26 12/512 Cobalt Violet 🇦🇪 — 67800
Samsung S26 12/128GB Black 🇪🇺 — 56800
Samsung S26 12/256GB Black 🇨🇳 — 62000
Samsung S26 12/256GB Cobalt Violet 🇨🇳 — 60000
Samsung S26 12/256GB Cobalt Violet 🇰🇿 — 58500
Samsung S26 12/256GB Черный 🇷🇺 — 61500
Samsung S26 12/512GB Cobalt Violet 🇰🇿 — 68000"""]).items
        duplicate = parse_documents(["Samsung S26 12/256GB Cobalt Violet 🇭🇰 — 59000"]).items
        _, _ = await self.service.render(items=merge_lowest([first, duplicate]))
        content = "\n".join(self.service.publisher.publish.await_args.args[0].values())
        self.assertNotIn("59 000", content)
        self.assertIn("57 500", content)
        self.assertIn("67 800", content)
        self.assertIn("68 000", content)
        self.assertLess(content.index("12/128GB"), content.index("12/256"))
        self.assertLess(content.index("12/256"), content.index("12/512"))
        rows = re.findall(r"<code>(.*?)</code>", content)
        black = [i for i, row in enumerate(rows) if "12/256" in row and ("Black" in row or "Черный" in row)]
        self.assertEqual(len(black), 2)
        self.assertEqual(black[1], black[0] + 1)

    async def test_galaxy_fold_prefix_and_model_spacing_merge_in_same_region(self):
        rows = parse_documents(["\n".join([
            "Galaxy Z Fold7 12/256 Blue Shadow 🇰🇿 — 97 600",
            "Samsung Galaxy Z Fold 7 12/256GB Blue Shadow 🇰🇿 — 99 000",
            "Samsung Galaxy Z Fold7 12/256GB Blue Shadow 🇭🇰 — 98 000",
            "Samsung Galaxy Z Fold7 12/512GB Blue Shadow 🇰🇿 — 110 000",
            "Samsung Galaxy Z Fold7 12/256GB Silver Shadow 🇰🇿 — 97 000",
            "Samsung Galaxy Z Fold7 12/256GB Blue Shadow Уценка 🇰🇿 — 90 000",
            "Samsung Galaxy Z Flip8 12/256GB Graphite 🇰🇿 — 64 000",
            "Galaxy Z Flip 8 12/256 Graphite 🇰🇿 — 63 000",
        ])]).items
        self.assertTrue(all(item.block == "Samsung Fold / Flip" for item in rows))
        merged = merge_lowest([rows])
        self.assertEqual(sorted(item.price for item in merged),
                         list(map(Decimal, (63000, 90000, 97000, 97600, 98000, 110000))))
        restored = Item.from_dict({"title": "Galaxy Z Fold7 12/256 Blue Shadow 🇰🇿",
                                   "price": "97600", "currency": "RUB", "block": "Samsung",
                                   "sim": "unknown", "accessory": False})
        self.assertEqual(restored.block, "Samsung Fold / Flip")

    async def test_user_samsung_a57_examples_only_publish_lowest_price_per_colour(self):
        rows = parse_documents(["\n".join([
            "Samsung A57 5G 12/256GB A.Icyblue 🇦🇪 — 40 300",
            "Samsung A57 5G 12/256GB A.Icyblue 🇮🇩 — 40 000",
            "Samsung A57 5G 12/256GB A.Lilac 🇦🇪 — 40 300",
            "Samsung A57 5G 12/256GB A.Lilac 🇮🇩 — 40 000",
        ])]).items
        count, _ = await self.service.render(items=merge_lowest([rows]))
        content = "\n".join(self.service.publisher.publish.await_args.args[0].values())
        self.assertEqual(count, 2)
        self.assertEqual(content.count("40 000"), 2)
        self.assertNotIn("40 300", content)
        self.assertIn("A.Icyblue 🇮🇩", content)
        self.assertIn("A.Lilac 🇮🇩", content)

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
