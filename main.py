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
        raise ValueError("SESSION_STRING повреждена; выполни /login в управляющем боте") from None

    # Short Telegram flood-waits are normal right after a fresh login/redeploy.
    # Let Telethon wait them out instead of dropping the supplier runtime and
    # reconnecting every 30 seconds (which can create an endless GetDialogs loop).
    client = TelegramClient(
        session,
        settings.api_id,
        settings.api_hash,
        auto_reconnect=True,
        connection_retries=5,
        retry_delay=2,
        request_retries=3,
        flood_sleep_threshold=60,
    )
    service.attach_client(client)
    await service.connect()
    me = await client.get_me()
    if me is None or me.bot:
        raise ValueError("Сессия должна принадлежать пользовательскому аккаунту, которому доступны прайсы")
    if controller is not None and not settings.admin_ids:
        controller.admins = {me.id}

    # Persist an env-provided session into durable state too, so /login and
    # Railway Variables converge on the same PostgreSQL-backed source of truth.
    saved = client.session.save()
    if saved and service.state.get("session_string") != saved:
        service.state.set("session_string", saved)
        settings.session = saved

    # Do NOT preload the entire dialog list here. get_dialogs(limit=None) was
    # the source of repeated GetDialogsRequest FloodWait errors on Railway.
    # Resolve the configured target directly. Numeric channel IDs may need a
    # small one-time dialog cache warm-up because StringSession does not keep
    # the full entity cache between fresh containers.
    try:
        target = await client.get_entity(settings.target)
    except (ValueError, TypeError):
        await client.get_dialogs(limit=100)
        target = await client.get_entity(settings.target)

    permissions = await client.get_permissions(target, me)
    if not (permissions.is_admin or permissions.is_creator):
        raise RuntimeError("Аккаунт сессии должен быть администратором целевого канала")

    for reader in service.readers:
        await reader.resolve()

    service.startup_error = None
    service.ready = True
    log.info("Чтение прайсов готово; источников: %s", len(service.readers))


async def run_supplier(service, settings, controller=None, retry_seconds=30):
    database = getattr(service.state, "database", None)
    if database is not None:
        def waiting():
            service.startup_error = "Жду завершения предыдущего Railway deployment"
            log.warning("Жду PostgreSQL runtime lock перед подключением Telegram-сессии")
        await asyncio.to_thread(database.acquire_runtime_lock, waiting)
        log.info("PostgreSQL runtime lock получен")

    while not service.stop_event.is_set():
        service.ready = False
        try:
            # /login writes the latest session to StateStore/PostgreSQL while
            # this task may be retrying. Reload it before every attempt.
            stored = service.state.get("session_string", "")
            if stored:
                settings.session = stored
            await asyncio.wait_for(prepare_supplier(service, settings, controller), timeout=120)
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
    settings = Settings.from_env(require_sync=False)
    state = StateStore(settings.state_file)
    state.acquire()

    stored_session = state.get("session_string", "")
    if stored_session:
        settings.session = stored_session

    control_client = None
    controller = None
    service = SyncService(None, settings, state)
    try:
        if settings.bot_token:
            control_client = TelegramClient(StringSession(), settings.api_id, settings.api_hash,
                                             flood_sleep_threshold=0)
            await asyncio.wait_for(control_client.start(bot_token=settings.bot_token), timeout=30)
            bot = await control_client.get_me()
            controller = Controller(control_client, service, settings.admin_ids, username=bot.username)
            log.info("Управляющий бот @%s готов. Открой его в личных сообщениях и отправь /start",
                     bot.username)
            print(f"🤖 Управляющий бот @{bot.username} готов — /start должен отвечать", flush=True)
            if state.database is not None:
                print("💾 State и Telegram-сессия сохраняются в PostgreSQL", flush=True)
            else:
                print("⚠️ DATABASE_URL не задан: session хранится только в state.json", flush=True)
            if not settings.admin_ids:
                log.warning("ADMIN_IDS не задан: до авторизации сессии /start покажет ID. "
                            "Для входа через /login заранее укажи ADMIN_IDS/ADMIN_ID")
        else:
            log.warning("BOT_TOKEN / CONTROL_BOT_TOKEN не задан: управляющий бот НЕ запущен. "
                        "Добавь токен своего бота из BotFather в одну из этих переменных")
            print("❌ Управляющий бот не запущен: нет BOT_TOKEN / CONTROL_BOT_TOKEN", flush=True)
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
