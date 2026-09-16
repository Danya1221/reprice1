from pathlib import Path


def replace_once(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected 1 match, got {count}")
    return text.replace(old, new, 1)

# config.py -----------------------------------------------------------------
p = Path("config.py")
s = p.read_text(encoding="utf-8")
s = replace_once(
    s,
    '    session: str = ""\n    target: object = ""\n',
    '    session: str = ""\n    session_2: str = ""\n    target: object = ""\n',
    "settings second session field",
)
s = replace_once(
    s,
    '            session=os.getenv("SESSION_STRING", "").strip(),\n            target=peer(os.getenv("TARGET_CHANNEL", "")),\n',
    '            session=os.getenv("SESSION_STRING", "").strip(),\n            session_2=os.getenv("SESSION_STRING_2", "").strip(),\n            target=peer(os.getenv("TARGET_CHANNEL", "")),\n',
    "settings second session env",
)
p.write_text(s, encoding="utf-8")

# runtime.py ----------------------------------------------------------------
p = Path("runtime.py")
s = p.read_text(encoding="utf-8")
head = s.split("class SyncService:", 1)[0]
new_class = r'''class SyncService:
    def __init__(self, client, settings, state):
        self.client = client
        self.settings = settings
        self.state = state
        self.clients = {}
        self.reader_groups = {
            1: [SupplierReader(client, settings, source) for source in settings.sources]
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

    def _slot_sources(self, slot):
        # Read every configured supplier from both accounts. A static feed is safe
        # to read twice; a supplier bot may intentionally return account-specific prices.
        return self.settings.sources

    def attach_client(self, client, slot=1):
        slot = int(slot)
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

    def cached_items(self, include_closed=False):
        cache = self.state.get("sources", {})
        groups = []
        for slot in (1, 2):
            if slot == 2 and not self.account_configured(2):
                continue
            for source in self._slot_sources(slot):
                value = cache.get(self.source_cache_key(source, slot), {})
                if value.get("status") == "closed" and not include_closed:
                    continue
                if value.get("items"):
                    groups.append([Item.from_dict(item) for item in value.get("items", [])])
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
        if not closed:
            rendered_rows = sum(content.count("<code>") for content in pages.values())
            if rendered_rows != len(selected):
                raise RuntimeError(
                    f"Защита публикации: рендер потерял позиции ({rendered_rows} из {len(selected)}). "
                    "Текущий прайс оставлен без изменений."
                )
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
        return len(self.settings.sources) >= 2 and all(status == "open" for status in source_statuses)

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
                        fresh_by_source[index].append(status)
                        cache[key] = {
                            "status": status,
                            "checked": timestamp(),
                            "items": previous.get("items", []) if result.closed else [i.to_dict() for i in result.items],
                            "rejected": result.rejected[:200],
                            "rejected_count": len(result.rejected),
                            "error": None,
                            "account": slot,
                        }
                        if status == "open":
                            fresh_open_groups.append(result.items)
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
                        cache[key] = {
                            **old,
                            "error": f"{type(exc).__name__}: {exc}",
                            "checked": timestamp(),
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
                catalog = merge_lowest(fresh_open_groups)
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
'''
p.write_text(head + new_class, encoding="utf-8")

# main.py -------------------------------------------------------------------
p = Path("main.py")
s = p.read_text(encoding="utf-8")
start = s.index("async def prepare_supplier")
end = s.index("\n\nasync def main()", start)
replacement = r'''async def prepare_supplier(service, settings, controller=None):
    """Prepare one or two Telegram user accounts used to read supplier prices."""
    settings.validate(require_sync=False)
    if not settings.session:
        raise ValueError("Сессия аккаунта 1 не подключена; выполни /login 1 в управляющем боте")
    if not settings.sources:
        raise ValueError("Укажи SUPPLIER_BOT для чтения прайса")

    sessions = [(1, settings.session), (2, getattr(settings, "session_2", ""))]
    primary_ready = False
    for slot, session_string in sessions:
        if not session_string:
            continue
        try:
            try:
                session = StringSession(session_string)
            except Exception:
                raise ValueError(f"Сессия аккаунта {slot} повреждена; выполни /login {slot}") from None

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
            service.attach_client(client, slot)
            ok = await service.connect_account(slot)
            if not ok:
                raise ValueError(service.account_errors.get(slot, f"Аккаунт {slot} не подключился"))

            me = await client.get_me()
            if me is None or me.bot:
                raise ValueError(f"Сессия аккаунта {slot} должна принадлежать пользовательскому Telegram-аккаунту")
            if controller is not None and slot == 1 and not settings.admin_ids:
                controller.admins = {me.id}

            saved = client.session.save()
            state_key = "session_string" if slot == 1 else "session_string_2"
            attr = "session" if slot == 1 else "session_2"
            if saved and service.state.get(state_key) != saved:
                service.state.set(state_key, saved)
            setattr(settings, attr, saved or session_string)

            for reader in service.readers_for_slot(slot):
                try:
                    await reader.resolve()
                except Exception as exc:
                    if slot == 1:
                        raise
                    log.warning(
                        "Аккаунт 2 не видит %s: %s: %s",
                        reader.source.label, type(exc).__name__, exc,
                    )
                    reader.entity = None

            if slot == 1:
                primary_ready = True
            log.info("Telegram-аккаунт %s подключён; источников: %s", slot, len(service.readers_for_slot(slot)))
        except Exception as exc:
            if slot == 1:
                raise
            service.account_errors[slot] = str(exc) or type(exc).__name__
            client = service.clients.get(slot)
            if client is not None:
                with suppress(Exception):
                    await client.disconnect()
            service.attach_client(None, slot)
            log.warning("Второй Telegram-аккаунт не подключён: %s", service.account_errors[slot])

    if not primary_ready:
        raise ValueError("Аккаунт 1 не подключён; выполни /login 1")
    service.startup_error = None
    service.ready = True


async def run_supplier(service, settings, controller=None, retry_seconds=30):
    """Run supplier sync forever and hot-reload either saved Telegram session."""
    database = getattr(service.state, "database", None)
    if database is not None:
        def waiting():
            service.startup_error = "Жду завершения предыдущего Railway deployment"
            log.warning("Жду PostgreSQL runtime lock перед подключением Telegram-сессии")

        await asyncio.to_thread(database.acquire_runtime_lock, waiting)
        log.info("PostgreSQL runtime lock получен")

    while not service.stop_event.is_set():
        service.ready = False
        service.reload_requested = False
        try:
            stored = service.state.get("session_string", "")
            stored_2 = service.state.get("session_string_2", "")
            if stored:
                settings.session = stored
            if stored_2:
                settings.session_2 = stored_2
            await asyncio.wait_for(prepare_supplier(service, settings, controller), timeout=180)
            await service.run()
            if service.stop_event.is_set():
                return
            if service.reload_requested:
                continue
            if service.startup_error:
                raise RuntimeError(service.startup_error)
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            service.startup_error = str(exc) or type(exc).__name__
            service.ready = False
            log.error(
                "Чтение ботов-поставщиков недоступно: %s. Управляющий бот остаётся доступен",
                service.startup_error,
            )
        finally:
            await service.disconnect_clients()

        if service.reload_requested:
            continue
        service.wake.clear()
        try:
            await asyncio.wait_for(service.wake.wait(), timeout=retry_seconds)
        except asyncio.TimeoutError:
            pass
'''
s = s[:start] + replacement + s[end:]
s = replace_once(
    s,
    '''    stored_session = state.get("session_string", "")
    if stored_session:
        settings.session = stored_session
''',
    '''    stored_session = state.get("session_string", "")
    stored_session_2 = state.get("session_string_2", "")
    if stored_session:
        settings.session = stored_session
    if stored_session_2:
        settings.session_2 = stored_session_2
''',
    "main load second session",
)
s = replace_once(
    s,
    '''        if service.client is not None:
            with suppress(Exception):
                await service.client.disconnect()
            service.attach_client(None)
''',
    '''        with suppress(Exception):
            await service.disconnect_clients()
''',
    "main disconnect all accounts",
)
p.write_text(s, encoding="utf-8")

# control_botapi.py ----------------------------------------------------------
p = Path("control_botapi.py")
s = p.read_text(encoding="utf-8")
s = replace_once(
    s,
    '''            [{"text": "🔄 Запросить сейчас", "callback_data": "sync"}, {"text": "📊 Статус", "callback_data": "status"}],
            [{"text": "⏱ Интервал", "callback_data": "interval"}, {"text": "💰 Наценка", "callback_data": "markup"}],
''',
    '''            [{"text": "🔄 Запросить сейчас", "callback_data": "sync"}, {"text": "📊 Статус", "callback_data": "status"}],
            [{"text": "👥 Telegram-аккаунты", "callback_data": "accounts"}],
            [{"text": "⏱ Интервал", "callback_data": "interval"}, {"text": "💰 Наценка", "callback_data": "markup"}],
''',
    "accounts menu row",
)
s = replace_once(
    s,
    '''    async def begin_login(self, chat_id, user_id):
        await self._close_login(user_id)
        self.login_flows[user_id] = {"stage": "phone", "client": None, "chat_id": chat_id}
        await self.send(chat_id, "🔐 Вход в Telegram-аккаунт поставщиков\\n\\nОтправь номер телефона в международном формате, например:\\n+79991234567\\n\\nДля отмены: /cancel")

    async def finish_login(self, chat_id, user_id, flow):
        session_string = flow["client"].session.save()
        self.service.state.set("session_string", session_string)
        self.service.settings.session = session_string
        self.service.startup_error = None
        await self._close_login(user_id)
        await self.send(chat_id, "✅ Вход выполнен, сессия сохранена в базе.\\nПодключение к поставщикам произойдёт автоматически.\\n\\nПроверь /status, затем нажми «🔄 Запросить сейчас».", self.menu())
''',
    '''    async def accounts_menu(self, chat_id):
        connected_1 = bool(1 in getattr(self.service, "clients", {}) and self.service.ready)
        connected_2 = bool(2 in getattr(self.service, "clients", {}))
        saved_1 = bool(self.service.state.get("session_string", "") or self.service.settings.session)
        saved_2 = bool(self.service.state.get("session_string_2", "") or getattr(self.service.settings, "session_2", ""))
        text = (
            "👥 Telegram-аккаунты поставщиков\\n\\n"
            f"1. {'🟢 подключён' if connected_1 else ('🟡 сохранён' if saved_1 else '⚪ не подключён')}\\n"
            f"2. {'🟢 подключён' if connected_2 else ('🟡 сохранён' if saved_2 else '⚪ не подключён')}\\n\\n"
            "Один и тот же бот поставщика читается с обоих аккаунтов. "
            "Если цены отличаются, в итоговый прайс попадёт меньшая закупочная цена."
        )
        await self.send(chat_id, text, {"inline_keyboard": [
            [{"text": "🔑 Войти в аккаунт 1", "callback_data": "account:login:1"}],
            [{"text": "🔑 Войти в аккаунт 2", "callback_data": "account:login:2"}],
        ]})

    async def begin_login(self, chat_id, user_id, slot=1):
        slot = int(slot)
        if slot not in {1, 2}:
            raise ValueError("Аккаунт может быть только 1 или 2")
        await self._close_login(user_id)
        self.login_flows[user_id] = {"stage": "phone", "client": None, "chat_id": chat_id, "slot": slot}
        await self.send(chat_id, f"🔐 Вход в Telegram-аккаунт {slot}\\n\\nОтправь номер телефона в международном формате, например:\\n+79991234567\\n\\nДля отмены: /cancel")

    async def finish_login(self, chat_id, user_id, flow):
        slot = int(flow.get("slot", 1))
        session_string = flow["client"].session.save()
        state_key = "session_string" if slot == 1 else "session_string_2"
        attr = "session" if slot == 1 else "session_2"
        self.service.state.set(state_key, session_string)
        setattr(self.service.settings, attr, session_string)
        self.service.startup_error = None
        if hasattr(self.service, "account_errors"):
            self.service.account_errors.pop(slot, None)
        if hasattr(self.service, "request_reconnect"):
            self.service.request_reconnect()
        await self._close_login(user_id)
        await self.send(chat_id, f"✅ Аккаунт {slot} подключён, сессия сохранена в базе.\\nПереподключаю чтение поставщиков автоматически.\\n\\nПосле подключения нажми «🔄 Запросить сейчас».", self.menu())
''',
    "dual account login flow",
)
s = replace_once(
    s,
    '''            elif data == "status":
                await self.send(chat_id, self.service.status(), self.menu())
            elif data == "markup":
''',
    '''            elif data == "status":
                await self.send(chat_id, self.service.status(), self.menu())
            elif data == "accounts":
                await self.accounts_menu(chat_id)
            elif data.startswith("account:login:"):
                await self.begin_login(chat_id, user_id, int(data.rsplit(":", 1)[1]))
            elif data == "markup":
''',
    "account callbacks",
)
s = replace_once(
    s,
    '''        commands = {"/start", "/help", "/id", "/status", "/sync", "/stop", "/resume", "/markup",
                    "/percent", "/interval", "/order", "/rejected", "/login", "/cancel"}
''',
    '''        commands = {"/start", "/help", "/id", "/status", "/sync", "/stop", "/resume", "/markup",
                    "/percent", "/interval", "/order", "/rejected", "/login", "/login2", "/accounts", "/cancel"}
''',
    "account commands",
)
s = replace_once(
    s,
    '''                text_out = ("🛠 Управление прайсом\\n\\n/login — войти в Telegram-аккаунт поставщиков\\n"
                            "/markup 500 — наценка\\n/percent 5 — процент\\n/interval 15 — интервал в минутах\\n"
''',
    '''                text_out = ("🛠 Управление прайсом\\n\\n/login 1 — подключить аккаунт 1\\n/login 2 — подключить аккаунт 2\\n/accounts — состояние аккаунтов\\n"
                            "/markup 500 — наценка\\n/percent 5 — процент\\n/interval 15 — интервал в минутах\\n"
''',
    "account help",
)
s = replace_once(
    s,
    '''            elif command == "/login":
                await self.begin_login(chat_id, user_id)
            elif command == "/cancel":
''',
    '''            elif command in {"/login", "/login2"}:
                requested = "2" if command == "/login2" else (value.strip() or "1")
                if requested not in {"1", "2"}:
                    raise ValueError("Используй /login 1 или /login 2")
                await self.begin_login(chat_id, user_id, int(requested))
            elif command == "/accounts":
                await self.accounts_menu(chat_id)
            elif command == "/cancel":
''',
    "account login commands",
)
p.write_text(s, encoding="utf-8")

# .env.example ---------------------------------------------------------------
p = Path(".env.example")
s = p.read_text(encoding="utf-8")
s = replace_once(
    s,
    'SESSION_STRING=\nSUPPLIER_BOT=@supplier_bot\n',
    'SESSION_STRING=\n# Optional second Telegram user account. It can also be connected with /login 2.\nSESSION_STRING_2=\nSUPPLIER_BOT=@supplier_bot\n',
    "env second session",
)
s = s.replace(
    '# Optional second price feed, read by the same Telegram user account.\n',
    '# Optional second price feed. When account 2 is connected, both supplier sources are read from both accounts.\n',
)
p.write_text(s, encoding="utf-8")

# tests ----------------------------------------------------------------------
p = Path("tests/test_dual_accounts.py")
p.write_text(r'''import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from config import Settings, Source
from control_botapi import BotAPIController
from prices import Item, ParseResult
from runtime import SyncService
from state import StateStore


class DualAccountRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = StateStore(Path(self.temp.name) / "state.json")
        self.settings = Settings(
            sources=(Source("@hi", label="HI"),),
            off_hours=False,
            session="ONE",
            session_2="TWO",
        )
        self.client1 = SimpleNamespace(
            is_connected=lambda: True,
            connect=AsyncMock(),
            is_user_authorized=AsyncMock(return_value=True),
            disconnect=AsyncMock(),
        )
        self.client2 = SimpleNamespace(
            is_connected=lambda: True,
            connect=AsyncMock(),
            is_user_authorized=AsyncMock(return_value=True),
            disconnect=AsyncMock(),
        )
        self.service = SyncService(self.client1, self.settings, self.state)
        self.service.attach_client(self.client2, 2)
        self.service.publisher.publish = AsyncMock(return_value=0)

    def tearDown(self):
        self.temp.cleanup()

    async def test_same_hi_variant_from_two_accounts_uses_lower_price(self):
        expensive = Item("iPhone 17 256 Black", Decimal("60000"), "RUB", "iPhone 17")
        cheap = Item("iPhone 17 256 Black", Decimal("57000"), "RUB", "iPhone 17")
        self.service.readers_for_slot(1)[0].fetch = AsyncMock(return_value=ParseResult([expensive], []))
        self.service.readers_for_slot(2)[0].fetch = AsyncMock(return_value=ParseResult([cheap], []))

        with patch("runtime.is_open", return_value=True):
            result = await self.service.sync(force=True)

        self.assertIn("аккаунт 2", result)
        pages = self.service.publisher.publish.call_args.args[0]
        body = "\n".join(pages.values())
        self.assertIn("57 000", body)
        self.assertNotIn("60 000", body)
        cache = self.state.get("sources")
        self.assertIn("@hi", cache)
        self.assertIn("account2:@hi", cache)


class DualAccountControlTests(unittest.IsolatedAsyncioTestCase):
    async def test_second_login_is_saved_in_separate_slot_and_requests_reconnect(self):
        service = MagicMock()
        service.settings = SimpleNamespace(api_id=123, api_hash="hash", session="", session_2="")
        service.state = MagicMock()
        service.startup_error = "missing"
        service.account_errors = {}
        service.request_reconnect = MagicMock()
        controller = BotAPIController("TOKEN", service, [42])
        controller.send = AsyncMock()
        temp = SimpleNamespace(
            session=SimpleNamespace(save=lambda: "SECOND_SESSION"),
            disconnect=AsyncMock(),
        )
        controller.login_flows[42] = {"stage": "password", "client": temp, "chat_id": 42, "slot": 2}

        await controller.finish_login(42, 42, controller.login_flows[42])

        service.state.set.assert_called_with("session_string_2", "SECOND_SESSION")
        self.assertEqual(service.settings.session_2, "SECOND_SESSION")
        service.request_reconnect.assert_called_once()
        temp.disconnect.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
''', encoding="utf-8")
