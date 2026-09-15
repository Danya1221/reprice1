from pathlib import Path
import re


def rep(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected 1 match, got {count}")
    return text.replace(old, new, 1)


p = Path("prices.py")
s = p.read_text(encoding="utf-8")

s, n = re.subn(
    r'def apple_watch_block\(title\):\n.*?\n    return "Apple Watch"\n\n',
    'def apple_watch_block(title):\n'
    '    if re.search(r"\\b(?:apple\\s*)?watch\\b", title, re.I):\n'
    '        return "Apple Watch"\n'
    '    return ""\n\n',
    s,
    count=1,
    flags=re.S,
)
if n != 1:
    raise SystemExit(f"Apple Watch function: got {n}")

anchor = '    return "iPhone " + model\n\n\n\ndef brand_of(text):\n'
insert = '''    return "iPhone " + model\n\n\ndef iphone_publish_block(model):\n    if not model:\n        return ""\n    match = re.match(r"iPhone\\s+(\\d{1,2})\\b", model, re.I)\n    if match and 11 <= int(match.group(1)) <= 15:\n        return "iPhone 11–15"\n    return model\n\n\ndef brand_of(text):\n'''
s = rep(s, anchor, insert, "iPhone publication group")

old_samsung = '''def samsung_block(title):
    if not re.search(r"\\bsamsung\\b|\\bgalaxy\\b|самсунг", title, re.I):
        return ""

    # Galaxy Tab S is a tablet family, never a Galaxy S phone.
    if re.search(r"\\b(?:galaxy\\s*)?tab\\s*s\\s*\\d*", title, re.I):
        return "Samsung Tab S"

    # Keep foldables separate when they are present.
    if re.search(r"\\b(?:galaxy\\s*)?(?:z\\s*)?(?:fold|flip)\\b", title, re.I):
        return "Samsung Fold / Flip"

    # The requested phone layout is deliberately balanced into two messages:
    # A-series together with S25, then the complete S26 family.
    if re.search(r"\\b(?:galaxy\\s*)?a\\s*\\d{1,3}[a-z]*\\b|\\bsamsung\\s+a\\s*\\d{1,3}[a-z]*\\b", title, re.I):
        return "Samsung A + S25"
    if re.search(r"\\b(?:galaxy\\s*)?s\\s*25(?:\\s*(?:\\+|plus|ultra|fe))?\\b|\\bsamsung\\s+s\\s*25", title, re.I):
        return "Samsung A + S25"
    if re.search(r"\\b(?:galaxy\\s*)?s\\s*26(?:\\s*(?:\\+|plus|ultra|fe))?\\b|\\bsamsung\\s+s\\s*26", title, re.I):
        return "Samsung S26"

    # Header-only sections from supplier menus.
    if re.search(r"\\bgalaxy\\s+a\\b|\\bsamsung\\s+galaxy\\s+a\\b", title, re.I):
        return "Samsung A + S25"
    if re.search(r"\\bgalaxy\\s+s\\s*25\\b", title, re.I):
        return "Samsung A + S25"
    if re.search(r"\\bgalaxy\\s+s\\s*26\\b", title, re.I):
        return "Samsung S26"
    if re.search(r"\\b(?:galaxy\\s*)?tab\\s*s\\b", title, re.I):
        return "Samsung Tab S"
    return "Samsung"
'''
new_samsung = '''def samsung_block(title):
    plain = FLAGS.sub("", clean(title)).strip()
    has_brand = bool(re.search(r"\\bsamsung\\b|\\bgalaxy\\b|самсунг", plain, re.I))

    # Supplier often omits "Samsung" from every product row.
    if re.search(r"^(?:samsung\\s+|galaxy\\s+)?buds\\s*[34]\\b", plain, re.I):
        return "Samsung Buds"
    if re.search(r"^(?:samsung\\s+|galaxy\\s+)?a\\s*\\d{2,3}[a-z]*\\b", plain, re.I):
        return "Samsung A + S25"
    if re.search(r"^(?:samsung\\s+|galaxy\\s+)?s\\s*25(?:\\s*(?:fe|edge|ultra|\\+|plus))?\\b", plain, re.I):
        return "Samsung A + S25"
    if re.search(r"^(?:samsung\\s+|galaxy\\s+)?s\\s*26(?:\\s*(?:fe|edge|ultra|\\+|plus))?\\b", plain, re.I):
        return "Samsung S26"
    if re.search(r"^(?:samsung\\s+|galaxy\\s+)?tab\\s*s\\s*\\d*", plain, re.I):
        return "Samsung Tab S"
    if re.search(r"^(?:samsung\\s+|galaxy\\s+)?(?:z\\s*)?(?:fold|flip)\\b", plain, re.I):
        return "Samsung Fold / Flip"

    if not has_brand:
        return ""
    if re.search(r"\\b(?:galaxy\\s*)?tab\\s*s\\s*\\d*", plain, re.I):
        return "Samsung Tab S"
    if re.search(r"\\b(?:galaxy\\s*)?(?:z\\s*)?(?:fold|flip)\\b", plain, re.I):
        return "Samsung Fold / Flip"
    if re.search(r"\\b(?:galaxy\\s*)?buds\\s*[34]\\b", plain, re.I):
        return "Samsung Buds"
    if re.search(r"\\bgalaxy\\s+a\\b|\\bsamsung\\s+galaxy\\s+a\\b", plain, re.I):
        return "Samsung A + S25"
    if re.search(r"\\bgalaxy\\s+s\\s*25\\b", plain, re.I):
        return "Samsung A + S25"
    if re.search(r"\\bgalaxy\\s+s\\s*26\\b", plain, re.I):
        return "Samsung S26"
    return "Samsung"
'''
s = rep(s, old_samsung, new_samsung, "Samsung classifier")

s = rep(
    s,
    '        samsung_rank = {"Samsung A + S25": 0, "Samsung S26": 1, "Samsung Tab S": 2, "Samsung Fold / Flip": 3, "Samsung": 4}.get(name, 0) if family == "Samsung" else 0\n',
    '        samsung_rank = {"Samsung Buds": 0, "Samsung A + S25": 1, "Samsung S26": 2, "Samsung Tab S": 3, "Samsung Fold / Flip": 4, "Samsung": 5}.get(name, 0) if family == "Samsung" else 0\n',
    "Samsung order",
)

s = rep(
    s,
    '            known = iphone_model(data["title"]) or product_block(data["title"])\n            if known:\n                data["block"] = known\n',
    '            model = iphone_model(data["title"])\n            known = iphone_publish_block(model) if model else product_block(data["title"])\n            if known:\n                data["block"] = known\n',
    "saved rows",
)

s = rep(
    s,
    '        block = "Аксессуары" if accessory else (special or model or own_brand or section or "Товары")\n',
    '        publish_model = iphone_publish_block(model) if model else ""\n        block = "Аксессуары" if accessory else (special or publish_model or own_brand or section or "Товары")\n',
    "new iPhones",
)

# Sort Samsung A before S25, independent of where the country flag is placed.
old_sort = '''def item_sort(item):
    size = 0 if re.search(r"\\bM\\b", item.title) else 1 if re.search(r"\\bL\\b", item.title) else 2
    return size, clean(item.title).casefold()
'''
new_sort = '''def item_sort(item):
    size = 0 if re.search(r"\\bM\\b", item.title) else 1 if re.search(r"\\bL\\b", item.title) else 2
    plain = FLAGS.sub("", clean(item.title)).strip().casefold()
    return size, plain


def samsung_a_s25_sort(item):
    plain = FLAGS.sub("", clean(item.title)).strip()
    a = re.search(r"\\bA\\s*(\\d{2,3})\\b", plain, re.I)
    if a:
        return 0, int(a.group(1)), 0, plain.casefold()
    if re.search(r"\\bS\\s*25\\s*FE\\b", plain, re.I):
        return 1, 0, 0, plain.casefold()
    if re.search(r"\\bS\\s*25\\s*Edge\\b", plain, re.I):
        return 1, 2, 0, plain.casefold()
    if re.search(r"\\bS\\s*25\\s*Ultra\\b", plain, re.I):
        return 1, 3, 0, plain.casefold()
    if re.search(r"\\bS\\s*25\\b", plain, re.I):
        return 1, 1, 0, plain.casefold()
    return 9, 0, 0, plain.casefold()
'''
s = rep(s, old_sort, new_sort, "Samsung item sort")

old_render = '''                last_sim = None
                for item in sorted(status_items, key=lambda i: (SIM_ORDER.get(i.sim, 99), item_sort(i))):
                    if iphone_model(item.title) and item.sim != last_sim:
                        label = SIM_LABELS.get(item.sim)
                        if label:
                            lines.append("<b>— " + label + " —</b>")
                        last_sim = item.sim
                    row = display_title(item) + " — " + price_text(marked_price(item, settings, overrides), item.currency)
'''
new_render = '''                last_sim = None
                last_samsung_section = None
                sorter = samsung_a_s25_sort if block == "Samsung A + S25" else item_sort
                for item in sorted(status_items, key=lambda i: (SIM_ORDER.get(i.sim, 99), sorter(i))):
                    if block == "Samsung A + S25":
                        samsung_section = "Galaxy S25" if re.search(r"\\bS\\s*25\\b", item.title, re.I) else "Galaxy A"
                        if samsung_section != last_samsung_section:
                            lines.append("<b>— " + samsung_section + " —</b>")
                            last_samsung_section = samsung_section
                    if iphone_model(item.title) and item.sim != last_sim:
                        label = SIM_LABELS.get(item.sim)
                        if label:
                            lines.append("<b>— " + label + " —</b>")
                        last_sim = item.sim
                    row = display_title(item) + " — " + price_text(marked_price(item, settings, overrides), item.currency)
'''
s = rep(s, old_render, new_render, "Samsung subsections")

p.write_text(s, encoding="utf-8")

reg = Path("tests/test_parser_regressions.py")
t = reg.read_text(encoding="utf-8")
t = rep(
    t,
    '        self.assertEqual([i.block for i in items], ["Apple Watch Series 11", "Apple Watch Ultra 3", "MacBook / iMac", "MacBook / iMac"])\n',
    '        self.assertEqual([i.block for i in items], ["Apple Watch", "Apple Watch", "MacBook / iMac", "MacBook / iMac"])\n'
    '        watch_pages = [page for page in render_blocks(items, Settings()).values() if page.startswith("<b>Apple Watch</b>")]\n'
    '        self.assertEqual(len(watch_pages), 1)\n'
    '        self.assertIn("Series 11", watch_pages[0])\n'
    '        self.assertIn("Ultra 3", watch_pages[0])\n',
    "watch regression",
)
marker = '\n    def test_saved_wrong_block_is_reclassified(self):\n'
extra = '''\n    def test_iphone_11_to_15_are_one_publication_block(self):\n        items = self.parse("""iPhone: 13-14-15\n🇮🇳 13 128GB Midnight - 45100\n🇺🇸 14 128GB Midnight - 46400\n🇮🇳 15 128GB Black - 55400\n🇮🇳 15 Plus 128GB Pink - 61400\n🇦🇪 15 Pro 128GB Blue - 83600\niPhone 12 128 Black — 40000\niPhone 11 Pro Max 256 Green — 39000""").items\n        self.assertEqual({item.block for item in items}, {"iPhone 11–15"})\n        self.assertTrue(any("iPhone 15 Pro" in item.title for item in items))\n        pages = render_blocks(items, Settings())\n        self.assertTrue(all(page.startswith("<b>iPhone 11–15</b>") for page in pages.values()))\n\n'''
if marker not in t:
    raise SystemExit("regression marker missing")
t = t.replace(marker, extra + marker, 1)
reg.write_text(t, encoding="utf-8")

complete = Path("tests/test_complete_prices.py")
c = complete.read_text(encoding="utf-8")
c = rep(
    c,
    '["Realme", "Tecno", "Infinix", "OnePlus", "Nothing", "AirPods", "Apple Watch Series 10", "iPad", "MacBook / iMac"]',
    '["Realme", "Tecno", "Infinix", "OnePlus", "Nothing", "AirPods", "Apple Watch", "iPad", "MacBook / iMac"]',
    "complete watch expectation",
)
complete.write_text(c, encoding="utf-8")

prices_test = Path("tests/test_prices.py")
pt = prices_test.read_text(encoding="utf-8")
marker = '    def test_preserve_explicit_condition_and_original_packaging(self):\n'
extra = '''    def test_supplier_style_samsung_sections(self):\n        items = self.parse("""Buds 3 FE Black 🇦🇪 - 6400\nBuds 4 White 🇰🇿 - 10400\n🇷🇺A17 6/128GB Gray — 15000\n🇷🇺A27 8/256GB Blue — 23800\n🇷🇺A57 12/512GB Navy — 40500\nS25 FE 8/128 Navy 🇮🇳 - 37400\nS25 12/128 Navy 🇪🇺 - 45000\nS25 Edge 12/256 Jetblack 🇪🇺 - 49100\nS25 Ultra 12/1TB Gray 🇨🇱 - 79400\nS26 FE 8/128 Graphite 🇿🇦 - 46500\nS26+ 12/256 White 🇰🇿 - 69500\nS26 Ultra 16/1TB Black 🇨🇱 - 117500""").items\n        self.assertEqual([item.block for item in items], [\n            "Samsung Buds", "Samsung Buds",\n            "Samsung A + S25", "Samsung A + S25", "Samsung A + S25",\n            "Samsung A + S25", "Samsung A + S25", "Samsung A + S25", "Samsung A + S25",\n            "Samsung S26", "Samsung S26", "Samsung S26",\n        ])\n        pages = render_blocks(items, Settings())\n        text = "\\n".join(pages.values())\n        self.assertIn("<b>Samsung Buds</b>", text)\n        self.assertIn("<b>— Galaxy A —</b>", text)\n        self.assertIn("<b>— Galaxy S25 —</b>", text)\n        self.assertIn("<b>Samsung S26</b>", text)\n        combined = next(page for page in pages.values() if page.startswith("<b>Samsung A + S25</b>"))\n        self.assertLess(combined.index("Galaxy A"), combined.index("Galaxy S25"))\n\n    def test_all_apple_watches_share_one_block(self):\n        items = self.parse("""Apple Watch\nSeries 11 46mm Black — 40000\nSE 3 44mm Silver — 30000\nUltra 3 49mm Black — 70000""").items\n        self.assertEqual([item.block for item in items], ["Apple Watch"] * 3)\n        pages = render_blocks(items, Settings())\n        self.assertEqual(len(pages), 1)\n        self.assertTrue(next(iter(pages.values())).startswith("<b>Apple Watch</b>"))\n\n'''
if marker not in pt:
    raise SystemExit("price test marker missing")
pt = pt.replace(marker, extra + marker, 1)
prices_test.write_text(pt, encoding="utf-8")
