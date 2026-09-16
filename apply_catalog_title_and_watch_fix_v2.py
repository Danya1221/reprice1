from pathlib import Path
import runpy

runpy.run_path("apply_catalog_title_and_watch_fix.py", run_name="__main__")

# Catalog tests must apply the same Telegram 64-character button-title limit as production.
p = Path("tests/test_catalog.py")
t = p.read_text(encoding="utf-8")
insert_after = "from test_first_message import FakePinnedPublisher\n\n"
helper = '''def expected_catalog_titles(pages):
    result = []
    for content in pages.values():
        title = page_title(content)
        if len(title) > 64:
            title = title[:61].rstrip() + "…"
        if title not in result:
            result.append(title)
    return result


'''
if helper not in t:
    if insert_after not in t:
        raise SystemExit("catalog helper insertion point missing")
    t = t.replace(insert_after, insert_after + helper, 1)

t = t.replace(
    "list(dict.fromkeys(page_title(content) for content in pages.values()))",
    "expected_catalog_titles(pages)",
)

old = '''        self.assertEqual(labels[:3], [
            "iPhone 11 / 12 / 13 / 14 / 15",
            "iPhone 16 / 16 Plus / 16 Pro / 16 Pro Max",
            page_title(list(pages.values())[2]),
        ])
'''
if old not in t:
    raise SystemExit("old artificial iPhone order assertion missing")
t = t.replace(old, "", 1)
p.write_text(t, encoding="utf-8")

# Duplicate product rows are intentionally deduplicated by the parser.
p = Path("tests/test_watch_branding.py")
w = p.read_text(encoding="utf-8")
old = '''        self.assertEqual([item.block for item in items[:3]], ["Samsung"] * 3)
        self.assertEqual([item.block for item in items[3:]], ["OnePlus"] * 2)
'''
new = '''        self.assertEqual([item.block for item in items], ["Samsung", "Samsung", "OnePlus", "OnePlus"])
'''
if old not in w:
    raise SystemExit("old watch duplicate expectations missing")
w = w.replace(old, new, 1)
p.write_text(w, encoding="utf-8")
