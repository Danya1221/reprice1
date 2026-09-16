from pathlib import Path


def rep(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected 1 match, got {count}")
    return text.replace(old, new, 1)


p = Path("prices.py")
s = p.read_text(encoding="utf-8")

s = rep(
    s,
    '("Apple", r"\\bapple\\b|\\biphone\\b|айфон|\\bipad\\b|\\bmacbook\\b|\\bairpods\\b|\\bimac\\b|apple\\s*watch"),',
    '("Apple", r"\\bapple\\b|\\biphone\\b|айфон|\\bipad\\b|\\bmacbook\\b|\\bairpods\\b|\\bimac\\b|"
    r"\\bmac\\s+mini\\b|\\bmac\\s+studio\\b|\\bapple\\s*tv\\b|\\bairtag\\b|apple\\s*watch"),',
    "Apple brand aliases",
)

marker = 'def iphone_bundle_header(bundle, titles):\n'
helper = '''def iphone_bundle_section_rank(title):\n    """Operator-requested visual order inside the large iPhone messages."""\n    name = title.casefold().strip()\n    if name == "iphone 17e":\n        return (0, name)\n    if name == "iphone air":\n        return (1, name)\n    if name == "iphone 17":\n        return (2, name)\n    if name == "iphone 17 pro":\n        return (3, name)\n    if name == "iphone 17 pro max":\n        return (4, name)\n    return (10, name)\n\n\n'''
if marker not in s:
    raise SystemExit("iphone bundle header marker missing")
s = s.replace(marker, helper + marker, 1)

old = '''    if bundle == "iphone17":\n        base = "iPhone 17 / 17 Pro / 17 Pro Max / Air"\n        if any(title.casefold() == "iphone 17e" for title in titles):\n            base += " / 17e"\n        return base\n'''
new = '''    if bundle == "iphone17":\n        ordered = sorted(titles, key=iphone_bundle_section_rank)\n        labels = []\n        for title in ordered:\n            name = title.casefold()\n            if name == "iphone air":\n                labels.append("17 Air")\n            elif name.startswith("iphone "):\n                labels.append(title[7:])\n            else:\n                labels.append(title)\n        return "iPhone " + " / ".join(labels)\n'''
s = rep(s, old, new, "iPhone 17 bundle header")

s = rep(
    s,
    '                bundle_sections = [entry for entry in sections if iphone_bundle_key(entry["title"]) == bundle]\n',
    '                bundle_sections = [entry for entry in sections if iphone_bundle_key(entry["title"]) == bundle]\n                if bundle == "iphone17":\n                    bundle_sections.sort(key=lambda entry: iphone_bundle_section_rank(entry["title"]))\n',
    "iPhone 17 bundle order",
)

storage_marker = 'def iphone_storage_rank(item):\n'
storage_helper = '''def product_storage_key(item):\n    """Storage part for RAM/storage products such as Galaxy S26 12/256 or 16/1TB."""\n    title = FLAGS.sub("", clean(item.title))\n    match = re.search(r"\\b\\d{1,2}\\s*/\\s*(\\d{1,4})\\s*(GB|TB)?\\b", title, re.I)\n    if not match:\n        return ""\n    return match.group(1) + (match.group(2) or "GB").upper()\n\n\n'''
if storage_marker not in s:
    raise SystemExit("storage marker missing")
s = s.replace(storage_marker, storage_helper + storage_marker, 1)

old = '''        if status_index and lines and lines[-1] != "":\n            lines.append("")\n        if has_activation:\n            lines.append("<b>— " + CONDITION_LABELS[status] + " —</b>")\n'''
new = '''        if status_index and lines and lines[-1] != "":\n            lines.append("")\n        # Ordinary/non-activated stock is the default and needs no noisy heading.\n        # Only explicitly active stock gets its own visible section.\n        if has_activation and status == "active":\n            lines.append("<b>— Актив —</b>")\n            lines.append("")\n'''
s = rep(s, old, new, "activation heading cleanup")

old = '''                    lines.append("<b>— " + samsung_section + " —</b>")\n                    last_samsung_section = samsung_section\n'''
new = '''                    lines.append("<b>— " + samsung_section + " —</b>")\n                    lines.append("")\n                    last_samsung_section = samsung_section\n'''
s = rep(s, old, new, "Samsung section spacing")

old = '''                if label:\n                    if lines and lines[-1] != "" and not lines[-1].startswith("<b>"):\n                        lines.append("")\n                    lines.append("<b>— " + label + " —</b>")\n                last_sim = item.sim\n'''
new = '''                if label:\n                    if lines and lines[-1] != "":\n                        lines.append("")\n                    lines.append("<b>— " + label + " —</b>")\n                    lines.append("")\n                last_sim = item.sim\n'''
s = rep(s, old, new, "SIM heading spacing")

old = '''            else:\n                model_key = model_group_key(item)\n                if (last_model_key is not None and model_key != last_model_key\n                        and lines and lines[-1] != "" and not lines[-1].startswith("<b>")):\n                    lines.append("")\n                last_model_key = model_key\n'''
new = '''            else:\n                model_key = model_group_key(item)\n                model_changed = last_model_key is not None and model_key != last_model_key\n                if (model_changed and lines and lines[-1] != "" and not lines[-1].startswith("<b>")):\n                    lines.append("")\n                if model_changed:\n                    last_storage = None\n                if block.startswith("Samsung"):\n                    storage = product_storage_key(item)\n                    if (storage and last_storage is not None and storage != last_storage\n                            and lines and lines[-1] != "" and not lines[-1].startswith("<b>")):\n                        lines.append("")\n                    if storage:\n                        last_storage = storage\n                last_model_key = model_key\n'''
s = rep(s, old, new, "Samsung storage spacing")

s = rep(
    s,
    '        bodies.append("<b>— " + html.escape(section["title"]) + " —</b>\\n" + section["body"])\n',
    '        bodies.append("<b>— " + html.escape(section["title"]) + " —</b>\\n\\n" + section["body"])\n',
    "logical section heading spacing",
)

p.write_text(s, encoding="utf-8")


tp = Path("tests/test_prices.py")
t = tp.read_text(encoding="utf-8")

t = t.replace(
    '        self.assertTrue(any(page.startswith("<b>iPhone 17 / 17 Pro / 17 Pro Max / Air</b>") for page in pages))',
    '        self.assertTrue(any(page.startswith("<b>iPhone 17 Air / 17 / 17 Pro / 17 Pro Max</b>") for page in pages))',
)

t = rep(
    t,
    '        self.assertGreaterEqual(iphone16.count("— Не активированное —"), 2)\n        self.assertGreaterEqual(iphone16.count("— Актив —"), 2)\n',
    '        self.assertNotIn("— Не активированное —", iphone16)\n        self.assertGreaterEqual(iphone16.count("— Актив —"), 2)\n',
    "iPhone activation test",
)

t = rep(
    t,
    '        self.assertIn("— Не активированное —", content)\n        self.assertIn("— Актив —", content)\n        self.assertNotIn("Статус не указан", content)\n        self.assertLess(content.index("— Не активированное —"), content.index("— Актив —"))\n        self.assertLess(content.index("256 Blue"), content.index("— Актив —"))\n',
    '        self.assertNotIn("— Не активированное —", content)\n        self.assertIn("— Актив —", content)\n        self.assertNotIn("Статус не указан", content)\n        self.assertLess(content.index("256 Blue"), content.index("— Актив —"))\n',
    "active/inactive test",
)

marker = '    def test_preserve_explicit_condition_and_original_packaging(self):\n'
extra = '''    def test_service_headings_have_visible_blank_lines(self):\n        items = self.parse("iPhone 17e 256 Black 1Sim+eSim — 63800\\niPhone 17e 512 White 1Sim+eSim Актив — 70000").items\n        content = next(iter(render_blocks(items, Settings()).values()))\n        self.assertIn("— iPhone 17e —</b>\\n\\n<b>— SIM + eSIM —</b>\\n\\n<code>", content)\n        self.assertIn("— Актив —</b>\\n\\n<b>— SIM + eSIM —</b>\\n\\n<code>", content)\n        self.assertNotIn("— Не активированное —", content)\n\n    def test_iphone17_visual_order_is_17e_air_pro_pro_max(self):\n        items = self.parse("""iPhone 17 Pro Max 256 Silver — 100000\niPhone Air 256 Gold — 80000\niPhone 17e 256 Black — 65000\niPhone 17 Pro 256 Blue — 90000""").items\n        content = next(iter(render_blocks(items, Settings()).values()))\n        positions = [content.index(label) for label in ["— iPhone 17e —", "— iPhone Air —", "— iPhone 17 Pro —", "— iPhone 17 Pro Max —"]]\n        self.assertEqual(positions, sorted(positions))\n        self.assertTrue(content.startswith("<b>iPhone 17e / 17 Air / 17 Pro / 17 Pro Max</b>"))\n\n    def test_samsung_memory_sizes_have_blank_lines(self):\n        items = self.parse("""S26 12/128 Black — 57300\nS26 12/128 Cobalt Violet — 57300\nS26 12/256 Black — 64300\nS26 12/256 Sky Blue — 63000\nS26 12/512 Black — 70800""").items\n        content = next(iter(render_blocks(items, Settings()).values()))\n        self.assertIn("12/128 Cobalt Violet — 57 300</code>\\n\\n<code>Galaxy S26 12/256", content)\n        self.assertIn("12/256 Sky Blue — 63 000</code>\\n\\n<code>Galaxy S26 12/512", content)\n\n    def test_mac_mini_and_apple_tv_never_inherit_previous_brand(self):\n        items = self.parse("""Kodak\nKodak Mini Shot 3 — 12000\nMac mini M4 16/256 Silver — 68800\nApple TV 4K 64GB — 20200""").items\n        mac = next(item for item in items if item.block == "Mac mini")\n        tv = next(item for item in items if item.block == "Apple TV")\n        self.assertTrue(mac.title.startswith("Mac mini"))\n        self.assertTrue(tv.title.startswith("Apple TV"))\n        self.assertNotIn("Kodak", mac.title)\n        self.assertNotIn("Kodak", tv.title)\n        apple = next(page for page in render_blocks(items, Settings()).values() if page.startswith("<b>Apple</b>"))\n        self.assertIn("— Mac mini —", apple)\n        self.assertIn("— Apple TV —", apple)\n\n'''
if marker not in t:
    raise SystemExit("test insertion marker missing")
t = t.replace(marker, extra + marker, 1)

tp.write_text(t, encoding="utf-8")
