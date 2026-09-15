from pathlib import Path
import re


def rep(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected 1 match, got {count}")
    return text.replace(old, new, 1)


# 1) Catalog: never pin it, never steal a price slot, always keep it after price posts.
p = Path("catalog_publisher.py")
s = p.read_text(encoding="utf-8")
old = '''        # Save the sent ID before pinning so denied pin permission never duplicates it.\n        self.save_catalog(records)\n        if index == 0 and not record.get("pinned"):\n            try:\n                await self.api("pinChatMessage", chat_id=self.target, message_id=record["id"], disable_notification=True)\n                record["pinned"] = True\n                record.pop("pin_error", None)\n            except RuntimeError as exc:\n                record["pin_error"] = str(exc)\n            self.save_catalog(records)\n'''
s = rep(s, old, '        self.save_catalog(records)\n', "remove catalog pinning")

start = s.index('    async def _prepare_catalog(self, count):\n')
end = s.index('    async def _update_catalog(self, pages, records):\n', start)
new_prepare = '''    async def _prepare_catalog(self, count):\n        """Ensure catalog messages exist strictly after every managed price post."""\n        records = self.catalog_records()\n        stored = self.state.get("published", {}) or {}\n        manifest = stored.get("messages", {}) if stored.get("binding") == self.binding() else {}\n        price_ids = [int(entry["id"]) for entry in manifest.values() if entry.get("id")]\n        catalog_ids = [int(record["id"]) for record in records if record.get("id")]\n\n        # Old versions pinned the catalog and sometimes reused the first price slot.\n        # Recreate such records once so the custom intro remains the only pin and\n        # the catalog becomes physically the last managed message.\n        must_rebuild = bool(records) and (\n            any(record.get("pinned") for record in records)\n            or (price_ids and catalog_ids and min(catalog_ids) <= max(price_ids))\n        )\n        if must_rebuild:\n            for record in list(records):\n                try:\n                    await self._delete(record["id"])\n                except RuntimeError as exc:\n                    if "message to delete not found" not in str(exc).lower():\n                        raise\n            records = []\n            self.save_catalog(records)\n\n        for index in range(count):\n            if index >= len(records) or not records[index].get("hash"):\n                await self._catalog_entry(records, index, CATALOG_TEXT + "\\n\\nОбновляю разделы…", [])\n        return records\n\n'''
s = s[:start] + new_prepare + s[end:]

old_publish = '''    async def publish(self, pages):\n        async with self.layout_lock:\n            await self.ensure_target()\n            await self._ensure_first_message()\n            count = max(1, (len({key.rsplit(":", 1)[0] for key in pages}) + 79) // 80)\n            records = await self._prepare_catalog(count)\n            changes = await super().publish(pages)\n            changes += await self._update_catalog(pages, records)\n            return changes\n'''
new_publish = '''    async def publish(self, pages):\n        async with self.layout_lock:\n            await self.ensure_target()\n            await self._ensure_first_message()\n            # First publish/reorder every price page. Only then create or move the\n            # navigation catalog, otherwise Telegram places it before later posts.\n            changes = await super().publish(pages)\n            count = max(1, (len({key.rsplit(":", 1)[0] for key in pages}) + 79) // 80)\n            records = await self._prepare_catalog(count)\n            changes += await self._update_catalog(pages, records)\n            return changes\n'''
s = rep(s, old_publish, new_publish, "catalog publish order")
p.write_text(s, encoding="utf-8")


# 2) Order UI: collect every cached supplier block and make pagination obvious.
p = Path("control_catalog.py")
s = p.read_text(encoding="utf-8")
s = rep(s, 'from prices import ordered_blocks\n', 'from prices import Item, ordered_blocks\n', "Item import")
old_known = '''    def known_blocks(self):\n        return {item.block for item in self.service.cached_items(include_closed=True)}\n'''
new_known = '''    def known_blocks(self):\n        blocks = {item.block for item in self.service.cached_items(include_closed=True)}\n        # Read every raw cached supplier row as well. This avoids the order screen\n        # looking like it contains only iPhones when merged/current items are partial.\n        sources = self.service.state.get("sources", {}) or {}\n        for source in sources.values():\n            for raw in source.get("items", []) or []:\n                try:\n                    block = Item.from_dict(raw).block\n                except (KeyError, TypeError, ValueError):\n                    continue\n                if block:\n                    blocks.add(block)\n        blocks.update(block for block in self.service.options().get("block_order", []) if block)\n        return blocks\n'''
s = rep(s, old_known, new_known, "all known blocks")

old_show = '''    async def show_order(self, chat_id, user_id, page=0):\n        known = self.known_blocks()\n        preferred = self.order_drafts.get(user_id, self.service.options().get("block_order", []))\n        order = ordered_blocks(known, preferred)\n        self.order_drafts[user_id] = order\n        page = max(0, min(page, max(0, (len(order) - 1) // 8)))\n        rows = []\n        for index, block in enumerate(order[page * 8:page * 8 + 8], start=page * 8):\n            ident = block_id(block)\n            row = [{"text": f"{index + 1}. {block}", "callback_data": f"order:show:{page}"}]\n            if index:\n                row.append({"text": "↑", "callback_data": f"order:up:{ident}:{page}"})\n            if index + 1 < len(order):\n                row.append({"text": "↓", "callback_data": f"order:down:{ident}:{page}"})\n            rows.append(row)\n        navigation = []\n        if page:\n            navigation.append({"text": "←", "callback_data": f"order:show:{page - 1}"})\n        if (page + 1) * 8 < len(order):\n            navigation.append({"text": "→", "callback_data": f"order:show:{page + 1}"})\n        if navigation:\n            rows.append(navigation)\n        if order:\n            rows.append([{"text": "✅ Применить порядок", "callback_data": "order:apply"},\n                         {"text": "По умолчанию", "callback_data": "order:reset"}])\n        await self.send(chat_id, "Подними или опусти блок стрелками, затем нажми «Применить порядок»."\n                        if order else "Сначала запроси прайс — здесь появятся все его блоки.",\n                        {"inline_keyboard": rows})\n'''
new_show = '''    async def show_order(self, chat_id, user_id, page=0):\n        known = self.known_blocks()\n        preferred = self.order_drafts.get(user_id, self.service.options().get("block_order", []))\n        order = ordered_blocks(known, preferred)\n        self.order_drafts[user_id] = order\n        page_size = 24\n        total_pages = max(1, (len(order) + page_size - 1) // page_size)\n        page = max(0, min(page, total_pages - 1))\n        rows = []\n        start = page * page_size\n        for index, block in enumerate(order[start:start + page_size], start=start):\n            ident = block_id(block)\n            row = [{"text": f"{index + 1}. {block}", "callback_data": f"order:show:{page}"}]\n            if index:\n                row.append({"text": "↑", "callback_data": f"order:up:{ident}:{page}"})\n            if index + 1 < len(order):\n                row.append({"text": "↓", "callback_data": f"order:down:{ident}:{page}"})\n            rows.append(row)\n        navigation = []\n        if page:\n            navigation.append({"text": "← Предыдущие", "callback_data": f"order:show:{page - 1}"})\n        if page + 1 < total_pages:\n            navigation.append({"text": "Следующие →", "callback_data": f"order:show:{page + 1}"})\n        if navigation:\n            rows.append(navigation)\n        if order:\n            rows.append([{"text": "✅ Применить порядок", "callback_data": "order:apply"},\n                         {"text": "По умолчанию", "callback_data": "order:reset"}])\n        text = (\n            f"Порядок блоков: всего {len(order)} · страница {page + 1}/{total_pages}.\\n"\n            "Здесь собраны блоки из всех сохранённых прайсов поставщиков. "\n            "Подними или опусти блок стрелками, затем нажми «Применить порядок»."\n            if order else "Сначала запроси прайс — здесь появятся все его блоки."\n        )\n        await self.send(chat_id, text, {"inline_keyboard": rows})\n'''
s = rep(s, old_show, new_show, "expanded block order UI")
p.write_text(s, encoding="utf-8")


# 3) Telegram may split a large block, but don't print "Часть 1/2" to customers.
p = Path("prices.py")
s = p.read_text(encoding="utf-8")
s = rep(s, '            part = f"\\nЧасть {index + 1}" if index else ""\n            pages[key] = header + part + "\\n\\n" + "\\n".join(chunk)\n', '            pages[key] = header + "\\n\\n" + "\\n".join(chunk)\n', "remove part labels")
p.write_text(s, encoding="utf-8")


# 4) Tests for the required physical order and customer-visible format.
p = Path("tests/test_catalog.py")
s = p.read_text(encoding="utf-8")
s = rep(s, '        first = self.state.get("catalog")["messages"][0]["id"]\n        self.assertLess(first, min(before))\n', '        catalog_id = self.state.get("catalog")["messages"][0]["id"]\n        self.assertGreater(catalog_id, max(before))\n', "catalog after price assertion")
s = rep(s, '''    async def test_existing_first_price_becomes_catalog_without_losing_products(self):\n        await self.publisher.ensure_target()\n        self.state.set("published", {"binding": self.publisher.binding(), "messages": {\n            "old:0": {"id": 20, "content": "old"}, "old:1": {"id": 21, "content": "old2"}}})\n        await self.publisher.publish(self.pages())\n        self.assertEqual(self.state.get("catalog")["messages"][0]["id"], 20)\n        entries = self.state.get("published")["messages"].values()\n        self.assertEqual(len(entries), 3)\n        self.assertNotIn(20, [entry["id"] for entry in entries])\n''', '''    async def test_existing_price_slots_stay_prices_and_catalog_is_last(self):\n        await self.publisher.ensure_target()\n        self.state.set("published", {"binding": self.publisher.binding(), "messages": {\n            "old:0": {"id": 20, "content": "old"}, "old:1": {"id": 21, "content": "old2"}}})\n        await self.publisher.publish(self.pages())\n        entries = self.state.get("published")["messages"].values()\n        self.assertEqual(len(entries), 3)\n        price_ids = [entry["id"] for entry in entries]\n        self.assertIn(20, price_ids)\n        self.assertIn(21, price_ids)\n        self.assertGreater(self.state.get("catalog")["messages"][0]["id"], max(price_ids))\n''', "existing slots test")

pattern = r'    async def test_pin_failure_keeps_catalog_id_and_does_not_duplicate\(self\):.*?(?=    async def test_custom_intro_stays_before_catalog_and_is_unchanged)'
replacement = '''    async def test_catalog_is_never_pinned(self):\n        await self.publisher.publish(self.pages())\n        self.assertFalse(any(method == "pinChatMessage" for method, _ in self.publisher.calls))\n        record = self.state.get("catalog")["messages"][0]\n        self.assertNotIn("pinned", record)\n\n'''
s, n = re.subn(pattern, replacement, s, count=1, flags=re.S)
if n != 1:
    raise SystemExit(f"catalog pin test: got {n}")

old_custom = '''    async def test_custom_intro_stays_before_catalog_and_is_unchanged(self):\n        await self.publisher.set_first_message("Гарантия и выдача")\n        first = self.state.get("first_message")["id"]\n        await self.publisher.publish(self.pages())\n        self.assertLess(first, self.state.get("catalog")["messages"][0]["id"])\n        self.assertEqual(self.state.get("first_message_text"), "Гарантия и выдача")\n'''
new_custom = '''    async def test_custom_intro_is_only_pin_and_catalog_is_last(self):\n        await self.publisher.set_first_message("Гарантия и выдача")\n        first = self.state.get("first_message")["id"]\n        await self.publisher.publish(self.pages())\n        price_ids = [entry["id"] for entry in self.state.get("published")["messages"].values()]\n        catalog_id = self.state.get("catalog")["messages"][0]["id"]\n        self.assertLess(first, min(price_ids))\n        self.assertGreater(catalog_id, max(price_ids))\n        pins = [payload["message_id"] for method, payload in self.publisher.calls if method == "pinChatMessage"]\n        self.assertEqual(pins, [first])\n        self.assertEqual(self.state.get("first_message_text"), "Гарантия и выдача")\n'''
s = rep(s, old_custom, new_custom, "custom intro pin/order test")

insert = '''\n    async def test_old_pinned_catalog_is_rebuilt_last(self):\n        await self.publisher.publish(self.pages())\n        record = self.state.get("catalog")["messages"][0]\n        old_id = record["id"]\n        record["pinned"] = True\n        self.state.set("catalog", {"binding": self.publisher.binding(), "messages": [record]})\n        self.publisher.calls.clear()\n        await self.publisher.publish(self.pages())\n        new_id = self.state.get("catalog")["messages"][0]["id"]\n        price_ids = [entry["id"] for entry in self.state.get("published")["messages"].values()]\n        self.assertNotEqual(old_id, new_id)\n        self.assertGreater(new_id, max(price_ids))\n        self.assertTrue(any(method == "deleteMessage" and payload.get("message_id") == old_id\n                            for method, payload in self.publisher.calls))\n        self.assertFalse(any(method == "pinChatMessage" for method, _ in self.publisher.calls))\n'''
marker = '\n    async def test_restart_keeps_order_ids_and_catalog_without_resending(self):\n'
if marker not in s:
    raise SystemExit("catalog regression insertion marker missing")
s = s.replace(marker, insert + marker, 1)

# Catalog order screen must expose raw blocks outside the merged/current cache.
insert_control = '''\n    async def test_order_screen_includes_blocks_from_all_raw_supplier_caches(self):\n        raw = parse_documents(["Samsung Galaxy S26 12/256 Black — 70000\\nVivo V70 12/256 Grey — 46300\\nOura Ring 4 Silver — 35000"]).items\n        self.state.set("sources", {"extra": {"items": [item.to_dict() for item in raw]}})\n        await self.controller.show_order(42, 42, 0)\n        text = self.controller.send.await_args.args[1]\n        keyboard = self.controller.send.await_args.args[2]["inline_keyboard"]\n        labels = [button["text"] for row in keyboard for button in row]\n        self.assertIn("Samsung", " ".join(labels))\n        self.assertIn("Vivo", " ".join(labels))\n        self.assertIn("Oura Ring", " ".join(labels))\n        self.assertIn("всего 5", text)\n'''
marker2 = '\n    async def test_unknown_user_and_group_cannot_change_order_or_selection(self):\n'
if marker2 not in s:
    raise SystemExit("control test insertion marker missing")
s = s.replace(marker2, insert_control + marker2, 1)
p.write_text(s, encoding="utf-8")


p = Path("tests/test_prices.py")
s = p.read_text(encoding="utf-8")
insert_price = '''\n    def test_split_pages_have_no_part_labels(self):\n        items = [Item(f"iPhone 17 256GB Black {i} 🇺🇸", Decimal(60000), "RUB", "iPhone 17", "esim")\n                 for i in range(220)]\n        pages = render_blocks(items, Settings())\n        self.assertGreater(len(pages), 1)\n        self.assertTrue(all("Часть " not in page for page in pages.values()))\n'''
marker3 = '\n    def test_closure_and_block_filter(self):\n'
if marker3 not in s:
    raise SystemExit("price test insertion marker missing")
s = s.replace(marker3, insert_price + marker3, 1)
p.write_text(s, encoding="utf-8")
