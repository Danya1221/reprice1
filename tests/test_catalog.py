import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from catalog_publisher import CatalogPublisher
from config import Settings
from control_catalog import CatalogController, block_id
from prices import parse_documents, render_blocks
from state import StateStore
from test_first_message import FakePinnedPublisher


class FakeCatalogPublisher(CatalogPublisher):
    def __init__(self, state):
        super().__init__("TOKEN", -100777, state, Settings(send_delay=0))
        self.calls = []
        self.next_id = 100

    api = FakePinnedPublisher.api


class CatalogTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = StateStore(Path(self.temp.name) / "state.json")
        self.publisher = FakeCatalogPublisher(self.state)
        self.items = parse_documents(["iPhone 17 256 Black — 60000\nDyson HS08 — 40000\nAirPods 4 — 10000"]).items

    def tearDown(self):
        self.temp.cleanup()

    def pages(self, order=()):
        return render_blocks(self.items, Settings(), {"block_order": order})

    def catalog_edit(self):
        return [payload for method, payload in self.publisher.calls
                if method == "editMessageText" and payload.get("reply_markup")][-1]

    async def test_catalog_links_and_physical_post_order_follow_new_order(self):
        await self.publisher.publish(self.pages())
        before = {entry["id"] for entry in self.state.get("published")["messages"].values()}
        catalog_id = self.state.get("catalog")["messages"][0]["id"]
        self.assertGreater(catalog_id, max(before))
        self.publisher.calls.clear()
        await self.publisher.publish(self.pages(["Dyson", "AirPods", "iPhone 17"]))
        manifest = self.state.get("published")["messages"]
        self.assertEqual(before, {entry["id"] for entry in manifest.values()})
        ordered = sorted(manifest.values(), key=lambda e: e["id"])
        self.assertIn("Dyson", ordered[0]["content"])
        buttons = [b for row in self.catalog_edit()["reply_markup"]["inline_keyboard"] for b in row]
        self.assertEqual([b["text"] for b in buttons], ["Другое", "Apple", "iPhone 17"])
        self.assertEqual(buttons[0]["url"], f"https://t.me/c/777/{ordered[0]['id']}")
        self.assertFalse(any(method == "sendMessage" for method, _ in self.publisher.calls))
        self.publisher.calls.clear()
        await self.publisher.publish(self.pages(["Dyson", "AirPods", "iPhone 17"]))
        self.assertEqual(self.publisher.calls, [])

    async def test_catalog_buttons_change_in_same_publish_when_page_family_changes(self):
        first = {"old:0": "<b>Honor</b>\n\n<code>Honor 400 — 30000</code>"}
        await self.publisher.publish(first)
        message_id = next(iter(self.state.get("published")["messages"].values()))["id"]
        self.publisher.calls.clear()
        second = {"new:0": "<b>Samsung</b>\n\n<b>— Samsung S26 —</b>\n<code>S26 12/256 — 65000</code>"}
        await self.publisher.publish(second)
        manifest = self.state.get("published")["messages"]
        self.assertEqual(next(iter(manifest.values()))["id"], message_id)
        buttons = [b for row in self.catalog_edit()["reply_markup"]["inline_keyboard"] for b in row]
        self.assertEqual([b["text"] for b in buttons], ["Samsung"])
        self.assertFalse(any(method == "sendMessage" for method, _ in self.publisher.calls))

    async def test_existing_price_slots_stay_prices_and_catalog_is_last(self):
        await self.publisher.ensure_target()
        self.state.set("published", {"binding": self.publisher.binding(), "messages": {
            "old:0": {"id": 20, "content": "old"}, "old:1": {"id": 21, "content": "old2"}}})
        await self.publisher.publish(self.pages())
        entries = self.state.get("published")["messages"].values()
        self.assertEqual(len(entries), len(self.pages()))
        price_ids = [entry["id"] for entry in entries]
        self.assertIn(20, price_ids)
        self.assertIn(21, price_ids)
        self.assertGreater(self.state.get("catalog")["messages"][0]["id"], max(price_ids))

    async def test_catalog_stays_compact_even_with_many_price_blocks(self):
        pages = {f"block{i}:0": f"<b>Device {i}</b>\n\n<code>Device {i} — 1000</code>" for i in range(91)}
        await self.publisher.publish(pages)
        records = self.state.get("catalog")["messages"]
        self.assertEqual(len(records), 1)
        edit = self.catalog_edit()
        buttons = [b for row in edit["reply_markup"]["inline_keyboard"] for b in row]
        self.assertEqual([b["text"] for b in buttons], ["Другое"])
        self.assertTrue(all(len(row) <= 2 for row in edit["reply_markup"]["inline_keyboard"]))

    async def test_catalog_is_never_pinned(self):
        await self.publisher.publish(self.pages())
        self.assertFalse(any(method == "pinChatMessage" for method, _ in self.publisher.calls))
        record = self.state.get("catalog")["messages"][0]
        self.assertNotIn("pinned", record)

    async def test_custom_intro_is_only_pin_and_catalog_is_last(self):
        await self.publisher.set_first_message("Гарантия и выдача")
        first = self.state.get("first_message")["id"]
        await self.publisher.publish(self.pages())
        price_ids = [entry["id"] for entry in self.state.get("published")["messages"].values()]
        catalog_id = self.state.get("catalog")["messages"][0]["id"]
        self.assertLess(first, min(price_ids))
        self.assertGreater(catalog_id, max(price_ids))
        pins = [payload["message_id"] for method, payload in self.publisher.calls if method == "pinChatMessage"]
        self.assertEqual(pins, [first])
        self.assertEqual(self.state.get("first_message_text"), "Гарантия и выдача")

    async def test_old_pinned_catalog_is_rebuilt_last(self):
        await self.publisher.publish(self.pages())
        record = self.state.get("catalog")["messages"][0]
        old_id = record["id"]
        record["pinned"] = True
        self.state.set("catalog", {"binding": self.publisher.binding(), "messages": [record]})
        self.publisher.calls.clear()
        await self.publisher.publish(self.pages())
        new_id = self.state.get("catalog")["messages"][0]["id"]
        price_ids = [entry["id"] for entry in self.state.get("published")["messages"].values()]
        self.assertNotEqual(old_id, new_id)
        self.assertGreater(new_id, max(price_ids))
        self.assertTrue(any(method == "deleteMessage" and payload.get("message_id") == old_id
                            for method, payload in self.publisher.calls))
        self.assertFalse(any(method == "pinChatMessage" for method, _ in self.publisher.calls))

    async def test_restart_keeps_order_ids_and_catalog_without_resending(self):
        await self.publisher.publish(self.pages(["Dyson"]))
        state = StateStore(self.state.path)
        restarted = FakeCatalogPublisher(state)
        await restarted.publish(self.pages(["Dyson"]))
        self.assertFalse(any(method == "sendMessage" for method, _ in restarted.calls))
        self.assertEqual(self.state.get("published"), state.get("published"))

    async def test_pin_failure_of_custom_intro_does_not_send_second_intro(self):
        real = self.publisher.api
        async def denied(method, **kwargs):
            if method == "pinChatMessage":
                raise RuntimeError("CHAT_ADMIN_REQUIRED")
            return await real(method, **kwargs)
        self.publisher.api = denied
        with self.assertRaises(RuntimeError):
            await self.publisher.set_first_message("Гарантия")
        ident = self.state.get("first_message")["id"]
        self.publisher.calls.clear()
        with self.assertRaises(RuntimeError):
            await self.publisher.set_first_message("Гарантия")
        self.assertEqual(ident, self.state.get("first_message")["id"])
        self.assertFalse(any(method == "sendMessage" for method, _ in self.publisher.calls))

    async def test_saved_first_message_is_rebuilt_before_prices_after_layout_repair(self):
        await self.publisher.ensure_target()
        binding = self.publisher.binding()
        self.state.set("first_message_text", "Гарантия и выдача")
        self.state.set("first_message", {
            "binding": binding, "chat_id": -100777, "id": 50,
            "hash": "old", "text": "Гарантия и выдача", "pinned": True,
        })
        self.state.set("catalog", {
            "binding": binding, "messages": [{"id": 50, "hash": "catalog", "pinned": True}],
        })
        self.state.set("published", {
            "binding": binding, "messages": {"old:0": {"id": 60, "hash": "x", "content": "old"}},
        })

        await self.publisher.publish(self.pages())

        first = self.state.get("first_message")["id"]
        price_ids = [entry["id"] for entry in self.state.get("published")["messages"].values()]
        catalog = self.state.get("catalog")["messages"][0]["id"]
        self.assertLess(first, min(price_ids))
        self.assertGreater(catalog, max(price_ids))
        self.assertEqual(self.state.get("first_message_layout_version"), 2)
        self.assertTrue(any(method == "pinChatMessage" and payload.get("message_id") == first
                            for method, payload in self.publisher.calls))

    async def test_samsung_series_share_one_compact_catalog_button(self):
        items = parse_documents(["""Samsung
A56 8/256 Black — 35000
S26 12/256 Black — 65000
Z Fold 7 12/256 Black — 120000"""]).items
        pages = render_blocks(items, Settings())
        await self.publisher.publish(pages)
        edit = self.catalog_edit()
        buttons = [button for row in edit["reply_markup"]["inline_keyboard"] for button in row]
        samsung = [button for button in buttons if button["text"] == "Samsung"]
        self.assertEqual(len(samsung), 1)
        self.assertTrue(samsung[0].get("url"))

    async def test_catalog_keeps_base_groups_compact_and_iphone_models_direct(self):
        items = parse_documents(["""iPhone 16 128 Black — 60000
iPhone 16 Plus 128 Black — 70000
iPhone 16 Pro 256 Black — 80000
Apple Watch Ultra 3 49mm Black — 70000
Samsung A57 8/256 Blue — 32000
Xiaomi 15 12/256 White — 48000
Oura Ring 4 Silver — 35000
Bose Onyx 9 Black — 30000
DJI Osmo Pocket 4 — 45000
PlayStation 5 Pro — 70000
Dyson HS08 — 40000"""]).items
        await self.publisher.publish(render_blocks(items, Settings()))
        buttons = [button for row in self.catalog_edit()["reply_markup"]["inline_keyboard"] for button in row]
        labels = [button["text"] for button in buttons]
        for model in ["iPhone 16", "iPhone 16 Plus", "iPhone 16 Pro"]:
            self.assertIn(model, labels)
        self.assertTrue({"Apple", "Samsung", "Смартфоны", "Часы / носимое", "Аудио", "Фото / видео", "Игры", "Другое"}.issubset(set(labels)))

    async def test_paired_iphone_models_have_separate_buttons_to_same_message(self):
        items = parse_documents(["iPhone 16 128 Black — 60000\niPhone 16 Plus 128 Pink — 70000"]).items
        await self.publisher.publish(render_blocks(items, Settings()))
        manifest = self.state.get("published")["messages"]
        self.assertEqual(len(manifest), 1)
        message_id = next(iter(manifest.values()))["id"]
        buttons = [button for row in self.catalog_edit()["reply_markup"]["inline_keyboard"] for button in row]
        iphone_buttons = {button["text"]: button["url"] for button in buttons if button["text"].startswith("iPhone 16")}
        self.assertEqual(set(iphone_buttons), {"iPhone 16", "iPhone 16 Plus"})
        self.assertEqual(set(iphone_buttons.values()), {f"https://t.me/c/777/{message_id}"})

    async def test_closed_catalog_keeps_buttons_and_removes_prices(self):
        await self.publisher.publish(self.pages())
        await self.publisher.hide_existing()
        entries = self.state.get("published")["messages"].values()
        for entry in entries:
            self.assertIn("Продажи закрыты", entry["content"])
            self.assertNotIn("60 000", entry["content"])
        self.assertEqual(len(self.state.get("catalog")["messages"]), 1)

    async def test_edit_failure_is_retried_without_sending_duplicates(self):
        await self.publisher.publish(self.pages())
        real = self.publisher.api
        failed = False
        async def error(method, **kwargs):
            nonlocal failed
            if method == "editMessageText" and not failed:
                failed = True
                raise RuntimeError("Temporary failure")
            return await real(method, **kwargs)
        self.publisher.api = error
        with self.assertRaises(RuntimeError):
            await self.publisher.publish(self.pages(["Dyson"]))
        self.publisher.calls.clear()
        await self.publisher.publish(self.pages(["Dyson"]))
        self.assertEqual(len(self.state.get("published")["messages"]), len(self.pages(["Dyson"])))


class CatalogControlTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = StateStore(Path(self.temp.name) / "state.json")
        self.options = {}
        def set_option(key, value):
            self.options[key] = value
        self.service = SimpleNamespace(
            cached_items=lambda **kw: parse_documents(["iPhone 17 256 Black — 60000\nDyson HS08 — 40000"]).items,
            options=lambda: self.options, state=self.state, set_option=set_option,
            refresh_format=AsyncMock(return_value=(2, 2)))
        self.controller = CatalogController("TOKEN", self.service, [42])
        self.controller.send = AsyncMock()
        self.controller.answer_callback = AsyncMock()

    async def asyncTearDown(self):
        if self.controller.task:
            await self.controller.task
        self.temp.cleanup()

    def callback(self, data, user=42, kind="private"):
        return {"id": "callback", "data": data, "from": {"id": user},
                "message": {"chat": {"id": user, "type": kind}}}

    async def test_order_changes_only_after_apply(self):
        await self.controller.handle_callback(self.callback("order:show:0"))
        await self.controller.handle_callback(self.callback(f"order:up:{block_id('Dyson')}:0"))
        self.assertEqual(self.options, {})
        await self.controller.handle_callback(self.callback("order:apply"))
        await self.controller.task
        self.assertEqual(self.options["block_order"], ["Dyson", "iPhone 17"])
        self.service.refresh_format.assert_awaited_once()

    async def test_order_screen_includes_blocks_from_all_raw_supplier_caches(self):
        raw = parse_documents(["Samsung Galaxy S26 12/256 Black — 70000\nVivo V70 12/256 Grey — 46300\nOura Ring 4 Silver — 35000"]).items
        self.state.set("sources", {"extra": {"items": [item.to_dict() for item in raw]}})
        await self.controller.show_order(42, 42, 0)
        text = self.controller.send.await_args.args[1]
        keyboard = self.controller.send.await_args.args[2]["inline_keyboard"]
        labels = [button["text"] for row in keyboard for button in row]
        self.assertIn("Samsung", " ".join(labels))
        self.assertIn("Vivo", " ".join(labels))
        self.assertIn("Oura Ring", " ".join(labels))
        self.assertIn("всего 5", text)

    async def test_unknown_user_and_group_cannot_change_order_or_selection(self):
        for callback in [self.callback("catalog:all", user=99), self.callback("order:apply", kind="supergroup")]:
            await self.controller.handle_callback(callback)
        self.assertEqual(self.options, {})
        self.assertIsNone(self.state.get("options"))
        self.service.refresh_format.assert_not_awaited()
