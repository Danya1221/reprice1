import tempfile
import unittest
from pathlib import Path

from bot_publisher import BotAPIPublisher
from config import Settings
from state import StateStore


class FakeBotPublisher(BotAPIPublisher):
    def __init__(self, target, state, settings, chats, members=None):
        super().__init__("TOKEN", target, state, settings)
        self.chats = chats
        self.members = members or {}
        self.calls = []

    async def api(self, method, **payload):
        self.calls.append((method, payload))
        if method == "getMe":
            return {"id": 999, "username": "control_bot"}
        if method == "getChat":
            chat_id = payload["chat_id"]
            if chat_id not in self.chats:
                raise RuntimeError("chat not found")
            return dict(self.chats[chat_id])
        if method == "getChatMember":
            chat_id = payload["chat_id"]
            return dict(self.members.get(chat_id, {"status": "administrator", "can_post_messages": True}))
        if method == "sendMessage":
            return {"message_id": 123}
        if method in {"editMessageText", "deleteMessage"}:
            return True
        raise AssertionError(method)


class BotPublisherTargetTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = StateStore(Path(self.temp.name) / "state.json")

    def tearDown(self):
        self.temp.cleanup()

    async def test_supplier_or_user_id_is_never_used_as_destination(self):
        settings = Settings(send_delay=0, admin_ids=(42,))
        chats = {
            6781674751: {"id": 6781674751, "type": "private", "username": "supplier_bot"},
            42: {"id": 42, "type": "private", "first_name": "Admin"},
        }
        publisher = FakeBotPublisher(6781674751, self.state, settings, chats)

        with self.assertRaisesRegex(RuntimeError, "/bind"):
            await publisher.ensure_target()

        self.assertIsNone(publisher.target)
        self.assertFalse(publisher.used_admin_fallback)

    async def test_valid_channel_is_used_directly(self):
        settings = Settings(send_delay=0, admin_ids=(42,))
        chats = {
            "@prices": {"id": -100123, "type": "channel", "title": "Prices"},
            42: {"id": 42, "type": "private", "first_name": "Admin"},
        }
        publisher = FakeBotPublisher("@prices", self.state, settings, chats)

        target = await publisher.ensure_target()

        self.assertEqual(target, -100123)
        self.assertFalse(publisher.used_admin_fallback)

    async def test_publish_works_after_persistent_group_binding(self):
        settings = Settings(send_delay=0, admin_ids=(42,))
        group_id = -100777
        chats = {
            6781674751: {"id": 6781674751, "type": "private", "username": "supplier_bot"},
            group_id: {"id": group_id, "type": "supergroup", "title": "Розница"},
        }
        publisher = FakeBotPublisher(6781674751, self.state, settings, chats)
        await publisher.bind_group(group_id, title="Розница", chat_type="supergroup")

        changes = await publisher.publish({"iphone:0": "Прайс"})

        self.assertEqual(changes, 1)
        self.assertTrue(any(method == "sendMessage" and payload["chat_id"] == group_id
                            for method, payload in publisher.calls))


if __name__ == "__main__":
    unittest.main()
