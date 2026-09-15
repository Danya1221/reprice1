from pathlib import Path
import re


def rep(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected 1 match, got {count}")
    return text.replace(old, new, 1)


p = Path("prices.py")
s = p.read_text(encoding="utf-8")
s = rep(s, 'SHORT_IPHONE = re.compile(r"^(\\d{1,2}(?:e)?(?:\\s+(?:pro\\s+max|pro|plus|mini))?)\\s+(?=\\d{2,4}\\s*(?:gb|tb|гб|тб)?\\b)", re.I)', 'SHORT_IPHONE = re.compile(r"^(\\d{1,2}(?:e)?(?:\\s+(?:pro\\s+max|pro|plus|mini|air))?)\\s+(?=\\d{1,4}\\s*(?:gb|tb|гб|тб)?\\b)", re.I)', "short iPhone TB/Air")
s = rep(s, '    ("Ray-Ban Meta", r"ray[\\s-]?ban|wayfarer|skyler"),\n', '    ("Ray-Ban Meta", r"ray[\\s-]?ban|wayfarer|skyler"),\n    ("Oura Ring", r"\\boura(?:\\s+ring)?\\b"),\n', "Oura")
s = rep(s, '    ("Xiaomi", r"\\bxiaomi\\b|\\bredmi\\b|\\bpoco\\b"),', '    ("Xiaomi", r"\\bxiaomi\\b|\\bredmi\\b|\\bpoco\\b|^\\s*(?:redmi\\s+)?note\\s+\\d{1,2}\\b"),', "bare Note")
s, n = re.subn(r'\n# Apple: iPhone 17-family devices bought in these markets are eSIM-only\..*?ESIM_ONLY_17_FLAGS = \{.*?\}\n\n', '\n', s, count=1, flags=re.S)
if n != 1:
    raise SystemExit(f"region flag constants: got {n}")
s = rep(s, '        r"\\b(?:nano\\s*)?sim\\s*(?:\\+|/|&|and|и)\\s*esim\\b|"\n        r"\\besim\\s*(?:\\+|/|&|and|и)\\s*(?:nano\\s*)?sim\\b",', '        r"\\b(?:1\\s*)?(?:nano\\s*)?sim\\s*(?:\\+|/|&|and|и)\\s*esim\\b|"\n        r"\\besim\\s*(?:\\+|/|&|and|и)\\s*(?:1\\s*)?(?:nano\\s*)?sim\\b",', "1Sim+eSim")
s = rep(s, '    words = [x if re.fullmatch(r"\\d+e?", x, re.I) else x.title() for x in suffix.split()]\n    return "iPhone " + " ".join(words)\n', '    words = [x if re.fullmatch(r"\\d+e?", x, re.I) else x.title() for x in suffix.split()]\n    model = " ".join(words)\n    if model.casefold() == "17 air":\n        return "iPhone Air"\n    return "iPhone " + model\n', "iPhone Air model")
s, n = re.subn(r'\ndef iphone17_family\(model\):.*?\n    return "hybrid"\n\n', '\n', s, count=1, flags=re.S)
if n != 1:
    raise SystemExit(f"region SIM functions: got {n}")
old_block = 'def product_block(title):\n    for label in ("AirPods", "Apple Watch", "iPad", "MacBook", "iMac", "Mac mini", "Mac Studio", "Apple TV", "AirTag"):\n        if re.search(r"\\b" + re.escape(label) + r"\\b", title, re.I):\n            return label\n    return brand_of(title)\n'
new_block = 'def apple_watch_block(title):\n    if not re.search(r"\\b(?:apple\\s*)?watch\\b", title, re.I):\n        return ""\n    ultra = re.search(r"\\bultra(?:\\s*(\\d{1,2}))?\\b", title, re.I)\n    if ultra:\n        return "Apple Watch Ultra" + (" " + ultra.group(1) if ultra.group(1) else "")\n    series = re.search(r"\\b(?:series|s)\\s*(\\d{1,2})\\b", title, re.I)\n    if series:\n        return "Apple Watch Series " + series.group(1)\n    se = re.search(r"\\bse(?:\\s*(\\d{1,2}))?\\b", title, re.I)\n    if se:\n        return "Apple Watch SE" + (" " + se.group(1) if se.group(1) else "")\n    return "Apple Watch"\n\n\ndef product_block(title):\n    watch = apple_watch_block(title)\n    if watch:\n        return watch\n    if re.search(r"\\b(?:MacBook|iMac)\\b", title, re.I):\n        return "MacBook / iMac"\n    for label in ("AirPods", "iPad", "Mac mini", "Mac Studio", "Apple TV", "AirTag"):\n        if re.search(r"\\b" + re.escape(label) + r"\\b", title, re.I):\n            return label\n    return brand_of(title)\n'
s = rep(s, old_block, new_block, "Watch/Mac grouping")
s = rep(s, '    defaults = ["iPhone", "AirPods", "Apple Watch", "iPad", "MacBook", "Apple", "Ray-Ban Meta",\n                "Samsung", "Honor", "Realme", "Huawei", "Tecno", "Xiaomi", "Google", "Dyson",\n                "CPO", "ASIS", "Аксессуары", "Товары"]', '    defaults = ["iPhone", "AirPods", "Apple Watch", "iPad", "MacBook / iMac", "Apple", "Ray-Ban Meta",\n                "Samsung", "Honor", "Realme", "Huawei", "Tecno", "Xiaomi", "Google", "Dyson",\n                "Oura Ring", "CPO", "ASIS", "Аксессуары", "Товары"]', "block defaults")
s = rep(s, '        family = "iPhone" if model else name\n        rank = defaults.index(family) if family in defaults else len(defaults) - 4\n        number = re.search(r"\\d+", model)\n', '        family = "iPhone" if model else ("Apple Watch" if name.startswith("Apple Watch") else name)\n        rank = defaults.index(family) if family in defaults else len(defaults) - 4\n        numbered = model if model else (name if family == "Apple Watch" else "")\n        number = re.search(r"\\d+", numbered)\n', "watch sorting")
s = rep(s, '    @classmethod\n    def from_dict(cls, value):\n        return cls(**{**value, "price": Decimal(value["price"])})\n', '    @classmethod\n    def from_dict(cls, value):\n        data = {**value, "price": Decimal(value["price"])}\n        data["title"] = normal_title(data["title"])\n        if data.get("block") not in {"ASIS", "CPO", "Аксессуары"}:\n            known = iphone_model(data["title"]) or product_block(data["title"])\n            if known:\n                data["block"] = known\n        return cls(**data)\n', "saved row repair")
s = rep(s, '            elif is_header and re.fullmatch(r"(?:2\\s*)?(?:e[\\s-]?)?sim(?:\\s*[+/]\\s*(?:e[\\s-]?)?sim)?", line, re.I):', '            elif is_header and re.fullmatch(r"(?:[12]\\s*)?(?:e[\\s-]?)?sim(?:\\s*[+/&]\\s*(?:[12]\\s*)?(?:e[\\s-]?)?sim)?", line, re.I):', "SIM header")
s = rep(s, '        if model:\n            sim = infer_iphone17_sim(title, model, sim)\n', '', "remove flag SIM inference")
s = rep(s, '        header = "<b>— " + html.escape(block) + " —</b>"', '        header = "<b>" + html.escape(block) + "</b>"', "plain block heading")
p.write_text(s, encoding="utf-8")

cp = Path("catalog_publisher.py")
c = cp.read_text(encoding="utf-8")
c = rep(c, 'def page_title(content):\n    match = re.match(r"<b>— (.*?) —</b>", content)\n    return html.unescape(match[1]) if match else "Прайс"', 'def page_title(content):\n    match = re.match(r"<b>\\s*(?:—\\s*)?(.*?)(?:\\s*—)?\\s*</b>", content)\n    return html.unescape(match[1]) if match else "Прайс"', "catalog heading")
cp.write_text(c, encoding="utf-8")

tp = Path("tests/test_prices.py")
t = tp.read_text(encoding="utf-8")
pattern = r'    def test_country_flag_infers_iphone17_sim\(self\):.*?(?=    def test_explicit_sim_beats_region_fallback\(self\):)'
replacement = '    def test_country_flag_does_not_infer_iphone17_sim(self):\n        item = self.parse("🇺🇸 iPhone 17 256GB Black — 58800").items[0]\n        self.assertEqual(item.sim, "unknown")\n        self.assertEqual(len(select_items([item], Settings(sim_filter="esim"))), 0)\n\n    def test_supplier_sim_markers_drive_iphone17_variants(self):\n        items = self.parse("""iPhone 17\\nSIM + eSIM\\n17 256 Black 🇮🇳 — 62000\\neSIM\\n17 Pro 256 Black 🇺🇸 — 70000\\n2 SIM\\n17 Pro Max 256 Black 🇨🇳 — 80000""").items\n        self.assertEqual([item.sim for item in items], ["hybrid", "esim", "dual"])\n\n'
t, n = re.subn(pattern, replacement, t, count=1, flags=re.S)
if n != 1:
    raise SystemExit(f"regional tests: got {n}")
t = rep(t, '    def test_explicit_sim_beats_region_fallback(self):', '    def test_explicit_sim_marker_is_used(self):', "explicit SIM test name")
t = rep(t, '        self.assertTrue(any("— CPO —" in page for page in contents))', '        self.assertTrue(any("<b>CPO</b>" in page for page in contents))', "CPO heading test")
t = rep(t, '        self.assertTrue(any("— ASIS —" in page for page in contents))', '        self.assertTrue(any("<b>ASIS</b>" in page for page in contents))', "ASIS heading test")
tp.write_text(t, encoding="utf-8")

catalog_test = Path("tests/test_catalog.py")
ct = catalog_test.read_text(encoding="utf-8")
ct = rep(ct, 'self.assertTrue(ordered[0]["content"].startswith("<b>— Dyson —</b>"))', 'self.assertTrue(ordered[0]["content"].startswith("<b>Dyson</b>"))', "catalog heading expectation")
catalog_test.write_text(ct, encoding="utf-8")

complete_test = Path("tests/test_complete_prices.py")
co = complete_test.read_text(encoding="utf-8")
co = rep(co, '["Realme", "Tecno", "Infinix", "OnePlus", "Nothing", "AirPods", "Apple Watch", "iPad", "MacBook"]', '["Realme", "Tecno", "Infinix", "OnePlus", "Nothing", "AirPods", "Apple Watch Series 10", "iPad", "MacBook / iMac"]', "Apple family expectations")
complete_test.write_text(co, encoding="utf-8")

display_test = Path("tests/test_display_format.py")
dt = display_test.read_text(encoding="utf-8")
dt = rep(dt, 'self.assertTrue(content.startswith("<b>— AirPods —</b>"))', 'self.assertTrue(content.startswith("<b>AirPods</b>"))', "display heading expectation")
display_test.write_text(dt, encoding="utf-8")

regression = (
    'import unittest\n'
    'from config import Settings\n'
    'from prices import Item, parse_documents, render_blocks\n\n'
    'class ParserRegressionTests(unittest.TestCase):\n'
    '    def parse(self, text):\n'
    '        return parse_documents([text])\n\n'
    '    def test_1sim_plus_esim_is_hybrid(self):\n'
    '        items = self.parse("iPhone 17\\n1Sim+eSim\\n17 256 Black — 60000\\niPhone 17 Pro 256 White (1Sim+eSim) — 70000").items\n'
    '        self.assertEqual([i.sim for i in items], ["hybrid", "hybrid"])\n'
    '        self.assertIn("SIM + eSIM", "\\n".join(render_blocks(items, Settings()).values()))\n\n'
    '    def test_short_iphone_tb_and_air_blocks(self):\n'
    '        items = self.parse("17 Pro 1TB Orange — 100000\\n17 Pro Max 2TB Silver — 120000\\n17 Air 1TB Blue — 90000").items\n'
    '        self.assertEqual([i.block for i in items], ["iPhone 17 Pro", "iPhone 17 Pro Max", "iPhone Air"])\n'
    '        self.assertTrue(all(i.title.startswith("iPhone ") for i in items))\n\n'
    '    def test_redmi_note_and_oura_do_not_inherit_dji(self):\n'
    '        items = self.parse("DJI\\nOsmo Pocket 3 — 38000\\nNote 17 8/256 Black — 21900\\nOura Ring 4 Silver — 35000").items\n'
    '        self.assertEqual([i.block for i in items], ["DJI / Insta360", "Xiaomi", "Oura Ring"])\n'
    '        self.assertNotIn("DJI Note", items[1].title)\n'
    '        self.assertNotIn("DJI Oura", items[2].title)\n\n'
    '    def test_watch_series_and_macs_are_grouped(self):\n'
    '        items = self.parse("Apple Watch\\nSeries 11 46mm Jet Black — 40000\\nUltra 3 49mm Black — 70000\\nMacBook Air M4 16/256 — 90000\\niMac M4 24 16/256 — 110000").items\n'
    '        self.assertEqual([i.block for i in items], ["Apple Watch Series 11", "Apple Watch Ultra 3", "MacBook / iMac", "MacBook / iMac"])\n'
    '        pages = "\\n".join(render_blocks(items, Settings()).values())\n'
    '        self.assertIn("<b>MacBook / iMac</b>", pages)\n'
    '        self.assertNotIn("<b>— MacBook / iMac —</b>", pages)\n\n'
    '    def test_saved_wrong_block_is_reclassified(self):\n'
    '        item = Item.from_dict({"title": "Oura Ring 4 Silver", "price": "35000", "currency": "RUB", "block": "DJI / Insta360", "sim": "unknown", "accessory": False})\n'
    '        self.assertEqual(item.block, "Oura Ring")\n\n'
    'if __name__ == "__main__":\n'
    '    unittest.main()\n'
)
Path("tests/test_parser_regressions.py").write_text(regression, encoding="utf-8")
