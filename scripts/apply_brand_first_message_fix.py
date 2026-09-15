from pathlib import Path


def rep(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected 1 match, got {count}")
    return text.replace(old, new, 1)


# 1) Product classification: keep Xiaomi Note 14S and RODE out of DJI context,
# and split Samsung into real series so each series gets its own catalog button.
p = Path("prices.py")
s = p.read_text(encoding="utf-8")
s = rep(
    s,
    '    ("DJI / Insta360", r"\\bdji\\b|\\binsta\\s*360\\b"),\n',
    '    ("Rode", r"\\br(?:o|ø)de\\b"),\n    ("DJI / Insta360", r"\\bdji\\b|\\binsta\\s*360\\b"),\n',
    "Rode brand",
)
s = rep(
    s,
    '    ("Xiaomi", r"\\bxiaomi\\b|\\bredmi\\b|\\bpoco\\b|^\\s*(?:redmi\\s+)?note\\s+\\d{1,2}\\b"),',
    '    ("Xiaomi", r"\\bxiaomi\\b|\\bredmi\\b|\\bpoco\\b|^\\s*(?:redmi\\s+)?note\\s+\\d{1,2}[a-z]*\\b"),',
    "Xiaomi Note suffix",
)
old_product = '''def product_block(title):\n    watch = apple_watch_block(title)\n    if watch:\n        return watch\n    if re.search(r"\\b(?:MacBook|iMac)\\b", title, re.I):\n        return "MacBook / iMac"\n    for label in ("AirPods", "iPad", "Mac mini", "Mac Studio", "Apple TV", "AirTag"):\n        if re.search(r"\\b" + re.escape(label) + r"\\b", title, re.I):\n            return label\n    return brand_of(title)\n'''
new_product = '''def samsung_block(title):\n    if not re.search(r"\\bsamsung\\b|\\bgalaxy\\b|самсунг", title, re.I):\n        return ""\n    if re.search(r"\\b(?:galaxy\\s*)?(?:z\\s*)?(?:fold|flip)\\b", title, re.I):\n        return "Samsung Fold / Flip"\n    if re.search(r"\\b(?:galaxy\\s*)?a\\s*\\d{1,3}[a-z]*\\b|\\bsamsung\\s+a\\s*\\d{1,3}[a-z]*\\b", title, re.I):\n        return "Samsung Galaxy A"\n    if re.search(r"\\b(?:galaxy\\s*)?s\\s*\\d{1,3}(?:\\s*(?:\\+|plus|ultra|fe))?\\b|\\bsamsung\\s+s\\s*\\d{1,3}", title, re.I):\n        return "Samsung Galaxy S"\n    # Headers like "Samsung Galaxy A" / "Samsung Galaxy S" have no model number.\n    if re.search(r"\\bgalaxy\\s+a\\b|\\bsamsung\\s+galaxy\\s+a\\b", title, re.I):\n        return "Samsung Galaxy A"\n    if re.search(r"\\bgalaxy\\s+s\\b|\\bsamsung\\s+galaxy\\s+s\\b", title, re.I):\n        return "Samsung Galaxy S"\n    return "Samsung"\n\n\ndef product_block(title):\n    watch = apple_watch_block(title)\n    if watch:\n        return watch\n    samsung = samsung_block(title)\n    if samsung:\n        return samsung\n    if re.search(r"\\b(?:MacBook|iMac)\\b", title, re.I):\n        return "MacBook / iMac"\n    for label in ("AirPods", "iPad", "Mac mini", "Mac Studio", "Apple TV", "AirTag"):\n        if re.search(r"\\b" + re.escape(label) + r"\\b", title, re.I):\n            return label\n    return brand_of(title)\n'''
s = rep(s, old_product, new_product, "Samsung series block")
s = rep(
    s,
    '                "Samsung", "Honor", "Realme", "Huawei", "Tecno", "Xiaomi", "Google", "Dyson",\n                "Oura Ring", "CPO", "ASIS", "Аксессуары", "Товары"]',
    '                "Samsung", "Honor", "Realme", "Huawei", "Tecno", "Xiaomi", "Google", "Rode", "Dyson",\n                "Oura Ring", "CPO", "ASIS", "Аксессуары", "Товары"]',
    "Rode order",
)
s = rep(
    s,
    '        family = "iPhone" if model else ("Apple Watch" if name.startswith("Apple Watch") else name)\n',
    '        family = "iPhone" if model else ("Apple Watch" if name.startswith("Apple Watch") else ("Samsung" if name.startswith("Samsung") else name))\n',
    "Samsung order family",
)
p.write_text(s, encoding="utf-8")


# 2) Catalog/first-message layout repair. One time per persisted state, rebuild the
# managed sequence as: custom first message -> prices -> catalog.
cp = Path("catalog_publisher.py")
c = cp.read_text(encoding="utf-8")
needle = '''    async def publish(self, pages):\n        async with self.layout_lock:\n            await self.ensure_target()\n            await self._ensure_first_message()\n            # First publish/reorder every price page. Only then create or move the\n            # navigation catalog, otherwise Telegram places it before later posts.\n            changes = await super().publish(pages)\n'''
replacement = '''    async def _repair_first_message_layout_once(self):\n        text = str(self.state.get("first_message_text", "") or "").strip()\n        if not text or self.state.get("first_message_layout_version", 0) >= 2:\n            return\n\n        # A previous catalog implementation could share/delete the intro message ID.\n        # Rebuild the managed sequence once from persistent text so the intro is\n        # guaranteed to exist, be pinned, and physically precede every price post.\n        records = self.catalog_records()\n        first = self.state.get("first_message", {}) or {}\n        ids = []\n        if first.get("id"):\n            ids.append(int(first["id"]))\n        ids.extend(int(record["id"]) for record in records if record.get("id"))\n        for message_id in dict.fromkeys(ids):\n            try:\n                await self._delete(message_id)\n            except RuntimeError as exc:\n                if "message to delete not found" not in str(exc).lower():\n                    raise\n        self.save_catalog([])\n        self.state.set("first_message", {})\n        await self._clear_managed_price_posts()\n        self.state.set("first_message_layout_version", 2)\n\n    async def publish(self, pages):\n        async with self.layout_lock:\n            await self.ensure_target()\n            await self._repair_first_message_layout_once()\n            await self._ensure_first_message()\n            # First publish/reorder every price page. Only then create or move the\n            # navigation catalog, otherwise Telegram places it before later posts.\n            changes = await super().publish(pages)\n'''
c = rep(c, needle, replacement, "layout repair")
c = rep(
    c,
    '            return await super().set_first_message(text)\n',
    '            changes = await super().set_first_message(text)\n            # A freshly created/edited intro already has the correct layout; do not\n            # rebuild and pin it again on the immediately following publish.\n            self.state.set("first_message_layout_version", 2)\n            return changes\n',
    "fresh intro layout version",
)
cp.write_text(c, encoding="utf-8")


# 3) Regression tests from the screenshots and Samsung series behavior.
tp = Path("tests/test_prices.py")
t = tp.read_text(encoding="utf-8")nt = rep(
    t,
    '        self.assertEqual(result.items[0].block, "Samsung")\n',
    '        self.assertEqual(result.items[0].block, "Samsung Galaxy S")\n',
    "existing Samsung S expectation",
)
insert = '''\n    def test_note_14s_and_rode_do_not_inherit_dji_section(self):\n        items = self.parse("""DJI / Insta360\nNote 14S 8/256 Aurora Purple 🇪🇺 — 17800\nNote 14S 8/256 Midnight Black 🇪🇺 — 17800\nRODE Wireless Me Dual Set 🇷🇺 — 12700\nRODE Wireless Pro 🇷🇺 — 23300\nDJI Osmo Pocket 4 Creator Combo — 44800\nInsta360 X6 Standard Bundle — 51100""").items\n        self.assertEqual([item.block for item in items], [\n            "Xiaomi", "Xiaomi", "Rode", "Rode", "DJI / Insta360", "DJI / Insta360"\n        ])\n        self.assertTrue(items[0].title.startswith("Note 14S"))\n        self.assertTrue(items[2].title.startswith("RODE Wireless"))\n        self.assertNotIn("DJI / Insta360 Note", items[0].title)\n        self.assertNotIn("DJI / Insta360 RODE", items[2].title)\n\n    def test_samsung_series_are_read_and_split_into_catalog_blocks(self):\n        items = self.parse("""Samsung\nGalaxy A\nA27 8/256 Black 🇪🇺 — 24000\nA56 8/256 Awesome Graphite 🇪🇺 — 35000\nA57 12/256 Blue 🇪🇺 — 42000\nGalaxy S\nS26 12/256 Black 🇦🇪 — 65000\nS26+ 12/256 Cobalt Violet 🇦🇪 — 68300\nS26 Ultra 12/512 Titanium Black 🇦🇪 — 99000\nGalaxy Z\nZ Fold 7 12/256 Black 🇦🇪 — 120000\nZ Flip 7 12/256 Mint 🇦🇪 — 78000""").items\n        self.assertEqual([item.block for item in items], [\n            "Samsung Galaxy A", "Samsung Galaxy A", "Samsung Galaxy A",\n            "Samsung Galaxy S", "Samsung Galaxy S", "Samsung Galaxy S",\n            "Samsung Fold / Flip", "Samsung Fold / Flip",\n        ])\n        pages = render_blocks(items, Settings())\n        headers = [content.split("\\n", 1)[0] for content in pages.values()]\n        self.assertIn("<b>Samsung Galaxy A</b>", headers)\n        self.assertIn("<b>Samsung Galaxy S</b>", headers)\n        self.assertIn("<b>Samsung Fold / Flip</b>", headers)\n'''
marker = '\n    def test_preserve_explicit_condition_and_original_packaging(self):\n'
if marker not in t:
    raise SystemExit("price test marker missing")
t = t.replace(marker, insert + marker, 1)
tp.write_text(t, encoding="utf-8")


ctp = Path("tests/test_catalog.py")
ct = ctp.read_text(encoding="utf-8")
insert_catalog = '''\n    async def test_saved_first_message_is_rebuilt_before_prices_after_layout_repair(self):\n        await self.publisher.ensure_target()\n        binding = self.publisher.binding()\n        self.state.set("first_message_text", "Гарантия и выдача")\n        self.state.set("first_message", {\n            "binding": binding, "chat_id": -100777, "id": 50,\n            "hash": "old", "text": "Гарантия и выдача", "pinned": True,\n        })\n        self.state.set("catalog", {\n            "binding": binding, "messages": [{"id": 50, "hash": "catalog", "pinned": True}],\n        })\n        self.state.set("published", {\n            "binding": binding, "messages": {"old:0": {"id": 60, "hash": "x", "content": "old"}},\n        })\n\n        await self.publisher.publish(self.pages())\n\n        first = self.state.get("first_message")["id"]\n        price_ids = [entry["id"] for entry in self.state.get("published")["messages"].values()]\n        catalog = self.state.get("catalog")["messages"][0]["id"]\n        self.assertLess(first, min(price_ids))\n        self.assertGreater(catalog, max(price_ids))\n        self.assertEqual(self.state.get("first_message_layout_version"), 2)\n        self.assertTrue(any(method == "pinChatMessage" and payload.get("message_id") == first\n                            for method, payload in self.publisher.calls))\n\n    async def test_samsung_series_get_separate_working_catalog_buttons(self):\n        items = parse_documents(["""Samsung\nA56 8/256 Black — 35000\nS26 12/256 Black — 65000\nZ Fold 7 12/256 Black — 120000"""]).items\n        pages = render_blocks(items, Settings())\n        await self.publisher.publish(pages)\n        edit = self.catalog_edit()\n        buttons = [button for row in edit["reply_markup"]["inline_keyboard"] for button in row]\n        samsung = [button for button in buttons if button["text"].startswith("Samsung")]\n        self.assertEqual([button["text"] for button in samsung], [\n            "Samsung Galaxy A", "Samsung Galaxy S", "Samsung Fold / Flip"\n        ])\n        self.assertTrue(all(button.get("url") for button in samsung))\n'''
marker2 = '\n    async def test_closed_catalog_keeps_buttons_and_removes_prices(self):\n'
if marker2 not in ct:
    raise SystemExit("catalog test marker missing")
ct = ct.replace(marker2, insert_catalog + marker2, 1)
ctp.write_text(ct, encoding="utf-8")
