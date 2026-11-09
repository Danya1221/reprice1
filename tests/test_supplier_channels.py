import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from config import Settings, Source
from control_catalog import CatalogController
from prices import Item, ParseResult
from runtime import SyncService
from state import StateStore


class ChannelServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = StateStore(Path(self.temp.name) / "state.json")
        self.settings = Settings(sources=(Source("@hi"), Source("@other", mode="feed")))
        self.client = SimpleNamespace(is_connected=lambda: True, is_user_authorized=AsyncMock(return_value=True),
                                      get_entity=AsyncMock(return_value=SimpleNamespace(
                                          broadcast=True, title="Прайс", username="supplier_channel")),
                                      get_messages=AsyncMock(return_value=[]))
        self.service = SyncService(self.client, self.settings, self.state)

    def tearDown(self):
        self.temp.cleanup()

    async def test_channel_named_price_excludes_iphones_only_for_that_channel(self):
        with patch("runtime.resolve_input_peer", AsyncMock(return_value=object())), patch("runtime.utils.get_peer_id", return_value=-100123):
            await self.service.add_supplier_channel("@supplier_channel")
        channel = self.service.settings.sources[-1]
        iphone = Item("iPhone 17 256 Black", Decimal("60000"), "RUB", "iPhone 17")
        samsung = Item("Samsung S26 256 Black", Decimal("50000"), "RUB", "Samsung")
        self.assertEqual(self.service.source_items(channel, [iphone, samsung]), [samsung])
        self.assertEqual(self.service.source_items(self.service.base_sources[0], [iphone]), [iphone])
        restarted = SyncService(self.client, Settings(sources=self.service.base_sources), StateStore(self.state.path))
        self.assertEqual(restarted.source_items(restarted.settings.sources[-1], [iphone, samsung]), [samsung])
        restarted.ready = False
        await restarted.set_supplier_channel_iphone_filter(channel.peer, enabled=False)
        self.assertEqual(restarted.source_items(channel, [iphone, samsung]), [iphone, samsung])

    async def test_channel_is_persistent_and_not_read_by_second_account(self):
        with patch("runtime.resolve_input_peer", AsyncMock(return_value=object())), patch("runtime.utils.get_peer_id", return_value=-100123):
            await self.service.add_supplier_channel("@supplier_channel")
            with self.assertRaisesRegex(ValueError, "уже добавлен"):
                await self.service.add_supplier_channel("@supplier_channel")
        self.assertEqual(len(self.service.readers_for_slot(1)), 3)
        self.assertEqual(self.service._slot_sources(2), (self.settings.sources[0],))
        self.assertEqual(self.settings.sources[-1].mode, "feed")
        restarted = SyncService(self.client, Settings(sources=self.service.base_sources),
                                StateStore(self.state.path))
        self.assertEqual(restarted.settings.sources[-1].peer, "@supplier_channel")
        await restarted.remove_supplier_channel("@supplier_channel")
        self.assertEqual(len(restarted.settings.sources), 2)
        self.assertEqual(StateStore(self.state.path).get("supplier_channels"), [])

    async def test_lists_only_broadcast_channels_of_account_one(self):
        self.client.get_me = AsyncMock(return_value=SimpleNamespace(username="pricing"))
        async def dialogs(**kwargs):
            self.assertIsNone(kwargs["limit"])
            yield SimpleNamespace(entity=SimpleNamespace(broadcast=True, title="Прайс А"), name="Прайс А")
            yield SimpleNamespace(entity=SimpleNamespace(broadcast=False, title="Группа"), name="Группа")

        self.client.iter_dialogs = dialogs
        with patch("runtime.utils.get_peer_id", return_value=-100123):
            account, channels = await self.service.list_available_supplier_channels()
        self.assertEqual(account, "@pricing")
        self.assertEqual(channels, [{"id": -100123, "title": "Прайс А"}])

    async def test_secondary_channel_is_persisted_and_only_read_by_account_two(self):
        client2 = SimpleNamespace(is_connected=lambda: True, is_user_authorized=AsyncMock(return_value=True),
                                  get_entity=AsyncMock(return_value=SimpleNamespace(
                                      broadcast=True, title="Прайс 2", username="supplier_two")),
                                  get_messages=AsyncMock(return_value=[]))
        self.service.attach_client(client2, 2)
        with patch("runtime.resolve_input_peer", AsyncMock(return_value=object())), patch(
                "runtime.utils.get_peer_id", return_value=-100456):
            await self.service.add_supplier_channel("@supplier_two", slot=2)
        channels = self.state.get("supplier_channels")
        self.assertEqual(channels[0]["slot"], 2)
        self.assertEqual([source.peer for source in self.service._slot_sources(1)], ["@hi", "@other"])
        self.assertEqual([source.peer for source in self.service._slot_sources(2)], ["@hi", "@supplier_two"])
        self.assertEqual(self.service.source_cache_key(self.service._slot_sources(2)[-1], 2),
                         "account2:@supplier_two")
        restarted = SyncService(self.client, Settings(sources=self.service.base_sources),
                                StateStore(self.state.path))
        restarted.attach_client(client2, 2)
        self.assertEqual([source.peer for source in restarted._slot_sources(2)],
                         ["@hi", "@supplier_two"])
        await restarted.remove_supplier_channel("@supplier_two", slot=2)
        self.assertEqual(restarted._slot_sources(2), restarted.base_sources[:1])

    async def test_legacy_channels_stay_on_account_one(self):
        self.state.set("supplier_channels", [{"peer": "@legacy", "id": -100100,
                                                "title": "Старый канал"}])
        restarted = SyncService(self.client, Settings(sources=self.service.base_sources),
                                StateStore(self.state.path))
        self.assertEqual([source.peer for source in restarted._slot_sources(1)],
                         ["@hi", "@other", "@legacy"])
        self.assertEqual([source.peer for source in restarted._slot_sources(2)], ["@hi"])

    async def test_secondary_channel_publishes_its_products(self):
        client2 = SimpleNamespace(is_connected=lambda: True, is_user_authorized=AsyncMock(return_value=True),
                                  get_entity=AsyncMock(return_value=SimpleNamespace(
                                      broadcast=True, title="Прайс 2", username="supplier_two")),
                                  get_messages=AsyncMock(return_value=[]))
        self.service.attach_client(client2, 2)
        with patch("runtime.resolve_input_peer", AsyncMock(return_value=object())), patch(
                "runtime.utils.get_peer_id", return_value=-100456):
            await self.service.add_supplier_channel("@supplier_two", slot=2)
        self.service.readers_for_slot(1)[0].fetch = AsyncMock(return_value=ParseResult([], [], closed=True))
        self.service.readers_for_slot(1)[1].fetch = AsyncMock(return_value=ParseResult([], [], closed=True))
        self.service.readers_for_slot(2)[0].fetch = AsyncMock(return_value=ParseResult([], [], closed=True))
        self.service.readers_for_slot(2)[1].fetch = AsyncMock(return_value=ParseResult([
            Item("Samsung Galaxy S26 256 Black", Decimal("45000"), "RUB", "Samsung")], []))
        self.service.publisher.publish = AsyncMock(return_value=1)
        with patch("runtime.is_open", return_value=True):
            await self.service.sync(force=True)
        cache = self.state.get("sources")
        self.assertIn("account2:@supplier_two", cache)
        self.assertNotIn("@supplier_two", cache)
        body = "\n".join(self.service.publisher.publish.call_args.args[0].values())
        self.assertIn("45 000", body)

    async def test_secondary_channel_requires_connected_account_two(self):
        with self.assertRaisesRegex(ValueError, "аккаунт 2"):
            await self.service.add_supplier_channel("@some", slot=2)
        with self.assertRaisesRegex(ValueError, "аккаунт 2"):
            await self.service.list_available_supplier_channels(slot=2)

    async def test_source_visibility_hides_prices_without_stopping_reading(self):
        with patch("runtime.resolve_input_peer", AsyncMock(return_value=object())), patch(
                "runtime.utils.get_peer_id", return_value=-100123):
            await self.service.add_supplier_channel("@supplier_channel")
        phone = Item("Samsung Galaxy S26 256 Black", Decimal("45000"), "RUB", "Samsung")
        self.service.readers[0].fetch = AsyncMock(return_value=ParseResult([], [], closed=True))
        self.service.readers[1].fetch = AsyncMock(return_value=ParseResult([], [], closed=True))
        feed = self.service.readers[2]
        feed.fetch = AsyncMock(return_value=ParseResult([phone], []))
        self.service.publisher.publish = AsyncMock(return_value=1)
        with patch("runtime.is_open", return_value=True):
            await self.service.sync(force=True)
            self.assertIn("45 000", "\n".join(self.service.publisher.publish.call_args.args[0].values()))
            await self.service.set_supplier_channel_visibility("@supplier_channel", visible=False)
            self.assertEqual(self.service.cached_items(), [])
            self.assertEqual(self.service.publisher.publish.call_args.args[0], {})
            self.assertFalse(self.state.get("supplier_channels")[0]["visible"])
            await self.service.sync(force=True)
            self.assertEqual(feed.fetch.await_count, 2)
            self.assertEqual(self.state.get("sources")["@supplier_channel"]["status"], "open")
            await self.service.set_supplier_channel_visibility("@supplier_channel", visible=True)
            self.assertIn("45 000", "\n".join(self.service.publisher.publish.call_args.args[0].values()))
        restarted = SyncService(self.client, Settings(sources=self.service.base_sources),
                                StateStore(self.state.path))
        self.assertTrue(restarted.source_visible(restarted._slot_sources(1)[-1], 1))

    async def test_failed_parse_records_read_message_count(self):
        with patch("runtime.resolve_input_peer", AsyncMock(return_value=object())), patch(
                "runtime.utils.get_peer_id", return_value=-100123):
            await self.service.add_supplier_channel("@supplier_channel")
        for reader in self.service.readers[:2]:
            reader.fetch = AsyncMock(return_value=ParseResult([], [], closed=True))
        failing = self.service.readers[2]
        failing.last_read_stats = {"messages": 12, "parsed": 0}
        failing.fetch = AsyncMock(side_effect=RuntimeError("Товары не распознаны"))
        with patch("runtime.is_open", return_value=True):
            await self.service.sync(force=True)
        cached = self.state.get("sources")["@supplier_channel"]
        self.assertEqual(cached["messages_count"], 12)
        self.assertEqual(cached["parsed_count"], 0)
        self.assertIn("Товары не распознаны", cached["error"])

    async def test_unreadable_channel_is_not_saved(self):
        self.client.get_messages.side_effect = PermissionError("no access")
        with patch("runtime.resolve_input_peer", AsyncMock(return_value=object())):
            with self.assertRaises(PermissionError):
                await self.service.add_supplier_channel("@hidden")
        self.assertEqual(self.state.get("supplier_channels", []), [])
        self.assertEqual(len(self.service.readers_for_slot(1)), 2)

    async def test_group_is_not_accepted_as_channel(self):
        self.client.get_entity.return_value.broadcast = False
        with patch("runtime.resolve_input_peer", AsyncMock(return_value=object())):
            with self.assertRaisesRegex(ValueError, "не канал"):
                await self.service.add_supplier_channel("@group")
        self.assertEqual(self.state.get("supplier_channels", []), [])

    async def test_channel_products_are_published_and_not_filtered_by_hi_rule(self):
        with patch("runtime.resolve_input_peer", AsyncMock(return_value=object())), patch("runtime.utils.get_peer_id", return_value=-100123):
            await self.service.add_supplier_channel("@supplier_channel")
        phone = Item("Samsung Galaxy S26 256 Black", Decimal("45000"), "RUB", "Samsung")
        self.service.readers[0].fetch = AsyncMock(return_value=ParseResult([], [], closed=True))
        self.service.readers[1].fetch = AsyncMock(return_value=ParseResult([], [], closed=True))
        self.service.readers[2].fetch = AsyncMock(return_value=ParseResult([phone], []))
        self.service.publisher.publish = AsyncMock(return_value=1)
        with patch("runtime.is_open", return_value=True):
            await self.service.sync()
        pages = self.service.publisher.publish.await_args.args[0]
        self.assertIn("Samsung", "\n".join(pages.values()))
        self.assertIn("45 000", "\n".join(pages.values()))


class ChannelControllerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = StateStore(Path(self.temp.name) / "state.json")
        self.service = SimpleNamespace(state=self.state, add_supplier_channel=AsyncMock(return_value="Опт"),
                                       list_available_supplier_channels=AsyncMock(
                                           return_value=("@pricing", [{"id": -100123, "title": "Опт"}])),
                                       remove_supplier_channel=AsyncMock(), publisher=SimpleNamespace(bind_group=AsyncMock()))
        self.controller = CatalogController("token", self.service, [42])
        self.controller.send = AsyncMock()
        self.controller.answer_callback = AsyncMock()
        self.chat = {"id": 42, "type": "private"}

    def tearDown(self):
        self.temp.cleanup()

    async def test_forward_adds_source_without_rebinding_publish_destination(self):
        await self.controller.handle_callback({"id": "1", "from": {"id": 42},
                                               "message": {"chat": self.chat}, "data": "channels:add"})
        await self.controller.handle_callback({"id": "2", "from": {"id": 42},
                                               "message": {"chat": self.chat}, "data": "channels:account:1"})
        await self.controller.handle_message({"from": {"id": 42}, "chat": self.chat,
             "forward_origin": {"type": "channel", "chat": {"id": -100123, "type": "channel", "title": "Опт"}}})
        self.service.add_supplier_channel.assert_awaited_once_with(-100123, slot=1)
        self.service.publisher.bind_group.assert_not_awaited()
        self.assertNotIn(42, self.controller.channel_waiting)

    async def test_username_and_cancel(self):
        self.controller.channel_waiting.add(42)
        await self.controller.handle_message({"from": {"id": 42}, "chat": self.chat, "text": "https://t.me/supplier_channel"})
        self.service.add_supplier_channel.assert_awaited_once_with("@supplier_channel", slot=1)
        self.controller.channel_waiting.add(42)
        await self.controller.handle_message({"from": {"id": 42}, "chat": self.chat, "text": "/cancel"})
        self.assertNotIn(42, self.controller.channel_waiting)

    async def test_select_channel_from_account_dialogs(self):
        await self.controller.handle_callback({"id": "1", "from": {"id": 42},
                                               "message": {"chat": self.chat}, "data": "channels:add"})
        await self.controller.handle_callback({"id": "2", "from": {"id": 42},
                                               "message": {"chat": self.chat}, "data": "channels:account:1"})
        await self.controller.handle_callback({"id": "3", "from": {"id": 42},
                                               "message": {"chat": self.chat}, "data": "channels:list:1:0"})
        self.service.list_available_supplier_channels.assert_awaited_once_with(slot=1)
        self.assertIn("Опт", str(self.controller.send.await_args))
        await self.controller.handle_callback({"id": "4", "from": {"id": 42},
                                               "message": {"chat": self.chat}, "data": "channels:pick:1:-100123"})
        self.service.add_supplier_channel.assert_awaited_once_with(-100123, slot=1)
        self.assertNotIn(42, self.controller.channel_waiting)

    async def test_unlisted_channel_cannot_be_picked(self):
        await self.controller.handle_callback({"id": "1", "from": {"id": 42},
                                               "message": {"chat": self.chat}, "data": "channels:add"})
        await self.controller.handle_callback({"id": "2", "from": {"id": 42},
                                               "message": {"chat": self.chat}, "data": "channels:account:1"})
        await self.controller.handle_callback({"id": "3", "from": {"id": 42},
                                               "message": {"chat": self.chat}, "data": "channels:list:1:0"})
        await self.controller.handle_callback({"id": "4", "from": {"id": 42},
                                               "message": {"chat": self.chat}, "data": "channels:pick:1:-100987"})
        self.service.add_supplier_channel.assert_not_awaited()

    async def test_forward_resolution_failure_offers_channel_picker(self):
        self.service.add_supplier_channel.side_effect = RuntimeError("Не найден access_hash")
        await self.controller.handle_callback({"id": "1", "from": {"id": 42},
                                               "message": {"chat": self.chat}, "data": "channels:add"})
        await self.controller.handle_callback({"id": "2", "from": {"id": 42},
                                               "message": {"chat": self.chat}, "data": "channels:account:1"})
        await self.controller.handle_message({"from": {"id": 42}, "chat": self.chat,
            "forward_origin": {"type": "channel", "chat": {"id": -100123, "type": "channel"}}})
        markup = self.controller.send.await_args.args[2]
        self.assertEqual(markup["inline_keyboard"][0][0]["callback_data"], "channels:list:1:0")
        self.assertIn(42, self.controller.channel_waiting)

    async def test_account_two_is_used_for_selected_and_forwarded_channels(self):
        await self.controller.handle_callback({"id": "1", "from": {"id": 42},
                                               "message": {"chat": self.chat}, "data": "channels:add"})
        await self.controller.handle_callback({"id": "2", "from": {"id": 42},
                                               "message": {"chat": self.chat}, "data": "channels:account:2"})
        await self.controller.handle_callback({"id": "3", "from": {"id": 42},
                                               "message": {"chat": self.chat}, "data": "channels:list:2:0"})
        self.service.list_available_supplier_channels.assert_awaited_once_with(slot=2)
        await self.controller.handle_callback({"id": "4", "from": {"id": 42},
                                               "message": {"chat": self.chat}, "data": "channels:pick:2:-100123"})
        self.service.add_supplier_channel.assert_awaited_once_with(-100123, slot=2)

        self.service.add_supplier_channel.reset_mock()
        await self.controller.handle_callback({"id": "5", "from": {"id": 42},
                                               "message": {"chat": self.chat}, "data": "channels:add"})
        await self.controller.handle_callback({"id": "6", "from": {"id": 42},
                                               "message": {"chat": self.chat}, "data": "channels:account:2"})
        await self.controller.handle_message({"from": {"id": 42}, "chat": self.chat,
            "forward_origin": {"type": "channel", "chat": {"id": -100987, "type": "channel"}}})
        self.service.add_supplier_channel.assert_awaited_once_with(-100987, slot=2)

    async def test_cannot_pick_channel_from_other_account(self):
        await self.controller.handle_callback({"id": "1", "from": {"id": 42},
                                               "message": {"chat": self.chat}, "data": "channels:add"})
        await self.controller.handle_callback({"id": "2", "from": {"id": 42},
                                               "message": {"chat": self.chat}, "data": "channels:account:1"})
        await self.controller.handle_callback({"id": "3", "from": {"id": 42},
                                               "message": {"chat": self.chat}, "data": "channels:list:1:0"})
        await self.controller.handle_callback({"id": "4", "from": {"id": 42},
                                               "message": {"chat": self.chat}, "data": "channels:pick:2:-100123"})
        self.service.add_supplier_channel.assert_not_awaited()

    async def test_channel_status_and_visibility_buttons(self):
        self.state.set("supplier_channels", [{"peer": "@supplier_channel", "id": -100123,
                                               "title": "Опт", "slot": 2, "visible": True}])
        self.state.set("sources", {"account2:@supplier_channel": {
            "checked": "2026-11-02T11:30:00+00:00", "last_success": "2026-11-02T11:30:00+00:00",
            "status": "open", "items": [], "messages_count": 17, "parsed_count": 0,
            "rejected_count": 3, "error": None,
        }})
        await self.controller.show_channels(42)
        body = self.controller.send.await_args.args[1]
        self.assertIn("Сообщений: 17", body)
        self.assertIn("товаров: 0", body)
        self.assertIn("Сообщения прочитаны", body)
        self.service.set_supplier_channel_visibility = AsyncMock(return_value=False)
        token = __import__("control_channels").channel_token("@supplier_channel", 2)
        await self.controller.handle_callback({"id": "1", "from": {"id": 42},
                                               "message": {"chat": self.chat},
                                               "data": "channels:toggle:" + token})
        self.service.set_supplier_channel_visibility.assert_awaited_once_with(
            "@supplier_channel", slot=2, visible=False)

    async def test_details_explain_global_filter_rejection(self):
        self.state.set("supplier_channels", [{"peer": "@supplier_channel", "id": -100123,
                                               "title": "Опт", "slot": 1, "visible": True}])
        product = Item("Samsung Galaxy S26 256 Black", Decimal("45000"), "RUB", "Samsung")
        self.state.set("sources", {"@supplier_channel": {
            "checked": "2026-11-02T11:30:00+00:00", "status": "open",
            "items": [product.to_dict()], "messages_count": 22, "parsed_count": 1}})
        self.service.source_items = lambda source, items: items
        self.service.settings = Settings()
        self.service.options = lambda: {"disabled_blocks": [Item.from_dict(product.to_dict()).block]}
        await self.controller.show_channel_details(42, self.state.get("supplier_channels")[0])
        text = self.controller.send.await_args.args[1]
        self.assertIn("Прошло общие фильтры: 0", text)
        self.assertIn("Товары отфильтрованы", text)

    async def test_other_user_cannot_add_channel(self):
        await self.controller.handle_callback({"id": "1", "from": {"id": 99},
                                               "message": {"chat": {"id": 99, "type": "private"}}, "data": "channels:add"})
        self.assertNotIn(99, self.controller.channel_waiting)
        self.service.add_supplier_channel.assert_not_awaited()
