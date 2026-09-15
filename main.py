import asyncio
import logging
import signal
from contextlib import suppress

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl import types

from config import Settings
from control_botapi import BotAPIController
from runtime import SyncService
from state import StateStore
from supplier import resolve_input_peer

log = logging.getLogger(__name__)


def cached_target_peer(state, target_value):
    cache = state.get("resolved_target_peer", {})
    if not isinstance(cache, dict) or cache.get("source") != str(target_value):
        return None
    try:
        if cache.get("kind") == "channel":
            return types.InputPeerChannel(
                channel_id=int(cache["id"]),
                access_hash=int(cache["access_hash"]),
            )
        if cache.get("kind") == "chat":
            return types.InputPeerChat(chat_id=int(cache["id"]))
    except (KeyError, TypeError, ValueError):
        return None
    return None


def save_target_peer(state, target_value, input_peer):
    if isinstance(input_peer, types.InputPeerChannel):
        payload = {
            "source": str(target_value),
            "kind": "channel",
            "id": input_peer.channel_id,
            "access_hash": input_peer.access_hash,
        }
    elif isinstance(input_peer, types.InputPeerChat):
        payload = {
            "source": str(target_value),
            "kind": "chat",
            "id": input_peer.chat_id,
        }
    else:
        return
    if state.get("resolved_target_peer") != payload:
        state.set("resolved_target_peer", payload)


async def prepare_supplier(service, settings, controller=None):
    """Prepare the user Telegram session used only for supplier/channel access.

    The control bot is deliberately NOT a Telethon bot client. It runs through
    Telegram Bot API in control_botapi.py, so BotFather authorization FloodWaits
    can never block this supplier session or crash the Railway service.
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

    saved = client.session.save()
    if saved and service.state.get("session_string") != saved:
        service.state.set("session_string", saved)
        settings.session = saved

    # Reuse the full InputPeer after the first successful resolution. PostgreSQL
    # keeps channel_id + access_hash across Railway redeploys, so StringSession's
    # empty entity cache no longer matters after initial setup.
    target_peer = cached_target_peer(service.state, settings.target)
    target = None
    if target_peer is not None:
        try:
            target = await client.get_entity(target_peer)
        except Exception:
            target_peer = None

    if target_peer is None:
        target_peer = await resolve_input_peer(
            client,
            settings.target,
            label="TARGET_CHANNEL",
            expected="channel",
        )
        target = await client.get_entity(target_peer)
        save_target_peer(service.state, settings.target, target_peer)

    if not isinstance(target, (types.Channel, types.Chat)):
        raise RuntimeError(
            "TARGET_CHANNEL указывает не на канал/группу. "
            "Поддерживаются @username, -100... ID и raw channel ID"
        )

    permissions = await client.get_permissions(target_peer, me)
    if not (permissions.is_admin or permissions.is_creator):
        raise RuntimeError("Аккаунт сессии должен быть администратором целевого канала")

    service.publisher.target = target_peer

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

    try:
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

        state.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
