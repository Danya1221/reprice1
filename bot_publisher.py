"""Publish managed price messages through Telegram Bot API.

The supplier account still uses Telethon to read supplier bots/channels. Publishing is
kept separate on purpose: Bot API chat IDs do not require MTProto access_hash values,
so Railway restarts cannot break TARGET_CHANNEL entity resolution.
"""
import asyncio
import hashlib
import html
import re
from contextlib import suppress

import aiohttp
from telethon.extensions import html as telegram_html


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def plain(text):
    return telegram_html.parse(text)[0]


class BotAPIPublisher:
    def __init__(self, token, target, state, settings):
        self.token = (token or "").strip()
        self.base = f"https://api.telegram.org/bot{self.token}"
        self.configured_target = target
        self.target = None
        self.state = state
        self.settings = settings
        self.http = None
        self.lock = asyncio.Lock()
        self.bot_id = None
        self.bot_username = ""

    async def api(self, method, **payload):
        if not self.token:
            raise RuntimeError("BOT_TOKEN не задан: без него нельзя публиковать прайс в канал")
        if self.http is None or self.http.closed:
            self.http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=40))
        async with self.http.post(f"{self.base}/{method}", json=payload) as response:
            data = await response.json(content_type=None)
        if not data.get("ok"):
            raise RuntimeError(f"Bot API {method}: {data.get('description', data)}")
        return data.get("result")

    def _target_candidates(self):
        value = self.configured_target
        if isinstance(value, str):
            text = value.strip()
            match = re.fullmatch(r"(?:https?://)?t\.me/([A-Za-z0-9_]+)/?", text, re.I)
            if match:
                return ["@" + match.group(1)]
            if text.startswith("@"):
                return [text]
            if re.fullmatch(r"-?\d+", text):
                value = int(text)
            else:
                return [text]
        if isinstance(value, int):
            if value > 0:
                # Telegram exposes channel/supergroup IDs through Bot API as
                # -100<raw_channel_id>. Users often paste only the raw ID.
                return [int(f"-100{value}"), value]
            return [value]
        return [value]

    async def ensure_target(self):
        if self.target is not None:
            return self.target

        me = await self.api("getMe")
        self.bot_id = int(me["id"])
        self.bot_username = me.get("username") or ""

        cached = self.state.get("botapi_target", {})
        candidates = []
        if cached.get("configured") == str(self.configured_target) and cached.get("chat_id"):
            candidates.append(cached["chat_id"])
        for candidate in self._target_candidates():
            if candidate not in candidates:
                candidates.append(candidate)

        last_error = None
        chat = None
        for candidate in candidates:
            try:
                chat = await self.api("getChat", chat_id=candidate)
                break
            except Exception as exc:
                last_error = exc

        if chat is None:
            bot = f"@{self.bot_username}" if self.bot_username else "управляющего бота"
            raise RuntimeError(
                f"TARGET_CHANNEL {self.configured_target!r} не найден через Bot API. "
                f"Добавь {bot} в целевой канал администратором и оставь TARGET_CHANNEL как "
                f"@username, -100... или raw ID. Последняя ошибка: {last_error}"
            )

        if chat.get("type") not in {"channel", "supergroup", "group"}:
            raise RuntimeError("TARGET_CHANNEL должен указывать на канал или группу, а не на пользователя")

        chat_id = int(chat["id"])
        member = await self.api("getChatMember", chat_id=chat_id, user_id=self.bot_id)
        status = member.get("status")
        can_post = status == "creator" or (
            status == "administrator" and member.get("can_post_messages", True) is not False
        )
        if not can_post:
            bot = f"@{self.bot_username}" if self.bot_username else "Управляющий бот"
            raise RuntimeError(
                f"{bot} должен быть администратором TARGET_CHANNEL с правом публикации сообщений"
            )

        self.target = chat_id
        self.state.set("botapi_target", {
            "configured": str(self.configured_target),
            "chat_id": chat_id,
            "type": chat.get("type"),
            "title": chat.get("title", ""),
        })
        return chat_id

    def binding(self):
        value = self.target if self.target is not None else self.configured_target
        return f"botapi:{value}"

    async def _send(self, content):
        target = await self.ensure_target()
        return await self.api(
            "sendMessage",
            chat_id=target,
            text=content,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )

    async def _edit(self, message_id, content):
        target = await self.ensure_target()
        return await self.api(
            "editMessageText",
            chat_id=target,
            message_id=int(message_id),
            text=content,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )

    async def _delete(self, message_id):
        target = await self.ensure_target()
        return await self.api("deleteMessage", chat_id=target, message_id=int(message_id))

    async def hide_existing(self):
        await self.ensure_target()
        stored = self.state.get("published", {})
        manifest = stored.get("messages", {}) if stored.get("binding") == self.binding() else {}
        if not manifest:
            return 0
        pages = {}
        for key, entry in manifest.items():
            old = entry.get("content") or html.escape(self.settings.header)
            sections = old.split("\n\n", 2)
            heading = "\n\n".join(sections[:2]) if len(sections) >= 2 else html.escape(self.settings.header)
            pages[key] = heading + "\n\nПродажи закрыты"
        return await self.publish(pages)

    async def publish(self, pages):
        async with self.lock:
            await self.ensure_target()
            binding = self.binding()
            stored = self.state.get("published", {})
            manifest = stored.get("messages", {}) if stored.get("binding") == binding else {}
            changes = 0

            for key, content in pages.items():
                entry = manifest.get(key)
                message_id = entry.get("id") if entry else None
                if message_id:
                    try:
                        await self._edit(message_id, content)
                        changes += 1
                    except RuntimeError as exc:
                        text = str(exc).lower()
                        if "message is not modified" in text:
                            pass
                        elif ("message to edit not found" in text
                              or "message can't be edited" in text
                              or "message cannot be edited" in text):
                            message_id = None
                        else:
                            raise

                if not message_id:
                    self.state.set("pending_publish", {
                        "binding": binding,
                        "key": key,
                        "text": content,
                    })
                    message = await self._send(content)
                    message_id = int(message["message_id"])
                    changes += 1

                manifest[key] = {
                    "id": int(message_id),
                    "hash": digest(content),
                    "content": content,
                }
                self.state.update({
                    "published": {"binding": binding, "messages": manifest},
                    "pending_publish": None,
                })
                if changes:
                    await asyncio.sleep(self.settings.send_delay)

            for key in list(manifest):
                if key in pages:
                    continue
                with suppress(RuntimeError):
                    await self._delete(manifest[key]["id"])
                del manifest[key]
                self.state.set("published", {"binding": binding, "messages": manifest})
                changes += 1
                await asyncio.sleep(self.settings.send_delay)

            return changes

    async def close(self):
        if self.http is not None and not self.http.closed:
            await self.http.close()
