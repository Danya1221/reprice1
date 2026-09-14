import asyncio
import logging
import signal
from contextlib import suppress

from telethon import TelegramClient
from telethon.sessions import StringSession

from config import Settings
from control import Controller
from runtime import SyncService
from state import StateStore

log = logging.getLogger(__name__)


async def main():
    settings = Settings.from_env()
    state = StateStore(settings.state_file)
    state.acquire()
    client = None
    control_client = None
    controller = None
    try:
        try:
            session = StringSession(settings.session)
        except Exception as exc:
            raise ValueError("SESSION_STRING повреждена; скопируй всю строку из SETUP_MODE") from exc
        client = TelegramClient(session, settings.api_id, settings.api_hash,
                                auto_reconnect=True, connection_retries=5, retry_delay=2,
                                request_retries=3, flood_sleep_threshold=0)
        service = SyncService(client, settings, state)
        await service.connect()
        me = await client.get_me()
        # Populate access hashes for private channels supplied as numeric IDs.
        await client.get_dialogs(limit=None)
        target = await client.get_entity(settings.target)
        permissions = await client.get_permissions(target, me)
        if not (permissions.is_admin or permissions.is_creator):
            raise RuntimeError("Аккаунт SESSION_STRING должен быть администратором канала с правами публикации и редактирования")
        for reader in service.readers:
            await reader.resolve()
        if settings.bot_token:
            control_client = TelegramClient(StringSession(), settings.api_id, settings.api_hash,
                                             flood_sleep_threshold=0)
            await control_client.start(bot_token=settings.bot_token)
            controller = Controller(control_client, service, settings.admin_ids or (me.id,))
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            with suppress(NotImplementedError):
                loop.add_signal_handler(sig, lambda: (service.stop_event.set(), service.wake.set()))
        log.info("reprice1 запущен; источников: %s", len(service.readers))
        runner = asyncio.create_task(service.run())
        stopper = asyncio.create_task(service.stop_event.wait())
        try:
            done, _ = await asyncio.wait({runner, stopper}, return_when=asyncio.FIRST_COMPLETED)
            if runner in done:
                await runner
        finally:
            runner.cancel()
            stopper.cancel()
            await asyncio.gather(runner, stopper, return_exceptions=True)
    finally:
        if controller:
            await controller.close()
        if control_client:
            await control_client.disconnect()
        if client:
            await client.disconnect()
        state.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
