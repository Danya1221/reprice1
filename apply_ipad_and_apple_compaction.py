from pathlib import Path


def replace_once(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected 1 match, got {count}")
    return text.replace(old, new, 1)

p = Path("prices.py")
s = p.read_text(encoding="utf-8")

marker = '\ndef apple_computer_block(title):\n'
helper = r'''
def apple_ipad_block(title):
    """Recognize supplier iPad rows that omit the word iPad."""
    plain = FLAGS.sub("", clean(title)).strip()
    if re.search(r"\bApple\s+Pencil\b", plain, re.I):
        return "Apple Accessories"
    if re.search(r"\biPad\b", plain, re.I):
        return "iPad"
    # Supplier examples:
    # MINI 7 128 ... Wi-Fi
    # AIR 11/13 M3/M4 128/256 ... Wi-Fi
    # PRO 11/13 M4/M5 256/1TB ... Wi-Fi/LTE
    # PRO 12.9 M2 128 ... LTE
    if re.search(r"^MINI\s+\d+\s+(?:\d{2,4}|\d+TB)\b.*\b(?:Wi[ -]?Fi|LTE|Cellular)\b", plain, re.I):
        return "iPad"
    if re.search(r"^(?:AIR|PRO)\s+(?:11|12\.9|13)\s+M\d+\s+(?:\d{2,4}|\d+TB)\b.*\b(?:Wi[ -]?Fi|LTE|Cellular)\b", plain, re.I):
        return "iPad"
    return ""

'''
if marker not in s:
    raise SystemExit("apple computer marker missing")
s = s.replace(marker, helper + marker, 1)

old = '''    apple_accessory = apple_accessory_block(title)
    if apple_accessory:
        return apple_accessory
    computer = apple_computer_block(title)
'''
new = '''    ipad = apple_ipad_block(title)
    if ipad:
        return ipad
    apple_accessory = apple_accessory_block(title)
    if apple_accessory:
        return apple_accessory
    computer = apple_computer_block(title)
'''
s = replace_once(s, old, new, "product ipad priority")

old = '    elif context and not brand_of(title) and not apple_computer_block(title) and not apple_accessory_block(title):\n'
new = '    elif context and not brand_of(title) and not apple_ipad_block(title) and not apple_computer_block(title) and not apple_accessory_block(title):\n'
s = replace_once(s, old, new, "context ipad guard")

old = '''def pack_physical_sections(sections, limit=3950):
    """Pack by ecosystem: Apple together, Samsung together, other small brands separately."""
    physical = []
'''
new = '''def pack_physical_sections(sections, limit=3950):
    """Pack by ecosystem: Apple together, Samsung together, other small brands separately."""
    # Saved custom block order may interleave Apple/Samsung with unrelated brands.
    # Compact these ecosystems at their first occurrence before packing so a tiny
    # Apple TV/Mac mini section can never be stranded behind Honor/Oura/etc.
    compacted = []
    emitted_families = set()
    for section in sections:
        if iphone_bundle_key(section["title"]):
            compacted.append(section)
            continue
        family = physical_section_family(section["title"])
        if family in {"apple", "samsung"}:
            if family in emitted_families:
                continue
            emitted_families.add(family)
            compacted.extend([
                entry for entry in sections
                if not iphone_bundle_key(entry["title"]) and physical_section_family(entry["title"]) == family
            ])
        else:
            compacted.append(section)
    sections = compacted

    physical = []
'''
s = replace_once(s, old, new, "ecosystem compaction")

p.write_text(s, encoding="utf-8")

p = Path("tests/test_prices.py")
t = p.read_text(encoding="utf-8")
marker = '\n    def test_supplier_macbook_shorthand_and_apple_adapters_are_read(self):\n'
extra = r'''
    def test_supplier_ipad_shorthand_and_pencil_are_read_as_apple(self):
        items = self.parse("""Apple Pencil TYPE-C 🇪🇺 - 6400
iPad 11 128 Blue Wi-Fi 🇺🇸 - 38600
MINI 7 128 Blue Wi-Fi 🇺🇸 - 43400
MINI 7 256 Gray Wi-Fi 🇺🇸 - 51300
AIR 13 M3 256 Starlight Wi-Fi 🇺🇸 - 75800
AIR 11 M4 128 Gray Wi-Fi 🇺🇸 - 60600
PRO 12.9 M2 128 Gray LTE 🇺🇸 - 71500
PRO 11 M4 1TB Black Wi-Fi (Nano Texture) 🇺🇸 - 99000
PRO 13 M4 256 Black LTE 🇺🇸 - 100500
PRO 11 M5 256 Black Wi-Fi 🇺🇸 - 96000""").items
        ipad = [item for item in items if item.block == "iPad"]
        pencil = [item for item in items if item.block == "Apple Accessories"]
        self.assertEqual(len(ipad), 9)
        self.assertEqual(len(pencil), 1)
        pages = list(render_blocks(items, Settings()).values())
        self.assertTrue(all(page.startswith("<b>Apple</b>") for page in pages), pages)

    def test_saved_order_cannot_strand_apple_tv_away_from_apple(self):
        items = self.parse("""AirPods Pro 3 Black — 20000
Honor 400 12/256 Black — 33000
Apple TV 4K 64GB (2022) — 20200
Oura Ring 5 Size 8 Black — 35000
Mac Mini (MU9D3) M4/16/256 Silver — 68500""").items
        pages = list(render_blocks(items, Settings(), {
            "block_order": ["AirPods", "Honor", "Apple TV", "Oura Ring", "Mac mini"]
        }).values())
        apple_pages = [page for page in pages if page.startswith("<b>Apple</b>")]
        self.assertEqual(len(apple_pages), 1, pages)
        apple = apple_pages[0]
        self.assertIn("— AirPods —", apple)
        self.assertIn("— Apple TV —", apple)
        self.assertIn("— Mac mini —", apple)
        self.assertNotIn("Honor", apple)
        self.assertNotIn("Oura", apple)

'''
if marker not in t:
    raise SystemExit("test marker missing")
t = t.replace(marker, extra + marker, 1)
p.write_text(t, encoding="utf-8")
