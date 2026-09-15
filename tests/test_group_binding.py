import tempfile
import unittest
from pathlib import Path

from bot_publisher import BotAPIPublisher
from config import Settings
from state import StateStore


class FakePublisher(BotAPIPublisher):
    def __init__(self, *args, chats=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.chats = chats or {}

    async def api(self, method, **payload):
        if method == "getMe":
            return {"id": 999, "username": "control_bot"}
        if method == "getChat":
            chat_id = payload["chat_id"]
            if chat_id not in self.chats:
                raise RuntimeError("chat not found")
            return self.chats[chat_id]
        if method == "getChatMember":
            return {"status": "administrator", "can_post_messages": True}
        raise AssertionError(method)


class GroupBindingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = StateStore(Path(self.temp.name) / "state.json")
        self.settings = Settings(admin_ids=(42,), send_delay=0)

    def tearDown(self):
        self.temp.cleanup()

    async def test_private_target_is_never_used_as_publish_destination(self):
        publisher = FakePublisher(
            "token", 6781674751, self.state, self.settings,
            chats={6781674751: {"id": 6781674751, "type": "private", "first_name": "supplier"}},
        )
        with self.assertRaisesRegex(RuntimeError, "/bind"):
            await publisher.ensure_target()
        self.assertIsNone(publisher.target)

    async def test_bind_group_overrides_bad_configured_target_and_persists(self):
        group_id = -1001234567890
        chats = {
            6781674751: {"id": 6781674751, "type": "private", "first_name": "supplier"},
            group_id: {"id": group_id, "type": "supergroup", "title": "Розница"},
        }
        publisher = FakePublisher("token", 6781674751, self.state, self.settings, chats=chats)
        await publisher.bind_group(group_id, title="Розница", chat_type="supergroup")
        self.assertEqual(publisher.target, group_id)
        self.assertEqual(self.state.get("publish_target")["chat_id"], group_id)

        restarted = FakePublisher("token", 6781674751, self.state, self.settings, chats=chats)
        self.assertEqual(await restarted.ensure_target(), group_id)
        self.assertEqual(restarted.target_title, "Розница")


if __name__ == "__main__":
    unittest.main()
