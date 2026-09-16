from pathlib import Path


def replace_once(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected 1 match, got {count}")
    return text.replace(old, new, 1)


p = Path("bot_publisher.py")
s = p.read_text(encoding="utf-8")
old = '''        if not missing:
            return 0

        deleted = 0
        for entry in list(manifest.values()):
            message_id = entry.get("id")
            if not message_id:
                continue
            try:
                await self._delete(message_id)
                deleted += 1
                await asyncio.sleep(max(0, self.settings.send_delay))
            except RuntimeError as exc:
                if not is_missing_message_error(exc):
                    raise

        manifest.clear()
        self.state.set("published", {"binding": binding, "messages": manifest})
        return deleted
'''
new = '''        if not missing:
            return 0

        # Availability-safe rebuild: never erase the last complete price before a
        # replacement exists. Keep the old manifest as a staging snapshot, publish
        # a complete fresh set under new IDs, and delete the old live posts only
        # after every replacement page has been checkpointed successfully.
        snapshot = {key: dict(entry) for key, entry in manifest.items()}
        self.state.set("rebuild_old_manifest", {
            "binding": binding,
            "messages": snapshot,
        })
        manifest.clear()
        self.state.set("published", {"binding": binding, "messages": manifest})
        return 0
'''
s = replace_once(s, old, new, "safe missing-id rebuild")

old = '''            stored = self.state.get("published", {})
            manifest = stored.get("messages", {}) if stored.get("binding") == binding else {}
            manifest = self.arrange_manifest(pages, manifest)
            self.state.set("published", {"binding": binding, "messages": manifest})
            changes = await self._verify_or_rebuild_manifest(pages, manifest, binding)
'''
new = '''            stored = self.state.get("published", {})
            manifest = stored.get("messages", {}) if stored.get("binding") == binding else {}
            rebuild_state = self.state.get("rebuild_old_manifest", {}) or {}
            rebuilding = (
                rebuild_state.get("binding") == binding
                and bool(rebuild_state.get("messages"))
            )
            # During a staged rebuild, the current manifest is the partial/new set.
            # Do not remap those checkpointed new IDs onto different page keys.
            if not rebuilding:
                manifest = self.arrange_manifest(pages, manifest)
            self.state.set("published", {"binding": binding, "messages": manifest})
            changes = 0
            if not rebuilding:
                changes = await self._verify_or_rebuild_manifest(pages, manifest, binding)
'''
s = replace_once(s, old, new, "publish staged rebuild start")

old = '''            for key in list(manifest):
                if key in pages:
                    continue
                deleted = False
                try:
                    await self._delete(manifest[key]["id"])
                    deleted = True
                except RuntimeError as exc:
                    if not is_missing_message_error(exc):
                        raise
                del manifest[key]
                self.state.set("published", {"binding": binding, "messages": manifest})
                changes += 1
                if deleted:
                    await asyncio.sleep(max(0, self.settings.send_delay))

            return changes
'''
new = '''            for key in list(manifest):
                if key in pages:
                    continue
                deleted = False
                try:
                    await self._delete(manifest[key]["id"])
                    deleted = True
                except RuntimeError as exc:
                    if not is_missing_message_error(exc):
                        raise
                del manifest[key]
                self.state.set("published", {"binding": binding, "messages": manifest})
                changes += 1
                if deleted:
                    await asyncio.sleep(max(0, self.settings.send_delay))

            # Commit a staged rebuild only after the complete new page set exists.
            # If any send/edit above raises, execution never reaches this block and
            # the previous live price remains untouched for the next retry.
            rebuild_state = self.state.get("rebuild_old_manifest", {}) or {}
            if rebuild_state.get("binding") == binding and rebuild_state.get("messages"):
                new_ids = {int(entry["id"]) for entry in manifest.values() if entry.get("id")}
                for entry in rebuild_state.get("messages", {}).values():
                    old_id = entry.get("id")
                    if not old_id or int(old_id) in new_ids:
                        continue
                    deleted = False
                    try:
                        await self._delete(old_id)
                        deleted = True
                    except RuntimeError as exc:
                        if not is_missing_message_error(exc):
                            raise
                    changes += 1
                    if deleted:
                        await asyncio.sleep(max(0, self.settings.send_delay))
                self.state.set("rebuild_old_manifest", {})

            return changes
'''
s = replace_once(s, old, new, "staged rebuild commit")
p.write_text(s, encoding="utf-8")

p = Path("runtime.py")
s = p.read_text(encoding="utf-8")
old = '''        selected = select_items(catalog, self.settings, options)
        pages = render_blocks(selected, self.settings, options, closed=closed)
        changes = await self.publisher.publish(pages)
'''
new = '''        selected = select_items(catalog, self.settings, options)
        pages = render_blocks(selected, self.settings, options, closed=closed)
        if not closed:
            rendered_rows = sum(content.count("<code>") for content in pages.values())
            if rendered_rows != len(selected):
                raise RuntimeError(
                    f"Защита публикации: рендер потерял позиции ({rendered_rows} из {len(selected)}). "
                    "Текущий прайс оставлен без изменений."
                )
        changes = await self.publisher.publish(pages)
'''
s = replace_once(s, old, new, "render completeness guard")
p.write_text(s, encoding="utf-8")

p = Path("tests/test_bot_publisher.py")
t = p.read_text(encoding="utf-8")
marker = '\n\n\nif __name__ == "__main__":\n'
extra = '''
    async def test_failed_rebuild_keeps_old_live_posts_until_new_set_is_complete(self):
        settings = Settings(send_delay=0, admin_ids=(42,))
        group_id = -100777
        chats = {group_id: {"id": group_id, "type": "supergroup", "title": "Розница"}}
        publisher = FakeBotPublisher(group_id, self.state, settings, chats)
        pages = {"one:0": "Первый блок", "two:0": "Второй блок"}

        await publisher.publish(pages)
        old_manifest = self.state.get("published")["messages"]
        old_ids = [old_manifest[key]["id"] for key in pages]
        publisher.missing_ids.add(old_ids[0])
        original_api = publisher.api

        async def fail_replacement_send(method, **payload):
            if method == "sendMessage":
                publisher.calls.append((method, payload))
                raise RuntimeError("Bot API sendMessage: временный сбой соединения: ServerDisconnectedError")
            return await original_api(method, **payload)

        publisher.api = fail_replacement_send
        publisher.calls.clear()
        with self.assertRaisesRegex(RuntimeError, "ServerDisconnectedError"):
            await publisher.publish(pages)

        # The surviving old block must still be live; destructive deletion is delayed
        # until a complete new set has been sent successfully.
        self.assertFalse(any(method == "deleteMessage" and payload["message_id"] == old_ids[1]
                             for method, payload in publisher.calls))
        staged = self.state.get("rebuild_old_manifest", {})
        self.assertEqual(staged.get("binding"), publisher.binding())
        self.assertEqual(len(staged.get("messages", {})), 2)
'''
if marker not in t:
    raise SystemExit("bot publisher test marker missing")
t = t.replace(marker, '\n' + extra + marker, 1)
p.write_text(t, encoding="utf-8")

p = Path("tests/test_runtime.py")
t = p.read_text(encoding="utf-8")
marker = '\n    async def test_format_change_during_initialization_is_reported(self):\n'
extra = '''
    async def test_render_guard_never_publishes_a_partial_price(self):
        with patch("runtime.render_blocks", return_value={"bad:0": "<b>Пусто</b>"}):
            with self.assertRaisesRegex(RuntimeError, "рендер потерял позиции"):
                await self.service.render(items=[self.item])
        self.service.publisher.publish.assert_not_awaited()

'''
if marker not in t:
    raise SystemExit("runtime test marker missing")
t = t.replace(marker, '\n' + extra + marker, 1)
p.write_text(t, encoding="utf-8")
