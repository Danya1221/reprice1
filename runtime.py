"""Synchronization lifecycle for two supplier prices."""
import asyncio
import logging
from collections import OrderedDict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from telethon import errors

from prices import Item, render_blocks, select_items
from publisher import Publisher
from supplier import SupplierReader

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
                raise LoginRequired("Сессия недействительна. Выполни /login в управляющем боте")
        except (errors.AuthKeyDuplicatedError, errors.UnauthorizedError) as exc:
            raise LoginRequired(
                "Telegram отозвал сессию. Останови другие копии и выполни /login заново"
            ) from exc

    def source_key(self, reader):
        return str(reader.source.peer)

    def cached_items(self, include_closed=False):
        cache = self.state.get("sources", {})
        groups = []
        for reader in self.readers:
            source = cache.get(self.source_key(reader), {})
            if source.get("status") == "closed" and not include_closed:
                continue
            groups.append([Item.from_dict(item) for item in source.get("items", [])])
        return merge_lowest(groups)

    async def render(self, closed=False, items=None):
        options = self.options()
        catalog = self.cached_items(include_closed=closed) if items is None else items
        if closed and not catalog:
            changes = await self.publisher.hide_existing()
            self.state.update({"last_publish": timestamp(), "published_items": 0})
            return 0, changes
        selected = select_items(catalog, self.settings, options)
        pages = render_blocks(selected, self.settings, options, closed=closed)
        changes = await self.publisher.publish(pages)
        self.state.update({"last_publish": timestamp(), "published_items": 0 if closed else len(selected)})
        return len(selected), changes

    def _both_fresh_open(self, fresh_status):
        return len(self.readers) >= 2 and all(fresh_status.get(self.source_key(r)) == "open" for r in self.readers)

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
                fresh_status = {}
                fresh_open_groups = []

                for reader in self.readers:
                    key = self.source_key(reader)
                    try:
                        budget = self.settings.response_timeout * (len(reader.source.buttons) + 1) * 2 + 120
                        result = await asyncio.wait_for(reader.fetch(), timeout=budget)
                        previous = cache.get(key, {})
                        status = "closed" if result.closed else "open"
                        fresh_status[key] = status
                        cache[key] = {
                            "status": status,
                            "checked": timestamp(),
                            "items": previous.get("items", []) if result.closed else [i.to_dict() for i in result.items],
                            "rejected": result.rejected[:200],
                            "rejected_count": len(result.rejected),
                            "error": None,
                        }
                        if status == "open":
                            fresh_open_groups.append(result.items)
                    except (errors.AuthKeyDuplicatedError, errors.UnauthorizedError) as exc:
                        raise LoginRequired("Сессия Telegram отозвана; выполни /login заново") from exc
                    except errors.FloodWaitError:
                        raise
                    except Exception as exc:
                        log.warning("Не удалось прочитать %s: %s", reader.source.label, type(exc).__name__)
                        old = cache.get(key, {})
                        cache[key] = {
                            **old,
                            "error": f"{type(exc).__name__}: {exc}",
                            "checked": timestamp(),
                        }
                        fresh_status[key] = "error"
                        errors_found.append(reader.source.label + ": " + str(exc))

                self.state.set("sources", cache)

                statuses = [fresh_status.get(self.source_key(r), "error") for r in self.readers]
                if self.readers and all(status == "closed" for status in statuses):
                    await self.render(closed=True)
                    message = "Оба поставщика закрыты: цены скрыты"
                    self.state.set("last_result", message)
                    return message

                if not fresh_open_groups:
                    message = "Прайс сохранён; нет свежего открытого источника"
                    if errors_found:
                        message += ": " + " | ".join(errors_found)
                    self.state.set("last_result", message)
                    return message

                # Before 10:00 MSK publication may open early only when BOTH
                # suppliers are freshly open. At/after 10:00 one open source is enough.
                if not is_open(self.settings) and not self._both_fresh_open(fresh_status):
                    await self.publisher.hide_existing()
                    message = (
                        f"До {self.settings.open_hour:02d}:00: ждём открытия обоих поставщиков. "
                        "Цены скрыты"
                    )
                    self.state.set("last_result", message)
                    return message

                # Only freshly successful open sources participate. Identical
                # variants choose the lower purchase price BEFORE markup.
                catalog = merge_lowest(fresh_open_groups)
                count, changes = await self.render(items=catalog)
                source_note = "/".join(statuses)
                message = f"Прайс обновлён: {count} позиций, изменений: {changes}; источники: {source_note}"
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
            await self.connect()
            cache = self.state.get("sources", {})
            statuses = [cache.get(self.source_key(r), {}).get("status") for r in self.readers]
            closed = bool(self.readers) and all(status == "closed" for status in statuses)
            if closed:
                return await self.render(closed=True)
            return await self.render(closed=False)

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
        last_start_state = None
        while not self.stop_event.is_set():
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
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=5)
            except asyncio.TimeoutError:
                pass
