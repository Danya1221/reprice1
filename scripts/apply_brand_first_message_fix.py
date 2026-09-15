from pathlib import Path


def rep(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected 1 match, got {count}")
    return text.replace(old, new, 1)


# 1) Product classification: keep Xiaomi Note 14S and RODE out of DJI context.
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
s = rep(
    s,
    '                "Samsung", "Honor", "Realme", "Huawei", "Tecno", "Xiaomi", "Google", "Dyson",\n                "Oura Ring", "CPO", "ASIS", "Аксессуары", "Товары"]',
    '                "Samsung", "Honor", "Realme", "Huawei", "Tecno", "Xiaomi", "Google", "Rode", "Dyson",\n                "Oura Ring", "CPO", "ASIS", "Аксессуары", "Товары"]',
    "Rode order",
)
p.write_text(s, encoding="utf-8")


# 2) Catalog/first-message layout repair. One time per persisted state, rebuild the
# managed sequence as: custom first message -> prices -> catalog.
cp = Path("catalog_publisher.py")
c = cp.read_text(encoding="utf-8")
needle = '''    async def publish(self, pages):\n        async with self.layout_lock:\n            await self.ensure_target()\n            await self._ensure_first_message()\n            # First publish/reorder every price page. Only then create or move the\n            # navigation catalog, otherwise Telegram places it before later posts.\n            changes = await super().publish(pages)\n'''
replacement = '''    async def _repair_first_message_layout_once(self):\n        text = str(self.state.get("first_message_text", "") or "").strip()\n        if not text or self.state.get("first_message_layout_version", 0) >= 2:\n            return\n\n        # A previous catalog implementation could share/delete the intro message ID.\n        # Rebuild the managed sequence once from persistent text so the intro is\n        # guaranteed to exist, be pinned, and physically precede every price post.\n        records = self.catalog_records()\n        first = self.state.get("first_message", {}) or {}\n        ids = []\n        if first.get("id"):\n            ids.append(int(first["id"]))\n        ids.extend(int(record["id"]) for record in records if record.get("id"))\n        for message_id in dict.fromkeys(ids):\n            try:\n                await self._delete(message_id)\n            except RuntimeError as exc:\n                if "message to delete not found" not in str(exc).lower():\n                    raise\n        self.save_catalog([])\n        self.state.set("first_message", {})\n        await self._clear_managed_price_posts()\n        self.state.set("first_message_layout_version", 2)\n\n    async def publish(self, pages):\n        async with self.layout_lock:\n            await self.ensure_target()\n            await self._repair_first_message_layout_once()\n            await self._ensure_first_message()\n            # First publish/reorder every price page. Only then create or move the\n            # navigation catalog, otherwise Telegram places it before later posts.\n            changes = await super().publish(pages)\n'''
c = rep(c, needle, replacement, "layout repair")
cp.write_text(c, encoding="utf-8")


# 3) Regression tests from the screenshot.
tp = Path("tests/test_prices.py")
t = tp.read_text(encoding="utf-8")
insert = '''\n    def test_note_14s_and_rode_do_not_inherit_dji_section(self):\n        items = self.parse("""DJI / Insta360\nNote 14S 8/256 Aurora Purple 🇪🇺 — 17800\nNote 14S 8/256 Midnight Black 🇪🇺 — 17800\nRODE Wireless Me Dual Set 🇷🇺 — 12700\nRODE Wireless Pro 🇷🇺 — 23300\nDJI Osmo Pocket 4 Creator Combo — 44800\nInsta360 X6 Standard Bundle — 51100""").items\n        self.assertEqual([item.block for item in items], [\n            "Xiaomi", "Xiaomi", "Rode", "Rode", "DJI / Insta360", "DJI / Insta360"\n        ])\n        self.assertTrue(items[0].title.startswith("Note 14S"))\n        self.assertTrue(items[2].title.startswith("RODE Wireless"))\n        self.assertNotIn("DJI / Insta360 Note", items[0].title)\n        self.assertNotIn("DJI / Insta360 RODE", items[2].title)\n'''
marker = '\n    def test_preserve_explicit_condition_and_original_packaging(self):\n'
if marker not in t:
    raise SystemExit("price test marker missing")
t = t.replace(marker, insert + marker, 1)
tp.write_text(t, encoding="utf-8")


ctp = Path("tests/test_catalog.py")
ct = ctp.read_text(encoding="utf-8")
insert_catalog = '''\n    async def test_saved_first_message_is_rebuilt_before_prices_after_layout_repair(self):\n        await self.publisher.ensure_target()\n        binding = self.publisher.binding()\n        self.state.set("first_message_text", "Гарантия и выдача")\n        self.state.set("first_message", {\n            "binding": binding, "chat_id": -100777, "id": 50,\n            "hash": "old", "text": "Гарантия и выдача", "pinned": True,\n        })\n        self.state.set("catalog", {\n            "binding": binding, "messages": [{"id": 50, "hash": "catalog", "pinned": True}],\n        })\n        self.state.set("published", {\n            "binding": binding, "messages": {"old:0": {"id": 60, "hash": "x", "content": "old"}},\n        })\n\n        await self.publisher.publish(self.pages())\n\n        first = self.state.get("first_message")["id"]\n        price_ids = [entry["id"] for entry in self.state.get("published")["messages"].values()]\n        catalog = self.state.get("catalog")["messages"][0]["id"]\n        self.assertLess(first, min(price_ids))\n        self.assertGreater(catalog, max(price_ids))\n        self.assertEqual(self.state.get("first_message_layout_version"), 2)\n        self.assertTrue(any(method == "pinChatMessage" and payload.get("message_id") == first\n                            for method, payload in self.publisher.calls))\n'''
marker2 = '\n    async def test_closed_catalog_keeps_buttons_and_removes_prices(self):\n'
if marker2 not in ct:
    raise SystemExit("catalog test marker missing")
ct = ct.replace(marker2, insert_catalog + marker2, 1)
ctp.write_text(ct, encoding="utf-8")
