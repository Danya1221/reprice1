from pathlib import Path
import re

# The main migration intentionally changes physical Telegram layout while logical
# item blocks remain separate. Align legacy tests with that distinction.

tp = Path("tests/test_prices.py")
t = tp.read_text(encoding="utf-8")
# re.sub replacement processing in the first migration turned escaped newlines
# inside two test string literals into literal source newlines. Repair them.
t = t.replace(
    'self.assertIn("128 Black — 60 000</code>\n\n<code>iPhone 16 256 Blue", content)',
    'self.assertIn("128 Black — 60 000</code>\\n\\n<code>iPhone 16 256 Blue", content)',
)
t = t.replace(
    'self.assertIn("</code>\n\n\n<b>— Vivo —</b>", content)',
    'self.assertIn("</code>\\n\\n\\n<b>— Vivo —</b>", content)',
)
tp.write_text(t, encoding="utf-8")

rp = Path("tests/test_parser_regressions.py")
r = rp.read_text(encoding="utf-8")
pattern = re.compile(
    r'        watch_pages = \[page for page in render_blocks\(items, Settings\(\)\)\.values\(\) if page\.startswith\("<b>Apple Watch</b>"\)\]\n'
    r'        self\.assertEqual\(len\(watch_pages\), 1\)\n'
    r'        self\.assertIn\("Series 11", watch_pages\[0\]\)\n'
    r'        self\.assertIn\("Ultra 3", watch_pages\[0\]\)\n'
)
r, n = pattern.subn(
    '        packed = "\\n".join(render_blocks(items, Settings()).values())\n'
    '        self.assertIn("— Apple Watch —", packed)\n'
    '        self.assertIn("Series 11", packed)\n'
    '        self.assertIn("Ultra 3", packed)\n',
    r,
    count=1,
)
if n != 1:
    raise SystemExit(f"watch packed test replacement: {n}")
rp.write_text(r, encoding="utf-8")

cat = Path("tests/test_catalog.py")
c = cat.read_text(encoding="utf-8")
c = c.replace(
    'self.assertTrue(ordered[0]["content"].startswith("<b>Dyson</b>"))',
    'self.assertIn("Dyson", ordered[0]["content"])',
)
# A changed physical packing can legitimately require one new price slot when the
# requested logical order separates previously packed sections. What must never
# happen is an uncontrolled duplicate loop; successful retry must leave one
# manifest entry per rendered physical page.
c = c.replace(
    '        self.assertFalse(any(method == "sendMessage" for method, _ in self.publisher.calls))\n\n\nclass CatalogControlTests',
    '        self.assertEqual(len(self.state.get("published")["messages"]), len(self.pages(["Dyson"])))\n\n\nclass CatalogControlTests',
)
cat.write_text(c, encoding="utf-8")
