import asyncio
import unittest
from types import SimpleNamespace

from config import Settings, Source
from supplier import SupplierReader, SupplierTimeout, button_label


def message(number, text):
    return SimpleNamespace(id=number, raw_text=text, buttons=[], document=None, out=False)


class FakeSource:
    def __init__(self):
        self.rows = [message(1, "Старое меню")]
        self.handlers = []

    async def get_messages(self, *args, **kwargs):
        return list(reversed(self.rows))

    def add_event_handler(self, handler, builder):
        self.handlers.append(handler)

    def remove_event_handler(self, handler, builder):
        self.handlers.remove(handler)


class SupplierTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = FakeSource()
        self.reader = SupplierReader(self.client, Settings(
            action_delay=0.001, quiet_seconds=0.006, response_timeout=0.04), Source("@supplier"))
        self.reader.entity = "@supplier"

    async def test_stale_menu_is_never_a_reply(self):
        async def action():
            pass
        with self.assertRaises(SupplierTimeout):
            await self.reader.collect_action(action)
        self.assertEqual(self.client.handlers, [])

    async def test_same_message_edit_is_fresh(self):
        async def action():
            self.client.rows[0].raw_text = "iPhone 17 256 Black — 60000"
        replies = await self.reader.collect_action(action)
        self.assertEqual([m.id for m in replies], [1])
        self.assertIn("60000", replies[0].raw_text)

    async def test_new_reply_wins_over_old_menu(self):
        async def action():
            self.client.rows.append(message(2, "iPhone 17 256 Black — 60000"))
        replies = await self.reader.collect_action(action)
        self.assertEqual([m.id for m in replies], [2])

    async def test_collects_all_multipart_replies(self):
        async def action():
            self.client.rows.extend([message(2, "iPhone 17 256 Black — 60000"),
                                     message(3, "Dyson HS08 — 40000")])
        replies = await self.reader.collect_action(action)
        self.assertEqual([m.id for m in replies], [2, 3])

    async def test_deleted_reply_is_retained_from_event(self):
        async def action():
            event = SimpleNamespace(message=message(2, "iPhone 17 256 Black — 60000"))
            await self.client.handlers[0](event)
            # The message never reaches history: supplier deleted it immediately.
        replies = await self.reader.collect_action(action)
        self.assertEqual([m.id for m in replies], [2])

    async def test_cleanup_after_action_error(self):
        async def action():
            raise ConnectionError("disconnected")
        with self.assertRaises(ConnectionError):
            await self.reader.collect_action(action)
        self.assertEqual(self.client.handlers, [])

    def test_button_labels_ignore_emoji(self):
        self.assertEqual(button_label("📦 Прайс"), button_label("Прайс"))
