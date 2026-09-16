from pathlib import Path


def rep(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected 1 match, got {count}")
    return text.replace(old, new, 1)

# prices.py: every iPhone model gets its own publication block.
p = Path("prices.py")
s = p.read_text(encoding="utf-8")
s = rep(
    s,
    '''def iphone_publish_block(model):
    if not model:
        return ""
    match = re.match(r"iPhone\\s+(\\d{1,2})\\b", model, re.I)
    if match and 11 <= int(match.group(1)) <= 15:
        return "iPhone 11–15"
    return model
''',
    '''def iphone_publish_block(model):
    """Publish every iPhone model/variant as its own Telegram block."""
    return model or ""
''',
    "iPhone publication blocks",
)
p.write_text(s, encoding="utf-8")

# catalog_publisher.py: individual iPhone model buttons, compact groups for the rest.
cp = Path("catalog_publisher.py")
c = cp.read_text(encoding="utf-8")
c = rep(
    c,
    '''def catalog_group(title):
    """Collapse many physical price blocks into a small customer-facing catalog."""
    name = title.casefold().strip()
    if name.startswith(("iphone", "apple watch", "airpods", "ipad", "macbook", "mac mini", "mac studio", "apple tv", "apple", "cpo", "asis")):
        return "Apple"
''',
    '''def catalog_group(title):
    """Keep iPhone models directly navigable while grouping the rest compactly."""
    name = title.casefold().strip()
    if name.startswith("iphone"):
        return title.strip()
    if name.startswith(("apple watch", "airpods", "ipad", "macbook", "mac mini", "mac studio", "apple tv", "apple", "cpo", "asis")):
        return "Apple"
''',
    "iPhone catalog buttons",
)
c = c.replace(
    'CATALOG_TEXT = "🗂 КАТАЛОГ — выбери категорию\\nНажми на категорию — перейдёшь к началу нужной части прайса.\\nСкопируй позицию вместе с ценой и пришли её менеджеру."',
    'CATALOG_TEXT = "🗂 КАТАЛОГ — выбери модель или категорию\\nНажми на кнопку — перейдёшь к нужной части прайса.\\nСкопируй позицию вместе с ценой и пришли её менеджеру."',
)
cp.write_text(c, encoding="utf-8")

# Regression tests for parsing/rendering.
rp = Path("tests/test_parser_regressions.py")
r = rp.read_text(encoding="utf-8")
old = '''    def test_iphone_11_to_15_are_one_publication_block(self):
        items = self.parse("""iPhone: 13-14-15
🇮🇳 13 128GB Midnight - 45100
🇺🇸 14 128GB Midnight - 46400
🇮🇳 15 128GB Black - 55400
🇮🇳 15 Plus 128GB Pink - 61400
🇦🇪 15 Pro 128GB Blue - 83600
iPhone 12 128 Black — 40000
iPhone 11 Pro Max 256 Green — 39000""").items
        self.assertEqual({item.block for item in items}, {"iPhone 11–15"})
        self.assertTrue(any("iPhone 15 Pro" in item.title for item in items))
        pages = render_blocks(items, Settings())
        self.assertTrue(all(page.startswith("<b>iPhone 11–15</b>") for page in pages.values()))
'''
new = '''    def test_iphone_11_to_15_publish_as_individual_models(self):
        items = self.parse("""iPhone: 13-14-15
🇮🇳 13 128GB Midnight - 45100
🇺🇸 14 128GB Midnight - 46400
🇮🇳 15 128GB Black - 55400
🇮🇳 15 Plus 128GB Pink - 61400
🇦🇪 15 Pro 128GB Blue - 83600
iPhone 12 128 Black — 40000
iPhone 11 Pro Max 256 Green — 39000""").items
        self.assertEqual({item.block for item in items}, {
            "iPhone 11 Pro Max", "iPhone 12", "iPhone 13", "iPhone 14",
            "iPhone 15", "iPhone 15 Plus", "iPhone 15 Pro",
        })
        pages = render_blocks(items, Settings())
        headers = {page.split("\\n", 1)[0] for page in pages.values()}
        self.assertIn("<b>iPhone 13</b>", headers)
        self.assertIn("<b>iPhone 15 Plus</b>", headers)
        self.assertIn("<b>iPhone 15 Pro</b>", headers)
'''
r = rep(r, old, new, "iPhone 11-15 regression")
rp.write_text(r, encoding="utf-8")

# Price tests: old combined-block spacing test becomes a per-model + activation test.
tp = Path("tests/test_prices.py")
t = tp.read_text(encoding="utf-8")
old = '''    def test_iphone_combined_block_has_model_gaps(self):
        items = self.parse("""iPhone: 13-14-15
13 128GB Midnight — 45100
13 256GB Blue — 50000
14 128GB Midnight — 46400
15 128GB Black — 55400""").items
        page = next(iter(render_blocks(items, Settings()).values()))
        self.assertIn("iPhone 13 256GB Blue — 50 000</code>\\n\\n<code>iPhone 14", page)
        self.assertIn("iPhone 14 128GB Midnight — 46 400</code>\\n\\n<code>iPhone 15", page)
'''
new = '''    def test_iphone_models_are_separate_and_keep_activation_sections(self):
        items = self.parse("""iPhone 16
16 128GB Black 🇮🇳 — 63700
16 128GB Black 🇮🇳 Актив — 60800
16 Plus 128GB Black 🇮🇳 — 73700
16 Plus 128GB Pink 🇮🇳 Актив — 72000
16 Pro 256GB Natural 🇦🇪 — 83600""").items
        self.assertEqual({item.block for item in items}, {"iPhone 16", "iPhone 16 Plus", "iPhone 16 Pro"})
        pages = render_blocks(items, Settings())
        iphone16 = next(page for page in pages.values() if page.startswith("<b>iPhone 16</b>"))
        iphone16plus = next(page for page in pages.values() if page.startswith("<b>iPhone 16 Plus</b>"))
        self.assertIn("— Не активированное —", iphone16)
        self.assertIn("— Актив —", iphone16)
        self.assertIn("— Не активированное —", iphone16plus)
        self.assertIn("— Актив —", iphone16plus)
'''
t = rep(t, old, new, "iPhone rendering test")
tp.write_text(t, encoding="utf-8")

# Catalog tests: iPhones are direct buttons; other families remain compact.
cat = Path("tests/test_catalog.py")
ct = cat.read_text(encoding="utf-8")
ct = ct.replace(
    'self.assertEqual([b["text"] for b in buttons], ["Другое", "Apple"])',
    'self.assertEqual([b["text"] for b in buttons], ["Другое", "Apple", "iPhone 17"])',
)
old = '''    async def test_catalog_has_at_most_eight_customer_categories(self):
        items = parse_documents(["""iPhone 17 256 Black — 60000
Apple Watch Ultra 3 49mm Black — 70000
Samsung A57 8/256 Blue — 32000
Xiaomi 15 12/256 White — 48000
Oura Ring 4 Silver — 35000
Bose Onyx 9 Black — 30000
DJI Osmo Pocket 4 — 45000
PlayStation 5 Pro — 70000
Dyson HS08 — 40000"""]).items
        await self.publisher.publish(render_blocks(items, Settings()))
        buttons = [button for row in self.catalog_edit()["reply_markup"]["inline_keyboard"] for button in row]
        self.assertLessEqual(len(buttons), 8)
        self.assertEqual({button["text"] for button in buttons}, {
            "Apple", "Samsung", "Смартфоны", "Часы / носимое", "Аудио", "Фото / видео", "Игры", "Другое"
        })
'''
new = '''    async def test_catalog_keeps_base_groups_compact_and_iphone_models_direct(self):
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
        await self.publisher.publish(render_blocks(items, Settings()))
        buttons = [button for row in self.catalog_edit()["reply_markup"]["inline_keyboard"] for button in row]
        labels = [button["text"] for button in buttons]
        for model in ["iPhone 16", "iPhone 16 Plus", "iPhone 16 Pro"]:
            self.assertIn(model, labels)
        self.assertTrue({"Apple", "Samsung", "Смартфоны", "Часы / носимое", "Аудио", "Фото / видео", "Игры", "Другое"}.issubset(set(labels)))
'''
ct = rep(ct, old, new, "catalog iPhone direct buttons test")
cat.write_text(ct, encoding="utf-8")
