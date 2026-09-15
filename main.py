import asyncio
import logging
import signal
from contextlib import suppress

from telethon import TelegramClient
from telethon.sessions import StringSession

from bot_publisher import BotAPIPublisher
from config import Settings
from control_botapi import BotAPIController
from runtime import SyncService
from state import StateStore

log = logging.getLogger(__name__)


async def prepare_supplier(service, settings, controller=None):
    """Prepare the user Telegram session used only for supplier access.

    Reading suppliers uses the user's Telethon session. Publishing to TARGET_CHANNEL
    uses Bot API separately, so TARGET_CHANNEL never needs an MTProto access_hash.
    """
    settings.validate()
    try:
        session = StringSession(settings.session)
    except Exception:
        raise ValueError("SESSION_STRING повреждена; выполни /login в управляющем боте") from None

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

    # Keep an env-provided session and /login session converged in persistent state.
    saved = client.session.save()
    if saved and service.state.get("session_string") != saved:
        service.state.set("session_string", saved)
        settings.session = saved

    # TARGET_CHANNEL is intentionally resolved through Bot API, not through this
    # user Telethon session. Bot API accepts channel chat_id directly and does not
    # require an access_hash, which removes the PeerUser/PeerChannel restart loop.
    if hasattr(service.publisher, "ensure_target"):
        await service.publisher.ensure_target()

    # Supplier usernames still need full MTProto peers because messages are read
    # and commands/buttons are sent from the logged-in user account.
    for reader in service.readers:
        await reader.resolve()

    service.startup_error = None
    service.ready = True
    log.info("Чтение прайсов готово; источников: %s", len(service.readers))


async def run_supplier(service, settings, controller=None, retry_seconds=30):
    """Run supplier sync forever without taking the independent control bot down."""
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
            # /login may replace the session while this task is retrying.
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
            log.error(
                "Чтение прайсов недоступно: %s. Управляющий бот остаётся доступен",
                service.startup_error,
            )
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

    controller = None
    control_task = None
    service = SyncService(None, settings, state)

    # Always publish through Bot API. The control bot must be an administrator of
    # TARGET_CHANNEL. The supplier user account no longer needs channel admin rights
    # and TARGET_CHANNEL no longer depends on Telethon's transient entity cache.
    if settings.bot_token:
        service.publisher = BotAPIPublisher(
            settings.bot_token,
            settings.target,
            state,
            settings,
        )

    try:
        # The control bot uses Telegram HTTP Bot API. This completely avoids
        # ImportBotAuthorizationRequest and its long MTProto FloodWait.
        if settings.bot_token:
            controller = BotAPIController(settings.bot_token, service, settings.admin_ids)
            try:
                await controller.start()
                control_task = asyncio.create_task(controller.run(), name="control-bot-api")
            except Exception as exc:
                log.exception("Управляющий бот не запущен через Bot API")
                print(
                    f"❌ Управляющий бот временно недоступен: {type(exc).__name__}: {exc}",
                    flush=True,
                )
                with suppress(Exception):
                    await controller.close()
                controller = None
        else:
            log.warning(
                "BOT_TOKEN / CONTROL_BOT_TOKEN не задан: управляющий бот НЕ запущен. "
                "Добавь токен своего бота из BotFather в одну из этих переменных"
            )
            print("❌ Управляющий бот не запущен: нет BOT_TOKEN / CONTROL_BOT_TOKEN", flush=True)

        if state.database is not None:
            print("💾 State и пользовательская Telegram-сессия сохраняются в PostgreSQL", flush=True)
        else:
            print("⚠️ DATABASE_URL не задан: session хранится только в state.json", flush=True)

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            with suppress(NotImplementedError):
                loop.add_signal_handler(sig, lambda: (service.stop_event.set(), service.wake.set()))

        log.info("reprice1 запущен; проверка подключения к поставщикам")
        supplier_task = asyncio.create_task(
            run_supplier(service, settings, controller), name="supplier-runtime"
        )
        stopper = asyncio.create_task(service.stop_event.wait(), name="stopper")

        watched = {supplier_task, stopper}
        if control_task is not None:
            watched.add(control_task)

        done, _ = await asyncio.wait(watched, return_when=asyncio.FIRST_COMPLETED)

        if supplier_task in done:
            await supplier_task

        if control_task is not None and control_task in done:
            await control_task

    finally:
        service.stop_event.set()
        service.wake.set()

        if control_task is not None:
            control_task.cancel()
            await asyncio.gather(control_task, return_exceptions=True)
        if controller is not None:
            with suppress(Exception):
                await controller.close()

        if service.client is not None:
            with suppress(Exception):
                await service.client.disconnect()
            service.attach_client(None)

        if hasattr(service.publisher, "close"):
            with suppress(Exception):
                await service.publisher.close()

        state.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
