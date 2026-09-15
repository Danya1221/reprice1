from pathlib import Path


def rep(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected 1 match, got {count}")
    return text.replace(old, new, 1)


# ---- prices.py: visual gaps whenever the model changes ----
p = Path("prices.py")
s = p.read_text(encoding="utf-8")

anchor = '''def display_title(item):
'''
helper = r'''def model_group_key(item):
    """A stable model family key used only for visual spacing in rendered price lists."""
    plain = FLAGS.sub("", normal_title(item.title)).strip()

    model = iphone_model(plain)
    if model:
        return model.casefold()

    watch = re.search(r"\b(?:Series\s*\d{1,2}|SE(?:\s*\d{1,2})?|Ultra(?:\s*\d{1,2})?)\b", plain, re.I)
    if watch and re.search(r"\bWatch\b", plain, re.I):
        return ("apple watch " + watch.group(0)).casefold()

    # Most phone/tablet price rows put RAM/storage immediately after the model.
    storage = re.search(r"\b(?:\d{1,2}/\d{2,4}(?:GB|TB)?|\d{2,4}(?:GB|TB))\b", plain, re.I)
    if storage:
        prefix = plain[:storage.start()].strip(" -—–/·")
        if prefix:
            return clean(prefix).casefold()

    known = [
        r"\b(?:Samsung\s+)?Buds\s*\d+(?:\s*FE)?\b",
        r"\b(?:Samsung\s+)?A\s*\d{2,3}[A-Za-z]*\b",
        r"\b(?:Samsung\s+)?S\s*\d{2}(?:\s*(?:FE|Edge|Ultra|\+|Plus))?\b",
        r"\b(?:Samsung\s+)?Tab\s*S\s*\d+(?:\s*Ultra)?\b",
        r"\b(?:Z\s*)?(?:Fold|Flip)\s*\d+\b",
        r"\b(?:Oura\s+Ring|Oura)\s*\d+\b",
        r"\bCoros\s+Pace\s*\d+\b",
        r"\bDJI\s+Osmo\s+(?:Pocket|Action)\s*\d+\b",
        r"\bInsta\s*360\s*[A-Z]*\d+\b|\bInsta360\s*[A-Z]*\d+\b",
        r"\b(?:Ray-Ban\s+Meta\s+)?(?:Wayfarer|Skyler)\b",
        r"\bR(?:O|Ø)DE\s+Wireless\s+(?:Me|Pro|Micro|GO(?:\s*\d+)?)\b",
        r"\b(?:AirPods|iPad)\s+[A-Za-z]*\s*\d+\b",
    ]
    for pattern in known:
        match = re.search(pattern, plain, re.I)
        if match:
            return clean(match.group(0)).casefold()

    # Generic numbered product families: keep through the first real model number,
    # plus a short modifier such as Pro/Ultra/FE/Edge/S3 when present.
    tokens = plain.split()
    for index, token in enumerate(tokens):
        if not re.search(r"\d", token):
            continue
        if index == 0 and re.fullmatch(r"Insta360", token, re.I):
            continue
        end = index + 1
        while end < len(tokens) and end <= index + 2 and re.fullmatch(
            r"(?:Pro|Max|Ultra|Plus|FE|Edge|Mini|Air|S\d+[A-Za-z]*|Gen\d+)", tokens[end], re.I
        ):
            end += 1
        return clean(" ".join(tokens[:end])).casefold()

    # If there is no reliable model signal, keep rows together rather than
    # inserting random gaps based on colour/accessory wording.
    return item.block.casefold()


'''
s = rep(s, anchor, helper + anchor, "model spacing helper")

s = rep(
    s,
    '''                last_sim = None
                last_samsung_section = None
                sorter = samsung_a_s25_sort if block == "Samsung A + S25" else item_sort
''',
    '''                last_sim = None
                last_samsung_section = None
                last_model_key = None
                sorter = samsung_a_s25_sort if block == "Samsung A + S25" else item_sort
''',
    "render state",
)

s = rep(
    s,
    '''                        if samsung_section != last_samsung_section:
                            lines.append("<b>— " + samsung_section + " —</b>")
                            last_samsung_section = samsung_section
''',
    '''                        if samsung_section != last_samsung_section:
                            if last_samsung_section is not None and lines and lines[-1] != "":
                                lines.append("")
                            lines.append("<b>— " + samsung_section + " —</b>")
                            last_samsung_section = samsung_section
''',
    "Samsung section spacing",
)

s = rep(
    s,
    '''                        last_sim = item.sim
                    row = display_title(item) + " — " + price_text(marked_price(item, settings, overrides), item.currency)
''',
    '''                        last_sim = item.sim
                    model_key = model_group_key(item)
                    if (last_model_key is not None and model_key != last_model_key
                            and lines and lines[-1] != "" and not lines[-1].startswith("<b>")):
                        lines.append("")
                    last_model_key = model_key
                    row = display_title(item) + " — " + price_text(marked_price(item, settings, overrides), item.currency)
''',
    "model gap insertion",
)

p.write_text(s, encoding="utf-8")


# ---- catalog_publisher.py: max eight useful category buttons ----
cp = Path("catalog_publisher.py")
c = cp.read_text(encoding="utf-8")
c = rep(
    c,
    'CATALOG_TEXT = "🗂 КАТАЛОГ — выбери раздел\\nНажми на нужный раздел — перейдёшь к прайсу.\\nСкопируй позицию вместе с ценой и пришли её менеджеру."',
    'CATALOG_TEXT = "🗂 КАТАЛОГ — выбери категорию\\nНажми на категорию — перейдёшь к началу нужной части прайса.\\nСкопируй позицию вместе с ценой и пришли её менеджеру."',
    "catalog intro",
)

anchor = '''class CatalogPublisher(PinnedBotAPIPublisher):
'''
helper = r'''def catalog_group(title):
    """Collapse many physical price blocks into a small customer-facing catalog."""
    name = title.casefold().strip()
    if name.startswith(("iphone", "apple watch", "airpods", "ipad", "macbook", "mac mini", "mac studio", "apple tv", "apple", "cpo", "asis")):
        return "Apple"
    if name.startswith("samsung"):
        return "Samsung"
    if name.startswith(("honor", "realme", "huawei", "tecno", "xiaomi", "google", "oneplus", "oppo", "vivo", "nothing", "nubia", "infinix")):
        return "Смартфоны"
    if name.startswith(("oura", "coros", "garmin", "ray-ban")):
        return "Часы / носимое"
    if name.startswith(("bowers", "harman", "bose", "marshall", "jbl", "anker", "rode")):
        return "Аудио"
    if name.startswith(("dji", "insta360", "gopro", "kodak", "fujifilm", "canon", "sony")):
        return "Фото / видео"
    if name.startswith(("nintendo", "playstation", "xbox")):
        return "Игры"
    return "Другое"


'''
c = rep(c, anchor, helper + anchor, "catalog grouping helper")

old_loop = '''        buttons = []
        seen = set()
        for key, content in pages.items():
            block_key = key.rsplit(":", 1)[0]
            if block_key in seen or key not in manifest:
                continue
            seen.add(block_key)
            link = message_link(self.target, manifest[key]["id"])
            if link:
                buttons.append({"text": page_title(content), "url": link})
'''
new_loop = '''        buttons = []
        seen_blocks = set()
        seen_groups = set()
        for key, content in pages.items():
            block_key = key.rsplit(":", 1)[0]
            if block_key in seen_blocks or key not in manifest:
                continue
            seen_blocks.add(block_key)
            group = catalog_group(page_title(content))
            if group in seen_groups:
                continue
            link = message_link(self.target, manifest[key]["id"])
            if link:
                seen_groups.add(group)
                buttons.append({"text": group, "url": link})
'''
c = rep(c, old_loop, new_loop, "compact catalog loop")

c = rep(
    c,
    '            count = max(1, (len({key.rsplit(":", 1)[0] for key in pages}) + 79) // 80)\n',
    '            count = 1\n',
    "single catalog message",
)
cp.write_text(c, encoding="utf-8")


# ---- tests ----
tp = Path("tests/test_prices.py")
t = tp.read_text(encoding="utf-8")
marker = '    def test_preserve_explicit_condition_and_original_packaging(self):\n'
extra = '''    def test_blank_line_is_added_when_model_changes(self):\n        items = self.parse("""A17 6/128GB Gray — 15000\nA17 8/256GB Blue — 17800\nA27 6/128GB Black — 20800\nA27 8/256GB Blue — 23800\nS25 12/256 Navy — 50500\nS25 Ultra 12/256 Black — 65000""").items\n        page = next(value for value in render_blocks(items, Settings()).values() if value.startswith("<b>Samsung A + S25</b>"))\n        self.assertIn("A17 8/256GB Blue — 17 800</code>\\n\\n<code>Samsung A27", page)\n        self.assertIn("<b>— Galaxy S25 —</b>", page)\n        self.assertIn("S25 12/256 Navy — 50 500</code>\\n\\n<code>Samsung S25 Ultra", page)\n\n    def test_iphone_combined_block_has_model_gaps(self):\n        items = self.parse("""iPhone: 13-14-15\n13 128GB Midnight — 45100\n13 256GB Blue — 50000\n14 128GB Midnight — 46400\n15 128GB Black — 55400""").items\n        page = next(iter(render_blocks(items, Settings()).values()))\n        self.assertIn("iPhone 13 256GB Blue — 50 000</code>\\n\\n<code>iPhone 14", page)\n        self.assertIn("iPhone 14 128GB Midnight — 46 400</code>\\n\\n<code>iPhone 15", page)\n\n'''
if marker not in t:
    raise SystemExit("prices test marker missing")
t = t.replace(marker, extra + marker, 1)
tp.write_text(t, encoding="utf-8")

cat = Path("tests/test_catalog.py")
ct = cat.read_text(encoding="utf-8")
ct = rep(
    ct,
    '        self.assertEqual([b["text"] for b in buttons], ["Dyson", "AirPods", "iPhone 17"])\n        self.assertEqual(buttons[0]["url"], f"https://t.me/c/777/{ordered[0][\'id\']}")\n',
    '        self.assertEqual([b["text"] for b in buttons], ["Другое", "Apple"])\n        self.assertEqual(buttons[0]["url"], f"https://t.me/c/777/{ordered[0][\'id\']}")\n',
    "catalog order expectation",
)

old_pagination = '''    async def test_pagination_keeps_all_catalog_sections(self):
        pages = {f"block{i}:0": f"<b>— Device {i} —</b>\\n\\n<code>Device {i} — 1000</code>" for i in range(91)}
        await self.publisher.publish(pages)
        records = self.state.get("catalog")["messages"]
        self.assertEqual(len(records), 2)
        edits = [payload for method, payload in self.publisher.calls if method == "editMessageText" and payload.get("reply_markup")]
        buttons = [b for edit in edits for row in edit["reply_markup"]["inline_keyboard"] for b in row]
        self.assertEqual(sum(b["text"].startswith("Device") for b in buttons), 91)
        self.assertTrue(all(len(row) <= 2 for edit in edits for row in edit["reply_markup"]["inline_keyboard"]))
'''
new_pagination = '''    async def test_catalog_stays_compact_even_with_many_price_blocks(self):
        pages = {f"block{i}:0": f"<b>Device {i}</b>\\n\\n<code>Device {i} — 1000</code>" for i in range(91)}
        await self.publisher.publish(pages)
        records = self.state.get("catalog")["messages"]
        self.assertEqual(len(records), 1)
        edit = self.catalog_edit()
        buttons = [b for row in edit["reply_markup"]["inline_keyboard"] for b in row]
        self.assertEqual([b["text"] for b in buttons], ["Другое"])
        self.assertTrue(all(len(row) <= 2 for row in edit["reply_markup"]["inline_keyboard"]))
'''
ct = rep(ct, old_pagination, new_pagination, "catalog pagination test")

old_samsung = '''    async def test_samsung_series_get_separate_working_catalog_buttons(self):
        items = parse_documents(["""Samsung
A56 8/256 Black — 35000
S26 12/256 Black — 65000
Z Fold 7 12/256 Black — 120000"""]).items
        pages = render_blocks(items, Settings())
        await self.publisher.publish(pages)
        edit = self.catalog_edit()
        buttons = [button for row in edit["reply_markup"]["inline_keyboard"] for button in row]
        samsung = [button for button in buttons if button["text"].startswith("Samsung")]
        self.assertEqual([button["text"] for button in samsung], [
            "Samsung A + S25", "Samsung S26", "Samsung Fold / Flip"
        ])
        self.assertTrue(all(button.get("url") for button in samsung))
'''
new_samsung = '''    async def test_samsung_series_share_one_compact_catalog_button(self):
        items = parse_documents(["""Samsung
A56 8/256 Black — 35000
S26 12/256 Black — 65000
Z Fold 7 12/256 Black — 120000"""]).items
        pages = render_blocks(items, Settings())
        await self.publisher.publish(pages)
        edit = self.catalog_edit()
        buttons = [button for row in edit["reply_markup"]["inline_keyboard"] for button in row]
        samsung = [button for button in buttons if button["text"] == "Samsung"]
        self.assertEqual(len(samsung), 1)
        self.assertTrue(samsung[0].get("url"))
'''
ct = rep(ct, old_samsung, new_samsung, "Samsung compact catalog test")

marker = '    async def test_closed_catalog_keeps_buttons_and_removes_prices(self):\n'
extra = '''    async def test_catalog_has_at_most_eight_customer_categories(self):\n        items = parse_documents(["""iPhone 17 256 Black — 60000\nApple Watch Ultra 3 49mm Black — 70000\nSamsung A57 8/256 Blue — 32000\nXiaomi 15 12/256 White — 48000\nOura Ring 4 Silver — 35000\nBose Onyx 9 Black — 30000\nDJI Osmo Pocket 4 — 45000\nPlayStation 5 Pro — 70000\nDyson HS08 — 40000"""]).items\n        await self.publisher.publish(render_blocks(items, Settings()))\n        buttons = [button for row in self.catalog_edit()["reply_markup"]["inline_keyboard"] for button in row]\n        self.assertLessEqual(len(buttons), 8)\n        self.assertEqual({button["text"] for button in buttons}, {\n            "Apple", "Samsung", "Смартфоны", "Часы / носимое", "Аудио", "Фото / видео", "Игры", "Другое"\n        })\n\n'''
if marker not in ct:
    raise SystemExit("catalog test marker missing")
ct = ct.replace(marker, extra + marker, 1)
cat.write_text(ct, encoding="utf-8")
