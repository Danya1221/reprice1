import asyncio
import io
import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from openpyxl import Workbook

from config import Settings, Source
from control_catalog import CatalogController
from prices import Item
from runtime import SyncService
from state import StateStore
from supernova import parse_supernova


def workbook(rows):
    book = Workbook()
    sheet = book.active
    sheet.title = "Прайс"
    sheet.append(["ИТОГО ПО ЗАКАЗУ"])
    sheet.append(["Наименование", "Re.order", "Кол-во", "Цена от 1 шт", "Цена от 5 шт", "Цена от 10 шт"])
    for row in rows:
        sheet.append(row)
    sheet.append(["Файл выгружен для: пользователь"])
    result = io.BytesIO()
    book.save(result)
    book.close()
    return result.getvalue()


class SupernovaWorkbookTests(unittest.TestCase):
    def test_uses_retail_price_and_accepts_lower_bound_stock(self):
        data = workbook([["iPhone 17 256 Black 🇪🇺", None, "20+", 60000, 59000, 58000],
                         ["iPhone 17 256 Blue 🇪🇺", None, 0, 61000, 60000, 59000]])
        items, unavailable = parse_supernova(data)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].price, Decimal("60000"))
        self.assertIn("🇪🇺", items[0].title)
        self.assertEqual(len(unavailable), 1)

    def test_incomplete_or_duplicated_file_is_rejected(self):
        good = ["iPhone 17 256 Black 🇪🇺", None, 2, 60000]
        for rows in ([good, ["Broken", None, None, 10]], [good, good]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                parse_supernova(workbook(rows))


class SupernovaServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = StateStore(Path(self.temp.name) / "state.json")
        self.settings = Settings(sources=(Source("@hi"),))
        self.msg = SimpleNamespace(id=10, sender_id=777, out=False, fwd_from=None,
                                   date=datetime.fromtimestamp(2000000000, timezone.utc),
                                   file=SimpleNamespace(name="price.xlsx", size=1000))
        self.client = SimpleNamespace(get_me=AsyncMock(return_value=SimpleNamespace(id=42, bot=False)),
                    get_entity=AsyncMock(return_value=SimpleNamespace(id=777, bot=True)),
                    get_messages=AsyncMock(return_value=[self.msg]),
                    download_media=AsyncMock(return_value=workbook([["iPhone 17 256 Black 🇪🇺", None, 2, 59000]])))
        self.service = SyncService(self.client, self.settings, self.state)
        self.service.refresh_format = AsyncMock(return_value=(1, 1))
        self.base = Item("iPhone 17 256 Black 🇪🇺", Decimal("60000"), "RUB", "iPhone 17")
        self.state.set("sources", {"@hi": {"status": "open", "items": [self.base.to_dict()]}})
        await self.service.configure_supernova(1)

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def poll_at(self, now):
        with patch("runtime.time.time", return_value=now), patch("runtime.is_open", return_value=True):
            await self.service.poll_supernova()

    async def test_reads_correct_bot_and_rejects_snapshot_from_old_bot(self):
        await self.poll_at(2000000000)
        self.client.get_entity.assert_awaited_with("@vodorod00bot")
        snapshot = self.state.get("supernova_snapshot")
        snapshot.pop("bot")
        self.state.set("supernova_snapshot", snapshot)
        self.assertEqual(self.service.supernova_items(now=2000000000), [])
        self.assertEqual(self.service.supernova_expired_items(now=2000000000), [])
        await self.poll_at(2000000000)
        self.assertEqual(self.state.get("supernova_snapshot")["bot"], "@vodorod00bot")

    async def test_file_is_saved_while_general_sync_holds_publication_lock(self):
        async def refresh():
            async with self.service.lock:
                return (1, 1)
        self.service.refresh_format = AsyncMock(side_effect=refresh)
        await self.service.lock.acquire()
        try:
            await asyncio.wait_for(self.poll_at(2000000000), timeout=1)
            self.assertEqual(self.state.get("supernova_snapshot")["message_id"], 10)
            self.assertIsNotNone(self.service.supernova_refresh_task)
        finally:
            self.service.lock.release()
        await asyncio.wait_for(self.service.supernova_refresh_task, timeout=1)
        self.service.refresh_format.assert_awaited_once()

    async def test_fresh_export_merges_with_other_supplier_and_expires(self):
        await self.poll_at(2000000000)
        self.assertEqual(self.state.get("supernova_snapshot")["count"], 1)
        with patch("runtime.time.time", return_value=2000000000):
            self.assertEqual(self.service.cached_items()[0].price, Decimal("59000"))
        self.assertEqual(self.state.get("sources")["@hi"]["items"][0]["price"], "60000")
        self.service.refresh_format.assert_awaited_once()
        with patch("runtime.time.time", return_value=2000000601):
            await self.poll_at(2000000601)
            self.assertEqual(self.service.cached_items()[0].price, Decimal("60000"))
        self.assertEqual(self.service.refresh_format.await_count, 2)

    async def test_wrong_sender_old_file_and_forward_are_ignored(self):
        for change in ({"sender_id": 778}, {"fwd_from": object()}, {"out": True}):
            for field, value in change.items():
                original = getattr(self.msg, field)
                setattr(self.msg, field, value)
                await self.poll_at(2000000000)
                setattr(self.msg, field, original)
        await self.poll_at(2000000601)
        self.client.download_media.assert_not_awaited()
        self.assertIsNone(self.state.get("supernova_snapshot"))

    async def test_broken_new_file_preserves_prior_snapshot(self):
        await self.poll_at(2000000000)
        original = self.state.get("supernova_snapshot")
        self.msg.id = 11
        self.msg.date = datetime.fromtimestamp(2000000001, timezone.utc)
        self.client.download_media.return_value = b"bad xlsx"
        await self.poll_at(2000000001)
        self.assertEqual(self.state.get("supernova_snapshot"), original)
        self.assertIn("Excel", self.state.get("supernova_error"))

    async def test_switch_account_never_reuses_other_accounts_snapshot(self):
        await self.poll_at(2000000000)
        self.service.clients[2] = SimpleNamespace()
        await self.service.configure_supernova(2)
        self.assertEqual(self.service.supernova_items(now=2000000000), [])
        self.assertEqual(self.state.get("supernova_snapshot")["account_id"], 42)

    async def test_replacing_connected_session_withholds_old_numeric_prices(self):
        await self.poll_at(2000000000)
        replacement = SimpleNamespace()
        self.service.attach_client(replacement, 1)
        self.assertEqual(self.service.supernova_items(now=2000000000), [])
        self.assertEqual(len(self.service.supernova_expired_items(now=2000000000)), 1)

    async def test_omissions_do_not_remove_recent_old_item(self):
        await self.poll_at(2000000000)
        self.msg.id = 11
        self.msg.date = datetime.fromtimestamp(2000000050, timezone.utc)
        self.client.download_media.return_value = workbook([["iPhone 17 256 Blue 🇪🇺", None, 3, 62000]])
        await self.poll_at(2000000050)
        self.assertEqual(len(self.service.supernova_items(now=2000000050)), 2)
        self.assertEqual(len(self.service.supernova_items(now=2000000601)), 1)

    async def test_explicit_zero_stock_removes_that_offer_only(self):
        await self.poll_at(2000000000)
        self.msg.id = 11
        self.msg.date = datetime.fromtimestamp(2000000050, timezone.utc)
        self.client.download_media.return_value = workbook([
            ["iPhone 17 256 Black 🇪🇺", None, 0, 59000],
            ["iPhone 17 256 Blue 🇪🇺", None, 2, 62000],
        ])
        await self.poll_at(2000000050)
        active = self.service.supernova_items(now=2000000050)
        self.assertEqual(len(active), 1)
        self.assertIn("Blue", active[0].title)

    async def test_expired_price_stays_visible_as_on_request_without_other_sources(self):
        await self.poll_at(2000000000)
        self.state.set("sources", {})
        self.service.publisher.publish = AsyncMock(return_value=0)
        with patch("runtime.time.time", return_value=2000000601):
            await self.service.render()
        pages = self.service.publisher.publish.await_args.args[0]
        content = "\n".join(pages.values())
        self.assertIn("iPhone 17 256 Black", content)
        self.assertIn("По запросу", content)
        self.assertNotIn("59 000", content)

    async def test_fresh_other_supplier_price_replaces_expired_supernova(self):
        await self.poll_at(2000000000)
        self.service.publisher.publish = AsyncMock(return_value=0)
        with patch("runtime.time.time", return_value=2000000601):
            await self.service.render()
        pages = self.service.publisher.publish.await_args.args[0]
        content = "\n".join(pages.values())
        self.assertIn("60 000", content)
        self.assertNotIn("По запросу", content)

    async def test_admin_can_choose_account_in_control_bot(self):
        controller = CatalogController("token", self.service, [42])
        controller.send = AsyncMock()
        controller.answer_callback = AsyncMock()
        callback = {"id": "1", "from": {"id": 42},
                    "message": {"chat": {"id": 42, "type": "private"}},
                    "data": "supernova:account:1"}
        with patch("runtime.time.time", return_value=2000000000), patch("runtime.is_open", return_value=True):
            await controller.handle_callback(callback)
        self.assertEqual(self.state.get("supernova_settings"), {"enabled": True, "slot": 1})
        output = controller.send.await_args.args[1]
        self.assertIn("аккаунт 1", output)
        self.assertIn("10 минут", output)

    async def test_non_admin_cannot_enable_supernova(self):
        controller = CatalogController("token", self.service, [42])
        controller.answer_callback = AsyncMock()
        callback = {"id": "1", "from": {"id": 99},
                    "message": {"chat": {"id": 99, "type": "private"}},
                    "data": "supernova:disable"}
        await controller.handle_callback(callback)
        self.assertTrue(self.state.get("supernova_settings")["enabled"])
