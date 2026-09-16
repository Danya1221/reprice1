from pathlib import Path


def rep(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected 1 match, got {count}")
    return text.replace(old, new, 1)

# bot_publisher.py
p = Path("bot_publisher.py")
s = p.read_text(encoding="utf-8")
s = rep(
    s,
    'def plain(text):\n    return telegram_html.parse(text)[0]\n\n\nclass BotAPIPublisher:',
    '''def plain(text):\n    return telegram_html.parse(text)[0]\n\n\ndef is_missing_message_error(error):\n    """Telegram uses several texts for a stale/deleted stored message ID."""\n    text = str(error).lower().replace("-", "_")\n    return any(marker in text for marker in (\n        "message to edit not found",\n        "message to delete not found",\n        "message_id_invalid",\n        "message id invalid",\n        "message identifier is not specified",\n        "message can't be edited",\n        "message cannot be edited",\n    ))\n\n\nclass BotAPIPublisher:''',
    "missing message helper",
)

marker = '    async def publish(self, pages):\n'
helper = '''    async def _verify_or_rebuild_manifest(self, pages, manifest, binding):\n        """Keep stored IDs while they exist; rebuild all managed price posts if one was deleted.\n\n        Bot API has no getMessage method. Editing a message with its current stored\n        content is therefore the safest existence probe: a live unchanged message\n        returns `message is not modified`, while a deleted/stale ID returns one of\n        Telegram's invalid-message errors. Rebuilding the whole managed price set\n        preserves the requested physical order instead of appending one recovered\n        block at the bottom.\n        """\n        if not manifest:\n            return 0\n\n        missing = False\n        for key, entry in list(manifest.items()):\n            if key not in pages or not entry.get("id"):\n                continue\n            probe_content = entry.get("content")\n            if not probe_content:\n                continue\n            try:\n                await self._edit(entry["id"], probe_content)\n            except RuntimeError as exc:\n                text = str(exc).lower()\n                if "message is not modified" in text:\n                    continue\n                if is_missing_message_error(exc):\n                    missing = True\n                    break\n                raise\n\n        if not missing:\n            return 0\n\n        deleted = 0\n        for entry in list(manifest.values()):\n            message_id = entry.get("id")\n            if not message_id:\n                continue\n            try:\n                await self._delete(message_id)\n                deleted += 1\n                await asyncio.sleep(max(0, self.settings.send_delay))\n            except RuntimeError as exc:\n                if not is_missing_message_error(exc):\n                    raise\n\n        manifest.clear()\n        self.state.set("published", {"binding": binding, "messages": manifest})\n        return deleted\n\n'''
if marker not in s:
    raise SystemExit("publish marker missing")
s = s.replace(marker, helper + marker, 1)

s = rep(
    s,
    '            self.state.set("published", {"binding": binding, "messages": manifest})\n            changes = 0\n\n            for key, content in pages.items():',
    '            self.state.set("published", {"binding": binding, "messages": manifest})\n            changes = await self._verify_or_rebuild_manifest(pages, manifest, binding)\n\n            for key, content in pages.items():',
    "manifest preflight",
)

old = '''                        elif ("message to edit not found" in text\n                              or "message can't be edited" in text\n                              or "message cannot be edited" in text):\n                            message_id = None\n'''
new = '''                        elif is_missing_message_error(exc):\n                            message_id = None\n'''
s = rep(s, old, new, "edit stale ID handling")

s = rep(
    s,
    '                except RuntimeError as exc:\n                    if "message to delete not found" not in str(exc).lower():\n                        raise\n',
    '                except RuntimeError as exc:\n                    if not is_missing_message_error(exc):\n                        raise\n',
    "delete stale ID handling",
)
p.write_text(s, encoding="utf-8")

# first_message_publisher.py
p = Path("first_message_publisher.py")
s = p.read_text(encoding="utf-8")
s = rep(s, 'from bot_publisher import BotAPIPublisher, digest', 'from bot_publisher import BotAPIPublisher, digest, is_missing_message_error', "first import")
old = '''                    elif (\n                        "message to edit not found" in lowered\n                        or "message can't be edited" in lowered\n                        or "message cannot be edited" in lowered\n                    ):\n                        message_id = None\n'''
new = '''                    elif is_missing_message_error(exc):\n                        message_id = None\n'''
s = rep(s, old, new, "first edit stale ID")
s = rep(
    s,
    '                except RuntimeError as exc:\n                    if "message to delete not found" not in str(exc).lower():\n                        raise\n',
    '                except RuntimeError as exc:\n                    if not is_missing_message_error(exc):\n                        raise\n',
    "first delete stale ID",
)
p.write_text(s, encoding="utf-8")

# catalog_publisher.py
p = Path("catalog_publisher.py")
s = p.read_text(encoding="utf-8")
s = rep(s, 'from bot_publisher import digest', 'from bot_publisher import digest, is_missing_message_error', "catalog import")

old = '''        try:\n            return await self.api("editMessageText", **payload)\n        except RuntimeError as exc:\n            if "message is not modified" in str(exc).lower():\n                return None\n            raise\n'''
# _edit_or_send actually lives in control_catalog, not catalog_publisher; leave it for the next file.

s = s.replace('if "message to delete not found" not in str(exc).lower():\n                    raise', 'if not is_missing_message_error(exc):\n                    raise')
s = s.replace('if "message to delete not found" not in str(exc).lower():\n                        raise', 'if not is_missing_message_error(exc):\n                        raise')
s = s.replace('if "message to edit not found" in error:\n                    record = {}', 'if is_missing_message_error(exc):\n                    record = {}')
p.write_text(s, encoding="utf-8")

# control_catalog.py
p = Path("control_catalog.py")
s = p.read_text(encoding="utf-8")
s = rep(s, 'from control_first import FirstMessageController\nfrom prices import Item, ordered_blocks', 'from control_first import FirstMessageController\nfrom bot_publisher import is_missing_message_error\nfrom prices import Item, ordered_blocks', "control import")
old = '''        try:\n            return await self.api("editMessageText", **payload)\n        except RuntimeError as exc:\n            if "message is not modified" in str(exc).lower():\n                return None\n            raise\n'''
new = '''        try:\n            return await self.api("editMessageText", **payload)\n        except RuntimeError as exc:\n            if "message is not modified" in str(exc).lower():\n                return None\n            if is_missing_message_error(exc):\n                return await self.send(chat_id, text, reply_markup)\n            raise\n'''
s = rep(s, old, new, "control edit fallback")
p.write_text(s, encoding="utf-8")

# tests/test_bot_publisher.py
p = Path("tests/test_bot_publisher.py")
s = p.read_text(encoding="utf-8")
s = rep(
    s,
    '        self.calls = []\n',
    '        self.calls = []\n        self.next_message_id = 123\n        self.missing_ids = set()\n',
    "fake state",
)
s = rep(
    s,
    '        if method == "sendMessage":\n            return {"message_id": 123}\n        if method in {"editMessageText", "deleteMessage"}:\n            return True\n',
    '''        if method == "sendMessage":\n            message_id = self.next_message_id\n            self.next_message_id += 1\n            return {"message_id": message_id}\n        if method == "editMessageText":\n            if int(payload["message_id"]) in self.missing_ids:\n                raise RuntimeError("Bot API editMessageText: Bad Request: MESSAGE_ID_INVALID")\n            # Telegram returns this for our existence probe when content is unchanged.\n            raise RuntimeError("Bot API editMessageText: Bad Request: message is not modified")\n        if method == "deleteMessage":\n            if int(payload["message_id"]) in self.missing_ids:\n                raise RuntimeError("Bot API deleteMessage: Bad Request: MESSAGE_ID_INVALID")\n            return True\n''',
    "fake API",
)
s = rep(
    s,
    '    async def test_unchanged_page_is_not_edited_again(self):',
    '    async def test_unchanged_page_keeps_same_id_after_existence_probe(self):',
    "unchanged test name",
)
s = rep(
    s,
    '        self.assertEqual(second_changes, 0)\n        self.assertFalse(any(method == "editMessageText" for method, _ in publisher.calls))\n',
    '        self.assertEqual(second_changes, 0)\n        self.assertTrue(any(method == "editMessageText" for method, _ in publisher.calls))\n        self.assertFalse(any(method == "sendMessage" for method, _ in publisher.calls))\n',
    "unchanged probe expectation",
)
# The generic fake now reports `message is not modified` on edits, so changed-page
# behavior needs a one-shot edit implementation for the actual changed content.
old = '''        publisher.calls.clear()\n        changes = await publisher.publish({"iphone:0": "Новый прайс"})\n\n        self.assertEqual(changes, 1)\n        self.assertTrue(any(method == "editMessageText" for method, _ in publisher.calls))\n'''
new = '''        original_api = publisher.api\n        async def changed_api(method, **payload):\n            if method == "editMessageText" and payload.get("text") == "Новый прайс":\n                publisher.calls.append((method, payload))\n                return True\n            return await original_api(method, **payload)\n        publisher.api = changed_api\n        publisher.calls.clear()\n        changes = await publisher.publish({"iphone:0": "Новый прайс"})\n\n        self.assertEqual(changes, 1)\n        self.assertTrue(any(method == "editMessageText" and payload.get("text") == "Новый прайс"\n                            for method, payload in publisher.calls))\n'''
s = rep(s, old, new, "changed page fake")

marker = '\n\nif __name__ == "__main__":\n'
extra = '''\n    async def test_deleted_saved_id_rebuilds_all_managed_price_posts(self):\n        settings = Settings(send_delay=0, admin_ids=(42,))\n        group_id = -100777\n        chats = {group_id: {"id": group_id, "type": "supergroup", "title": "Розница"}}\n        publisher = FakeBotPublisher(group_id, self.state, settings, chats)\n        pages = {"one:0": "Первый блок", "two:0": "Второй блок"}\n\n        await publisher.publish(pages)\n        old_manifest = self.state.get("published")["messages"]\n        old_ids = [old_manifest[key]["id"] for key in pages]\n        publisher.missing_ids.add(old_ids[0])\n        publisher.calls.clear()\n\n        changes = await publisher.publish(pages)\n        new_manifest = self.state.get("published")["messages"]\n        new_ids = [new_manifest[key]["id"] for key in pages]\n\n        self.assertNotEqual(old_ids, new_ids)\n        self.assertGreater(changes, 1)\n        self.assertTrue(any(method == "deleteMessage" and payload["message_id"] == old_ids[1]\n                            for method, payload in publisher.calls))\n        self.assertEqual(sum(1 for method, _ in publisher.calls if method == "sendMessage"), 2)\n\n'''
if marker not in s:
    raise SystemExit("test marker missing")
s = s.replace(marker, extra + marker, 1)
p.write_text(s, encoding="utf-8")
