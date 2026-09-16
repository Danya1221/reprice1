from pathlib import Path

p = Path("bot_publisher.py")
s = p.read_text(encoding="utf-8")
start = s.index("    async def api(self, method, **payload):\n")
end = s.index("    def _target_candidates(self):\n", start)
new = '''    def _new_http(self):
        return aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=40))

    async def _reset_http(self):
        old = self.http
        self.http = None
        if old is not None and not old.closed:
            with suppress(Exception):
                await old.close()

    @staticmethod
    def _transport_retry_safe(method, exc):
        # Repeating sendMessage after a dropped response can create a duplicate:
        # Telegram may have accepted the message even though aiohttp never saw the
        # response. Connector failures happen before a request is sent and are safe.
        if isinstance(exc, aiohttp.ClientConnectorError):
            return True
        return method not in {"sendMessage", "copyMessage", "forwardMessage"}

    async def api(self, method, **payload):
        """Call Bot API with 429, HTTP 5xx and transient transport retries.

        Telegram occasionally closes an idle keep-alive connection and aiohttp raises
        ServerDisconnectedError. For idempotent Bot API calls we discard that stale
        ClientSession, reconnect and retry instead of failing the whole price sync.
        """
        if not self.token:
            raise RuntimeError("BOT_TOKEN не задан: без него нельзя публиковать прайс")

        max_attempts = 5
        for attempt in range(max_attempts):
            if self.http is None or self.http.closed:
                self.http = self._new_http()
            try:
                async with self.http.post(f"{self.base}/{method}", json=payload) as response:
                    data = await response.json(content_type=None)
                    status = response.status
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                retry_safe = self._transport_retry_safe(method, exc)
                await self._reset_http()
                if retry_safe and attempt < max_attempts - 1:
                    await asyncio.sleep(min(1 + attempt, 3))
                    continue
                raise RuntimeError(
                    f"Bot API {method}: временный сбой соединения: {type(exc).__name__}: {exc}"
                ) from exc

            if data.get("ok"):
                return data.get("result")

            parameters = data.get("parameters") or {}
            retry_after = parameters.get("retry_after")
            throttled = status == 429 or retry_after is not None
            if throttled and attempt < max_attempts - 1:
                try:
                    wait_seconds = max(1, int(retry_after or 1))
                except (TypeError, ValueError):
                    wait_seconds = 1
                await asyncio.sleep(wait_seconds + 1)
                continue

            if status in {500, 502, 503, 504} and method not in {"sendMessage", "copyMessage", "forwardMessage"} and attempt < max_attempts - 1:
                await self._reset_http()
                await asyncio.sleep(min(1 + attempt, 3))
                continue

            raise RuntimeError(f"Bot API {method}: {data.get('description', data)}")

        raise RuntimeError(f"Bot API {method}: превышено число повторных попыток")

'''
s = s[:start] + new + s[end:]
p.write_text(s, encoding="utf-8")

# Add focused transport-level regression tests without touching existing publisher fakes.
t = Path("tests/test_botapi_transport.py")
t.write_text('''import tempfile\nimport unittest\nfrom pathlib import Path\n\nimport aiohttp\n\nfrom bot_publisher import BotAPIPublisher\nfrom config import Settings\nfrom state import StateStore\n\n\nclass FakeResponse:\n    def __init__(self, data, status=200):\n        self.data = data\n        self.status = status\n\n    async def json(self, content_type=None):\n        return self.data\n\n\nclass FakeContext:\n    def __init__(self, outcome):\n        self.outcome = outcome\n\n    async def __aenter__(self):\n        if isinstance(self.outcome, BaseException):\n            raise self.outcome\n        return self.outcome\n\n    async def __aexit__(self, exc_type, exc, tb):\n        return False\n\n\nclass FakeSession:\n    def __init__(self, outcomes):\n        self.outcomes = list(outcomes)\n        self.closed = False\n        self.calls = []\n\n    def post(self, url, json):\n        self.calls.append((url, json))\n        if not self.outcomes:\n            raise AssertionError("No fake HTTP outcome left")\n        return FakeContext(self.outcomes.pop(0))\n\n    async def close(self):\n        self.closed = True\n\n\nclass BotAPITransportTests(unittest.IsolatedAsyncioTestCase):\n    def setUp(self):\n        self.temp = tempfile.TemporaryDirectory()\n        self.state = StateStore(Path(self.temp.name) / "state.json")\n        self.publisher = BotAPIPublisher("TOKEN", -100777, self.state, Settings(send_delay=0))\n\n    def tearDown(self):\n        self.temp.cleanup()\n\n    async def test_edit_reconnects_after_server_disconnected(self):\n        first = FakeSession([aiohttp.ServerDisconnectedError("Server disconnected")])\n        second = FakeSession([FakeResponse({"ok": True, "result": True})])\n        sessions = [first, second]\n        self.publisher._new_http = lambda: sessions.pop(0)\n\n        result = await self.publisher.api(\n            "editMessageText", chat_id=-100777, message_id=123, text="updated"\n        )\n\n        self.assertTrue(result)\n        self.assertTrue(first.closed)\n        self.assertEqual(len(second.calls), 1)\n\n    async def test_send_message_does_not_blind_retry_ambiguous_disconnect(self):\n        first = FakeSession([aiohttp.ServerDisconnectedError("Server disconnected")])\n        unused = FakeSession([FakeResponse({"ok": True, "result": {"message_id": 1}})])\n        sessions = [first, unused]\n        self.publisher._new_http = lambda: sessions.pop(0)\n\n        with self.assertRaisesRegex(RuntimeError, "ServerDisconnectedError"):\n            await self.publisher.api("sendMessage", chat_id=-100777, text="hello")\n\n        self.assertTrue(first.closed)\n        self.assertEqual(len(unused.calls), 0)\n\n    async def test_edit_retries_bot_api_502(self):\n        session = FakeSession([\n            FakeResponse({"ok": False, "description": "Bad Gateway"}, status=502),\n        ])\n        second = FakeSession([FakeResponse({"ok": True, "result": True})])\n        sessions = [session, second]\n        self.publisher._new_http = lambda: sessions.pop(0)\n\n        result = await self.publisher.api(\n            "editMessageText", chat_id=-100777, message_id=123, text="updated"\n        )\n\n        self.assertTrue(result)\n        self.assertTrue(session.closed)\n\n\nif __name__ == "__main__":\n    unittest.main()\n''', encoding="utf-8")
