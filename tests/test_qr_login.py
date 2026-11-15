import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from telethon.errors import SessionPasswordNeededError
from control_botapi import BotAPIController


class QRLoginTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.state = SimpleNamespace(set=MagicMock())
        self.service = SimpleNamespace(state=self.state, settings=SimpleNamespace(api_id=1, api_hash="hash"),
                                       request_reconnect=MagicMock(), account_errors={})
        self.bot = BotAPIController("token", self.service, [42])
        self.bot.send = AsyncMock()
        self.bot.send_login_qr = AsyncMock(return_value=99)
        self.bot.api = AsyncMock()
        self.bot.menu = lambda: {}
        self.qr = SimpleNamespace(url="tg://login?token=test", wait=AsyncMock())
        self.client = SimpleNamespace(connect=AsyncMock(), disconnect=AsyncMock(),
                                      qr_login=AsyncMock(return_value=self.qr),
                                      session=SimpleNamespace(save=lambda: "session"), sign_in=AsyncMock())

    async def test_qr_saves_selected_account_and_removes_code(self):
        with patch("control_botapi.TelegramClient", return_value=self.client):
            await self.bot.begin_qr_login(42, 42, 2)
            await self.bot.login_flows[42]["qr_task"]
        self.state.set.assert_called_once_with("session_string_2", "session")
        self.service.request_reconnect.assert_called_once()
        self.client.disconnect.assert_awaited_once()
        self.bot.api.assert_awaited_with("deleteMessage", chat_id=42, message_id=99)
        self.assertNotIn(42, self.bot.login_flows)

    async def test_2fa_uses_existing_password_flow(self):
        self.qr.wait.side_effect = SessionPasswordNeededError(request=None)
        with patch("control_botapi.TelegramClient", return_value=self.client):
            await self.bot.begin_qr_login(42, 42, 1)
            await self.bot.login_flows[42]["qr_task"]
        self.assertEqual(self.bot.login_flows[42]["stage"], "password")
        await self.bot.handle_login_input(42, 42, "password")
        self.state.set.assert_called_once_with("session_string", "session")
        self.client.sign_in.assert_awaited_once_with(password="password")

    async def test_cancel_cleans_up_running_qr(self):
        async def wait_forever():
            await asyncio.Event().wait()
        self.qr.wait.side_effect = wait_forever
        with patch("control_botapi.TelegramClient", return_value=self.client):
            await self.bot.begin_qr_login(42, 42, 1)
            await self.bot._close_login(42)
        self.client.disconnect.assert_awaited_once()
        self.bot.api.assert_awaited_with("deleteMessage", chat_id=42, message_id=99)
        self.state.set.assert_not_called()

    async def test_expired_code_is_removed(self):
        self.qr.wait.side_effect = asyncio.TimeoutError()
        with patch("control_botapi.TelegramClient", return_value=self.client):
            await self.bot.begin_qr_login(42, 42, 1)
            await self.bot.login_flows[42]["qr_task"]
        self.assertNotIn(42, self.bot.login_flows)
        self.bot.api.assert_awaited_with("deleteMessage", chat_id=42, message_id=99)
        self.assertIn("истёк", self.bot.send.await_args.args[1])
