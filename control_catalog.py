"""Catalog and block-order controls, built on the existing private operator UI."""
import asyncio
import hashlib

from control_first import FirstMessageController
from bot_publisher import is_missing_message_error
from prices import Item, ordered_blocks


def block_id(block):
    return hashlib.sha256(block.encode()).hexdigest()[:16]


class CatalogController(FirstMessageController):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.order_drafts = {}
        self.order_selected = {}

    def menu(self):
        rows = super().menu()["inline_keyboard"]
        rows.insert(4, [{"text": "↕️ Порядок блоков", "callback_data": "order:show:0"},
                        {"text": "🗂 Каталог", "callback_data": "catalog:refresh"}])
        rows.insert(5, [{"text": "📦 Показывать все позиции", "callback_data": "catalog:all"}])
        return {"inline_keyboard": rows}

    def known_blocks(self):
        blocks = {item.block for item in self.service.cached_items(include_closed=True)}
        # Read every raw cached supplier row as well. This avoids the order screen
        # looking like it contains only iPhones when merged/current items are partial.
        sources = self.service.state.get("sources", {}) or {}
        for source in sources.values():
            for raw in source.get("items", []) or []:
                try:
                    block = Item.from_dict(raw).block
                except (KeyError, TypeError, ValueError):
                    continue
                if block:
                    blocks.add(block)
        blocks.update(block for block in self.service.options().get("block_order", []) if block)
        return blocks

    async def _edit_or_send(self, chat_id, message_id, text, reply_markup=None):
        """Edit the control message in place; only /order without a callback sends one."""
        if not message_id:
            return await self.send(chat_id, text, reply_markup)
        payload = {
            "chat_id": chat_id,
            "message_id": int(message_id),
            "text": str(text),
            "disable_web_page_preview": True,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        try:
            return await self.api("editMessageText", **payload)
        except RuntimeError as exc:
            if "message is not modified" in str(exc).lower():
                return None
            if is_missing_message_error(exc):
                return await self.send(chat_id, text, reply_markup)
            raise

    def _draft_order(self, user_id):
        known = self.known_blocks()
        preferred = self.order_drafts.get(user_id, self.service.options().get("block_order", []))
        order = ordered_blocks(known, preferred)
        self.order_drafts[user_id] = order
        return order

    async def show_order(self, chat_id, user_id, page=0, message_id=None, notice=""):
        """Show the whole sortable block list in one compact message."""
        order = self._draft_order(user_id)
        selected = self.order_selected.get(user_id)
        if selected not in order:
            selected = None
            self.order_selected.pop(user_id, None)

        rows = []
        block_buttons = []
        for index, block in enumerate(order):
            ident = block_id(block)
            mark = "✅ " if block == selected else ""
            block_buttons.append({
                "text": f"{mark}{index + 1}. {block}",
                "callback_data": f"order:select:{ident}",
            })
        # Two columns keep 30-50 blocks readable without pagination.
        for start in range(0, len(block_buttons), 2):
            rows.append(block_buttons[start:start + 2])

        if selected:
            rows.extend([
                [{"text": "⏫ В начало", "callback_data": "order:move:first"},
                 {"text": "⬆️ Выше", "callback_data": "order:move:up"}],
                [{"text": "⬇️ Ниже", "callback_data": "order:move:down"},
                 {"text": "⏬ В конец", "callback_data": "order:move:last"}],
            ])
        if order:
            rows.append([{"text": "✅ Сохранить", "callback_data": "order:apply"},
                         {"text": "↩️ По умолчанию", "callback_data": "order:reset"}])
        rows.append([{"text": "⬅️ Назад", "callback_data": "order:back"}])

        if order:
            selected_text = f"\nВыбран: {selected}" if selected else "\nНажми на блок, который хочешь переместить."
            text = (
                f"↕️ Порядок блоков · всего {len(order)}\n"
                "Все блоки здесь сразу — без страниц."
                + selected_text
            )
        else:
            text = "Сначала запроси прайс — здесь появятся все его блоки."
        if notice:
            text = notice + "\n\n" + text
        await self._edit_or_send(chat_id, message_id, text, {"inline_keyboard": rows})

    def _move_selected(self, user_id, direction):
        order = self._draft_order(user_id)
        selected = self.order_selected.get(user_id)
        if selected not in order:
            return order
        index = order.index(selected)
        if direction == "first":
            target = 0
        elif direction == "last":
            target = len(order) - 1
        elif direction == "up":
            target = max(0, index - 1)
        elif direction == "down":
            target = min(len(order) - 1, index + 1)
        else:
            return order
        if target != index:
            order.pop(index)
            order.insert(target, selected)
            self.order_drafts[user_id] = order
        return order

    async def _refresh_order_result(self, chat_id, user_id, message_id):
        try:
            count, changes = await self.service.refresh_format()
            await self.show_order(
                chat_id, user_id, message_id=message_id,
                notice=f"✅ Сохранено. Прайс обновлён: {count} позиций.",
            )
        except Exception as exc:
            await self.show_order(
                chat_id, user_id, message_id=message_id,
                notice="⚠️ Порядок сохранён, но обновление не завершилось: " + str(exc),
            )

    async def refresh_order(self, chat_id, user_id, message_id):
        if self.task and not self.task.done():
            await self.show_order(
                chat_id, user_id, message_id=message_id,
                notice="⏳ Обновление уже идёт. Порядок сохранён.",
            )
            return
        await self.show_order(chat_id, user_id, message_id=message_id, notice="⏳ Сохранил порядок, обновляю прайс…")
        self.task = asyncio.create_task(self._refresh_order_result(chat_id, user_id, message_id))

    async def _refresh_result(self, chat_id):
        try:
            count, changes = await self.service.refresh_format()
            await self.send(chat_id, f"✅ Порядок и каталог обновлены. В прайсе {count} позиций.", self.menu())
        except Exception as exc:
            await self.send(chat_id, "Настройки сохранены; обновление не завершилось: " + str(exc), self.menu())

    async def refresh_catalog(self, chat_id):
        if self.task and not self.task.done():
            await self.send(chat_id, "Обновление уже идёт. Сохранённые настройки применятся при следующем обновлении.")
            return
        await self.send(chat_id, "Обновляю сообщения и кнопки каталога…")
        self.task = asyncio.create_task(self._refresh_result(chat_id))

    async def handle_callback(self, callback):
        data = callback.get("data") or ""
        if not data.startswith(("order:", "catalog:")):
            await super().handle_callback(callback)
            return
        user_id = callback.get("from", {}).get("id")
        message = callback.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        message_id = message.get("message_id")
        if not self.allowed(user_id, chat.get("type")):
            await self.answer_callback(callback["id"], "Нет доступа", True)
            return
        await self.answer_callback(callback["id"])
        try:
            if data.startswith("order:show:"):
                await self.show_order(chat_id, user_id, message_id=message_id)
            elif data.startswith("order:select:"):
                ident = data.rsplit(":", 1)[1]
                order = self._draft_order(user_id)
                block = next((block for block in order if block_id(block) == ident), None)
                if block:
                    self.order_selected[user_id] = block
                await self.show_order(chat_id, user_id, message_id=message_id)
            elif data.startswith("order:move:"):
                self._move_selected(user_id, data.rsplit(":", 1)[1])
                await self.show_order(chat_id, user_id, message_id=message_id)
            # Backward compatibility for old inline keyboards already sent to Telegram.
            elif data.startswith(("order:up:", "order:down:")):
                _, direction, ident, _page = data.split(":")
                order = self._draft_order(user_id)
                block = next((block for block in order if block_id(block) == ident), None)
                if block:
                    self.order_selected[user_id] = block
                    self._move_selected(user_id, "up" if direction == "up" else "down")
                await self.show_order(chat_id, user_id, message_id=message_id)
            elif data == "order:reset":
                order = ordered_blocks(self.known_blocks(), [])
                self.order_drafts[user_id] = order
                self.order_selected.pop(user_id, None)
                await self.show_order(chat_id, user_id, message_id=message_id, notice="↩️ Вернул порядок по умолчанию.")
            elif data == "order:apply":
                order = self.order_drafts.get(user_id)
                if order is None:
                    await self.show_order(chat_id, user_id, message_id=message_id)
                    return
                self.service.set_option("block_order", ordered_blocks(self.known_blocks(), order))
                await self.refresh_order(chat_id, user_id, message_id)
            elif data == "order:back":
                await self._edit_or_send(chat_id, message_id, "🛠 Управление прайсом", self.menu())
            elif data == "catalog:refresh":
                await self.refresh_catalog(chat_id)
            elif data == "catalog:all":
                options = self.service.options()
                options.update({"sim_filter": "all", "disabled_blocks": [], "include_blocks": [],
                                "exclude_blocks": [], "include_items": [], "exclude_items": [], "allow_accessories": True})
                self.service.state.set("options", options)
                await self.refresh_catalog(chat_id)
        except (ValueError, IndexError) as exc:
            if data.startswith("order:"):
                await self.show_order(chat_id, user_id, message_id=message_id, notice="Не удалось изменить порядок: " + str(exc))
            else:
                await self.send(chat_id, "Не удалось изменить порядок: " + str(exc))

    async def handle_message(self, message):
        text = (message.get("text") or "").strip()
        words = text.split(maxsplit=1)
        command, _, recipient = (words[0].lower() if words else "").partition("@")
        if command != "/order":
            await super().handle_message(message)
            return
        if recipient and self.username and recipient != self.username:
            return
        user_id = message.get("from", {}).get("id")
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if not self.allowed(user_id, chat.get("type")):
            await self.explain_access(chat_id, user_id, chat.get("type"))
            return
        if len(words) == 1:
            await self.show_order(chat_id, user_id)
            return
        names = {block.casefold(): block for block in self.known_blocks()}
        requested = [part.strip().casefold() for part in words[1].split(",") if part.strip()]
        unknown = [name for name in requested if name not in names]
        if unknown:
            await self.send(chat_id, "Нет таких блоков: " + ", ".join(unknown) + ". Открой /order, чтобы выбрать из списка.")
            return
        order = ordered_blocks(names.values(), [names[name] for name in requested])
        self.order_drafts[user_id] = order
        self.service.set_option("block_order", order)
        await self.refresh_catalog(chat_id)