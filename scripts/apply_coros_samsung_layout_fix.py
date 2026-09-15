from pathlib import Path


def rep(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected 1 match, got {count}")
    return text.replace(old, new, 1)


p = Path("prices.py")
s = p.read_text(encoding="utf-8")

# COROS is a standalone sports-watch brand. It must never inherit Xiaomi context.
s = rep(
    s,
    '    ("Rode", r"\\br(?:o|ø)de\\b"),\n',
    '    ("Rode", r"\\br(?:o|ø)de\\b"),\n    ("COROS", r"\\bcoros\\b"),\n',
    "COROS brand",
)

old_samsung = '''def samsung_block(title):\n    if not re.search(r"\\bsamsung\\b|\\bgalaxy\\b|самсунг", title, re.I):\n        return ""\n    if re.search(r"\\b(?:galaxy\\s*)?(?:z\\s*)?(?:fold|flip)\\b", title, re.I):\n        return "Samsung Fold / Flip"\n    if re.search(r"\\b(?:galaxy\\s*)?a\\s*\\d{1,3}[a-z]*\\b|\\bsamsung\\s+a\\s*\\d{1,3}[a-z]*\\b", title, re.I):\n        return "Samsung Galaxy A"\n    if re.search(r"\\b(?:galaxy\\s*)?s\\s*\\d{1,3}(?:\\s*(?:\\+|plus|ultra|fe))?\\b|\\bsamsung\\s+s\\s*\\d{1,3}", title, re.I):\n        return "Samsung Galaxy S"\n    if re.search(r"\\bgalaxy\\s+a\\b|\\bsamsung\\s+galaxy\\s+a\\b", title, re.I):\n        return "Samsung Galaxy A"\n    if re.search(r"\\bgalaxy\\s+s\\b|\\bsamsung\\s+galaxy\\s+s\\b", title, re.I):\n        return "Samsung Galaxy S"\n    return "Samsung"\n'''
new_samsung = '''def samsung_block(title):\n    if not re.search(r"\\bsamsung\\b|\\bgalaxy\\b|самсунг", title, re.I):\n        return ""\n\n    # Galaxy Tab S is a tablet family, never a Galaxy S phone.\n    if re.search(r"\\b(?:galaxy\\s*)?tab\\s*s\\s*\\d*", title, re.I):\n        return "Samsung Tab S"\n\n    # Keep foldables separate when they are present.\n    if re.search(r"\\b(?:galaxy\\s*)?(?:z\\s*)?(?:fold|flip)\\b", title, re.I):\n        return "Samsung Fold / Flip"\n\n    # The requested phone layout is deliberately balanced into two messages:\n    # A-series together with S25, then the complete S26 family.\n    if re.search(r"\\b(?:galaxy\\s*)?a\\s*\\d{1,3}[a-z]*\\b|\\bsamsung\\s+a\\s*\\d{1,3}[a-z]*\\b", title, re.I):\n        return "Samsung A + S25"\n    if re.search(r"\\b(?:galaxy\\s*)?s\\s*25(?:\\s*(?:\\+|plus|ultra|fe))?\\b|\\bsamsung\\s+s\\s*25", title, re.I):\n        return "Samsung A + S25"\n    if re.search(r"\\b(?:galaxy\\s*)?s\\s*26(?:\\s*(?:\\+|plus|ultra|fe))?\\b|\\bsamsung\\s+s\\s*26", title, re.I):\n        return "Samsung S26"\n\n    # Header-only sections from supplier menus.\n    if re.search(r"\\bgalaxy\\s+a\\b|\\bsamsung\\s+galaxy\\s+a\\b", title, re.I):\n        return "Samsung A + S25"\n    if re.search(r"\\bgalaxy\\s+s\\s*25\\b", title, re.I):\n        return "Samsung A + S25"\n    if re.search(r"\\bgalaxy\\s+s\\s*26\\b", title, re.I):\n        return "Samsung S26"\n    if re.search(r"\\b(?:galaxy\\s*)?tab\\s*s\\b", title, re.I):\n        return "Samsung Tab S"\n    return "Samsung"\n'''
s = rep(s, old_samsung, new_samsung, "Samsung grouping")

s = rep(
    s,
    '                "Samsung", "Honor", "Realme", "Huawei", "Tecno", "Xiaomi", "Google", "Rode", "Dyson",\n                "Oura Ring", "CPO", "ASIS", "Аксессуары", "Товары"]',
    '                "Samsung", "Honor", "Realme", "Huawei", "Tecno", "Xiaomi", "Google", "COROS", "Rode", "Dyson",\n                "Oura Ring", "CPO", "ASIS", "Аксессуары", "Товары"]',
    "COROS order",
)

s = rep(
    s,
    '        samsung_rank = {"Samsung Galaxy A": 0, "Samsung Galaxy S": 1, "Samsung Fold / Flip": 2, "Samsung": 3}.get(name, 0) if family == "Samsung" else 0\n',
    '        samsung_rank = {"Samsung A + S25": 0, "Samsung S26": 1, "Samsung Tab S": 2, "Samsung Fold / Flip": 3, "Samsung": 4}.get(name, 0) if family == "Samsung" else 0\n',
    "Samsung order",
)

s = rep(
    s,
    '    elif context and not brand_of(title):\n        title = context + " " + title\n',
    '    elif context and not brand_of(title):\n        # Samsung block labels are navigation names, not product-name prefixes.\n        # Prefix bare A/S/Tab rows with the brand only, otherwise rows become e.g.\n        # "Samsung A + S25 S25 ...".\n        prefix = "Samsung" if context.startswith("Samsung") else context\n        title = prefix + " " + title\n',
    "Samsung context title",
)

p.write_text(s, encoding="utf-8")

# Update old Samsung expectations and add screenshot regressions.
tp = Path("tests/test_prices.py")
t = tp.read_text(encoding="utf-8")
t = t.replace('"Samsung Galaxy A", "Samsung Galaxy A", "Samsung Galaxy A",\n            "Samsung Galaxy S", "Samsung Galaxy S", "Samsung Galaxy S",\n            "Samsung Fold / Flip", "Samsung Fold / Flip",',
              '"Samsung A + S25", "Samsung A + S25", "Samsung A + S25",\n            "Samsung S26", "Samsung S26", "Samsung S26",\n            "Samsung Fold / Flip", "Samsung Fold / Flip",')
t = t.replace('self.assertIn("<b>Samsung Galaxy A</b>", headers)', 'self.assertIn("<b>Samsung A + S25</b>", headers)')
t = t.replace('self.assertIn("<b>Samsung Galaxy S</b>", headers)', 'self.assertIn("<b>Samsung S26</b>", headers)')
t = t.replace('self.assertEqual(result.items[0].block, "Samsung Galaxy S")', 'self.assertEqual(result.items[0].block, "Samsung S26")')

insert = '''\n    def test_coros_does_not_inherit_xiaomi_context(self):\n        items = self.parse("""Xiaomi\nCoros Pace 4 Black Nylon 🇨🇳 — 21000\nCoros Pace 4 Black Silicone 🇨🇳 — 22000\nCoros Pace 4 Ice Crystal Black Silicone 🇨🇳 — 22700\nCoros Pace 4 Jacob&Co collaboration fabric strap version 🇨🇳 — 25300""").items\n        self.assertEqual([item.block for item in items], ["COROS"] * 4)\n        self.assertTrue(all(item.title.startswith("Coros ") for item in items))\n        self.assertTrue(all(not item.title.startswith("Xiaomi ") for item in items))\n\n    def test_requested_samsung_phone_split_and_tab_tablets(self):\n        items = self.parse("""Samsung\nGalaxy A\nA56 8/256 Black — 35000\nA57 12/256 Blue — 42000\nGalaxy S25\nS25 12/256 Black — 65000\nS25 Ultra 12/512 Titanium — 90000\nGalaxy S26\nS26 12/256 Black — 68000\nS26+ 12/256 Cobalt Violet — 71000\nS26 Ultra 12/512 Titanium Black — 99000\nGalaxy Tab S\nTab S10 12/256 Grey — 65000\nTab S11 Ultra 12/512 Silver — 98000""").items\n        self.assertEqual([item.block for item in items], [\n            "Samsung A + S25", "Samsung A + S25",\n            "Samsung A + S25", "Samsung A + S25",\n            "Samsung S26", "Samsung S26", "Samsung S26",\n            "Samsung Tab S", "Samsung Tab S",\n        ])\n        self.assertTrue(all(not item.title.startswith("Samsung A +") for item in items))\n        pages = render_blocks(items, Settings())\n        headers = [content.split("\\n", 1)[0] for content in pages.values()]\n        self.assertIn("<b>Samsung A + S25</b>", headers)\n        self.assertIn("<b>Samsung S26</b>", headers)\n        self.assertIn("<b>Samsung Tab S</b>", headers)\n'''
marker = '\n    def test_preserve_explicit_condition_and_original_packaging(self):\n'
if marker not in t:
    raise SystemExit("test_prices marker missing")
t = t.replace(marker, insert + marker, 1)
tp.write_text(t, encoding="utf-8")

# Catalog button expectations now match the requested groups.
cp = Path("tests/test_catalog.py")
c = cp.read_text(encoding="utf-8")
c = c.replace('"Samsung Galaxy A", "Samsung Galaxy S", "Samsung Fold / Flip"',
              '"Samsung A + S25", "Samsung S26", "Samsung Fold / Flip"')
cp.write_text(c, encoding="utf-8")
