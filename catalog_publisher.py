"""Ordered price posts and a persistent two-column navigation message."""
import asyncio
import html
import json
import re

from bot_publisher import digest
from first_message_publisher import PinnedBotAPIPublisher


CATALOG_TEXT = "🗂 КАТАЛОГ — выбери раздел\nНажми на нужный раздел — перейдёшь к прайсу.\nСкопируй позицию вместе с ценой и пришли её менеджеру."


def message_link(chat_id, message_id, username=""):
    if username:
        return f"https://t.me/{username.lstrip('@')}/{message_id}"
    marked = str(chat_id)
    if marked.startswith("-100"):
        return f"https://t.me/c/{marked[4:]}/{message_id}"
    return None


def page_title(content):
    match = re.match(r"<b>\s*(?:—\s*)?(.*?)(?:\s*—)?\s*</b>", content)
    return html.unescape(match[1]) if match else "Прайс"


class CatalogPublisher(PinnedBotAPIPublisher):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.layout_lock = asyncio.Lock()

    def arrange_manifest(self, pages, manifest):
        # Telegram cannot move posts. Reuse their chronological slots, then
        # update the catalog from the final key -> message ID mapping.
        slots = sorted(manifest.values(), key=lambda entry: int(entry["id"]))
        arranged = {key: entry for key, entry in zip(pages, slots)}
        for entry in slots[len(pages):]:
            arranged["obsolete:" + str(entry["id"])] = entry
        return arranged

    def catalog_records(self):
        stored = self.state.get("catalog", {}) or {}
        return stored.get("messages", []) if stored.get("binding") == self.binding() else []

    def save_catalog(self, records):
        self.state.set("catalog", {"binding": self.binding(), "messages": records})

    async def _catalog_entry(self, records, index, text, keyboard):
        record = records[index] if index < len(records) else {}
        payload = {"chat_id": self.target, "text": text,
                   "reply_markup": {"inline_keyboard": keyboard}, "disable_web_page_preview": True}
        content_hash = digest(json.dumps([text, keyboard], ensure_ascii=False, sort_keys=True))
        changed = 0
        if record.get("id") and record.get("hash") != content_hash:
            try:
                await self.api("editMessageText", message_id=record["id"], **payload)
                changed = 1
            except RuntimeError as exc:
                error = str(exc).lower()
                if "message to edit not found" in error:
                    record = {}
                elif "message is not modified" not in error:
                    raise
        if not record.get("id"):
            message = await self.api("sendMessage", **payload)
            record = {"id": int(message["message_id"])}
            changed = 1
        record.update({"hash": content_hash, "text": text})
        if index == len(records):
            records.append(record)
        else:
            records[index] = record
        # Save the sent ID before pinning so denied pin permission never duplicates it.
        self.save_catalog(records)
        if index == 0 and not record.get("pinned"):
            try:
                await self.api("pinChatMessage", chat_id=self.target, message_id=record["id"], disable_notification=True)
                record["pinned"] = True
                record.pop("pin_error", None)
            except RuntimeError as exc:
                record["pin_error"] = str(exc)
            self.save_catalog(records)
        if changed:
            await asyncio.sleep(max(0, self.settings.send_delay))
        return changed

    async def _prepare_catalog(self, count):
        records = self.catalog_records()
        # When enabling navigation on an existing price, convert its first post
        # into the catalog; subsequent posts remain reusable price slots.
        if not records:
            stored = self.state.get("published", {}) or {}
            manifest = stored.get("messages", {}) if stored.get("binding") == self.binding() else {}
            if manifest:
                key = min(manifest, key=lambda k: int(manifest[k]["id"]))
                records = [{"id": int(manifest[key]["id"])}]
                del manifest[key]
                self.state.update({"catalog": {"binding": self.binding(), "messages": records},
                                   "published": {"binding": self.binding(), "messages": manifest}})
        for index in range(count):
            if index >= len(records) or not records[index].get("hash"):
                await self._catalog_entry(records, index, CATALOG_TEXT + "\n\nОбновляю разделы…", [])
        return records

    async def _update_catalog(self, pages, records):
        manifest = self.state.get("published", {}).get("messages", {})
        buttons = []
        seen = set()
        for key, content in pages.items():
            block_key = key.rsplit(":", 1)[0]
            if block_key in seen or key not in manifest:
                continue
            seen.add(block_key)
            link = message_link(self.target, manifest[key]["id"])
            if link:
                buttons.append({"text": page_title(content), "url": link})
        batches = [buttons[start:start + 80] for start in range(0, len(buttons), 80)] or [[]]
        changes = 0
        for index, batch in enumerate(batches):
            rows = [batch[start:start + 2] for start in range(0, len(batch), 2)]
            text = CATALOG_TEXT + (f"\nСтраница {index + 1} из {len(batches)}" if len(batches) > 1 else "")
            if not pages:
                text += "\n\nПока нет выбранных позиций."
            elif not batch:
                text += "\n\nСсылки на посты доступны в каналах и супергруппах."
            if index + 1 < len(batches):
                link = message_link(self.target, records[index + 1]["id"])
                if link:
                    rows.append([{"text": "Следующие разделы →", "url": link}])
            changes += await self._catalog_entry(records, index, text, rows)
        for index in range(len(records) - 1, len(batches) - 1, -1):
            try:
                await self._delete(records[index]["id"])
            except RuntimeError as exc:
                if "message to delete not found" not in str(exc).lower():
                    raise
            records.pop(index)
            self.save_catalog(records)
        return changes

    async def publish(self, pages):
        async with self.layout_lock:
            await self.ensure_target()
            await self._ensure_first_message()
            count = max(1, (len({key.rsplit(":", 1)[0] for key in pages}) + 79) // 80)
            records = await self._prepare_catalog(count)
            changes = await super().publish(pages)
            changes += await self._update_catalog(pages, records)
            return changes

    async def set_first_message(self, text):
        async with self.layout_lock:
            await self.ensure_target()
            previous = self.state.get("first_message", {}) or {}
            if not previous.get("id") or previous.get("binding") != self.binding():
                # Establish a newly requested intro ahead of the rebuilt catalog.
                records = self.catalog_records()
                for record in list(records):
                    try:
                        await self._delete(record["id"])
                    except RuntimeError as exc:
                        if "message to delete not found" not in str(exc).lower():
                            raise
                    records.remove(record)
                    self.save_catalog(records)
            return await super().set_first_message(text)
