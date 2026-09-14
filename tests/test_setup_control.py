import re
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp.test_utils import TestClient, TestServer

from control import Controller
from session_web import create_app, page


class SetupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = TestClient(TestServer(create_app(123, "test-api-hash", "setup-test-key")))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()

    async def test_index_renders_css_braces_and_no_store(self):
        response = await self.client.get("/")
        text = await response.text()
        self.assertEqual(response.status, 200)
        self.assertIn("body{font-family", text)
        self.assertNotIn("__BODY__", text)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertNotIn("setup-test-key", text)

    async def test_wrong_key_rejected_before_telegram_connection(self):
        with patch("session_web.TelegramClient") as telegram:
            response = await self.client.post("/phone", data={"key": "wrong", "phone": "+123"})
            self.assertEqual(response.status, 403)
            telegram.assert_not_called()

    async def test_correct_key_requires_csrf(self):
        response = await self.client.post("/phone", data={"key": "setup-test-key", "phone": "+123"})
        self.assertEqual(response.status, 403)

    async def test_code_requires_matching_login_flow(self):
        response = await self.client.get("/")
        text = await response.text()
        csrf = re.search(r'name="csrf" value="([^"]+)"', text)[1]
        response = await self.client.post("/code", data={"key": "setup-test-key", "csrf": csrf, "code": "12345"})
        self.assertEqual(response.status, 400)

    async def test_login_disconnects_session_after_export(self):
        response = await self.client.get("/")
        csrf = re.search(r'name="csrf" value="([^"]+)"', await response.text())[1]
        fake = SimpleNamespace(connect=AsyncMock(), disconnect=AsyncMock(),
            send_code_request=AsyncMock(return_value=SimpleNamespace(phone_code_hash="hash")),
            sign_in=AsyncMock(), is_user_authorized=AsyncMock(return_value=True),
            session=SimpleNamespace(save=lambda: "FAKE_SESSION_FOR_TEST"))
        with patch("session_web.TelegramClient", return_value=fake):
            response = await self.client.post("/phone", data={
                "key": "setup-test-key", "csrf": csrf, "phone": "+1234567890"})
            self.assertEqual(response.status, 200)
            response = await self.client.post("/code", data={
                "key": "setup-test-key", "csrf": csrf, "code": "12345"})
            text = await response.text()
            self.assertEqual(response.status, 200)
            self.assertIn("FAKE_SESSION_FOR_TEST", text)
            fake.disconnect.assert_awaited_once()


class ControlTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = MagicMock()
        self.service = MagicMock()
        self.service.pause = AsyncMock()
        self.controller = Controller(self.client, self.service, [42])

    async def test_unknown_user_cannot_control(self):
        event = SimpleNamespace(is_private=True, sender_id=99, raw_text="/stop", respond=AsyncMock())
        await self.controller.message(event)
        self.service.pause.assert_not_awaited()
        event.respond.assert_not_awaited()

    async def test_group_cannot_control(self):
        event = SimpleNamespace(is_private=False, sender_id=42, raw_text="/stop", respond=AsyncMock())
        await self.controller.message(event)
        self.service.pause.assert_not_awaited()

    async def test_callback_answer_happens_before_slow_operation(self):
        order = []
        async def answer():
            order.append("answer")
        async def pause():
            order.append("pause")
        self.service.pause = pause
        event = SimpleNamespace(is_private=True, sender_id=42, data=b"pause",
                                answer=answer, respond=AsyncMock())
        await self.controller.callback(event)
        self.assertEqual(order, ["answer", "pause"])
