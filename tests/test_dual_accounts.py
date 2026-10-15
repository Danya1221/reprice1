import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from config import Settings, Source
from control_botapi import BotAPIController
from prices import Item, ParseResult, parse_documents
from runtime import SyncService
from state import StateStore


class DualAccountRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = StateStore(Path(self.temp.name) / "state.json")
        self.settings = Settings(
            sources=(Source("@hi", label="HI"),),
            off_hours=False,
            session="ONE",
            session_2="TWO",
        )
        self.client1 = SimpleNamespace(
            is_connected=lambda: True,
            connect=AsyncMock(),
            is_user_authorized=AsyncMock(return_value=True),
            disconnect=AsyncMock(),
        )
        self.client2 = SimpleNamespace(
            is_connected=lambda: True,
            connect=AsyncMock(),
            is_user_authorized=AsyncMock(return_value=True),
            disconnect=AsyncMock(),
        )
        self.service = SyncService(self.client1, self.settings, self.state)
        self.service.attach_client(self.client2, 2)
        self.service.publisher.publish = AsyncMock(return_value=0)

    def tearDown(self):
        self.temp.cleanup()

    async def test_second_account_reads_only_primary_hi_supplier(self):
        other = Source("@other", label="OTHER")
        self.settings.sources = (self.settings.sources[0], other)
        service = SyncService(self.client1, self.settings, self.state)
        service.attach_client(self.client2, 2)

        primary = [reader.source.peer for reader in service.readers_for_slot(1)]
        secondary = [reader.source.peer for reader in service.readers_for_slot(2)]
        active = [(slot, reader.source.peer) for slot, reader in service.active_reader_entries()]

        self.assertEqual(primary, ["@hi", "@other"])
        self.assertEqual(secondary, ["@hi"])
        self.assertEqual(active, [(1, "@hi"), (1, "@other"), (2, "@hi")])

    async def test_same_hi_variant_from_two_accounts_uses_lower_price(self):
        expensive = Item("iPhone 17 256 Black", Decimal("60000"), "RUB", "iPhone 17")
        cheap = Item("iPhone 17 256 Black", Decimal("57000"), "RUB", "iPhone 17")
        self.service.readers_for_slot(1)[0].fetch = AsyncMock(return_value=ParseResult([expensive], []))
        self.service.readers_for_slot(2)[0].fetch = AsyncMock(return_value=ParseResult([cheap], []))

        with patch("runtime.is_open", return_value=True):
            result = await self.service.sync(force=True)

        self.assertIn("аккаунт 2", result)
        pages = self.service.publisher.publish.call_args.args[0]
        body = "\n".join(pages.values())
        self.assertIn("57 000", body)
        self.assertNotIn("60 000", body)
        cache = self.state.get("sources")
        self.assertIn("@hi", cache)
        self.assertIn("account2:@hi", cache)

    def add_other_supplier(self):
        self.settings.sources = (self.settings.sources[0], Source("@other", label="OTHER"))
        self.service = SyncService(self.client1, self.settings, self.state)
        self.service.attach_client(self.client2, 2)
        self.service.publisher.publish = AsyncMock(return_value=0)

    async def test_hi_non_apple_prices_from_both_accounts_cannot_undercut_other_supplier(self):
        self.add_other_supplier()
        first = parse_documents(["""iPhone 17 256 Black — 60000
Samsung S26 12/256 Black — 100
Dyson HS08 — 200
DJI Mic Mini (2TX+1RX+Charging Case) — 300"""])
        second = parse_documents(["""iPhone 17 256 Black — 57000
Samsung S26 12/256 Black — 50
Dyson HS08 — 150
DJI Mic Mini (2TX+1RX+Charging Case) — 250"""])
        other = parse_documents(["""iPhone 17 256 Black — 62000
Samsung S26 12/256 Black — 65000
Dyson HS08 — 40000
DJI Mic Mini (2TX+1RX+Charging Case) — 6900"""])
        self.service.readers_for_slot(1)[0].fetch = AsyncMock(return_value=first)
        self.service.readers_for_slot(1)[1].fetch = AsyncMock(return_value=other)
        self.service.readers_for_slot(2)[0].fetch = AsyncMock(return_value=second)

        with patch("runtime.is_open", return_value=True):
            await self.service.sync(force=True)

        prices = {item.block: item.price for item in self.service.cached_items()}
        self.assertEqual(prices, {"iPhone 17": Decimal("57000"), "Samsung S26": Decimal("65000"),
                                  "Dyson": Decimal("40000"), "DJI / Insta360": Decimal("6900")})
        cache = self.state.get("sources")
        self.assertEqual([item["block"] for item in cache["@hi"]["items"]], ["iPhone 17"])
        self.assertEqual([item["block"] for item in cache["account2:@hi"]["items"]], ["iPhone 17"])
        self.assertEqual(len(cache["@other"]["items"]), 4)
        body = "\n".join(self.service.publisher.publish.call_args.args[0].values())
        for price in ("57 000", "65 000", "40 000", "6 900"):
            self.assertIn(price, body)

    async def test_restart_and_format_refresh_ignore_non_apple_in_old_hi_cache(self):
        self.add_other_supplier()
        hi = parse_documents(["iPhone 17 256 Black — 57000\nSamsung S26 12/256 Black — 100"]).items
        other = parse_documents(["Samsung S26 12/256 Black — 65000\nDyson HS08 — 40000"]).items
        self.state.set("sources", {
            "@hi": {"status": "open", "items": [item.to_dict() for item in hi]},
            "account2:@hi": {"status": "open", "items": [item.to_dict() for item in hi]},
            "@other": {"status": "open", "items": [item.to_dict() for item in other]},
        })
        state = StateStore(self.state.path)
        service = SyncService(self.client1, self.settings, state)
        service.attach_client(self.client2, 2)
        service.publisher.publish = AsyncMock(return_value=0)
        for _, reader in service.active_reader_entries():
            reader.fetch = AsyncMock(side_effect=AssertionError("Format refresh must use cached data"))

        await service.refresh_format()

        self.assertEqual({item.block: item.price for item in service.cached_items(include_closed=True)},
                         {"iPhone 17": Decimal("57000"), "Samsung S26": Decimal("65000"), "Dyson": Decimal("40000")})
        body = "\n".join(service.publisher.publish.call_args.args[0].values())
        self.assertIn("65 000", body)
        for _, reader in service.active_reader_entries():
            reader.fetch.assert_not_awaited()

    async def test_hi_with_no_apple_publishes_empty_instead_of_reusing_non_apple(self):
        non_apple = parse_documents(["Samsung S26 12/256 Black — 65000\nDyson HS08 — 40000"])
        for _, reader in self.service.active_reader_entries():
            reader.fetch = AsyncMock(return_value=non_apple)
        with patch("runtime.is_open", return_value=True):
            result = await self.service.sync(force=True)
        self.assertIn("0 позиций", result)
        self.service.publisher.publish.assert_awaited_once_with({})
        self.assertEqual(self.service.cached_items(), [])

    async def test_hi_keeps_all_apple_device_families_and_only_apple_cpo_asis(self):
        apple = """16Е 128 Black — 43800
AirPods 4 — 10000
S11 42 Jet Black — 28300
iPad Air 11 M3 128 Wi-Fi Blue — 50000
MacBook Air M4 16/256 Silver — 90000
Mac mini M4 16/256 Silver — 68000
Mac Studio M4 32/512 — 150000
Apple TV 4K 128GB — 15000
AirTag 1 pack — 3000
Magic Mouse 3 USB-C White — 6200
iPhone 15 Pro 256 Black CPO — 60000
iPhone 14 128 Black ASIS — 30000"""
        excluded = """Samsung S26 12/256 Black CPO — 100
Samsung S25 12/256 Black ASIS — 100
Чехол для iPhone 17 — 1000
Google Pixel Watch 5 WI-FI 41mm Aluminum Case / Obsidian Active Band — 33100"""
        response = parse_documents([apple + "\n" + excluded])
        for _, reader in self.service.active_reader_entries():
            reader.fetch = AsyncMock(return_value=response)
        with patch("runtime.is_open", return_value=True):
            await self.service.sync(force=True)
        allowed = self.service.cached_items()
        self.assertEqual(len(allowed), 12)
        self.assertEqual({item.key for item in allowed}, {item.key for item in parse_documents([apple]).items})


class DualAccountControlTests(unittest.IsolatedAsyncioTestCase):
    async def test_second_login_is_saved_in_separate_slot_and_requests_reconnect(self):
        service = MagicMock()
        service.settings = SimpleNamespace(api_id=123, api_hash="hash", session="", session_2="")
        service.state = MagicMock()
        service.startup_error = "missing"
        service.account_errors = {}
        service.request_reconnect = MagicMock()
        controller = BotAPIController("TOKEN", service, [42])
        controller.send = AsyncMock()
        temp = SimpleNamespace(
            session=SimpleNamespace(save=lambda: "SECOND_SESSION"),
            disconnect=AsyncMock(),
        )
        controller.login_flows[42] = {"stage": "password", "client": temp, "chat_id": 42, "slot": 2}

        await controller.finish_login(42, 42, controller.login_flows[42])

        service.state.set.assert_called_with("session_string_2", "SECOND_SESSION")
        self.assertEqual(service.settings.session_2, "SECOND_SESSION")
        service.request_reconnect.assert_called_once()
        temp.disconnect.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
