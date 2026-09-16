from pathlib import Path


def replace_between(text, start_marker, end_marker, replacement, label):
    start = text.find(start_marker)
    if start < 0:
        raise SystemExit(f"{label}: start marker missing")
    end = text.find(end_marker, start)
    if end < 0:
        raise SystemExit(f"{label}: end marker missing")
    return text[:start] + replacement + text[end:]


def replace_async_test(text, name, replacement):
    marker = f"    async def {name}(self):\n"
    start = text.find(marker)
    if start < 0:
        raise SystemExit(f"test {name}: marker missing")
    end = text.find("\n    async def ", start + len(marker))
    if end < 0:
        end = text.find("\n\nif __name__", start)
    if end < 0:
        raise SystemExit(f"test {name}: end missing")
    return text[:start] + replacement.rstrip() + "\n" + text[end:]


# 1) Watch brand classification: a bare Watch may be Apple, but explicitly
# branded Galaxy/OnePlus/etc watches must never land in Apple Watch.
p = Path("prices.py")
s = p.read_text(encoding="utf-8")
old = '''def apple_watch_block(title):
    if re.search(r"\\b(?:apple\\s*)?watch\\b", title, re.I):
        return "Apple Watch"
    return ""
'''
new = '''def apple_watch_block(title):
    plain = FLAGS.sub("", clean(title)).strip()
    if not re.search(r"\\bwatch\\b", plain, re.I):
        return ""
    # Never classify another manufacturer's watch as Apple just because the
    # product name contains the generic word "Watch".
    if re.search(
        r"\\b(?:galaxy|samsung|one\\s*plus|oneplus|xiaomi|redmi|huawei|honor|"
        r"garmin|coros|pixel|fitbit|amazfit|oppo|vivo|realme|nothing)\\b",
        plain,
        re.I,
    ):
        return ""
    if re.search(r"\\bapple\\s*watch\\b|^watch\\b", plain, re.I):
        return "Apple Watch"
    return ""
'''
if s.count(old) != 1:
    raise SystemExit(f"apple_watch_block: expected 1 old block, got {s.count(old)}")
s = s.replace(old, new, 1)
p.write_text(s, encoding="utf-8")


# 2) Catalog buttons must be the actual heading printed at the start of each
# physical price message. No invented Smartphone/Audio/Other buckets.
p = Path("catalog_publisher.py")
s = p.read_text(encoding="utf-8")
new_catalog_labels = '''def catalog_labels(content):
    """Use exactly the visible physical Telegram post heading for navigation."""
    title = re.sub(r"\\s+", " ", page_title(content)).strip() or "Прайс"
    # Telegram inline button text is bounded; keep the beginning intact because
    # it is the same text the customer sees at the top of the price message.
    if len(title) > 64:
        title = title[:61].rstrip() + "…"
    return [title]


'''
s = replace_between(
    s,
    "def catalog_labels(content):\n",
    "def iphone_model_label(raw):\n",
    new_catalog_labels,
    "catalog_labels",
)

new_update_catalog = '''    async def _update_catalog(self, pages, records):
        manifest = self.state.get("published", {}).get("messages", {})
        buttons = []
        seen_titles = set()
        for key, content in pages.items():
            if key not in manifest:
                continue
            link = message_link(self.target, manifest[key]["id"])
            if not link:
                continue
            for title in catalog_labels(content):
                if title in seen_titles:
                    continue
                seen_titles.add(title)
                buttons.append({"text": title, "url": link})

        # Preserve the exact physical post order. The catalog is navigation to
        # those posts, so it must not reorder or rename them independently.
        batches = [buttons[start:start + 80] for start in range(0, len(buttons), 80)] or [[]]
        changes = 0

        # Create any extra catalog pages before adding inter-page links so every
        # referenced message id already exists.
        while len(records) < len(batches):
            changes += await self._catalog_entry(
                records, len(records), CATALOG_TEXT + "\\n\\nОбновляю разделы…", []
            )

        for index, batch in enumerate(batches):
            rows = [batch[start:start + 2] for start in range(0, len(batch), 2)]
            text = CATALOG_TEXT + (f"\\nСтраница {index + 1} из {len(batches)}" if len(batches) > 1 else "")
            if not pages:
                text += "\\n\\nПока нет выбранных позиций."
            elif not batch:
                text += "\\n\\nСсылки на посты доступны в каналах и супергруппах."
            if index + 1 < len(batches):
                link = message_link(self.target, records[index + 1]["id"])
                if link:
                    rows.append([{"text": "Следующие разделы →", "url": link}])
            changes += await self._catalog_entry(records, index, text, rows)
        for index in range(len(records) - 1, len(batches) - 1, -1):
            try:
                await self._delete(records[index]["id"])
            except RuntimeError as exc:
                if not is_missing_message_error(exc):
                    raise
            records.pop(index)
            self.save_catalog(records)
        return changes

'''
s = replace_between(
    s,
    "    async def _update_catalog(self, pages, records):\n",
    "    async def _repair_first_message_layout_once(self):\n",
    new_update_catalog,
    "_update_catalog",
)

s = s.replace(
    '            force_catalog = int(self.state.get("catalog_layout_version", 0) or 0) < 4\n',
    '            force_catalog = int(self.state.get("catalog_layout_version", 0) or 0) < 5\n',
    1,
)
s = s.replace(
    '                self.state.set("catalog_layout_version", 4)\n',
    '                self.state.set("catalog_layout_version", 5)\n',
    1,
)
p.write_text(s, encoding="utf-8")


# 3) Regression tests for the screenshot and real Apple Watch handling.
p = Path("tests/test_watch_branding.py")
p.write_text('''import unittest\n\nfrom prices import parse_documents\n\n\nclass WatchBrandingTests(unittest.TestCase):\n    def test_galaxy_and_oneplus_watches_never_become_apple_watch(self):\n        items = parse_documents(["""Galaxy Watch Ultra (2025) 47mm LTE Silver 🇦🇪 — 27300\nGalaxy Watch Ultra (2025) 47mm LTE White 🇦🇪 — 26800\nGalaxy Watch Ultra (2025) 47mm LTE White 🇦🇪 — 26800\nOnePlus Watch Lite Black Steel OPWWE262 🇪🇺 — 10300\nOnePlus Watch Lite Silver Steel OPWWE262 🇪🇺 — 10300"""]).items\n        self.assertEqual([item.block for item in items[:3]], ["Samsung"] * 3)\n        self.assertEqual([item.block for item in items[3:]], ["OnePlus"] * 2)\n\n    def test_real_apple_watch_still_reads_with_and_without_apple_prefix(self):\n        direct = parse_documents(["Apple Watch Series 11 46mm Black — 40000\\nWatch S10 46mm Silver — 35000"]).items\n        self.assertEqual([item.block for item in direct], ["Apple Watch", "Apple Watch"])\n\n        sectioned = parse_documents(["""Apple Watch\nSeries 11 46mm Black — 40000\nSE 3 44mm Silver — 30000\nUltra 3 49mm Black — 70000"""]).items\n        self.assertEqual([item.block for item in sectioned], ["Apple Watch"] * 3)\n\n\nif __name__ == "__main__":\n    unittest.main()\n''', encoding="utf-8")


# 4) Update catalog tests from invented groups to physical-post headings.
p = Path("tests/test_catalog.py")
t = p.read_text(encoding="utf-8")
t = t.replace(
    "from catalog_publisher import CatalogPublisher\n",
    "from catalog_publisher import CatalogPublisher, page_title\n",
    1,
)

t = replace_async_test(t, "test_catalog_links_and_physical_post_order_follow_new_order", '''    async def test_catalog_links_and_physical_post_order_follow_new_order(self):
        await self.publisher.publish(self.pages())
        before = {entry["id"] for entry in self.state.get("published")["messages"].values()}
        catalog_id = self.state.get("catalog")["messages"][0]["id"]
        self.assertGreater(catalog_id, max(before))
        self.publisher.calls.clear()
        pages = self.pages(["Dyson", "AirPods", "iPhone 17"])
        await self.publisher.publish(pages)
        manifest = self.state.get("published")["messages"]
        self.assertEqual(before, {entry["id"] for entry in manifest.values()})
        ordered = sorted(manifest.values(), key=lambda e: e["id"])
        self.assertIn("Dyson", ordered[0]["content"])
        buttons = [b for row in self.catalog_edit()["reply_markup"]["inline_keyboard"] for b in row]
        expected = list(dict.fromkeys(page_title(content) for content in pages.values()))
        self.assertEqual([b["text"] for b in buttons], expected)
        self.assertEqual(buttons[0]["url"], f"https://t.me/c/777/{ordered[0]['id']}")
        self.assertFalse(any(method == "sendMessage" for method, _ in self.publisher.calls))
        self.publisher.calls.clear()
        await self.publisher.publish(pages)
        self.assertFalse(any(method == "sendMessage" for method, _ in self.publisher.calls))''')

t = replace_async_test(t, "test_catalog_stays_compact_even_with_many_price_blocks", '''    async def test_catalog_stays_compact_even_with_many_price_blocks(self):
        pages = {f"block{i}:0": f"<b>Device {i}</b>\\n\\n<code>Device {i} — 1000</code>" for i in range(12)}
        await self.publisher.publish(pages)
        records = self.state.get("catalog")["messages"]
        self.assertEqual(len(records), 1)
        edit = self.catalog_edit()
        buttons = [b for row in edit["reply_markup"]["inline_keyboard"] for b in row]
        self.assertEqual([b["text"] for b in buttons], [f"Device {i}" for i in range(12)])
        self.assertTrue(all(len(row) <= 2 for row in edit["reply_markup"]["inline_keyboard"]))''')

t = replace_async_test(t, "test_catalog_layout_version_forces_existing_keyboard_edit", '''    async def test_catalog_layout_version_forces_existing_keyboard_edit(self):
        items = parse_documents(["Mac mini M4 16/256 Silver — 68800"]).items
        pages = render_blocks(items, Settings())
        await self.publisher.publish(pages)
        catalog_id = self.state.get("catalog")["messages"][0]["id"]
        self.state.set("catalog_layout_version", 4)
        self.publisher.calls.clear()
        await self.publisher.publish(pages)
        self.assertEqual(self.state.get("catalog")["messages"][0]["id"], catalog_id)
        self.assertEqual(self.state.get("catalog_layout_version"), 5)
        edits = [payload for method, payload in self.publisher.calls if method == "editMessageText" and payload.get("reply_markup")]
        self.assertTrue(edits)
        buttons = [button for row in edits[-1]["reply_markup"]["inline_keyboard"] for button in row]
        self.assertEqual([button["text"] for button in buttons], ["Apple"])
        self.assertFalse(any(method == "sendMessage" for method, _ in self.publisher.calls))''')

t = replace_async_test(t, "test_catalog_keeps_base_groups_compact_and_iphone_models_direct", '''    async def test_catalog_keeps_base_groups_compact_and_iphone_models_direct(self):
        items = parse_documents(["""iPhone 16 128 Black — 60000
iPhone 16 Plus 128 Black — 70000
iPhone 16 Pro 256 Black — 80000
Apple Watch Ultra 3 49mm Black — 70000
Samsung A57 8/256 Blue — 32000
Xiaomi 15 12/256 White — 48000
Oura Ring 4 Silver — 35000
Bose Onyx 9 Black — 30000
DJI Osmo Pocket 4 — 45000
PlayStation 5 Pro — 70000
Dyson HS08 — 40000"""]).items
        pages = render_blocks(items, Settings())
        await self.publisher.publish(pages)
        buttons = [button for row in self.catalog_edit()["reply_markup"]["inline_keyboard"] for button in row]
        labels = [button["text"] for button in buttons]
        expected = list(dict.fromkeys(page_title(content) for content in pages.values()))
        self.assertEqual(labels, expected)
        for invented in ["Смартфоны", "Часы / носимое", "Аудио", "Фото / видео", "Игры", "Другое"]:
            self.assertNotIn(invented, labels)''')

t = replace_async_test(t, "test_paired_iphone_models_share_one_generation_button", '''    async def test_paired_iphone_models_share_one_generation_button(self):
        items = parse_documents(["iPhone 16 128 Black — 60000\\niPhone 16 Plus 128 Pink — 70000"]).items
        pages = render_blocks(items, Settings())
        await self.publisher.publish(pages)
        manifest = self.state.get("published")["messages"]
        self.assertEqual(len(manifest), 1)
        message_id = next(iter(manifest.values()))["id"]
        buttons = [button for row in self.catalog_edit()["reply_markup"]["inline_keyboard"] for button in row]
        self.assertEqual([button["text"] for button in buttons], [page_title(next(iter(pages.values())))])
        self.assertEqual(buttons[0]["url"], f"https://t.me/c/777/{message_id}")''')

t = replace_async_test(t, "test_many_iphone_models_produce_only_three_ordered_buttons", '''    async def test_many_iphone_models_produce_only_three_ordered_buttons(self):
        items = parse_documents(["""iPhone 11 128 Black — 40000
iPhone 12 128 Black — 45000
iPhone 13 128 Black — 50000
iPhone 14 Plus 128 Black — 55000
iPhone 15 Pro Max 256 Black — 70000
iPhone 16 128 Black — 60000
iPhone 16 Plus 128 Pink — 70000
iPhone 16 Pro 256 Black — 80000
iPhone 16 Pro Max 256 Black — 90000
iPhone 17e 256 Black — 70000
iPhone 17 256 Black — 80000
iPhone 17 Pro 256 Black — 90000
iPhone 17 Pro Max 256 Black — 100000
iPhone Air 256 Black — 85000
Mac mini M4 16/256 Silver — 68800
Samsung S26 12/256 Black — 65000"""]).items
        pages = render_blocks(items, Settings())
        await self.publisher.publish(pages)
        buttons = [button for row in self.catalog_edit()["reply_markup"]["inline_keyboard"] for button in row]
        labels = [button["text"] for button in buttons]
        expected = list(dict.fromkeys(page_title(content) for content in pages.values()))
        self.assertEqual(labels, expected)
        self.assertEqual(labels[:3], [
            "iPhone 11 / 12 / 13 / 14 / 15",
            "iPhone 16 / 16 Plus / 16 Pro / 16 Pro Max",
            page_title(list(pages.values())[2]),
        ])''')

p.write_text(t, encoding="utf-8")
