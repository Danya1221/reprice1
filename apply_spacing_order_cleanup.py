from pathlib import Path


def rep(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected 1 match, got {count}")
    return text.replace(old, new, 1)


p = Path("prices.py")
s = p.read_text(encoding="utf-8")

old_apple = '''("Apple", r"\\bapple\\b|\\biphone\\b|айфон|\\bipad\\b|\\bmacbook\\b|\\bairpods\\b|\\bimac\\b|apple\\s*watch"),'''
new_apple = '''("Apple", r"\\bapple\\b|\\biphone\\b|айфон|\\bipad\\b|\\bmacbook\\b|\\bairpods\\b|\\bimac\\b|"
              r"\\bmac\\s+mini\\b|\\bmac\\s+studio\\b|\\bapple\\s*tv\\b|\\bairtag\\b|apple\\s*watch"),'''
s = rep(s, old_apple, new_apple, "Apple brand aliases")

marker = 'def iphone_bundle_header(bundle, titles):\n'
helper = '''def iphone_bundle_section_rank(title):
    """Operator-requested visual order inside the large iPhone messages."""
    name = title.casefold().strip()
    if name == "iphone 17e":
        return (0, name)
    if name == "iphone air":
        return (1, name)
    if name == "iphone 17":
        return (2, name)
    if name == "iphone 17 pro":
        return (3, name)
    if name == "iphone 17 pro max":
        return (4, name)
    return (10, name)


'''
if marker not in s:
    raise SystemExit("iphone bundle header marker missing")
s = s.replace(marker, helper + marker, 1)

old = '''    if bundle == "iphone17":
        base = "iPhone 17 / 17 Pro / 17 Pro Max / Air"
        if any(title.casefold() == "iphone 17e" for title in titles):
            base += " / 17e"
        return base
'''
new = '''    if bundle == "iphone17":
        ordered = sorted(titles, key=iphone_bundle_section_rank)
        labels = []
        for title in ordered:
            name = title.casefold()
            if name == "iphone air":
                labels.append("17 Air")
            elif name.startswith("iphone "):
                labels.append(title[7:])
            else:
                labels.append(title)
        return "iPhone " + " / ".join(labels)
'''
s = rep(s, old, new, "iPhone 17 bundle header")

s = rep(
    s,
    '                bundle_sections = [entry for entry in sections if iphone_bundle_key(entry["title"]) == bundle]\n',
    '                bundle_sections = [entry for entry in sections if iphone_bundle_key(entry["title"]) == bundle]\n                if bundle == "iphone17":\n                    bundle_sections.sort(key=lambda entry: iphone_bundle_section_rank(entry["title"]))\n',
    "iPhone 17 bundle order",
)

storage_marker = 'def iphone_storage_rank(item):\n'
storage_helper = '''def product_storage_key(item):
    """Storage part for RAM/storage products such as Galaxy S26 12/256 or 16/1TB."""
    title = FLAGS.sub("", clean(item.title))
    match = re.search(r"\\b\\d{1,2}\\s*/\\s*(\\d{1,4})\\s*(GB|TB)?\\b", title, re.I)
    if not match:
        return ""
    return match.group(1) + (match.group(2) or "GB").upper()


'''
if storage_marker not in s:
    raise SystemExit("storage marker missing")
s = s.replace(storage_marker, storage_helper + storage_marker, 1)

old = '''        if status_index and lines and lines[-1] != "":
            lines.append("")
        if has_activation:
            lines.append("<b>— " + CONDITION_LABELS[status] + " —</b>")
'''
new = '''        if status_index and lines and lines[-1] != "":
            lines.append("")
        # Ordinary/non-activated stock is the default and needs no noisy heading.
        # Only explicitly active stock gets its own visible section.
        if has_activation and status == "active":
            lines.append("<b>— Актив —</b>")
            lines.append("")
'''
s = rep(s, old, new, "activation heading cleanup")

old = '''                    lines.append("<b>— " + samsung_section + " —</b>")
                    last_samsung_section = samsung_section
'''
new = '''                    lines.append("<b>— " + samsung_section + " —</b>")
                    lines.append("")
                    last_samsung_section = samsung_section
'''
s = rep(s, old, new, "Samsung section spacing")

old = '''                if label:
                    if lines and lines[-1] != "" and not lines[-1].startswith("<b>"):
                        lines.append("")
                    lines.append("<b>— " + label + " —</b>")
                last_sim = item.sim
'''
new = '''                if label:
                    if lines and lines[-1] != "":
                        lines.append("")
                    lines.append("<b>— " + label + " —</b>")
                    lines.append("")
                last_sim = item.sim
'''
s = rep(s, old, new, "SIM heading spacing")

old = '''            else:
                model_key = model_group_key(item)
                if (last_model_key is not None and model_key != last_model_key
                        and lines and lines[-1] != "" and not lines[-1].startswith("<b>")):
                    lines.append("")
                last_model_key = model_key
'''
new = '''            else:
                model_key = model_group_key(item)
                model_changed = last_model_key is not None and model_key != last_model_key
                if (model_changed and lines and lines[-1] != "" and not lines[-1].startswith("<b>")):
                    lines.append("")
                if model_changed:
                    last_storage = None
                if block.startswith("Samsung"):
                    storage = product_storage_key(item)
                    if (storage and last_storage is not None and storage != last_storage
                            and lines and lines[-1] != "" and not lines[-1].startswith("<b>")):
                        lines.append("")
                    if storage:
                        last_storage = storage
                last_model_key = model_key
'''
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
extra = '''    def test_service_headings_have_visible_blank_lines(self):
        items = self.parse("iPhone 17e 256 Black 1Sim+eSim — 63800\\niPhone 17e 512 White 1Sim+eSim Актив — 70000").items
        content = next(iter(render_blocks(items, Settings()).values()))
        self.assertIn("— iPhone 17e —</b>\\n\\n<b>— SIM + eSIM —</b>\\n\\n<code>", content)
        self.assertIn("— Актив —</b>\\n\\n<b>— SIM + eSIM —</b>\\n\\n<code>", content)
        self.assertNotIn("— Не активированное —", content)

    def test_iphone17_visual_order_is_17e_air_pro_pro_max(self):
        items = self.parse("""iPhone 17 Pro Max 256 Silver — 100000
iPhone Air 256 Gold — 80000
iPhone 17e 256 Black — 65000
iPhone 17 Pro 256 Blue — 90000""").items
        content = next(iter(render_blocks(items, Settings()).values()))
        positions = [content.index(label) for label in ["— iPhone 17e —", "— iPhone Air —", "— iPhone 17 Pro —", "— iPhone 17 Pro Max —"]]
        self.assertEqual(positions, sorted(positions))
        self.assertTrue(content.startswith("<b>iPhone 17e / 17 Air / 17 Pro / 17 Pro Max</b>"))

    def test_samsung_memory_sizes_have_blank_lines(self):
        items = self.parse("""S26 12/128 Black — 57300
S26 12/128 Cobalt Violet — 57300
S26 12/256 Black — 64300
S26 12/256 Sky Blue — 63000
S26 12/512 Black — 70800""").items
        content = next(iter(render_blocks(items, Settings()).values()))
        self.assertIn("12/128 Cobalt Violet — 57 300</code>\\n\\n<code>Galaxy S26 12/256", content)
        self.assertIn("12/256 Sky Blue — 63 000</code>\\n\\n<code>Galaxy S26 12/512", content)

    def test_mac_mini_and_apple_tv_never_inherit_previous_brand(self):
        items = self.parse("""Kodak
Kodak Mini Shot 3 — 12000
Mac mini M4 16/256 Silver — 68800
Apple TV 4K 64GB — 20200""").items
        mac = next(item for item in items if item.block == "Mac mini")
        tv = next(item for item in items if item.block == "Apple TV")
        self.assertTrue(mac.title.startswith("Mac mini"))
        self.assertTrue(tv.title.startswith("Apple TV"))
        self.assertNotIn("Kodak", mac.title)
        self.assertNotIn("Kodak", tv.title)
        apple = next(page for page in render_blocks(items, Settings()).values() if page.startswith("<b>Apple</b>"))
        self.assertIn("— Mac mini —", apple)
        self.assertIn("— Apple TV —", apple)

'''
if marker not in t:
    raise SystemExit("test insertion marker missing")
t = t.replace(marker, extra + marker, 1)

tp.write_text(t, encoding="utf-8")
