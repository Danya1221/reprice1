import asyncio
import os
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import quote

from telethon import functions, types

from supernova_auto import WEBVIEW_BRIDGE, SupernovaAutoError, click_export_page, supernova_webview
import test_supernova as fixtures


class TelegramWebviewTests(unittest.IsolatedAsyncioTestCase):
    async def test_menu_webview_uses_selected_account_and_telegram_url(self):
        bot = types.User(id=777, access_hash=888, bot=True, first_name="Supplier", bot_has_main_app=True)
        menu = types.BotMenuButton(text="Open", url="https://example.invalid/app")
        calls = []

        async def client(request):
            calls.append(request)
            if isinstance(request, functions.users.GetFullUserRequest):
                return SimpleNamespace(full_user=SimpleNamespace(bot_info=SimpleNamespace(menu_button=menu)))
            return SimpleNamespace(url="https://example.invalid/app#tgWebAppData=private")

        result = await supernova_webview(client, bot)
        self.assertTrue(result.startswith("https://example.invalid/app"))
        self.assertIsInstance(calls[1], functions.messages.RequestWebViewRequest)
        self.assertTrue(calls[1].from_bot_menu)

    async def test_main_app_fallback_and_non_bot_rejection(self):
        bot = types.User(id=777, access_hash=888, bot=True, first_name="Supplier", bot_has_main_app=True)
        calls = []

        async def client(request):
            calls.append(request)
            if isinstance(request, functions.users.GetFullUserRequest):
                return SimpleNamespace(full_user=SimpleNamespace(bot_info=None))
            return SimpleNamespace(url="https://example.invalid/app")

        await supernova_webview(client, bot)
        self.assertIsInstance(calls[1], functions.messages.RequestMainWebViewRequest)
        with self.assertRaises(SupernovaAutoError):
            await supernova_webview(client, SimpleNamespace(bot=False))

    async def test_keyboard_webview_uses_its_button_url(self):
        bot = types.User(id=777, access_hash=888, bot=True, first_name="Supplier")
        button = types.KeyboardButtonWebView("Прайс", "https://example.invalid/app")

        class Client:
            def __init__(self):
                self.calls = []

            async def __call__(self, request):
                self.calls.append(request)
                if isinstance(request, functions.users.GetFullUserRequest):
                    return SimpleNamespace(full_user=SimpleNamespace(bot_info=None))
                return SimpleNamespace(url="https://example.invalid/app")

            async def get_messages(self, peer, limit):
                return [SimpleNamespace(out=False, sender_id=777, buttons=[[button]])]

        client = Client()
        await supernova_webview(client, bot)
        self.assertIsInstance(client.calls[-1], functions.messages.RequestWebViewRequest)
        self.assertEqual(client.calls[-1].url, button.url)


@unittest.skipUnless(os.getenv("RUN_BROWSER_TESTS") == "1", "локальный браузер не включён")
class BrowserFixtureTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_price_and_excel_buttons_are_clicked(self):
        from playwright.async_api import async_playwright
        html = """<button onclick="document.getElementById('excel').hidden=false">Прайс</button>
        <button id="excel" hidden onclick="window.exportClicked=true">⬇️ Скачать прайс-лист (Excel)</button>
        <button onclick="window.paymentClicked=true">Оплатить</button>"""
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True, args=["--no-sandbox"])
            try:
                context = await browser.new_context()
                await context.add_init_script(WEBVIEW_BRIDGE)
                page = await context.new_page()
                await page.goto("data:text/html;charset=utf-8," + quote(html))
                await click_export_page(page)
                self.assertTrue(await page.evaluate("window.exportClicked === true"))
                self.assertFalse(await page.evaluate("window.paymentClicked === true"))
                self.assertEqual(await page.evaluate("typeof TelegramWebviewProxy.postEvent"), "function")
            finally:
                await browser.close()

    async def test_confirmation_request_is_never_accepted_automatically(self):
        from playwright.async_api import async_playwright
        html = "<button onclick='window.exportClicked=true'>Скачать прайс-лист (Excel)</button>"
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True, args=["--no-sandbox"])
            try:
                context = await browser.new_context()
                await context.add_init_script(WEBVIEW_BRIDGE)
                page = await context.new_page()
                await page.goto("data:text/html;charset=utf-8," + quote(html))
                await page.evaluate("TelegramWebviewProxy.postEvent('web_app_open_invoice', '{}')")
                with self.assertRaisesRegex(SupernovaAutoError, "подтверждение"):
                    await click_export_page(page)
                self.assertFalse(await page.evaluate("window.exportClicked === true"))
            finally:
                await browser.close()


class SupernovaAutomaticTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.SupernovaServiceTests.asyncSetUp
    asyncTearDown = fixtures.SupernovaServiceTests.asyncTearDown
    poll_at = fixtures.SupernovaServiceTests.poll_at

    async def test_only_new_verified_file_completes_automatic_request(self):
        self.client.get_messages.side_effect = [[SimpleNamespace(id=9)], [self.msg]]
        async def receive_during_open(client, peer, wait_for_delivery, progress):
            await self.service.poll_supernova()
            await wait_for_delivery()
        request = AsyncMock(side_effect=receive_during_open)
        with patch("runtime.request_supernova_export", new=request), \
             patch("runtime.time.time", return_value=2000000000), patch("runtime.is_open", return_value=True):
            self.assertTrue(self.service.start_supernova_export(force=True))
            await self.service.supernova_auto_task
        request.assert_awaited_once()
        self.client.get_entity.assert_any_await("@vodorod00bot")
        status = self.state.get("supernova_auto")
        self.assertEqual(status["status"], "Excel получен и прочитан")
        self.assertEqual(self.state.get("supernova_snapshot")["message_id"], 10)
        self.assertEqual(status["next_attempt"], 2000000480)

    async def test_failure_keeps_prices_and_never_records_url_or_auth_data(self):
        await self.poll_at(2000000000)
        previous = self.state.get("supernova_snapshot")
        self.client.get_messages.return_value = [SimpleNamespace(id=10)]
        with patch("runtime.request_supernova_export", new=AsyncMock(side_effect=RuntimeError("https://secret.invalid/#tgWebAppData=SECRET"))), \
             patch("runtime.time.time", return_value=2000000001):
            self.assertTrue(self.service.start_supernova_export(force=True))
            await self.service.supernova_auto_task
        status = self.state.get("supernova_auto")
        self.assertEqual(status["status"], "Ошибка обновления")
        self.assertNotIn("SECRET", str(status))
        self.assertEqual(status["next_attempt"], 2000000301)
        self.assertEqual(self.state.get("supernova_snapshot"), previous)

    async def test_failure_reports_stage_without_sensitive_exception_text(self):
        self.client.get_entity.side_effect = RuntimeError("secret auth data")
        self.service.start_supernova_export(force=True)
        await self.service.supernova_auto_task
        status = self.state.get("supernova_auto")
        self.assertEqual(status["status"], "Ошибка обновления")
        self.assertIn("Подключаюсь к боту Supernova", status["error"])
        self.assertIn("RuntimeError", status["error"])
        self.assertNotIn("secret", status["error"])

    async def test_enabling_auto_keeps_previous_error_when_no_new_request_starts(self):
        self.state.set("supernova_auto", {"slot": 1, "enabled": False, "next_attempt": 9999999999,
                                          "status": "Ошибка обновления", "error": "не удалось открыть"})
        await self.service.set_supernova_auto(True)
        status = self.state.get("supernova_auto")
        self.assertEqual(status["status"], "Ошибка обновления")
        self.assertEqual(status["error"], "не удалось открыть")

    async def test_manual_request_sends_error_after_it_finishes(self):
        from control_catalog import CatalogController
        self.client.get_entity.side_effect = RuntimeError("sensitive")
        controller = CatalogController("token", self.service, [42])
        controller.send = AsyncMock()
        controller.answer_callback = AsyncMock()
        callback = {"id": "1", "from": {"id": 42},
                    "message": {"chat": {"id": 42, "type": "private"}},
                    "data": "supernova:request"}
        await controller.handle_callback(callback)
        await asyncio.gather(*list(controller.supernova_notifications))
        texts = [call.args[1] for call in controller.send.await_args_list]
        self.assertTrue(any("Ошибка обновления" in text and "RuntimeError" in text for text in texts))
        self.assertTrue(all("sensitive" not in text for text in texts))

    async def test_disable_auto_cancels_inflight_request(self):
        entered = asyncio.Event()
        async def blocked(*args):
            entered.set()
            await asyncio.Event().wait()
        self.client.get_messages.return_value = [SimpleNamespace(id=9)]
        with patch("runtime.request_supernova_export", new=blocked):
            self.service.start_supernova_export(force=True)
            await asyncio.wait_for(entered.wait(), 1)
            await self.service.set_supernova_auto(False)
        self.assertIsNone(self.service.supernova_auto_task)
        self.assertFalse(self.state.get("supernova_auto")["enabled"])

    async def test_automatic_schedule_respects_moscow_working_hours(self):
        for hour, expected in [(6, False), (7, True), (16, True), (17, False), (23, False)]:
            with self.subTest(utc=hour):
                self.assertEqual(self.service.supernova_working_time(datetime(2026, 11, 5, hour, tzinfo=timezone.utc)), expected)
