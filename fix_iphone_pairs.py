from pathlib import Path


def rep(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected 1 match, got {count}")
    return text.replace(old, new, 1)

# The first migration has already transformed prices.py at this point.
p = Path("prices.py")
s = p.read_text(encoding="utf-8")
s = rep(
    s,
    '''def iphone_storage_key(item):
    title = FLAGS.sub("", clean(item.title))
    match = re.search(r"\\b(\\d{2,4})\\s*(GB|TB)?\\b", title, re.I)
    if not match:
        return ""
    value = int(match.group(1))
    unit = (match.group(2) or "GB").upper()
    return f"{value}{unit}"
''',
    '''def iphone_storage_key(item):
    title = FLAGS.sub("", clean(item.title))
    model = iphone_model(title)
    if model:
        title = re.sub(re.escape(model), "", title, count=1, flags=re.I).strip()
    match = re.search(r"\\b(\\d{1,4})\\s*(GB|TB)?\\b", title, re.I)
    if not match:
        return ""
    value = int(match.group(1))
    unit = (match.group(2) or "GB").upper()
    return f"{value}{unit}"
''',
    "iPhone storage after model name",
)
p.write_text(s, encoding="utf-8")

# Catalog: show buttons only for models that actually exist inside a paired post.
cp = Path("catalog_publisher.py")
c = cp.read_text(encoding="utf-8")
start = c.index("def catalog_labels(title):")
end = c.index("\n\ndef catalog_group(title):", start)
new_func = '''def catalog_labels(content):
    """Return direct iPhone buttons for models actually present in a physical post."""
    title = page_title(content)
    if title.casefold().startswith("iphone") and "/" in title:
        labels = re.findall(
            r"<b>—\\s*(iPhone\\s+\\d{1,2}(?:\\s+(?:Plus|Pro(?:\\s+Max)?))?)\\s*—</b>",
            content,
            re.I,
        )
        result = []
        for label in labels:
            canonical = re.sub(r"\\s+", " ", label).strip()
            canonical = re.sub(r"\\bplus\\b", "Plus", canonical, flags=re.I)
            canonical = re.sub(r"\\bpro\\s+max\\b", "Pro Max", canonical, flags=re.I)
            canonical = re.sub(r"\\bpro\\b", "Pro", canonical, flags=re.I)
            if canonical not in result:
                result.append(canonical)
        if result:
            return result
    return [catalog_group(title)]
'''
c = c[:start] + new_func + c[end:]
c = rep(c, "            labels = catalog_labels(page_title(content))\n", "            labels = catalog_labels(content)\n", "catalog labels from rendered content")
cp.write_text(c, encoding="utf-8")

# Align older tests with the new paired publication scheme.
tp = Path("tests/test_prices.py")
t = tp.read_text(encoding="utf-8")
t = t.replace(
    'self.assertEqual([i.block for i in items], ["iPhone 17", "iPhone 17 Pro Max"])',
    'self.assertEqual([i.block for i in items], ["iPhone 17 / 17 Plus", "iPhone 17 Pro / 17 Pro Max"])',
)
t = t.replace(
    'self.assertEqual({item.block for item in items}, {"iPhone 16", "iPhone 16 Plus", "iPhone 16 Pro"})',
    'self.assertEqual({item.block for item in items}, {"iPhone 16 / 16 Plus", "iPhone 16 Pro / 16 Pro Max"})',
)
t = t.replace(
    'iphone16 = next(page for page in pages.values() if page.startswith("<b>iPhone 16</b>"))\n        iphone16plus = next(page for page in pages.values() if page.startswith("<b>iPhone 16 Plus</b>"))\n        self.assertIn("— Не активированное —", iphone16)\n        self.assertIn("— Актив —", iphone16)\n        self.assertIn("— Не активированное —", iphone16plus)\n        self.assertIn("— Актив —", iphone16plus)',
    'iphone16 = next(page for page in pages.values() if page.startswith("<b>iPhone 16 / 16 Plus</b>"))\n        self.assertIn("— iPhone 16 —", iphone16)\n        self.assertIn("— iPhone 16 Plus —", iphone16)\n        self.assertGreaterEqual(iphone16.count("— Не активированное —"), 2)\n        self.assertGreaterEqual(iphone16.count("— Актив —"), 2)',
)
t = t.replace(
    'self.assertEqual([i.block for i in select_items(items, Settings(), {"disabled_blocks": ["Dyson"]})], ["iPhone 17"])',
    'self.assertEqual([i.block for i in select_items(items, Settings(), {"disabled_blocks": ["Dyson"]})], ["iPhone 17 / 17 Plus"])',
)
tp.write_text(t, encoding="utf-8")

rp = Path("tests/test_parser_regressions.py")
r = rp.read_text(encoding="utf-8")
r = r.replace(
    'self.assertEqual([i.block for i in items], ["iPhone 17 Pro", "iPhone 17 Pro Max", "iPhone Air"])',
    'self.assertEqual([i.block for i in items], ["iPhone 17 Pro / 17 Pro Max", "iPhone 17 Pro / 17 Pro Max", "iPhone Air"])',
)
old_set = '''        self.assertEqual({item.block for item in items}, {
            "iPhone 11 Pro Max", "iPhone 12", "iPhone 13", "iPhone 14",
            "iPhone 15", "iPhone 15 Plus", "iPhone 15 Pro",
        })
        pages = render_blocks(items, Settings())
        headers = {page.split("\\n", 1)[0] for page in pages.values()}
        self.assertIn("<b>iPhone 13</b>", headers)
        self.assertIn("<b>iPhone 15 Plus</b>", headers)
        self.assertIn("<b>iPhone 15 Pro</b>", headers)
'''
new_set = '''        self.assertEqual({item.block for item in items}, {
            "iPhone 11 Pro / 11 Pro Max", "iPhone 12 / 12 Plus",
            "iPhone 13 / 13 Plus", "iPhone 14 / 14 Plus",
            "iPhone 15 / 15 Plus", "iPhone 15 Pro / 15 Pro Max",
        })
        pages = render_blocks(items, Settings())
        headers = {page.split("\\n", 1)[0] for page in pages.values()}
        self.assertIn("<b>iPhone 13 / 13 Plus</b>", headers)
        self.assertIn("<b>iPhone 15 / 15 Plus</b>", headers)
        self.assertIn("<b>iPhone 15 Pro / 15 Pro Max</b>", headers)
'''
r = rep(r, old_set, new_set, "old iPhone 11-15 regression expectations")
rp.write_text(r, encoding="utf-8")

cat = Path("tests/test_catalog.py")
ct = cat.read_text(encoding="utf-8")
ct = ct.replace(
    'self.assertEqual(self.options["block_order"], ["Dyson", "iPhone 17"])',
    'self.assertEqual(self.options["block_order"], ["Dyson", "iPhone 17 / 17 Plus"])',
)
cat.write_text(ct, encoding="utf-8")
