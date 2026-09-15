"""Synchronization lifecycle: one lock, isolated sources, durable settings and cache."""
import asyncio
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from telethon import errors

from prices import Item, merge_sources, render_blocks, select_items
from publisher import Publisher
from supplier import SupplierReader

log = logging.getLogger(__name__)


class LoginRequired(RuntimeError):
    pass


def is_open(settings, now=None):
    """Whether the normal 10:00 publishing threshold has been reached today.

    There is deliberately no fixed evening close here: the public price closes
    when every configured supplier explicitly reports closed. Before OPEN_HOUR,
    both/all configured suppliers may still open the price early.
    """
    if not settings.off_hours:
        return True
    now = now or datetime.now(timezone.utc)
    hour = now.astimezone(ZoneInfo(settings.timezone)).hour
    return hour >= settings.open_hour


def publication_state(settings, statuses, now=None):
    """Return open/closed/unknown from time gate plus supplier states.

    Rules:
    - all suppliers closed -> closed;
    - before OPEN_HOUR -> open only when every supplier is confirmed open;
    - from OPEN_HOUR -> one confirmed open supplier is enough;
    - after OPEN_HOUR with no open source and at least one error -> unknown,
      so the last publication is preserved rather than falsely closed.
    """
    statuses = tuple(statuses)
    if not statuses:
        return "unknown"
    if all(status == "closed" for status in statuses):
        return "closed"
    if is_open(settings, now):
        return "open" if any(status == "open" for status in statuses) else "unknown"
    return "open" if all(status == "open" for status in statuses) else "closed"


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class SyncService:
    def __init__(self, client, settings, state):
        self.client = client
        self.settings = settings
        self.state = state
        self.readers = [SupplierReader(client, settings, source) for source in settings.sources]
        self.publisher = Publisher(client, settings.target, state, settings)
        self.lock = asyncio.Lock()
        self.wake = asyncio.Event()
        self.stop_event = asyncio.Event()
        self.busy = False
        self.last_attempt = 0.0
        self.retry_after = 0.0
        self.ready = client is not None
        self.startup_error = None

    def attach_client(self, client):
        self.client = client
        self.publisher.client = client
        for reader in self.readers:
            reader.client = client
            reader.entity = None

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

    async def connect(self):
        try:
            if not self.client.is_connected():
                await self.client.connect()
            if not await self.client.is_user_authorized():
                raise LoginRequired("Сессия недействительна. Получи SESSION_STRING через SETUP_MODE")
        except (errors.AuthKeyDuplicatedError, errors.UnauthorizedError) as exc:
            raise LoginRequired(
                "Telegram отозвал SESSION_STRING. Останови другие копии и создай новую сессию"
            ) from exc

    def source_key(self, reader):
        return str(reader.source.peer)

    def cached_items(self, include_closed=False):
        cache = self.state.get("sources", {})
        groups = []
        for reader in self.readers:
            source = cache.get(self.source_key(reader), {})
            # Never publish stale prices from a source that failed this refresh.
            if source.get("status") == "error" or source.get("error"):
                continue
            if source.get("status") == "closed" and not include_closed:
                continue
            groups.append([Item.from_dict(item) for item in source.get("items", [])])
        return merge_sources(groups)

    async def render(self, closed=False):
        options = self.options()
        catalog = self.cached_items(include_closed=closed)

        # Keep one stable message per known block. A temporarily missing brand
        # becomes "Сейчас нет в наличии" instead of delete -> send -> delete.
        known_blocks = set(self.state.get("known_blocks", []))
        known_blocks.update(item.block for item in self.cached_items(include_closed=True))
        if known_blocks != set(self.state.get("known_blocks", [])):
            self.state.set("known_blocks", sorted(known_blocks))

        if closed and not catalog and not known_blocks:
            changes = await self.publisher.hide_existing()
            self.state.update({"last_publish": timestamp(), "published_items": 0})
            return 0, changes

        items = select_items(catalog, self.settings, options)
        pages = render_blocks(
            items,
            self.settings,
            options,
            closed=closed,
            stable_blocks=sorted(known_blocks),
        )
        changes = await self.publisher.publish(pages)
        self.state.update({"last_publish": timestamp(), "published_items": 0 if closed else len(items)})
        return len(items), changes

    async def sync(self, force=False):
        # /stop waits for an active synchronization to finish before acknowledging.
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
                successful = 0

                for reader in self.readers:
                    key = self.source_key(reader)
                    try:
                        budget = self.settings.response_timeout * (len(reader.source.buttons) + 1) * 2 + 120
                        result = await asyncio.wait_for(reader.fetch(), timeout=budget)
                        successful += 1
                        previous = cache.get(key, {})
                        cache[key] = {
                            "status": "closed" if result.closed else "open",
                            "checked": timestamp(),
                            # Retain titles on closure; prices are never rendered while closed.
                            "items": previous.get("items", []) if result.closed else [i.to_dict() for i in result.items],
                            "rejected": result.rejected[:200],
                            "rejected_count": len(result.rejected),
                            "error": None,
                        }
                    except (errors.AuthKeyDuplicatedError, errors.UnauthorizedError) as exc:
                        raise LoginRequired("Сессия Telegram отозвана; получи новую SESSION_STRING") from exc
                    except errors.FloodWaitError:
                        raise
                    except Exception as exc:
                        log.warning("Не удалось прочитать %s: %s", reader.source.label, type(exc).__name__)
                        old = cache.get(key, {})
                        cache[key] = {
                            **old,
                            "status": "error",
                            "error": f"{type(exc).__name__}: {exc}",
                            "checked": timestamp(),
                        }
                        errors_found.append(reader.source.label + ": " + str(exc))

                # Persist diagnostics/cache even when a source failed.
                self.state.set("sources", cache)

                if successful == 0:
                    message = "Прайс сохранён; все источники недоступны"
                    if errors_found:
                        message += ": " + " | ".join(errors_found)
                    self.state.set("last_result", message)
                    return message

                statuses = [cache.get(self.source_key(reader), {}).get("status", "error")
                            for reader in self.readers]
                gate = publication_state(self.settings, statuses)

                if gate == "unknown":
                    # Example: one supplier says closed while the second is offline.
                    # We cannot honestly close or replace the current publication.
                    message = "Прайс сохранён: недостаточно данных для открытия/закрытия"
                    if errors_found:
                        message += "; " + " | ".join(errors_found)
                    self.state.set("last_result", message)
                    return message

                closed = gate == "closed"
                count, changes = await self.render(closed=closed)
                self.state.set("publication_closed", closed)

                if closed:
                    message = "Продажи закрыты, цены скрыты"
                else:
                    message = f"Прайс обновлён: {count} позиций, изменений: {changes}"
                if errors_found:
                    message += "; недоступны: " + " | ".join(errors_found)
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

    async def refresh_format(self):
        async with self.lock:
            if not self.ready:
                raise RuntimeError(self.startup_status())
            # Changes are applied to the last parsed cache without supplier commands.
            await self.connect()
            cache = self.state.get("sources", {})
            statuses = [cache.get(self.source_key(reader), {}).get("status", "error")
                        for reader in self.readers]
            gate = publication_state(self.settings, statuses)
            if gate == "unknown":
                closed = self.state.get("publication_closed", not is_open(self.settings))
            else:
                closed = gate == "closed"
            return await self.render(closed=closed)

    def status(self):
        options = self.options()
        cache = self.state.get("sources", {})
        lines = [
            "🟢 Синхронизация: включена" if self.enabled() else "⏸ Синхронизация: остановлена",
            f"Интервал: {options.get('poll_seconds', self.settings.poll_seconds) // 60} мин.",
            f"Наценка: {options.get('markup', str(self.settings.markup))} + {options.get('markup_percent', str(self.settings.markup_percent))}%",
            "SIM: " + options.get("sim_filter", self.settings.sim_filter),
            "Последняя проверка: " + self.state.get("last_check", "ещё не было"),
            "Результат: " + self.state.get("last_result", "ожидание"),
        ]
        if not self.ready:
            lines[0] = "⚠️ Чтение прайса недоступно: " + self.startup_status()
        for reader in self.readers:
            source = cache.get(self.source_key(reader), {})
            status = "ошибка чтения" if source.get("error") else {"open": "открыт", "closed": "закрыт"}.get(source.get("status"), "неизвестно")
            lines.append(f"{reader.source.label}: {status}; не распознано строк: {source.get('rejected_count', 0)}")
        return "\n".join(lines)

    async def run(self):
        last_window = None
        while not self.stop_event.is_set():
            self.wake.clear()
            now = asyncio.get_running_loop().time()
            # is_open is only the 10:00 start threshold now. Suppliers are still
            # polled before it so both of them can open the price early.
            window = is_open(self.settings)
            interval = self.options().get("poll_seconds", self.settings.poll_seconds)
            due = now - self.last_attempt >= interval or self.last_attempt == 0 or window != last_window
            if self.enabled() and due and now >= self.retry_after:
                await self.sync()
                last_window = window
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=5)
            except asyncio.TimeoutError:
                pass
