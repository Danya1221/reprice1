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


async def prepare_supplier(service, settings, controller=None):
    """Supplier failures must not prevent the independent control bot from answering."""
    settings.validate()
    try:
        session = StringSession(settings.session)
    except Exception:
        raise ValueError("SESSION_STRING повреждена; скопируй всю строку из SETUP_MODE") from None
    client = TelegramClient(session, settings.api_id, settings.api_hash,
                            auto_reconnect=True, connection_retries=5, retry_delay=2,
                            request_retries=3, flood_sleep_threshold=0)
    service.attach_client(client)
    await service.connect()
    me = await client.get_me()
    if me is None or me.bot:
        raise ValueError("SESSION_STRING должна принадлежать пользовательскому аккаунту, которому доступны прайсы")
    if controller is not None and not settings.admin_ids:
        # Keep the original owner fallback, but only after Telegram verifies that identity.
        controller.admins = {me.id}
    await client.get_dialogs(limit=None)
    target = await client.get_entity(settings.target)
    permissions = await client.get_permissions(target, me)
    if not (permissions.is_admin or permissions.is_creator):
        raise RuntimeError("Аккаунт SESSION_STRING должен быть администратором целевого канала")
    for reader in service.readers:
        await reader.resolve()
    service.startup_error = None
    service.ready = True
    log.info("Чтение прайсов готово; источников: %s", len(service.readers))


async def run_supplier(service, settings, controller=None, retry_seconds=30):
    while not service.stop_event.is_set():
        service.ready = False
        try:
            await asyncio.wait_for(prepare_supplier(service, settings, controller), timeout=90)
            await service.run()
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            service.startup_error = str(exc) or type(exc).__name__
            service.ready = False
            log.error("Чтение прайсов недоступно: %s. Управляющий бот остаётся доступен",
                      service.startup_error)
        finally:
            service.ready = False
            # Commands check readiness under this same lock before using the user client.
            async with service.lock:
                if service.client is not None:
                    with suppress(Exception):
                        await asyncio.wait_for(service.client.disconnect(), timeout=10)
                    service.attach_client(None)
        try:
            await asyncio.wait_for(service.stop_event.wait(), timeout=retry_seconds)
        except asyncio.TimeoutError:
            pass


async def main():
    # Only Telegram API credentials and common option syntax are needed to start control.
    # Missing/invalid supplier settings are reported by /start and /status after it starts.
    settings = Settings.from_env(require_sync=False)
    state = StateStore(settings.state_file)
    state.acquire()
    control_client = None
    controller = None
    service = SyncService(None, settings, state)
    try:
        if settings.bot_token:
            control_client = TelegramClient(StringSession(), settings.api_id, settings.api_hash,
                                             flood_sleep_threshold=0)
            await control_client.start(bot_token=settings.bot_token)
            bot = await control_client.get_me()
            controller = Controller(control_client, service, settings.admin_ids, username=bot.username)
            log.info("Управляющий бот @%s готов. Открой его в личных сообщениях и отправь /start",
                     bot.username)
            if not settings.admin_ids:
                log.warning("ADMIN_IDS не задан: доступ получит владелец SESSION_STRING после авторизации. "
                            "Для другого аккаунта укажи его ID в ADMIN_IDS")
        else:
            log.warning("BOT_TOKEN / CONTROL_BOT_TOKEN не задан: управляющий бот НЕ запущен. "
                        "Добавь токен своего бота из BotFather в одну из этих переменных")
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            with suppress(NotImplementedError):
                loop.add_signal_handler(sig, lambda: (service.stop_event.set(), service.wake.set()))
        log.info("reprice1 запущен; проверка подключения к поставщику")
        runner = asyncio.create_task(run_supplier(service, settings, controller))
        stopper = asyncio.create_task(service.stop_event.wait())
        try:
            done, _ = await asyncio.wait({runner, stopper}, return_when=asyncio.FIRST_COMPLETED)
            if runner in done:
                await runner
        finally:
            runner.cancel()
            stopper.cancel()
            if controller:
                await controller.close()
            await asyncio.gather(runner, stopper, return_exceptions=True)
    finally:
        if controller:
            await controller.close()
        if control_client:
            await control_client.disconnect()
        state.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
