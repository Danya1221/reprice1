"""Manage supplier channels separately from the publication destination."""
import hashlib
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from config import Source
from prices import Item, select_items
from control_first import FirstMessageController


def channel_token(peer, slot=1):
    return hashlib.sha256(f"{slot}:{peer}".encode()).hexdigest()[:16]


class ChannelController(FirstMessageController):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.channel_waiting = set()
        self.channel_choices = {}
        self.channel_slots = {}

    def menu(self):
        menu = super().menu()
        menu["inline_keyboard"].append([{"text": "📥 Каналы поставщиков", "callback_data": "channels:show:0"}])
        menu["inline_keyboard"].append([{"text": "🌟 Supernova", "callback_data": "supernova:show"}])
        return menu

    async def show_supernova(self, chat_id, notice=""):
        settings = self.service.state.get("supernova_settings", {})
        snapshot = self.service.state.get("supernova_snapshot", {})
        auto = self.service.state.get("supernova_auto", {}) or {}
        enabled = settings.get("enabled", False)
        slot = settings.get("slot")
        auto_enabled = auto.get("enabled", False) and auto.get("slot") == slot
        fresh = self.service.supernova_items() if enabled else []
        file_time = snapshot.get("file_time") if snapshot.get("slot") == slot else None
        when = (datetime.fromtimestamp(file_time, ZoneInfo("Europe/Moscow")).strftime("%d.%m %H:%M МСК")
                if file_time else "ещё не получен")
        status = f"Подключён аккаунт {slot}" if enabled else "Не подключён"
        text = (f"🌟 Supernova\n\n{status}\nАвтообновление: {'✅ включено' if auto_enabled else '⏸ выключено'}\nПоследний Excel: {when}\n"
                f"Активных позиций: {len(fresh)}\n\n"
                "При автообновлении PriceB открывает Mini App, запрашивает Excel и ждёт файл в чате Supernova. "
                f"Расписание: {self.service.settings.open_hour:02d}:00–{self.service.settings.close_hour:02d}:00 ({self.service.settings.timezone}), примерно раз в 8 минут. "
                "Цены используются до 10 минут с момента получения; затем нужен свежий Excel.")
        if enabled and auto.get("status"):
            text += "\n\nАвтозапрос: " + str(auto["status"])[:120]
        if enabled and auto.get("error"):
            text += "\n⚠️ " + str(auto["error"])[:240]
        error = self.service.state.get("supernova_error", "")
        if enabled and error:
            text += "\n\n⚠️ " + error[:300]
        rows = [
            [{"text": "👤 Аккаунт 1", "callback_data": "supernova:account:1"},
             {"text": "👤 Аккаунт 2", "callback_data": "supernova:account:2"}],
            [{"text": "Открыть Supernova", "url": "https://t.me/supernova_bot"}],
        ]
        if enabled:
            rows.append([{"text": "⏸ Выключить авто" if auto_enabled else "▶️ Включить авто",
                          "callback_data": "supernova:auto:off" if auto_enabled else "supernova:auto:on"}])
            rows.append([{"text": "🔄 Запросить Excel сейчас", "callback_data": "supernova:request"}])
            rows.append([{"text": "⏹ Отключить Supernova", "callback_data": "supernova:disable"}])
        rows.append([{"text": "⬅️ Главное меню", "callback_data": "supernova:back"}])
        await self.send(chat_id, (notice + "\n\n" if notice else "") + text, {"inline_keyboard": rows})

    @staticmethod
    def channel_key(item):
        slot = item.get("slot", 1)
        peer = str(item["peer"])
        return peer if slot == 1 else f"account{slot}:{peer}"

    @staticmethod
    def msk(value):
        if not value:
            return "ещё не было"
        try:
            return datetime.fromisoformat(value).astimezone(ZoneInfo("Europe/Moscow")).strftime("%d.%m %H:%M МСК")
        except (ValueError, TypeError):
            return str(value)[:20]

    def channel_diagnostics(self, item):
        slot = item.get("slot", 1)
        cached = self.service.state.get("sources", {}).get(self.channel_key(item), {})
        parsed = cached.get("parsed_count")
        if parsed is None and cached.get("status") and not cached.get("error"):
            parsed = len(cached.get("items", []))
        messages = cached.get("messages_count")
        visible = item.get("visible", True)
        if not cached.get("checked"):
            reading = "⏳ Чтение ещё не проверялось"
        elif cached.get("error"):
            reading = "❌ Ошибка чтения / разбора"
        elif cached.get("status") == "closed":
            reading = "🌙 Поставщик сообщил о закрытии"
        elif parsed == 0:
            reading = "⚠️ Сообщения прочитаны, товаров не найдено"
        else:
            reading = "✅ Чтение успешно"
        stats = f"Сообщений: {messages if messages is not None else '—'} · товаров: {parsed if parsed is not None else '—'}"
        status = f"Показ в прайсе: {'✅ включён' if visible else '⛔ выключен'}"
        return cached, reading, stats, status

    async def show_channels(self, chat_id, page=0, notice=""):
        channels = self.service.state.get("supplier_channels", [])
        page = max(0, min(page, max(0, (len(channels) - 1) // 5)))
        rows, details = [], []
        for item in channels[page * 5:page * 5 + 5]:
            slot = item.get("slot", 1)
            token = channel_token(item["peer"], slot)
            cached, reading, stats, status = self.channel_diagnostics(item)
            details.append(
                f"📡 [{slot}] {item['title']}\n"
                f"{reading}\n{stats}\n{status}\n"
                f"Проверено: {self.msk(cached.get('checked'))}"
            )
            rows.append([
                {"text": "🔎 Причина / детали", "callback_data": "channels:details:" + token},
                {"text": "👁 Скрыть товары" if item.get("visible", True) else "✅ Показывать товары",
                 "callback_data": "channels:toggle:" + token},
            ])
            rows.append([{"text": "🗑 Удалить " + item["title"][:35],
                          "callback_data": "channels:remove:" + token}])
        nav = []
        if page:
            nav.append({"text": "⬅️", "callback_data": f"channels:show:{page - 1}"})
        if (page + 1) * 5 < len(channels):
            nav.append({"text": "➡️", "callback_data": f"channels:show:{page + 1}"})
        if nav:
            rows.append(nav)
        rows.append([{"text": "🔄 Проверить чтение сейчас", "callback_data": "channels:check"}])
        rows.append([{"text": "⚙️ Категории прайса", "callback_data": "blocks:0"}])
        rows.append([{"text": "➕ Добавить канал", "callback_data": "channels:add"}])
        rows.append([{"text": "⬅️ Главное меню", "callback_data": "channels:back"}])
        body = (
            "📥 Каналы поставщиков\n\n"
            + ("\n\n".join(details) if details else "Пока нет добавленных каналов.")
            + "\n\nТовары из канала участвуют в прайсе, если распознаны, показ включён "
              "и они проходят общие фильтры. При совпадении модели выбирается самая низкая цена."
        )
        await self.send(chat_id, (notice + "\n\n" if notice else "") + body[:3900],
                        {"inline_keyboard": rows})

    async def show_channel_details(self, chat_id, item):
        cached, reading, stats, visibility = self.channel_diagnostics(item)
        slot = item.get("slot", 1)
        source = Source(item["peer"], mode="feed", label="Канал: " + item["title"])
        items = [Item.from_dict(data) for data in cached.get("items", [])]
        eligible = self.service.source_items(source, items)
        after_filters = select_items(eligible, self.service.settings, self.service.options())
        text = (
            f"🔎 Канал: {item['title']} · аккаунт {slot}\n\n"
            f"{reading}\n{stats}\n{visibility}\n"
            f"Последняя проверка: {self.msk(cached.get('checked'))}\n"
            f"Успешное чтение: {self.msk(cached.get('last_success'))}\n"
            f"Сохранено позиций: {len(items)}\n"
            f"Прошло общие фильтры: {len(after_filters)}\n"
            f"Не распознано строк: {cached.get('rejected_count', 0)}\n"
            f"Пропущено фото/видео/других медиа: {cached.get('ignored_media_count') if cached.get('ignored_media_count') is not None else '—'}\n"
            f"Прочитано файлов прайса: {cached.get('price_files_count') if cached.get('price_files_count') is not None else '—'}\n"
            f"Текстовых сообщений: {cached.get('text_posts_count') if cached.get('text_posts_count') is not None else '—'}\n"
            f"Строк с ценой до объединения: {cached.get('price_rows_count') if cached.get('price_rows_count') is not None else '—'}\n"
            f"Пропущено фото: {cached.get('ignored_photo_posts_count') if cached.get('ignored_photo_posts_count') is not None else '—'}\n"
            f"Пропущено видео: {cached.get('ignored_video_posts_count') if cached.get('ignored_video_posts_count') is not None else '—'}\n"
            f"Подписи фото/видео: {'✅ читаем текст' if item.get('read_captions', False) else '⛔ не читаем'}\n"
            f"Пропущено подписей: {cached.get('ignored_caption_posts_count') if cached.get('ignored_caption_posts_count') is not None else '—'}\n"
            f"Прочитано подписей: {cached.get('read_caption_posts_count') if cached.get('read_caption_posts_count') is not None else '—'}\n"
        )
        if cached.get("error"):
            text += "\n❌ Ошибка: " + str(cached["error"])[:550] + "\n"
        if cached.get("status") == "closed":
            text += "\nИсточник прислал сообщение о закрытии. Цены не активны.\n"
        if not item.get("visible", True):
            text += "\nПоказ товаров этого канала отключён. Чтение продолжается.\n"
        if items and not after_filters:
            text += "\nТовары отфильтрованы. Проверь настройки категорий, SIM и аксессуаров.\n"
        price_rows = cached.get("price_rows_count")
        parsed_count = cached.get("parsed_count")
        if price_rows is not None and parsed_count is not None and price_rows > parsed_count:
            text += f"\nОдинаковые позиции объединены: {price_rows - parsed_count}.\n"
        caption_posts = cached.get("ignored_caption_posts_count")
        if caption_posts:
            text += (f"\n⚠️ {caption_posts} сообщений с фото/видео содержали текстовую подпись. "
                     "По настройке подписи НЕ читаются, даже если в них есть цены.\n")
        if len(after_filters) > 0:
            text += "\nЭто число до объединения цен поставщиков: в общем прайсе может быть меньше позиций.\n"
        rejected = cached.get("rejected") or []
        if rejected:
            text += "\nПримеры нераспознанных строк:\n"
            text += "\n".join("• " + str(line)[:140] for line in rejected[:4])
        token = channel_token(item["peer"], slot)
        await self.send(chat_id, text[:3800], {"inline_keyboard": [
            [{"text": "✅ Показывать товары" if not item.get("visible", True) else "👁 Скрыть товары",
              "callback_data": "channels:toggle:" + token}],
            [{"text": "📝 Отключить чтение подписей" if item.get("read_captions", False) else
                      "📝 Читать только текст подписей", "callback_data": "channels:captions:" + token}],
            [{"text": "🔄 Проверить чтение", "callback_data": "channels:check"}],
            [{"text": "⚙️ Категории прайса", "callback_data": "blocks:0"}],
            [{"text": "⬅️ К списку каналов", "callback_data": "channels:show:0"}],
        ]})

    async def show_channel_choices(self, chat_id, user_id, slot, page=0):
        selected = self.channel_choices.get(user_id)
        if selected is None or selected[0] != slot or page == 0:
            account_name, channels = await self.service.list_available_supplier_channels(slot=slot)
            self.channel_choices[user_id] = (slot, account_name, channels)
        _, account_name, channels = self.channel_choices[user_id]
        page = max(0, min(page, max(0, (len(channels) - 1) // 8)))
        rows = [
            [{"text": item["title"][:55], "callback_data": f"channels:pick:{slot}:{item['id']}"}]
            for item in channels[page * 8:page * 8 + 8]
        ]
        nav = []
        if page:
            nav.append({"text": "⬅️", "callback_data": f"channels:list:{slot}:{page - 1}"})
        if (page + 1) * 8 < len(channels):
            nav.append({"text": "➡️", "callback_data": f"channels:list:{slot}:{page + 1}"})
        if nav:
            rows.append(nav)
        rows.append([{"text": "⬅️ Выбрать другой аккаунт", "callback_data": "channels:add"}])
        notice = (
            f"📋 Каналы Telegram-аккаунта {slot} ({account_name})\n"
            f"Найдено: {len(channels)}. Выбери канал с прайсом."
            if channels else
            f"📋 У Telegram-аккаунта {slot} ({account_name}) нет доступных каналов.\n"
            f"Проверь подписку этим аккаунтом или подключи его через /login {slot}."
        )
        await self.send(chat_id, notice, {"inline_keyboard": rows})

    async def handle_callback(self, callback):
        data = callback.get("data") or ""
        if data.startswith("supernova:"):
            user_id = callback.get("from", {}).get("id")
            chat = (callback.get("message") or {}).get("chat") or {}
            if not self.allowed(user_id, chat.get("type", "private")):
                await self.answer_callback(callback["id"], "Нет доступа", True)
                return
            await self.answer_callback(callback["id"])
            chat_id = chat.get("id")
            if data == "supernova:back":
                await self.send(chat_id, "🛠 Управление прайсом", self.menu())
            elif data == "supernova:show":
                await self.show_supernova(chat_id)
            elif data == "supernova:disable":
                await self.service.configure_supernova(None)
                await self.show_supernova(chat_id, "✅ Supernova отключена.")
            elif data in {"supernova:auto:on", "supernova:auto:off"}:
                try:
                    await self.service.set_supernova_auto(data.endswith(":on"))
                    await self.show_supernova(chat_id, "✅ Настройка автообновления сохранена.")
                except ValueError as exc:
                    await self.show_supernova(chat_id, str(exc))
            elif data == "supernova:request":
                try:
                    started = self.service.start_supernova_export(force=True)
                    await self.show_supernova(chat_id, "⏳ Запрашиваю Excel. Вернись в этот раздел, чтобы увидеть результат." if started else "⏳ Запрос уже выполняется.")
                except ValueError as exc:
                    await self.show_supernova(chat_id, str(exc))
            elif data in {"supernova:account:1", "supernova:account:2"}:
                slot = int(data.rsplit(":", 1)[1])
                try:
                    await self.service.configure_supernova(slot)
                    await self.service.poll_supernova()
                except Exception as exc:
                    await self.show_supernova(chat_id, "Не удалось подключить: " + str(exc)[:300])
                    return
                await self.show_supernova(chat_id, f"✅ Выбран аккаунт {slot}.")
            return
        if not data.startswith("channels:"):
            return await super().handle_callback(callback)
        user_id = callback.get("from", {}).get("id")
        chat = (callback.get("message") or {}).get("chat") or {}
        if not self.allowed(user_id, chat.get("type", "private")):
            await self.answer_callback(callback["id"], "Нет доступа", True)
            return
        await self.answer_callback(callback["id"])
        chat_id = chat.get("id")
        if data.startswith("channels:show:"):
            self.channel_waiting.discard(user_id)
            self.channel_choices.pop(user_id, None)
            self.channel_slots.pop(user_id, None)
            try:
                page = int(data.rsplit(":", 1)[1])
            except ValueError:
                page = 0
            await self.show_channels(chat_id, page)
        elif data == "channels:add":
            self.channel_waiting.discard(user_id)
            self.channel_choices.pop(user_id, None)
            self.channel_slots.pop(user_id, None)
            self.first_message_waiting.discard(user_id)
            getattr(self, "order_selected", {}).pop(user_id, None)
            await self.send(chat_id, "С какого Telegram-аккаунта читать канал?", {"inline_keyboard": [
                [{"text": "👤 Аккаунт 1", "callback_data": "channels:account:1"},
                 {"text": "👤 Аккаунт 2", "callback_data": "channels:account:2"}],
                [{"text": "⬅️ Назад", "callback_data": "channels:show:0"}],
            ]})
        elif data.startswith("channels:account:"):
            try:
                slot = int(data.rsplit(":", 1)[1])
            except ValueError:
                slot = 0
            if slot not in (1, 2):
                await self.send(chat_id, "Выбери аккаунт 1 или 2.")
                return
            self.channel_waiting.add(user_id)
            self.channel_slots[user_id] = slot
            self.channel_choices.pop(user_id, None)
            await self.send(chat_id,
                            f"Аккаунт {slot}. Выбери канал из списка или перешли сообщение "
                            "из канала / отправь @username или публичную ссылку.\n"
                            "Для отмены отправь /cancel.", {"inline_keyboard": [
                                [{"text": f"📋 Каналы аккаунта {slot}", "callback_data": f"channels:list:{slot}:0"}],
                                [{"text": "⬅️ Выбрать другой аккаунт", "callback_data": "channels:add"}],
                            ]})
        elif data.startswith("channels:list:"):
            try:
                _, _, slot_text, page_text = data.split(":")
                slot, page = int(slot_text), int(page_text)
            except ValueError:
                await self.send(chat_id, "Выбери аккаунт заново через «Добавить канал».")
                return
            if (user_id not in self.channel_waiting or
                    self.channel_slots.get(user_id) != slot):
                await self.send(chat_id, "Сначала выбери Telegram-аккаунт через «Добавить канал».")
                return
            try:
                await self.show_channel_choices(chat_id, user_id, slot, page)
            except Exception as exc:
                await self.send(chat_id, f"Не удалось загрузить каналы аккаунта {slot}: " + str(exc)[:250])
        elif data.startswith("channels:pick:"):
            try:
                _, _, slot_text, peer_text = data.split(":")
                slot, peer_id = int(slot_text), int(peer_text)
            except ValueError:
                await self.send(chat_id, "Некорректный канал. Открой список заново.")
                return
            selected = self.channel_choices.get(user_id)
            if (user_id not in self.channel_waiting or
                    self.channel_slots.get(user_id) != slot or
                    selected is None or selected[0] != slot or
                    not any(item["id"] == peer_id for item in selected[2])):
                await self.send(chat_id, "Список устарел. Нажми «Добавить канал» ещё раз.")
                return
            try:
                title = await self.service.add_supplier_channel(peer_id, slot=slot)
            except Exception as exc:
                await self.send(chat_id, "Не удалось подключить выбранный канал: " + str(exc)[:300],
                                {"inline_keyboard": [[{"text": "📋 Повторить выбор",
                                                      "callback_data": f"channels:list:{slot}:0"}]]})
                return
            self.channel_waiting.discard(user_id)
            self.channel_choices.pop(user_id, None)
            self.channel_slots.pop(user_id, None)
            await self.show_channels(chat_id, notice=f"✅ Канал «{title}» подключён к аккаунту {slot}.")
        elif data == "channels:check":
            await self.request_sync(chat_id)
            await self.send(chat_id, "После проверки открой «Каналы поставщиков», чтобы увидеть результат.")
        elif data.startswith(("channels:details:", "channels:toggle:", "channels:captions:")):
            token = data.rsplit(":", 1)[1]
            item = next((candidate for candidate in self.service.state.get("supplier_channels", [])
                         if channel_token(candidate["peer"], candidate.get("slot", 1)) == token), None)
            if item is None:
                await self.show_channels(chat_id, notice="Канал не найден.")
                return
            if data.startswith("channels:details:"):
                await self.show_channel_details(chat_id, item)
            elif data.startswith("channels:captions:"):
                enabled = not item.get("read_captions", False)
                try:
                    await self.service.set_supplier_channel_captions(
                        item["peer"], slot=item.get("slot", 1), enabled=enabled)
                except Exception as exc:
                    await self.send(chat_id, "Не удалось изменить чтение подписей: " + str(exc)[:300])
                    return
                if enabled:
                    await self.send(chat_id,
                        "✅ Теперь PriceB читает ТОЛЬКО ТЕКСТ подписей к фото/видео в этом канале. "
                        "Сами медиа не скачиваются и не распознаются. Обновляю прайс.")
                else:
                    await self.send(chat_id,
                        "⛔ Подписи к фото и видео больше не читаются. Обновляю прайс.")
                await self.request_sync(chat_id)
            else:
                visible = not item.get("visible", True)
                try:
                    await self.service.set_supplier_channel_visibility(item["peer"],
                                                                     slot=item.get("slot", 1),
                                                                     visible=visible)
                except Exception as exc:
                    # The setting can be persisted even if re-publishing failed.
                    await self.show_channels(chat_id, notice="⚠️ Проверь публикацию: " + str(exc)[:220])
                    return
                await self.show_channels(chat_id,
                    notice=(f"✅ Показ товаров «{item['title']}» включён." if visible
                            else f"⛔ Товары «{item['title']}» скрыты из прайса. Чтение продолжится."))
        elif data.startswith("channels:remove:"):
            token = data.rsplit(":", 1)[1]
            channel = next((item for item in self.service.state.get("supplier_channels", [])
                            if channel_token(item["peer"], item.get("slot", 1)) == token), None)
            if channel is None:
                await self.show_channels(chat_id, notice="Этот канал уже отключён.")
                return
            await self.service.remove_supplier_channel(channel["peer"], slot=channel.get("slot", 1))
            await self.show_channels(chat_id, notice=f"✅ Канал «{channel['title']}» отключён. Изменение применится при следующем обновлении прайса.")
        elif data == "channels:back":
            self.channel_waiting.discard(user_id)
            self.channel_choices.pop(user_id, None)
            self.channel_slots.pop(user_id, None)
            await self.send(chat_id, "🛠 Управление прайсом", self.menu())

    async def handle_message(self, message):
        user_id = message.get("from", {}).get("id")
        if user_id not in self.channel_waiting or user_id in self.login_flows:
            return await super().handle_message(message)
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if not self.allowed(user_id, chat.get("type", "private")):
            return await self.explain_access(chat_id, user_id, chat.get("type"))
        text = (message.get("text") or "").strip()
        if text.startswith("/"):
            self.channel_waiting.discard(user_id)
            self.channel_choices.pop(user_id, None)
            self.channel_slots.pop(user_id, None)
            if text.split()[0].lower().split("@")[0] == "/cancel":
                await self.send(chat_id, "Добавление канала отменено.", self.menu())
                return
            return await super().handle_message(message)
        has_forward = bool(message.get("forward_origin") or message.get("forward_from_chat"))
        forwarded = self._forwarded_chat(message) if has_forward else None
        if has_forward:
            if not forwarded or forwarded.get("type") != "channel":
                await self.send(chat_id, "Telegram не передал данные канала. Отправь его @username или публичную ссылку.")
                return
            value = forwarded["id"]
        else:
            match = re.fullmatch(r"(?:https?://)?(?:t\.me/)?@?([A-Za-z][A-Za-z0-9_]{3,31})/?", text, re.I)
            if not match:
                await self.send(chat_id, "Перешли сообщение из канала или отправь @username либо ссылку на канал. Для отмены — /cancel.")
                return
            value = "@" + match.group(1)
        try:
            slot = self.channel_slots.get(user_id, 1)
            title = await self.service.add_supplier_channel(value, slot=slot)
        except Exception as exc:
            slot = self.channel_slots.get(user_id, 1)
            await self.send(chat_id,
                            f"Канал не найден в Telegram-аккаунте {slot}. Проверь, что он открыт именно там, "
                            "или выбери его из списка.\n\n" + str(exc)[:350],
                            {"inline_keyboard": [[{"text": "📋 Выбрать доступный канал",
                                                  "callback_data": f"channels:list:{slot}:0"}]]})
            return
        self.channel_waiting.discard(user_id)
        self.channel_choices.pop(user_id, None)
        slot = self.channel_slots.pop(user_id, 1)
        await self.show_channels(chat_id, notice=f"✅ Канал «{title}» подключён к аккаунту {slot}.")
