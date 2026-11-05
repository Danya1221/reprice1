"""Synchronization lifecycle for two supplier prices."""
import asyncio
import logging
import time
from collections import OrderedDict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from telethon import errors, utils

from config import Source

from prices import (
    Item, iphone_model, merge_physical_order, physical_brand_label,
    physical_message_labels, physical_page_labels, product_block, render_blocks, select_items,
)
from publisher import Publisher
from supplier import SupplierReader, resolve_input_peer
from supernova import FRESH_SECONDS, MAX_BYTES, parse_supernova
from supernova_auto import SupernovaAutoError, request_supernova_export

log = logging.getLogger(__name__)


class LoginRequired(RuntimeError):
    pass


def is_open(settings, now=None):
    """Return whether the fixed daily start hour has been reached.

    There is intentionally no fixed closing hour. Publication closes only
    when every configured supplier explicitly reports closed.
    """
    now = now or datetime.now(timezone.utc)
    hour = now.astimezone(ZoneInfo(settings.timezone)).hour
    return hour >= settings.open_hour


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def merge_lowest(sources):
    """Same full variant across suppliers -> lower purchase price wins."""
    merged = OrderedDict()
    for items in sources:
        for item in items or []:
            current = merged.get(item.key)
            if current is None or item.price < current.price:
                merged[item.key] = item
    return list(merged.values())


class SyncService:
    def __init__(self, client, settings, state):
        self.client = client
        self.settings = settings
        self.state = state
        self.base_sources = tuple(settings.sources)
        self._load_channel_sources()
        self.clients = {}
        self.reader_groups = {
            1: [SupplierReader(client, settings, source) for source in self._slot_sources(1)]
        }
        self.readers = self.reader_groups[1]  # backward-compatible primary readers
        if client is not None:
            self.clients[1] = client
        self.publisher = Publisher(client, settings.target, state, settings)
        self.lock = asyncio.Lock()
        self.wake = asyncio.Event()
        self.stop_event = asyncio.Event()
        self.busy = False
        self.last_attempt = 0.0
        self.retry_after = 0.0
        self.ready = client is not None
        self.startup_error = None
        self.account_errors = {}
        self.reload_requested = False
        self.supernova_verified_account = None
        self.supernova_last_view = None
        self.supernova_retry_at = 0.0
        self.supernova_auto_task = None
        self.supernova_wake = asyncio.Event()

    def supernova_items(self, now=None):
        now = time.time() if now is None else now
        settings = self.state.get("supernova_settings", {})
        snapshot = self.state.get("supernova_snapshot", {})
        if (not settings.get("enabled") or snapshot.get("slot") != settings.get("slot")
                or not self.supernova_verified_account
                or snapshot.get("account_id") != self.supernova_verified_account):
            return []
        return [Item.from_dict(row["item"]) for row in snapshot.get("offers", [])
                if 0 <= now - row.get("seen", 0) <= FRESH_SECONDS]

    def supernova_expired_items(self, now=None):
        now = time.time() if now is None else now
        settings = self.state.get("supernova_settings", {})
        snapshot = self.state.get("supernova_snapshot", {})
        if (not settings.get("enabled") or snapshot.get("slot") != settings.get("slot")
                or (self.supernova_verified_account and snapshot.get("account_id") != self.supernova_verified_account)):
            return []
        return [Item.from_dict(row["item"]) for row in snapshot.get("offers", [])
                if (now >= row.get("seen", 0) and
                    (not self.supernova_verified_account or now - row.get("seen", 0) > FRESH_SECONDS))]

    async def configure_supernova(self, slot=None):
        if slot is not None and slot not in (1, 2):
            raise ValueError("Выбери аккаунт 1 или 2")
        previous = self.state.get("supernova_settings", {})
        async with self.lock:
            if slot is not None and (slot not in self.clients or not self.account_configured(slot)):
                raise ValueError(f"Сначала подключи Telegram-аккаунт {slot} через /login {slot}")
            self.state.set("supernova_settings", {"enabled": slot is not None, "slot": slot})
            if previous.get("slot") != slot:
                self.state.set("supernova_auto", {"enabled": slot is not None, "slot": slot, "next_attempt": 0})
            self.supernova_verified_account = None
            self.supernova_last_view = None
            self.wake.set()
        if previous.get("slot") != slot:
            await self.cancel_supernova_export()
        if previous.get("enabled") and previous.get("slot") != slot and self.ready and self.enabled() and self.state.get("sources", {}):
            await self.refresh_format()

    def supernova_working_time(self, now=None):
        now = now or datetime.now(timezone.utc)
        local = now.astimezone(ZoneInfo(self.settings.timezone))
        return self.settings.open_hour <= local.hour < self.settings.close_hour

    async def set_supernova_auto(self, enabled):
        config = self.state.get("supernova_settings", {})
        slot = config.get("slot")
        if enabled and (not config.get("enabled") or slot not in self.clients):
            raise ValueError("Сначала выбери подключённый аккаунт Supernova")
        current = self.state.get("supernova_auto", {})
        self.state.set("supernova_auto", {**current, "enabled": bool(enabled), "slot": slot,
                                        "next_attempt": current.get("next_attempt", 0),
                                        "status": "Автообновление включено" if enabled else "Автообновление остановлено",
                                        "error": ""})
        if not enabled:
            await self.cancel_supernova_export()
        elif self.ready and self.enabled() and self.supernova_working_time():
            self.start_supernova_export()

    async def cancel_supernova_export(self):
        task = self.supernova_auto_task
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.supernova_auto_task = None

    def start_supernova_export(self, force=False):
        config = self.state.get("supernova_settings", {})
        slot = config.get("slot")
        if not config.get("enabled") or slot not in self.clients or not self.ready:
            raise ValueError("Сначала подключи Telegram-аккаунт Supernova")
        if not self.enabled():
            raise ValueError("Сначала запусти синхронизацию")
        if time.monotonic() < self.supernova_retry_at:
            if force:
                raise ValueError("Telegram попросил подождать перед следующим запросом")
            return False
        if self.supernova_auto_task and not self.supernova_auto_task.done():
            return False
        auto = self.state.get("supernova_auto", {})
        if force and time.time() - auto.get("last_attempt", 0) < 60:
            raise ValueError("Подожди минуту перед повторным запросом")
        if not force and (not auto.get("enabled") or auto.get("slot") != slot or not self.supernova_working_time()
                          or time.time() < auto.get("next_attempt", 0)):
            return False
        started = time.time()
        self.state.set("supernova_auto", {**auto, "slot": slot, "last_attempt": started,
                                        "next_attempt": started + 300, "status": "Запрос запущен", "error": ""})
        self.supernova_auto_task = asyncio.create_task(self._perform_supernova_export(slot), name="supernova-export")
        return True

    async def _perform_supernova_export(self, slot):
        try:
            client = self.clients.get(slot)
            if client is None:
                raise SupernovaAutoError("Выбранный Telegram-аккаунт отключён")
            me = await client.get_me()
            peer = await client.get_entity("@supernova_bot")
            if not getattr(peer, "bot", False) or not getattr(me, "id", None):
                raise SupernovaAutoError("Не удалось проверить аккаунт и бота Supernova")
            before = await client.get_messages(peer, limit=1)
            baseline = max((message.id for message in before), default=0)
            async def wait_for_delivery():
                while True:
                    config = self.state.get("supernova_settings", {})
                    if not config.get("enabled") or config.get("slot") != slot or self.clients.get(slot) is not client:
                        raise SupernovaAutoError("Аккаунт Supernova изменился во время запроса")
                    snapshot = self.state.get("supernova_snapshot", {})
                    if (snapshot.get("slot") == slot and snapshot.get("account_id") == me.id
                            and snapshot.get("message_id", 0) > baseline):
                        return
                    self.supernova_wake.set()
                    await asyncio.sleep(5)

            # Telegram's webview request is valid without prolongation for less
            # than one minute; the separate reader checks the chat every 30 sec.
            await asyncio.wait_for(request_supernova_export(client, peer, wait_for_delivery), timeout=55)
            now = time.time()
            current = self.state.get("supernova_auto", {})
            self.state.set("supernova_auto", {**current, "last_success": now,
                                            "next_attempt": now + 480,
                                            "status": "Excel получен и прочитан", "error": ""})
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Do not log arbitrary exception text: webview URLs contain initData.
            log.warning("Автообновление Supernova не завершено: %s", type(exc).__name__)
            if isinstance(exc, errors.FloodWaitError):
                message = f"Telegram просит подождать {exc.seconds} сек."
                delay = max(300, exc.seconds + 1)
            else:
                message = str(exc) if isinstance(exc, SupernovaAutoError) else "Не удалось открыть Mini App или получить Excel"
                delay = 300
            current = self.state.get("supernova_auto", {})
            self.state.set("supernova_auto", {**current, "status": "Ошибка обновления", "error": message[:240],
                                            "next_attempt": time.time() + delay})

    async def poll_supernova(self):
        config = self.state.get("supernova_settings", {})
        slot = config.get("slot")
        if not config.get("enabled") or slot not in (1, 2):
            return
        if time.monotonic() < self.supernova_retry_at:
            return
        client = self.clients.get(slot)
        if client is None:
            self.supernova_verified_account = None
            return
        account_id = None
        try:
            me = await client.get_me()
            account_id = getattr(me, "id", None)
            if not account_id or getattr(me, "bot", False):
                raise ValueError("Подключённый аккаунт не является пользовательским")
            self.supernova_verified_account = account_id
            peer = await client.get_entity("@supernova_bot")
            if not getattr(peer, "bot", False):
                raise ValueError("Supernova не является Telegram-ботом")
            messages = await client.get_messages(peer, limit=100)
            now = time.time()
            candidate = next((m for m in messages
                              if not getattr(m, "out", False) and not getattr(m, "fwd_from", None)
                              and getattr(m, "sender_id", None) == peer.id
                              and getattr(m, "file", None) is not None
                              and (getattr(m.file, "name", "") or "").casefold().endswith(".xlsx")), None)
            snapshot = self.state.get("supernova_snapshot", {})
            if candidate is not None:
                observed = candidate.date.timestamp()
                same_account = snapshot.get("account_id") == account_id and snapshot.get("slot") == slot
                if (0 <= now - observed <= FRESH_SECONDS
                        and (not same_account or candidate.id > snapshot.get("message_id", 0))):
                    size = getattr(candidate.file, "size", None)
                    if not size or size > MAX_BYTES:
                        raise ValueError("Excel Supernova пуст или больше 5 МБ")
                    payload = await client.download_media(candidate, file=bytes)
                    items, unavailable = await asyncio.to_thread(parse_supernova, payload)
                    async with self.lock:
                        if self.state.get("supernova_settings", {}) != config:
                            return
                        now = time.time()
                        snapshot = self.state.get("supernova_snapshot", {})
                        same_account = snapshot.get("account_id") == account_id and snapshot.get("slot") == slot
                        if not 0 <= now - observed <= FRESH_SECONDS or (same_account and candidate.id <= snapshot.get("message_id", 0)):
                            return
                        # No declared total is present: keep omitted rows in history;
                        # expired offers remain visible as "По запросу".
                        prior = snapshot.get("offers", []) if same_account else []
                        offers = {Item.from_dict(row["item"]).key: row for row in prior}
                        for key in unavailable:
                            offers.pop(key, None)
                        for item in items:
                            offers[item.key] = {"item": item.to_dict(), "seen": observed}
                        self.state.set("supernova_snapshot", {"slot": slot, "account_id": account_id,
                                       "message_id": candidate.id, "file_time": observed,
                                       "offers": list(offers.values()), "count": len(items)})
                        self.state.set("supernova_error", "")
        except Exception as exc:
            if not account_id:
                self.supernova_verified_account = None
            if isinstance(exc, errors.FloodWaitError):
                self.supernova_retry_at = time.monotonic() + exc.seconds + 1
            log.warning("Ошибка чтения Supernova: %s", type(exc).__name__)
            message = f"{type(exc).__name__}: {exc}"[:400]
            if self.state.get("supernova_error", "") != message:
                self.state.set("supernova_error", message)
        items = self.supernova_items()
        expired = self.supernova_expired_items()
        view = (tuple(sorted((item.key, str(item.price)) for item in items)),
                tuple(sorted(item.key for item in expired)))
        if self.supernova_last_view != view:
            self.supernova_last_view = view
            source_statuses = self._cached_source_statuses(self.state.get("sources", {}))
            within_hours = is_open(self.settings) or self._both_fresh_open(source_statuses)
            if self.ready and self.enabled() and within_hours and (items or expired):
                try:
                    await self.refresh_format()
                except Exception as exc:
                    self.supernova_last_view = None
                    log.warning("Не удалось обновить прайс Supernova: %s", type(exc).__name__)

    def _load_channel_sources(self):
        self.channel_sources = {1: [], 2: []}
        self.channel_visibility = {}
        for item in self.state.get("supplier_channels", []):
            if not isinstance(item, dict) or not item.get("peer") or not item.get("title"):
                continue
            # Previously saved channels belong to account 1 for compatibility.
            slot = item.get("slot", 1)
            if slot not in (1, 2):
                slot = 1
            source = Source(item["peer"], mode="feed", label="Канал: " + item["title"],
                            read_captions=item.get("read_captions", False) is True)
            self.channel_sources[slot].append(source)
            self.channel_visibility[(slot, str(source.peer))] = item.get("visible", True) is not False
        self.settings.sources = self.base_sources + tuple(
            source for slot in (1, 2) for source in self.channel_sources[slot]
        )

    def _replace_channel_readers(self):
        for slot in (1, 2):
            if slot == 2 and slot not in self.reader_groups and slot not in self.clients:
                continue
            previous = {reader.source: reader for reader in self.reader_groups.get(slot, [])}
            client = self.clients.get(slot)
            readers = [previous.get(source) or SupplierReader(client, self.settings, source)
                       for source in self._slot_sources(slot)]
            self.reader_groups[slot] = readers
            if slot == 1:
                self.readers = readers
        self.last_attempt = 0
        self.wake.set()

    async def list_available_supplier_channels(self, slot=1):
        """Show broadcast channels visible to the selected Telegram account."""
        slot = int(slot)
        if slot not in (1, 2):
            raise ValueError("Неизвестный Telegram-аккаунт")
        client = self.clients.get(slot)
        if client is None or (slot == 1 and not self.ready):
            raise ValueError(f"Сначала подключи Telegram-аккаунт {slot} через /login {slot}")
        if not await self.connect_account(slot):
            raise LoginRequired(f"Аккаунт {slot} недоступен. Выполни /login {slot}")
        me = await client.get_me()
        account_name = (
            "@" + me.username if getattr(me, "username", None)
            else " ".join(filter(None, (
                getattr(me, "first_name", None), getattr(me, "last_name", None)
            ))) or "аккаунт 1"
        )
        available = []
        async for dialog in client.iter_dialogs(limit=None):
            entity = dialog.entity
            if getattr(entity, "broadcast", False):
                available.append({
                    "id": utils.get_peer_id(entity),
                    "title": str(getattr(entity, "title", "") or dialog.name or "Без названия"),
                })
        available.sort(key=lambda item: item["title"].casefold())
        return account_name, available

    async def add_supplier_channel(self, value, slot=1):
        slot = int(slot)
        if slot not in (1, 2):
            raise ValueError("Неизвестный Telegram-аккаунт")
        async with self.lock:
            client = self.clients.get(slot)
            if not client or (slot == 1 and not self.ready):
                raise ValueError(f"Сначала подключи Telegram-аккаунт {slot} через /login {slot}")
            if not await self.connect_account(slot):
                raise LoginRequired(f"Аккаунт {slot} недоступен. Выполни /login {slot}")
            resolved = await resolve_input_peer(client, value, label="Канал", expected="channel")
            entity = await client.get_entity(resolved)
            if not getattr(entity, "broadcast", False):
                raise ValueError("Это не канал. Выбери именно канал с прайсом")
            await client.get_messages(resolved, limit=1)
            channel_id = utils.get_peer_id(resolved)
            username = (getattr(entity, "username", None) or "").casefold()
            channels = self.state.get("supplier_channels", [])
            if any(str(item.get("id", item.get("peer"))) == str(channel_id) for item in channels):
                raise ValueError("Этот канал уже добавлен как источник")
            for source in self.settings.sources:
                current = str(source.peer).strip().lower().lstrip("@")
                if current == str(channel_id) or (username and current in {username, "https://t.me/" + username, "t.me/" + username}):
                    raise ValueError("Этот канал уже добавлен как источник")
            title = str(getattr(entity, "title", "") or username or channel_id)[:100]
            # Public usernames can be resolved again after a StringSession restart,
            # even when the account is not subscribed and the dialog cache is empty.
            peer = "@" + username if username else channel_id
            channels.append({"peer": peer, "id": channel_id, "title": title, "slot": slot})
            self.state.set("supplier_channels", channels)
            self._load_channel_sources()
            self._replace_channel_readers()
            return title

    def source_visible(self, source, slot=1):
        """Dynamic sources are always read, but can be excluded from publication."""
        return self.channel_visibility.get((int(slot), str(source.peer)), True)

    async def set_supplier_channel_visibility(self, peer, slot=1, visible=True):
        slot = int(slot)
        if slot not in (1, 2):
            raise ValueError("Неизвестный Telegram-аккаунт")
        async with self.lock:
            channels = self.state.get("supplier_channels", [])
            match = next((item for item in channels if str(item.get("peer")) == str(peer)
                          and item.get("slot", 1) == slot), None)
            if match is None:
                raise ValueError("Канал не найден")
            match["visible"] = bool(visible)
            self.state.set("supplier_channels", channels)
            self._load_channel_sources()
        # Refresh from cached prices without a new Telegram request.
        # Do not lock recursively: refresh_format acquires the same lock.
        if self.ready:
            await self.refresh_format()
        return bool(visible)

    async def set_supplier_channel_captions(self, peer, slot=1, enabled=False):
        """Opt in to parsing caption text; photos/videos themselves stay unread."""
        slot = int(slot)
        if slot not in (1, 2):
            raise ValueError("Неизвестный Telegram-аккаунт")
        async with self.lock:
            channels = self.state.get("supplier_channels", [])
            match = next((item for item in channels if str(item.get("peer")) == str(peer)
                          and item.get("slot", 1) == slot), None)
            if match is None:
                raise ValueError("Канал не найден")
            match["read_captions"] = bool(enabled)
            self.state.set("supplier_channels", channels)
            self._load_channel_sources()
            self._replace_channel_readers()
            # This is a parser change, not a publication-only toggle.
            # Full Telegram feed must be read again; cached items cannot help.
        return bool(enabled)

    async def remove_supplier_channel(self, peer, slot=1):
        slot = int(slot)
        if slot not in (1, 2):
            raise ValueError("Неизвестный Telegram-аккаунт")
        async with self.lock:
            channels = self.state.get("supplier_channels", [])
            remaining = [item for item in channels
                         if not (str(item.get("peer")) == str(peer) and item.get("slot", 1) == slot)]
            if len(remaining) == len(channels):
                raise ValueError("Канал уже удалён")
            self.state.set("supplier_channels", remaining)
            self._load_channel_sources()
            self._replace_channel_readers()

    def _slot_sources(self, slot):
        if int(slot) == 1:
            return self.base_sources + tuple(self.channel_sources[1])
        # Preserve the second account's independent HI comparison; other
        # predefined suppliers remain on account 1. Dynamic channels are
        # read only by the account explicitly selected when adding them.
        if int(slot) == 2:
            return self.base_sources[:1] + tuple(self.channel_sources[2])
        raise ValueError("Неизвестный Telegram-аккаунт")

    def attach_client(self, client, slot=1):
        slot = int(slot)
        if (self.state.get("supernova_settings", {}).get("slot") == slot
                and self.clients.get(slot) is not client):
            self.supernova_verified_account = None
        sources = self._slot_sources(slot)
        readers = self.reader_groups.get(slot)
        if readers is None or tuple(reader.source for reader in readers) != tuple(sources):
            readers = [SupplierReader(client, self.settings, source) for source in sources]
            self.reader_groups[slot] = readers
        else:
            for reader in readers:
                reader.client = client
                reader.entity = None

        if client is None:
            if self.state.get("supernova_settings", {}).get("slot") == slot:
                self.supernova_verified_account = None
            self.clients.pop(slot, None)
        else:
            self.clients[slot] = client
            self.account_errors.pop(slot, None)

        if slot == 1:
            self.client = client
            self.readers = readers
            self.publisher.client = client
            self.ready = client is not None
        return readers

    def readers_for_slot(self, slot):
        return self.reader_groups.get(int(slot), [])

    def active_reader_entries(self):
        for slot in sorted(self.clients):
            client = self.clients.get(slot)
            if client is None:
                continue
            for reader in self.reader_groups.get(slot, []):
                yield slot, reader

    def account_configured(self, slot):
        if int(slot) == 1:
            return bool(self.settings.session or self.state.get("session_string", "") or 1 in self.clients)
        return bool(getattr(self.settings, "session_2", "") or self.state.get("session_string_2", "") or 2 in self.clients)

    def startup_status(self):
        return self.startup_error or "Подключение к аккаунту поставщика ещё выполняется"

    def options(self):
        return self.state.get("options", {})

    def enabled(self):
        return self.options().get("enabled", True)

    def set_option(self, key, value):
        options = self.options()
        options[key] = value
        self.state.set("options", options)
        self.wake.set()

    def request_reconnect(self):
        """Reload saved Telegram sessions without restarting the control bot."""
        self.reload_requested = True
        self.wake.set()

    async def connect_account(self, slot):
        slot = int(slot)
        client = self.clients.get(slot)
        if client is None:
            if slot == 1:
                raise LoginRequired("Сессия аккаунта 1 не подключена. Выполни /login 1")
            return False
        try:
            if not client.is_connected():
                await client.connect()
            if not await client.is_user_authorized():
                raise LoginRequired(f"Сессия аккаунта {slot} недействительна. Выполни /login {slot}")
            self.account_errors.pop(slot, None)
            return True
        except (errors.AuthKeyDuplicatedError, errors.UnauthorizedError) as exc:
            message = (
                f"Telegram отозвал сессию аккаунта {slot}. Останови другие копии и выполни /login {slot} заново"
            )
            if slot == 1:
                raise LoginRequired(message) from exc
            self.account_errors[slot] = message
            return False
        except LoginRequired:
            raise
        except Exception as exc:
            if slot == 1:
                raise
            self.account_errors[slot] = f"{type(exc).__name__}: {exc}"
            return False

    async def connect(self):
        if 1 not in self.clients:
            raise LoginRequired("Сессия аккаунта 1 не подключена. Выполни /login 1")
        if not await self.connect_account(1):
            raise LoginRequired("Сессия аккаунта 1 недействительна. Выполни /login 1")
        for slot in sorted(list(self.clients)):
            if slot == 1:
                continue
            ok = await self.connect_account(slot)
            if not ok:
                client = self.clients.get(slot)
                if client is not None:
                    try:
                        await client.disconnect()
                    except Exception:
                        pass
                self.attach_client(None, slot)

    def source_cache_key(self, source, slot=1):
        base = str(source.peer)
        return base if int(slot) == 1 else f"account{int(slot)}:{base}"

    def source_key(self, reader, slot=1):
        return self.source_cache_key(reader.source, slot)

    def source_index(self, source):
        for index, configured in enumerate(self.settings.sources):
            if configured == source:
                return index
        raise ValueError("Неизвестный источник")

    def source_items(self, source, items):
        # Source 1 is HI, including its account-2 price comparison. Apply the
        # restriction before merging so an outdated HI price cannot win for
        # Samsung or another non-Apple product. Other suppliers stay unrestricted.
        if not self.base_sources or source != self.base_sources[0]:
            return list(items)
        result = []
        for item in items:
            block = item.block
            if block in {"CPO", "ASIS"}:
                block = iphone_model(item.title) or product_block(item.title)
            if not item.accessory and physical_brand_label(block) in {"Apple", "iPhone"}:
                result.append(item)
        return result

    def cached_items(self, include_closed=False):
        cache = self.state.get("sources", {})
        groups = []
        for slot in (1, 2):
            if slot == 2 and not self.account_configured(2):
                continue
            for source in self._slot_sources(slot):
                if not self.source_visible(source, slot):
                    continue
                value = cache.get(self.source_cache_key(source, slot), {})
                if value.get("status") == "closed" and not include_closed:
                    continue
                if value.get("items"):
                    groups.append(self.source_items(source, [Item.from_dict(item) for item in value["items"]]))
        groups.append(self.supernova_items())
        return merge_lowest(groups)

    async def render(self, closed=False, items=None):
        options = self.options()
        if "physical_order" not in options:
            # Adopt the already published layout once on upgrade. Empty orders
            # explicitly saved by the Reset button still mean default order.
            stored = self.state.get("published", {}) or {}
            binding = self.publisher.binding()
            target = self.state.get("publish_target", {}).get("chat_id")
            if target and hasattr(self.publisher, "configured_target"):
                # Bot API resolves the saved group only during publish().
                binding = f"botapi:{target}"
            if stored.get("binding") == binding:
                entries = sorted(stored.get("messages", {}).values(), key=lambda entry: int(entry["id"]))
                options["physical_order"] = physical_message_labels([
                    entry["content"] for entry in entries if entry.get("content")
                ])
        catalog = self.cached_items(include_closed=closed) if items is None else items
        request_only = []
        if not closed:
            available = {item.key for item in catalog}
            request_only = [item for item in self.supernova_expired_items() if item.key not in available]
            catalog = list(catalog) + request_only
        if closed and not catalog:
            changes = await self.publisher.hide_existing()
            self.state.update({"last_publish": timestamp(), "published_items": 0})
            return 0, changes
        selected = select_items(catalog, self.settings, options)
        render_options = {**options, "_request_only_keys": {item.key for item in request_only}}
        pages = render_blocks(selected, self.settings, render_options, closed=closed)
        if not closed:
            rendered_rows = sum(content.count("<code>") for content in pages.values())
            if rendered_rows != len(selected):
                raise RuntimeError(
                    f"Защита публикации: рендер потерял позиции ({rendered_rows} из {len(selected)}). "
                    "Текущий прайс оставлен без изменений."
                )
        # Remember every observed page, including pages added automatically.
        # Keep missing/disabled pages in the order so they return to their slots.
        if not closed:
            order = merge_physical_order(options.get("physical_order", []), physical_page_labels(pages))
            latest_options = self.options()
            if order != latest_options.get("physical_order"):
                latest_options["physical_order"] = order
                self.state.set("options", latest_options)
        changes = await self.publisher.publish(pages)
        self.state.update({"last_publish": timestamp(), "published_items": 0 if closed else len(selected)})
        return len(selected), changes

    @staticmethod
    def _aggregate_status(values):
        values = list(values)
        if "open" in values:
            return "open"
        if values and all(value == "closed" for value in values):
            return "closed"
        return "error"

    def _both_fresh_open(self, source_statuses):
        return len(self.base_sources) >= 2 and all(status == "open" for status in source_statuses[:len(self.base_sources)])

    async def sync(self, force=False):
        async with self.lock:
            if not self.ready:
                return self.startup_status()
            if not self.enabled() and not force:
                return "Синхронизация остановлена"
            if asyncio.get_running_loop().time() < self.retry_after:
                return "Ожидаю завершения FloodWait от Telegram"
            self.busy = True
            try:
                await self.connect()
                self.last_attempt = asyncio.get_running_loop().time()
                self.state.set("last_check", timestamp())

                old_cache = self.state.get("sources", {})
                cache = dict(old_cache)
                errors_found = []
                fresh_by_source = {index: [] for index in range(len(self.settings.sources))}
                fresh_open_groups = []

                for slot, reader in list(self.active_reader_entries()):
                    index = self.source_index(reader.source)
                    key = self.source_key(reader, slot)
                    label = reader.source.label + (f" · аккаунт {slot}" if slot > 1 else "")
                    try:
                        budget = self.settings.response_timeout * (len(reader.source.buttons) + self.settings.catalog_pages + 1) * 2 + 120
                        result = await asyncio.wait_for(reader.fetch(), timeout=budget)
                        previous = cache.get(key, {})
                        status = "closed" if result.closed else "open"
                        eligible = self.source_items(reader.source, result.items)
                        retained = self.source_items(reader.source, [
                            Item.from_dict(item) for item in previous.get("items", [])
                        ]) if result.closed else eligible
                        fresh_by_source[index].append(status)
                        cache[key] = {
                            "status": status,
                            "checked": timestamp(),
                            "last_success": timestamp(),
                            "messages_count": (getattr(reader, "last_read_stats", None) or {}).get("messages"),
                            "parsed_count": len(result.items),
                            "price_rows_count": (getattr(reader, "last_read_stats", None) or {}).get("price_rows"),
                            "text_posts_count": (getattr(reader, "last_read_stats", None) or {}).get("text_posts"),
                            "ignored_caption_posts_count": (getattr(reader, "last_read_stats", None) or {}).get("ignored_caption_posts"),
                            "read_caption_posts_count": (getattr(reader, "last_read_stats", None) or {}).get("read_caption_posts"),
                            "ignored_photo_posts_count": (getattr(reader, "last_read_stats", None) or {}).get("ignored_photo_posts"),
                            "ignored_video_posts_count": (getattr(reader, "last_read_stats", None) or {}).get("ignored_video_posts"),
                            "ignored_media_count": (getattr(reader, "last_read_stats", None) or {}).get("ignored_media"),
                            "price_files_count": (getattr(reader, "last_read_stats", None) or {}).get("price_files"),
                            "items": [item.to_dict() for item in retained],
                            "rejected": result.rejected[:200],
                            "rejected_count": len(result.rejected),
                            "error": None,
                            "account": slot,
                        }
                        if status == "open" and self.source_visible(reader.source, slot):
                            fresh_open_groups.append(eligible)
                    except (errors.AuthKeyDuplicatedError, errors.UnauthorizedError) as exc:
                        if slot == 1:
                            raise LoginRequired("Сессия Telegram отозвана; выполни /login 1 заново") from exc
                        error = f"Сессия аккаунта {slot} отозвана: {exc}"
                        self.account_errors[slot] = error
                        old = cache.get(key, {})
                        cache[key] = {**old, "error": error, "checked": timestamp(), "account": slot}
                        fresh_by_source[index].append("error")
                        errors_found.append(label + ": " + error)
                    except errors.FloodWaitError as exc:
                        if slot == 1:
                            raise
                        error = f"FloodWait {exc.seconds} сек."
                        old = cache.get(key, {})
                        cache[key] = {**old, "error": error, "checked": timestamp(), "account": slot}
                        fresh_by_source[index].append("error")
                        errors_found.append(label + ": " + error)
                    except Exception as exc:
                        log.warning("Не удалось прочитать %s: %s", label, type(exc).__name__)
                        old = cache.get(key, {})
                        stats = getattr(reader, "last_read_stats", None) or {}
                        cache[key] = {
                            **old,
                            "error": f"{type(exc).__name__}: {exc}",
                            "checked": timestamp(),
                            "messages_count": stats.get("messages"),
                            "parsed_count": stats.get("parsed"),
                            "price_rows_count": stats.get("price_rows"),
                            "text_posts_count": stats.get("text_posts"),
                            "ignored_caption_posts_count": stats.get("ignored_caption_posts"),
                            "read_caption_posts_count": stats.get("read_caption_posts"),
                            "ignored_photo_posts_count": stats.get("ignored_photo_posts"),
                            "ignored_video_posts_count": stats.get("ignored_video_posts"),
                            "ignored_media_count": stats.get("ignored_media"),
                            "price_files_count": stats.get("price_files"),
                            "account": slot,
                        }
                        fresh_by_source[index].append("error")
                        errors_found.append(label + ": " + str(exc))

                self.state.set("sources", cache)
                statuses = [self._aggregate_status(fresh_by_source[index])
                            for index in range(len(self.settings.sources))]

                if self.settings.sources and statuses and all(status == "closed" for status in statuses):
                    await self.render(closed=True)
                    message = "Все поставщики закрыты: цены скрыты"
                    self.state.set("last_result", message)
                    return message

                if not fresh_open_groups:
                    message = "Прайс сохранён; нет свежего открытого источника"
                    if errors_found:
                        message += ": " + " | ".join(errors_found)
                    self.state.set("last_result", message)
                    return message

                if not is_open(self.settings) and not self._both_fresh_open(statuses):
                    await self.publisher.hide_existing()
                    message = (
                        f"До {self.settings.open_hour:02d}:00: ждём открытия обоих поставщиков. "
                        "Цены скрыты"
                    )
                    self.state.set("last_result", message)
                    return message

                # Every fresh account/source result participates. Identical variants
                # choose the lowest purchase price before markup.
                catalog = merge_lowest(fresh_open_groups + [self.supernova_items()])
                count, changes = await self.render(items=catalog)
                source_note = "/".join(statuses)
                account_note = " + аккаунт 2" if 2 in self.clients else ""
                message = (
                    f"Прайс обновлён: {count} позиций, изменений: {changes}; "
                    f"источники: {source_note}{account_note}"
                )
                self.state.set("last_result", message)
                return message

            except errors.FloodWaitError as exc:
                self.retry_after = asyncio.get_running_loop().time() + exc.seconds + 1
                message = f"Telegram просит подождать {exc.seconds} сек."
                self.state.set("last_result", message)
                return message
            except LoginRequired:
                raise
            except Exception as exc:
                message = f"Ошибка: {type(exc).__name__}: {exc}"
                self.state.set("last_result", message)
                log.exception("Ошибка синхронизации")
                return message
            finally:
                self.busy = False

    async def pause(self):
        async with self.lock:
            self.set_option("enabled", False)

    def _cached_source_statuses(self, cache):
        result = []
        for source in self.settings.sources:
            values = []
            for slot in (1, 2):
                if slot == 2 and not self.account_configured(2):
                    continue
                if source not in self._slot_sources(slot):
                    continue
                value = cache.get(self.source_cache_key(source, slot), {})
                if value.get("status"):
                    values.append(value.get("status"))
            result.append(self._aggregate_status(values))
        return result

    async def refresh_format(self):
        async with self.lock:
            if not self.ready:
                raise RuntimeError(self.startup_status())
            await self.connect()
            cache = self.state.get("sources", {})
            statuses = self._cached_source_statuses(cache)
            closed = bool(statuses) and all(status == "closed" for status in statuses)
            if closed:
                return await self.render(closed=True)
            return await self.render(closed=False)

    def status(self):
        options = self.options()
        cache = self.state.get("sources", {})
        account_1 = "подключён" if 1 in self.clients and self.ready else "не подключён"
        if 2 in self.clients:
            account_2 = "подключён"
        elif self.account_configured(2):
            account_2 = "ошибка: " + self.account_errors.get(2, "ожидает подключения")
        else:
            account_2 = "не подключён"
        lines = [
            "🟢 Синхронизация: включена" if self.enabled() else "⏸ Синхронизация: остановлена",
            f"Telegram: аккаунт 1 — {account_1}; аккаунт 2 — {account_2}",
            f"Интервал: {options.get('poll_seconds', self.settings.poll_seconds) // 60} мин.",
            f"Наценка: {options.get('markup', str(self.settings.markup))} + {options.get('markup_percent', str(self.settings.markup_percent))}%",
            "SIM: " + options.get("sim_filter", self.settings.sim_filter),
            "Последняя проверка: " + self.state.get("last_check", "ещё не было"),
            "Результат: " + self.state.get("last_result", "ожидание"),
        ]
        if not self.ready:
            lines[0] = "⚠️ Чтение прайса недоступно: " + self.startup_status()
        for slot in (1, 2):
            if slot == 2 and not self.account_configured(2):
                continue
            for source in self._slot_sources(slot):
                value = cache.get(self.source_cache_key(source, slot), {})
                status = "ошибка чтения" if value.get("error") else {
                    "open": "открыт", "closed": "закрыт"
                }.get(value.get("status"), "неизвестно")
                suffix = f" · аккаунт {slot}" if slot > 1 else ""
                lines.append(
                    f"{source.label}{suffix}: {status}; получено позиций: {len(value.get('items', []))}; "
                    f"не распознано строк: {value.get('rejected_count', 0)}"
                )
        lines.append(f"Опубликовано позиций: {self.state.get('published_items', 0)}")
        return "\n".join(lines)

    async def disconnect_clients(self):
        for slot, client in list(self.clients.items()):
            if client is not None:
                try:
                    await client.disconnect()
                except Exception:
                    pass
            self.attach_client(None, slot)
        self.ready = False

    async def run(self):
        last_start_state = None
        while not self.stop_event.is_set() and not self.reload_requested:
            self.wake.clear()
            now = asyncio.get_running_loop().time()
            start_state = is_open(self.settings)
            interval = self.options().get("poll_seconds", self.settings.poll_seconds)
            due = now - self.last_attempt >= interval or self.last_attempt == 0 or start_state != last_start_state
            if self.enabled() and due and now >= self.retry_after:
                try:
                    await self.sync()
                except LoginRequired as exc:
                    self.startup_error = str(exc)
                    return
                last_start_state = start_state
            if self.reload_requested:
                return
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=5)
            except asyncio.TimeoutError:
                pass

    async def run_supernova(self):
        """Poll separately so a slow supplier does not block receiving the XLSX."""
        try:
            while not self.stop_event.is_set() and not self.reload_requested:
                self.supernova_wake.clear()
                await self.poll_supernova()
                config = self.state.get("supernova_settings", {})
                if config.get("enabled") and self.state.get("supernova_auto") is None:
                    self.state.set("supernova_auto", {"enabled": True, "slot": config.get("slot"), "next_attempt": 0})
                if self.state.get("supernova_auto", {}).get("enabled") and self.ready and self.enabled():
                    try:
                        self.start_supernova_export()
                    except ValueError:
                        pass
                try:
                    await asyncio.wait_for(self.supernova_wake.wait(), timeout=30)
                except asyncio.TimeoutError:
                    pass
        finally:
            await self.cancel_supernova_export()
