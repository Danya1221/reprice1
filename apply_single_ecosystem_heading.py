from pathlib import Path


def rep(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected 1 match, got {count}")
    return text.replace(old, new, 1)


p = Path("prices.py")
s = p.read_text(encoding="utf-8")
old = '''    if len(sections) == 1 and not bundle:\n        section = sections[0]\n        return "<b>" + html.escape(section["title"]) + "</b>\\n\\n" + section["body"]\n'''
new = '''    if len(sections) == 1 and not bundle:\n        section = sections[0]\n        heading = physical_brand_label(section["title"])\n        if heading != section["title"]:\n            return (\n                "<b>" + html.escape(heading) + "</b>\\n\\n"\n                + "<b>— " + html.escape(section["title"]) + " —</b>\\n\\n"\n                + section["body"]\n            )\n        return "<b>" + html.escape(section["title"]) + "</b>\\n\\n" + section["body"]\n'''
s = rep(s, old, new, "single ecosystem physical heading")
p.write_text(s, encoding="utf-8")

p = Path("tests/test_prices.py")
t = p.read_text(encoding="utf-8")
marker = '    def test_samsung_sections_stay_together_and_do_not_mix_with_honor(self):\n'
extra = '''    def test_single_apple_section_still_has_apple_as_top_heading(self):\n        for source, section in [\n            ("Mac mini M4 16/256 Silver — 68800", "Mac mini"),\n            ("Apple TV 4K 64GB — 20200", "Apple TV"),\n            ("MacBook Air M4 16/256 Silver — 90000", "MacBook / iMac"),\n        ]:\n            items = self.parse(source).items\n            content = next(iter(render_blocks(items, Settings()).values()))\n            self.assertTrue(content.startswith("<b>Apple</b>\\n\\n"), content)\n            self.assertIn(f"<b>— {section} —</b>", content)\n\n    def test_single_samsung_section_still_has_samsung_as_top_heading(self):\n        items = self.parse("S26 12/256 Black — 64300").items\n        content = next(iter(render_blocks(items, Settings()).values()))\n        self.assertTrue(content.startswith("<b>Samsung</b>\\n\\n"), content)\n        self.assertIn("<b>— Samsung S26 —</b>", content)\n\n'''
if marker not in t:
    raise SystemExit("test insertion marker missing")
t = t.replace(marker, extra + marker, 1)
p.write_text(t, encoding="utf-8")
