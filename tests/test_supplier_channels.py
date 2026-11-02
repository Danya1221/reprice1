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
        await self.controller.handle_message({"from": {"id": 42}, "chat": self.chat,
             "forward_origin": {"type": "channel", "chat": {"id": -100123, "type": "channel", "title": "Опт"}}})
        self.service.add_supplier_channel.assert_awaited_once_with(-100123)
        self.service.publisher.bind_group.assert_not_awaited()
        self.assertNotIn(42, self.controller.channel_waiting)

    async def test_username_and_cancel(self):
        self.controller.channel_waiting.add(42)
        await self.controller.handle_message({"from": {"id": 42}, "chat": self.chat, "text": "https://t.me/supplier_channel"})
        self.service.add_supplier_channel.assert_awaited_once_with("@supplier_channel")
        self.controller.channel_waiting.add(42)
        await self.controller.handle_message({"from": {"id": 42}, "chat": self.chat, "text": "/cancel"})
        self.assertNotIn(42, self.controller.channel_waiting)

    async def test_other_user_cannot_add_channel(self):
        await self.controller.handle_callback({"id": "1", "from": {"id": 99},
                                               "message": {"chat": {"id": 99, "type": "private"}}, "data": "channels:add"})
        self.assertNotIn(99, self.controller.channel_waiting)
        self.service.add_supplier_channel.assert_not_awaited()
