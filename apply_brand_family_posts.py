from pathlib import Path
import re


def replace_once(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected 1 match, got {count}")
    return text.replace(old, new, 1)


p = Path("prices.py")
s = p.read_text(encoding="utf-8")

old_defaults = '''    defaults = ["iPhone", "AirPods", "Apple Watch", "iPad", "MacBook / iMac", "Apple", "Ray-Ban Meta",
                "Samsung", "Honor", "Realme", "Huawei", "Tecno", "Xiaomi", "Google", "COROS", "Rode", "Dyson",
                "Oura Ring", "CPO", "ASIS", "Аксессуары", "Товары"]
'''
new_defaults = '''    defaults = ["iPhone", "AirPods", "Apple Watch", "iPad", "MacBook / iMac", "Mac mini", "Mac Studio",
                "Apple TV", "AirTag", "Apple", "Ray-Ban Meta", "Samsung", "Honor", "Realme", "Huawei", "Tecno",
                "Xiaomi", "Google", "COROS", "Rode", "Dyson", "Oura Ring", "CPO", "ASIS", "Аксессуары", "Товары"]
'''
s = replace_once(s, old_defaults, new_defaults, "Apple block ordering")

start = s.index("def pack_physical_sections(sections, limit=3950):")
end = s.index("\n\ndef render_blocks(", start)
new_pack = r'''def physical_section_family(title):
    """Keep major ecosystems together instead of mixing them with unrelated brands."""
    if title in {"CPO", "ASIS", "Аксессуары"}:
        return "isolated"
    label = physical_brand_label(title)
    if label == "Apple":
        return "apple"
    if label == "Samsung":
        return "samsung"
    return "other"


def pack_physical_sections(sections, limit=3950):
    """Pack by ecosystem: Apple together, Samsung together, other small brands separately."""
    physical = []
    pending = []
    pending_family = ""
    seen_iphone_bundles = set()

    def flush_pending():
        nonlocal pending, pending_family
        if pending:
            physical.append(("", pending))
            pending = []
            pending_family = ""

    index = 0
    while index < len(sections):
        section = sections[index]
        bundle = iphone_bundle_key(section["title"])
        if bundle:
            flush_pending()
            if bundle not in seen_iphone_bundles:
                seen_iphone_bundles.add(bundle)
                bundle_sections = [entry for entry in sections if iphone_bundle_key(entry["title"]) == bundle]
                for batch in split_bundle_sections(bundle_sections, bundle, limit):
                    physical.append((bundle, batch))
            index += 1
            continue

        family = physical_section_family(section["title"])
        if family == "isolated":
            flush_pending()
            physical.append(("", [section]))
            index += 1
            continue

        # Never let Apple/Samsung spill into Honor, Kodak, Marshall, Oura, etc.
        if pending and family != pending_family:
            flush_pending()

        candidate = pending + [section]
        if pending and units(build_physical_message(candidate)) > limit:
            flush_pending()
            pending = [section]
            pending_family = family
        else:
            pending = candidate
            pending_family = family
        index += 1

    flush_pending()
    return physical
'''
s = s[:start] + new_pack + s[end:]
p.write_text(s, encoding="utf-8")


tp = Path("tests/test_prices.py")
t = tp.read_text(encoding="utf-8")

old = '''        pages = render_blocks(items, Settings())
        headers = [content.split("\\n", 1)[0] for content in pages.values()]
        self.assertIn("<b>Samsung A + S25</b>", headers)
        self.assertIn("<b>Samsung S26</b>", headers)
        self.assertIn("<b>Samsung Fold / Flip</b>", headers)
'''
new = '''        pages = render_blocks(items, Settings())
        text = "\\n".join(pages.values())
        self.assertIn("<b>Samsung</b>", text)
        self.assertIn("<b>— Samsung A + S25 —</b>", text)
        self.assertIn("<b>— Samsung S26 —</b>", text)
        self.assertIn("<b>— Samsung Fold / Flip —</b>", text)
'''
t = replace_once(t, old, new, "Samsung series rendering test")

old = '''        pages = render_blocks(items, Settings())
        headers = [content.split("\\n", 1)[0] for content in pages.values()]
        self.assertIn("<b>Samsung A + S25</b>", headers)
        self.assertIn("<b>Samsung S26</b>", headers)
        self.assertIn("<b>Samsung Tab S</b>", headers)
'''
new = '''        pages = render_blocks(items, Settings())
        text = "\\n".join(pages.values())
        self.assertIn("<b>Samsung</b>", text)
        self.assertIn("<b>— Samsung A + S25 —</b>", text)
        self.assertIn("<b>— Samsung S26 —</b>", text)
        self.assertIn("<b>— Samsung Tab S —</b>", text)
'''
t = replace_once(t, old, new, "Samsung phone/tablet rendering test")

old = '''        pages = render_blocks(items, Settings())
        text = "\\n".join(pages.values())
        self.assertIn("<b>Samsung Buds</b>", text)
        self.assertIn("<b>— Galaxy A —</b>", text)
        self.assertIn("<b>— Galaxy S25 —</b>", text)
        self.assertIn("<b>Samsung S26</b>", text)
        combined = next(page for page in pages.values() if page.startswith("<b>Samsung A + S25</b>"))
        self.assertLess(combined.index("Galaxy A"), combined.index("Galaxy S25"))
'''
new = '''        pages = render_blocks(items, Settings())
        text = "\\n".join(pages.values())
        self.assertIn("<b>Samsung</b>", text)
        self.assertIn("<b>— Samsung Buds —</b>", text)
        self.assertIn("<b>— Galaxy A —</b>", text)
        self.assertIn("<b>— Galaxy S25 —</b>", text)
        self.assertIn("<b>— Samsung S26 —</b>", text)
        combined = next(page for page in pages.values() if page.startswith("<b>Samsung</b>"))
        self.assertLess(combined.index("Galaxy A"), combined.index("Galaxy S25"))
'''
t = replace_once(t, old, new, "Supplier Samsung rendering test")

marker = '    def test_blank_line_is_added_when_model_changes(self):\n'
extra = '''    def test_apple_sections_stay_together_and_never_mix_with_other_brands(self):\n        items = self.parse("""Kodak Mini Shot 3 — 12000\nMac mini M4 16/256 Silver — 68800\niMac M4 24 16/256 Blue — 110000\nMacBook Air M4 16/256 — 90000\nMarshall Major V Black — 5400\nOura Ring 5 Size 8 Black — 35800\nApple TV 4K 64GB — 20200""").items\n        pages = list(render_blocks(items, Settings()).values())\n        apple = next(page for page in pages if page.startswith("<b>Apple</b>"))\n        for label in ["MacBook / iMac", "Mac mini", "Apple TV"]:\n            self.assertIn(label, apple)\n        for foreign in ["Kodak", "Marshall", "Oura Ring"]:\n            self.assertNotIn("— " + foreign, apple)\n\n    def test_samsung_sections_stay_together_and_do_not_mix_with_honor(self):\n        items = self.parse("""Buds 4 Black — 9000\nA27 8/256 Blue — 23800\nS25 Ultra 12/256 Black — 65000\nS26 12/256 Black — 64900\nHonor 400 12/256 Black — 33000""").items\n        pages = list(render_blocks(items, Settings()).values())\n        samsung = next(page for page in pages if page.startswith("<b>Samsung</b>"))\n        self.assertIn("Samsung Buds", samsung)\n        self.assertIn("Samsung A + S25", samsung)\n        self.assertIn("Samsung S26", samsung)\n        self.assertNotIn("Honor", samsung)\n\n'''
if marker not in t:
    raise SystemExit("test insertion marker missing")
t = t.replace(marker, extra + marker, 1)
tp.write_text(t, encoding="utf-8")


cp = Path("tests/test_catalog.py")
c = cp.read_text(encoding="utf-8")
marker = '    async def test_existing_price_slots_stay_prices_and_catalog_is_last(self):\n'
extra = '''    async def test_catalog_buttons_change_in_same_publish_when_page_family_changes(self):\n        first = {"old:0": "<b>Honor</b>\\n\\n<code>Honor 400 — 30000</code>"}\n        await self.publisher.publish(first)\n        message_id = next(iter(self.state.get("published")["messages"].values()))["id"]\n        self.publisher.calls.clear()\n        second = {"new:0": "<b>Samsung</b>\\n\\n<b>— Samsung S26 —</b>\\n<code>S26 12/256 — 65000</code>"}\n        await self.publisher.publish(second)\n        manifest = self.state.get("published")["messages"]\n        self.assertEqual(next(iter(manifest.values()))["id"], message_id)\n        buttons = [b for row in self.catalog_edit()["reply_markup"]["inline_keyboard"] for b in row]\n        self.assertEqual([b["text"] for b in buttons], ["Samsung"])\n        self.assertFalse(any(method == "sendMessage" for method, _ in self.publisher.calls))\n\n'''
if marker not in c:
    raise SystemExit("catalog test insertion marker missing")
c = c.replace(marker, extra + marker, 1)
cp.write_text(c, encoding="utf-8")
