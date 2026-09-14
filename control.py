"""Optional private control bot; the supplier is read by a separate user session."""
import asyncio
import logging
from contextlib import suppress

from telethon import Button, events

from config import decimal_value
from prices import marked_price

log = logging.getLogger(__name__)


class Controller:
    def __init__(self, client, service, admins):
        self.client = client
        self.service = service
        self.admins = set(admins)
        self.task = None
        self.block_choices = {}
        client.add_event_handler(self.message, events.NewMessage(incoming=True))
        client.add_event_handler(self.callback, events.CallbackQuery())

    def allowed(self, event):
        return event.is_private and event.sender_id in self.admins

    def menu(self):
        return [
            [Button.inline("▶️ Запустить", b"resume"), Button.inline("⏸ Остановить", b"pause")],
            [Button.inline("🔄 Запросить сейчас", b"sync"), Button.inline("📊 Статус", b"status")],
            [Button.inline("⏱ Интервал", b"interval"), Button.inline("💰 Наценка", b"markup")],
            [Button.inline("📦 Блоки", b"blocks:0"), Button.inline("📱 SIM / eSIM", b"sim")],
        ]

    async def respond(self, event, text, buttons=None):
        await event.respond(text, parse_mode=None, buttons=buttons)

    async def background_sync(self, event):
        try:
            result = await self.service.sync(force=True)
            await self.respond(event, result, self.menu())
        except Exception:
            log.exception("Ошибка ручного обновления")
            await self.respond(event, "Ошибка обновления. Проверь статус и журнал Railway", self.menu())

    async def request_sync(self, event):
        if self.service.busy or (self.task and not self.task.done()):
            await self.respond(event, "Обновление уже выполняется")
            return
        await self.respond(event, "Запрашиваю прайс…")
        self.task = asyncio.create_task(self.background_sync(event))

    async def show_blocks(self, event, page=0):
        blocks = sorted({i.block for i in self.service.cached_items(include_closed=True)})
        self.block_choices = {str(n): block for n, block in enumerate(blocks)}
        disabled = set(self.service.options().get("disabled_blocks", []))
        page = max(0, min(page, max(0, (len(blocks)-1)//8)))
        buttons = [
            [Button.inline(("☑️ " if b not in disabled else "⬜ ") + b, f"toggle:{n}:{page}".encode())]
            for n, b in enumerate(blocks) if page * 8 <= n < (page + 1) * 8
        ]
        nav = []
        if page:
            nav.append(Button.inline("←", f"blocks:{page-1}".encode()))
        if (page+1)*8 < len(blocks):
            nav.append(Button.inline("→", f"blocks:{page+1}".encode()))
        if nav:
            buttons.append(nav)
        await self.respond(event, "Выбери блоки для своего прайса" if blocks else "Сначала запроси прайс", buttons)

    async def callback(self, event):
        if not self.allowed(event):
            await event.answer("Нет доступа", alert=True)
            return
        # Answer first: do not leave Telegram's loading spinner running during sync.
        await event.answer()
        data = event.data.decode()
        try:
            if data == "resume":
                self.service.set_option("enabled", True)
                self.service.last_attempt = 0
                await self.respond(event, "Синхронизация включена", self.menu())
            elif data == "pause":
                await self.service.pause()
                await self.respond(event, "Синхронизация остановлена", self.menu())
            elif data == "sync":
                await self.request_sync(event)
            elif data == "status":
                await self.respond(event, self.service.status(), self.menu())
            elif data == "markup":
                await self.respond(event, "Отправь /markup 500 — фиксированная наценка.\n/percent 5 — наценка 5%.\nМожно применять вместе.")
            elif data == "interval":
                await self.respond(event, "Интервал обновления:", [
                    [Button.inline(f"{m} мин.", f"every:{m}".encode()) for m in (5, 15, 30)],
                    [Button.inline("1 час", b"every:60"), Button.inline("2 часа", b"every:120")],
                ])
            elif data.startswith("every:"):
                minutes = int(data.split(":")[1])
                if minutes not in {5, 15, 30, 60, 120}:
                    raise ValueError("Недопустимый интервал")
                self.service.set_option("poll_seconds", minutes * 60)
                await self.respond(event, f"Интервал: {minutes} мин.", self.menu())
            elif data == "sim":
                await self.respond(event, "Фильтр iPhone; страна не используется для угадывания SIM:", [
                    [Button.inline("Все", b"sim:all"), Button.inline("SIM", b"sim:sim")],
                    [Button.inline("eSIM", b"sim:esim"), Button.inline("2 SIM", b"sim:dual")],
                    [Button.inline("Не указан", b"sim:unknown")],
                ])
            elif data.startswith("sim:"):
                choice = data.split(":")[1]
                if choice not in {"all", "sim", "esim", "dual", "unknown"}:
                    raise ValueError("Неизвестный фильтр")
                self.service.set_option("sim_filter", choice)
                await self.service.refresh_format()
                await self.respond(event, "Фильтр применён: " + choice, self.menu())
            elif data.startswith("blocks:"):
                await self.show_blocks(event, int(data.split(":")[1]))
            elif data.startswith("toggle:"):
                _, number, page = data.split(":")
                block = self.block_choices.get(number)
                if not block:
                    await self.show_blocks(event)
                    return
                disabled = set(self.service.options().get("disabled_blocks", []))
                disabled.symmetric_difference_update({block})
                self.service.set_option("disabled_blocks", sorted(disabled))
                await self.service.refresh_format()
                await self.show_blocks(event, int(page))
        except Exception as exc:
            await self.respond(event, "Не удалось применить: " + str(exc))

    async def message(self, event):
        if not self.allowed(event):
            return
        words = event.raw_text.strip().split(maxsplit=1)
        if not words:
            return
        command = words[0].split("@")[0].lower()
        value = words[1] if len(words) > 1 else ""
        try:
            if command in {"/start", "/help"}:
                await self.respond(event,
                    "Управление прайсом\n/markup 500 — наценка\n/percent 5 — процент\n"
                    "/interval 15 — интервал в минутах\n/order iPhone 17, Samsung, Dyson — порядок блоков\n"
                    "/rejected — строки, которые нужно проверить\n/status /sync /stop /resume",
                    self.menu())
            elif command == "/status":
                await self.respond(event, self.service.status(), self.menu())
            elif command == "/sync":
                await self.request_sync(event)
            elif command == "/stop":
                await self.service.pause()
                await self.respond(event, "Синхронизация остановлена", self.menu())
            elif command == "/resume":
                self.service.set_option("enabled", True)
                self.service.last_attempt = 0
                await self.respond(event, "Синхронизация включена", self.menu())
            elif command in {"/markup", "/percent"}:
                number = decimal_value(value)
                if command == "/percent" and number <= -100:
                    raise ValueError("Процент должен быть больше -100")
                option = "markup" if command == "/markup" else "markup_percent"
                proposed = {**self.service.options(), option: str(number)}
                for item in self.service.cached_items(include_closed=True):
                    marked_price(item, self.service.settings, proposed)
                self.service.set_option(option, str(number))
                await self.service.refresh_format()
                await self.respond(event, "Наценка применена", self.menu())
            elif command == "/interval":
                minutes = int(value)
                if not 1 <= minutes <= 1440:
                    raise ValueError("Интервал: от 1 до 1440 минут")
                self.service.set_option("poll_seconds", minutes * 60)
                await self.respond(event, f"Интервал: {minutes} мин.", self.menu())
            elif command == "/order":
                order = [b.strip() for b in value.split(",") if b.strip()]
                self.service.set_option("block_order", order)
                await self.respond(event, "Порядок сохранён. Для уже опубликованных сообщений применяется при пересоздании блоков.")
            elif command == "/rejected":
                lines = []
                for source in self.service.state.get("sources", {}).values():
                    lines.extend(source.get("rejected", []))
                await self.respond(event, "\n".join(lines)[:3500] or "Нераспознанных строк нет")
        except (ValueError, ArithmeticError) as exc:
            await self.respond(event, str(exc))
        except Exception:
            log.exception("Ошибка управления")
            await self.respond(event, "Настройка сохранена, но обновление не завершено. Проверь /status")

    async def close(self):
        if self.task:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
