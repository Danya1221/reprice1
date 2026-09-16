from pathlib import Path


def replace_once(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected 1 match, got {count}")
    return text.replace(old, new, 1)


p = Path("prices.py")
s = p.read_text(encoding="utf-8")

s = replace_once(
    s,
    'ACCESSORY = re.compile(r"акс(?:ессуар|ис)|чехол|стекло|кабел[ья]|кабель|адаптер|зарядк|"\n                       r"ремеш|бампер|case\\b|charger\\b|cable\\b", re.I)\n',
    'ACCESSORY = re.compile(r"акс(?:ессуар|ис)|чехол|стекло|кабел[ья]|кабель|адаптер|[АA]dapter|переходник|зарядк|"\n                       r"ремеш|бампер|case\\b|charger\\b|cable\\b", re.I)\n',
    "accessory aliases",
)

marker = 'def product_block(title):\n'
helper = '''def apple_computer_block(title):
    """Recognize supplier MacBook rows even when the word MacBook is omitted."""
    plain = FLAGS.sub("", clean(title)).strip()
    if re.search(r"\\b(?:MacBook|iMac)\\b", plain, re.I):
        return "MacBook / iMac"

    # Supplier examples: Neo 13 MHFD4 ... (A18 Pro 8/256),
    # Air 13/15 MDH... (M5 ...), Pro 14/16 ... (M4/M5 Pro ...).
    sku = r"[A-Z0-9]{4,}"
    chip = r"(?:A18\\s*Pro|M\\d+(?:\\s*(?:Pro|Max))?)"
    if re.search(r"^(?:Neo|Air)\\s+(?:13|15)\\s+" + sku + r"\\b.*\\(" + chip + r"\\b", plain, re.I):
        return "MacBook / iMac"
    if re.search(r"^Pro\\s+(?:14|16)\\s+" + sku + r"\\b.*\\(" + chip + r"\\b", plain, re.I):
        return "MacBook / iMac"
    return ""


def apple_accessory_block(title):
    """Apple power adapters / MacBook transitions belong to the common Apple post."""
    plain = FLAGS.sub("", clean(title)).strip()
    mixed_adapter = r"(?:Adapter|Аdapter|адаптер)"
    if "" in title and re.search(mixed_adapter, plain, re.I):
        return "Apple Accessories"
    if re.search(r"\\bпереходник\\s+для\\s+MacBook\\b", plain, re.I):
        return "Apple Accessories"
    if re.search(r"^" + mixed_adapter + r"\\s+(?:universal|20W|USB[ -]?C\\s+to\\s+USB)\\b", plain, re.I):
        return "Apple Accessories"
    return ""


'''
if marker not in s:
    raise SystemExit("product_block marker missing")
s = s.replace(marker, helper + marker, 1)

s = replace_once(
    s,
    '''def product_block(title):
    watch = apple_watch_block(title)
    if watch:
        return watch
    samsung = samsung_block(title)
    if samsung:
        return samsung
    if re.search(r"\\b(?:MacBook|iMac)\\b", title, re.I):
        return "MacBook / iMac"
    for label in ("AirPods", "iPad", "Mac mini", "Mac Studio", "Apple TV", "AirTag"):
        if re.search(r"\\b" + re.escape(label) + r"\\b", title, re.I):
            return label
    return brand_of(title)
''',
    '''def product_block(title):
    watch = apple_watch_block(title)
    if watch:
        return watch
    samsung = samsung_block(title)
    if samsung:
        return samsung
    computer = apple_computer_block(title)
    if computer:
        return computer
    apple_accessory = apple_accessory_block(title)
    if apple_accessory:
        return apple_accessory
    for label in ("AirPods", "iPad", "Mac mini", "Mac Studio", "Apple TV", "AirTag"):
        if re.search(r"\\b" + re.escape(label) + r"\\b", title, re.I):
            return label
    return brand_of(title)
''',
    "product block",
)

s = replace_once(
    s,
    '    elif context and not brand_of(title):\n',
    '    elif context and not brand_of(title) and not apple_computer_block(title) and not apple_accessory_block(title):\n',
    "context inheritance guard",
)

s = replace_once(
    s,
    '''        own_brand = product_block(title)
        accessory = bool(ACCESSORY.search(title)) or (section == "Аксессуары" and not own_brand)
        model = iphone_model(title)
''',
    '''        own_brand = product_block(title)
        apple_accessory = own_brand == "Apple Accessories"
        accessory = (bool(ACCESSORY.search(title)) or (section == "Аксессуары" and not own_brand)) and not apple_accessory
        model = iphone_model(title)
''',
    "apple accessory selection",
)

s = replace_once(
    s,
    '    defaults = ["iPhone", "AirPods", "Apple Watch", "iPad", "MacBook / iMac", "Mac mini", "Mac Studio",\n                "Apple TV", "AirTag", "Apple", "Ray-Ban Meta", "Samsung", "Honor", "Realme", "Huawei", "Tecno",\n',
    '    defaults = ["iPhone", "AirPods", "Apple Accessories", "Mac mini", "Apple TV", "AirTag",\n                "Apple Watch", "iPad", "MacBook / iMac", "Mac Studio", "Apple", "Ray-Ban Meta", "Samsung", "Honor", "Realme", "Huawei", "Tecno",\n',
    "apple ordering",
)

s = replace_once(
    s,
    '''    for block in names:
        lines = render_block_lines(block, groups[block], settings, overrides, closed=closed)
        logical_sections.extend(section_chunks(block, lines))
''',
    '''    for block in names:
        lines = render_block_lines(block, groups[block], settings, overrides, closed=closed)
        # Apple has many logical subcategories. Smaller chunks let the physical
        # packer fill Apple posts with several sections instead of orphaning a
        # one-line Mac mini or Apple TV message after a nearly-full MacBook page.
        chunk_limit = 1800 if physical_section_family(block) == "apple" else 3200
        logical_sections.extend(section_chunks(block, lines, limit=chunk_limit))
''',
    "apple section chunking",
)

p.write_text(s, encoding="utf-8")

# Regression coverage for the exact supplier format and physical Apple packing.
tp = Path("tests/test_prices.py")
t = tp.read_text(encoding="utf-8")
marker = '\n    def test_single_samsung_section_still_has_samsung_as_top_heading(self):\n'
extra = r'''
    def test_supplier_macbook_shorthand_and_apple_adapters_are_read(self):
        items = self.parse("""Mac Mini (MU9D3) M4/16/256 Silver - 68500
Neo 13 MHFD4 Citrus (A18 Pro 8/256) - 61100
Air 13 MDHE4 Midnight (M5 16/512) - 127100
Air 15 MDVE4 Starlight (M5 16/1TB) - 154200
Pro 14 Z1KH1 Space Black (M5 24/512GB) 🇺🇸 - 183500
Pro 16 MX2Y3 Space Black (M4 Pro 48/512GB) - 249000
 Adapter 20W 🇪🇺 - 900
(От 20 шт) - 850
Аdapter universal - 100
Переходник для MacBook - 100
 Аdapter USB-C to USB - 1300
Apple TV 4K 64GB (2022) 🇺🇸 - 20200""").items
        blocks = [item.block for item in items]
        self.assertIn("Mac mini", blocks)
        self.assertGreaterEqual(blocks.count("MacBook / iMac"), 5)
        self.assertGreaterEqual(blocks.count("Apple Accessories"), 4)
        self.assertIn("Apple TV", blocks)
        self.assertFalse(any(item.title.startswith("От 20") for item in items))

    def test_small_apple_sections_are_not_orphaned_into_single_model_posts(self):
        source = "\n".join([
            *[f"AirPods Pro 3 Variant {i} — {20000 + i}" for i in range(45)],
            "Mac Mini (MU9D3) M4/16/256 Silver — 68500",
            "Apple TV 4K 64GB (2022) — 20200",
            " Adapter 20W — 900",
            "Переходник для MacBook — 100",
            *[f"Air 13 MDH{i:02d} Midnight (M5 16/512) — {127000 + i}" for i in range(35)],
        ])
        items = self.parse(source).items
        pages = list(render_blocks(items, Settings()).values())
        apple_pages = [page for page in pages if page.startswith("<b>Apple</b>")]
        self.assertGreaterEqual(len(apple_pages), 2)
        mini_page = next(page for page in apple_pages if "— Mac mini —" in page)
        tv_page = next(page for page in apple_pages if "— Apple TV —" in page)
        self.assertIs(mini_page, tv_page)
        self.assertIn("— Apple Accessories —", mini_page)
        self.assertTrue(all(units(page) <= 4096 for page in pages))

'''
if marker not in t:
    raise SystemExit("test insertion marker missing")
t = t.replace(marker, extra + marker, 1)
tp.write_text(t, encoding="utf-8")
