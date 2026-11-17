import asyncio
from types import SimpleNamespace

import unittest

from control_botapi import BotAPIController


class ControlResponsivenessTest(unittest.IsolatedAsyncioTestCase):
 async def test_start_is_processed_while_callback_is_waiting(self):
    service = SimpleNamespace(stop_event=asyncio.Event())
    controller = BotAPIController('test', service, [])
    callback_entered = asyncio.Event()
    release_callback = asyncio.Event()
    start_received = asyncio.Event()
    polling_calls = 0

    async def api(method, **kwargs):
        nonlocal polling_calls
        polling_calls += 1
        if polling_calls == 1:
            return [{'update_id': 1, 'callback_query': {'data': 'slow'}}]
        if polling_calls == 2:
            await callback_entered.wait()
            return [{'update_id': 2, 'message': {'text': '/start'}}]
        await asyncio.Event().wait()

    async def callback(update):
        callback_entered.set()
        await release_callback.wait()

    async def message(update):
        assert update['text'] == '/start'
        start_received.set()

    controller.api = api
    controller.handle_callback = callback
    controller.handle_message = message
    task = asyncio.create_task(controller.run())
    try:
        await asyncio.wait_for(start_received.wait(), timeout=2)
        assert not release_callback.is_set()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
