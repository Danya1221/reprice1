from pathlib import Path


def rep(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected 1 match, got {count}")
    return text.replace(old, new, 1)

p = Path("catalog_publisher.py")
s = p.read_text(encoding="utf-8")

old = '''def catalog_labels(content):
    """Buttons represented by one physical Telegram price message."""
    # Major ecosystems are defined by the final physical post heading.  Do not
    # let an old logical section name (Mac mini, Apple TV, Samsung S26, etc.)
    # leak back into the public catalog buttons.
    title = page_title(content)
    if title.casefold().strip() == "apple":
        return ["Apple"]
    if title.casefold().strip() == "samsung":
        return ["Samsung"]

    plain = html.unescape(re.sub(r"<[^>]+>", " ", content))
    iphone_pattern = re.compile(
        r"\\biPhone\\s+(?:Air|\\d{1,2}e?(?:\\s+(?:Plus|Pro(?:\\s+Max)?))?)\\b",
        re.I,
    )
    iphones = []
    for raw in iphone_pattern.findall(plain):
        canonical = iphone_model_label(raw)
        if canonical and canonical not in iphones:
            iphones.append(canonical)
    if iphones:
        return iphones

    labels = []
    for part in [piece.strip() for piece in title.split("•") if piece.strip()]:
        group = catalog_group(part)
        if group not in labels:
            labels.append(group)
    return labels or [catalog_group(title)]
'''
new = '''def iphone_catalog_group(title):
    """One catalog button per physical iPhone generation message."""
    name = re.sub(r"\\s+", " ", title).casefold().strip()
    if not name.startswith("iphone"):
        return ""
    if re.search(r"\\b(?:11|12|13|14|15)\\b", name):
        return "iPhone 11–15"
    if re.search(r"\\b16(?:e)?\\b", name):
        return "iPhone 16"
    if "air" in name or re.search(r"\\b17(?:e)?\\b", name):
        return "iPhone 17"
    return "iPhone"


def catalog_labels(content):
    """Buttons represented by one physical Telegram price message."""
    # Navigation follows physical Telegram posts, not every logical model inside
    # them.  This is what keeps the catalog compact after iPhone bundling.
    title = page_title(content)
    iphone_group = iphone_catalog_group(title)
    if iphone_group:
        return [iphone_group]
    if title.casefold().strip() == "apple":
        return ["Apple"]
    if title.casefold().strip() == "samsung":
        return ["Samsung"]

    labels = []
    for part in [piece.strip() for piece in title.split("•") if piece.strip()]:
        group = catalog_group(part)
        if group not in labels:
            labels.append(group)
    return labels or [catalog_group(title)]
'''
s = rep(s, old, new, "compact physical iPhone catalog labels")

old = '''def catalog_group(title):
    """Keep iPhone models directly navigable while grouping the rest compactly."""
    name = title.casefold().strip()
    if name.startswith("iphone"):
        return title.strip()
'''
new = '''def catalog_group(title):
    """Map physical post headings to a small public navigation set."""
    name = title.casefold().strip()
    if name.startswith("iphone"):
        return iphone_catalog_group(title)
'''
s = rep(s, old, new, "catalog group iPhone generations")

old = '''        batches = [buttons[start:start + 80] for start in range(0, len(buttons), 80)] or [[]]
'''
new = '''        priority = {
            "iPhone 11–15": 0,
            "iPhone 16": 1,
            "iPhone 17": 2,
            "Apple": 3,
            "Samsung": 4,
            "Смартфоны": 5,
            "Часы / носимое": 6,
            "Аудио": 7,
            "Фото / видео": 8,
            "Игры": 9,
            "Другое": 10,
        }
        buttons.sort(key=lambda button: (priority.get(button["text"], 100), button["text"].casefold()))
        batches = [buttons[start:start + 80] for start in range(0, len(buttons), 80)] or [[]]
'''
s = rep(s, old, new, "fixed compact catalog ordering")

s = rep(
    s,
    '            force_catalog = int(self.state.get("catalog_layout_version", 0) or 0) < 3\n',
    '            force_catalog = int(self.state.get("catalog_layout_version", 0) or 0) < 4\n',
    "catalog version bump",
)
s = rep(
    s,
    '                self.state.set("catalog_layout_version", 3)\n',
    '                self.state.set("catalog_layout_version", 4)\n',
    "catalog version save",
)
p.write_text(s, encoding="utf-8")

p = Path("tests/test_catalog.py")
t = p.read_text(encoding="utf-8")
old = '''        for model in ["iPhone 16", "iPhone 16 Plus", "iPhone 16 Pro"]:
            self.assertIn(model, labels)
        self.assertTrue({"Apple", "Samsung", "Смартфоны", "Часы / носимое", "Аудио", "Фото / видео", "Игры", "Другое"}.issubset(set(labels)))
'''
new = '''        self.assertIn("iPhone 16", labels)
        self.assertNotIn("iPhone 16 Plus", labels)
        self.assertNotIn("iPhone 16 Pro", labels)
        self.assertTrue({"Apple", "Samsung", "Смартфоны", "Часы / носимое", "Аудио", "Фото / видео", "Игры", "Другое"}.issubset(set(labels)))
'''
t = rep(t, old, new, "compact catalog base test")

old = '''    async def test_paired_iphone_models_have_separate_buttons_to_same_message(self):
        items = parse_documents(["iPhone 16 128 Black — 60000\\niPhone 16 Plus 128 Pink — 70000"]).items
        await self.publisher.publish(render_blocks(items, Settings()))
        manifest = self.state.get("published")["messages"]
        self.assertEqual(len(manifest), 1)
        message_id = next(iter(manifest.values()))["id"]
        buttons = [button for row in self.catalog_edit()["reply_markup"]["inline_keyboard"] for button in row]
        iphone_buttons = {button["text"]: button["url"] for button in buttons if button["text"].startswith("iPhone 16")}
        self.assertEqual(set(iphone_buttons), {"iPhone 16", "iPhone 16 Plus"})
        self.assertEqual(set(iphone_buttons.values()), {f"https://t.me/c/777/{message_id}"})
'''
new = '''    async def test_paired_iphone_models_share_one_generation_button(self):
        items = parse_documents(["iPhone 16 128 Black — 60000\\niPhone 16 Plus 128 Pink — 70000"]).items
        await self.publisher.publish(render_blocks(items, Settings()))
        manifest = self.state.get("published")["messages"]
        self.assertEqual(len(manifest), 1)
        message_id = next(iter(manifest.values()))["id"]
        buttons = [button for row in self.catalog_edit()["reply_markup"]["inline_keyboard"] for button in row]
        iphone_buttons = [button for button in buttons if button["text"].startswith("iPhone")]
        self.assertEqual([button["text"] for button in iphone_buttons], ["iPhone 16"])
        self.assertEqual(iphone_buttons[0]["url"], f"https://t.me/c/777/{message_id}")
'''
t = rep(t, old, new, "paired iPhone button test")

marker = '    async def test_closed_catalog_keeps_buttons_and_removes_prices(self):\n'
extra = '''    async def test_many_iphone_models_produce_only_three_ordered_buttons(self):
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
        await self.publisher.publish(render_blocks(items, Settings()))
        buttons = [button for row in self.catalog_edit()["reply_markup"]["inline_keyboard"] for button in row]
        labels = [button["text"] for button in buttons]
        self.assertEqual(labels[:5], ["iPhone 11–15", "iPhone 16", "iPhone 17", "Apple", "Samsung"])
        self.assertFalse(any(label in labels for label in ["iPhone 16 Plus", "iPhone 17e", "iPhone Air", "iPhone 15 Pro Max"]))

'''
if marker not in t:
    raise SystemExit("catalog insertion marker missing")
t = t.replace(marker, extra + marker, 1)
p.write_text(t, encoding="utf-8")
