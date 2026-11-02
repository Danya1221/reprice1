"""Manage supplier channels separately from the publication destination."""
import hashlib
import re

from control_first import FirstMessageController


def channel_token(peer):
    return hashlib.sha256(str(peer).encode()).hexdigest()[:16]


class ChannelController(FirstMessageController):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.channel_waiting = set()

    def menu(self):
        menu = super().menu()
        menu["inline_keyboard"].append([{"text": "📥 Каналы поставщиков", "callback_data": "channels:show:0"}])
        return menu

    async def show_channels(self, chat_id, page=0, notice=""):
        channels = self.service.state.get("supplier_channels", [])
        page = max(0, min(page, max(0, (len(channels) - 1) // 8)))
        rows = []
        for item in channels[page * 8:page * 8 + 8]:
            rows.append([{"text": "🗑 " + item["title"][:55],
                          "callback_data": "channels:remove:" + channel_token(item["peer"])}])
        navigation = []
        if page:
            navigation.append({"text": "⬅️", "callback_data": f"channels:show:{page - 1}"})
        if (page + 1) * 8 < len(channels):
            navigation.append({"text": "➡️", "callback_data": f"channels:show:{page + 1}"})
        if navigation:
            rows.append(navigation)
        rows.append([{"text": "➕ Добавить канал", "callback_data": "channels:add"}])
        rows.append([{"text": "⬅️ Главное меню", "callback_data": "channels:back"}])
        text = ("📥 Каналы поставщиков\n\n"
                + (f"Подключено: {len(channels)}. Нажми 🗑, чтобы отключить канал." if channels else "Пока нет добавленных каналов.")
                + "\n\nКаналы читает Telegram-аккаунт 1. Бот читает доступную историю, "
                  "поэтому старые объявления с ценами тоже могут попасть в прайс.")
        await self.send(chat_id, (notice + "\n\n" if notice else "") + text, {"inline_keyboard": rows})

    async def handle_callback(self, callback):
        data = callback.get("data") or ""
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
            try:
                page = int(data.rsplit(":", 1)[1])
            except ValueError:
                page = 0
            await self.show_channels(chat_id, page)
        elif data == "channels:add":
            self.channel_waiting.add(user_id)
            self.first_message_waiting.discard(user_id)
            getattr(self, "order_selected", {}).pop(user_id, None)
            await self.send(chat_id, "Перешли сюда сообщение из канала с прайсом или отправь ссылку на канал / @username.\n\n"
                            "Аккаунт 1 должен иметь доступ к каналу. Для отмены отправь /cancel.")
        elif data.startswith("channels:remove:"):
            token = data.rsplit(":", 1)[1]
            channel = next((item for item in self.service.state.get("supplier_channels", [])
                            if channel_token(item["peer"]) == token), None)
            if channel is None:
                await self.show_channels(chat_id, notice="Этот канал уже отключён.")
                return
            await self.service.remove_supplier_channel(channel["peer"])
            await self.show_channels(chat_id, notice=f"✅ Канал «{channel['title']}» отключён. Изменение применится при следующем обновлении прайса.")
        elif data == "channels:back":
            self.channel_waiting.discard(user_id)
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
            title = await self.service.add_supplier_channel(value)
        except Exception as exc:
            await self.send(chat_id, "Не удалось подключить канал. Проверь, что аккаунт 1 имеет к нему доступ.\n\n" + str(exc)[:700])
            return
        self.channel_waiting.discard(user_id)
        await self.show_channels(chat_id, notice=f"✅ Канал «{title}» добавлен. Он будет прочитан при ближайшем обновлении.")
