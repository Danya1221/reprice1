"""Catalog and block-order controls, built on the existing private operator UI."""
import asyncio
import hashlib

from control_first import FirstMessageController
from prices import ordered_blocks


def block_id(block):
    return hashlib.sha256(block.encode()).hexdigest()[:16]


class CatalogController(FirstMessageController):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.order_drafts = {}

    def menu(self):
        rows = super().menu()["inline_keyboard"]
        rows.insert(4, [{"text": "↕️ Порядок блоков", "callback_data": "order:show:0"},
                        {"text": "🗂 Каталог", "callback_data": "catalog:refresh"}])
        rows.insert(5, [{"text": "📦 Показывать все позиции", "callback_data": "catalog:all"}])
        return {"inline_keyboard": rows}

    def known_blocks(self):
        return {item.block for item in self.service.cached_items(include_closed=True)}

    async def show_order(self, chat_id, user_id, page=0):
        known = self.known_blocks()
        preferred = self.order_drafts.get(user_id, self.service.options().get("block_order", []))
        order = ordered_blocks(known, preferred)
        self.order_drafts[user_id] = order
        page = max(0, min(page, max(0, (len(order) - 1) // 8)))
        rows = []
        for index, block in enumerate(order[page * 8:page * 8 + 8], start=page * 8):
            ident = block_id(block)
            row = [{"text": f"{index + 1}. {block}", "callback_data": f"order:show:{page}"}]
            if index:
                row.append({"text": "↑", "callback_data": f"order:up:{ident}:{page}"})
            if index + 1 < len(order):
                row.append({"text": "↓", "callback_data": f"order:down:{ident}:{page}"})
            rows.append(row)
        navigation = []
        if page:
            navigation.append({"text": "←", "callback_data": f"order:show:{page - 1}"})
        if (page + 1) * 8 < len(order):
            navigation.append({"text": "→", "callback_data": f"order:show:{page + 1}"})
        if navigation:
            rows.append(navigation)
        if order:
            rows.append([{"text": "✅ Применить порядок", "callback_data": "order:apply"},
                         {"text": "По умолчанию", "callback_data": "order:reset"}])
        await self.send(chat_id, "Подними или опусти блок стрелками, затем нажми «Применить порядок»."
                        if order else "Сначала запроси прайс — здесь появятся все его блоки.",
                        {"inline_keyboard": rows})

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
        chat = (callback.get("message") or {}).get("chat") or {}
        chat_id = chat.get("id")
        if not self.allowed(user_id, chat.get("type")):
            await self.answer_callback(callback["id"], "Нет доступа", True)
            return
        await self.answer_callback(callback["id"])
        try:
            if data.startswith("order:show:"):
                await self.show_order(chat_id, user_id, int(data.rsplit(":", 1)[1]))
            elif data.startswith(("order:up:", "order:down:")):
                _, direction, ident, page = data.split(":")
                order = ordered_blocks(self.known_blocks(), self.order_drafts.get(user_id, self.service.options().get("block_order", [])))
                block = next((block for block in order if block_id(block) == ident), None)
                if block:
                    index = order.index(block)
                    target = index + (-1 if direction == "up" else 1)
                    if 0 <= target < len(order):
                        order[index], order[target] = order[target], order[index]
                    self.order_drafts[user_id] = order
                await self.show_order(chat_id, user_id, int(page))
            elif data == "order:reset":
                self.order_drafts[user_id] = []
                await self.show_order(chat_id, user_id)
            elif data == "order:apply":
                order = self.order_drafts.get(user_id)
                if order is None:
                    await self.show_order(chat_id, user_id)
                    return
                self.service.set_option("block_order", ordered_blocks(self.known_blocks(), order))
                await self.refresh_catalog(chat_id)
            elif data == "catalog:refresh":
                await self.refresh_catalog(chat_id)
            elif data == "catalog:all":
                options = self.service.options()
                options.update({"sim_filter": "all", "disabled_blocks": [], "include_blocks": [],
                                "exclude_blocks": [], "include_items": [], "exclude_items": [], "allow_accessories": True})
                self.service.state.set("options", options)
                await self.refresh_catalog(chat_id)
        except (ValueError, IndexError) as exc:
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
