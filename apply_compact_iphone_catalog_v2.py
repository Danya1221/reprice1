from pathlib import Path
import runpy

# Apply the actual compact-catalog migration first.
runpy.run_path("apply_compact_iphone_catalog.py", run_name="__main__")

p = Path("tests/test_catalog.py")
t = p.read_text(encoding="utf-8")

old = '        self.assertEqual([b["text"] for b in buttons], ["Другое", "Apple", "iPhone 17"])\n'
new = '        self.assertEqual([b["text"] for b in buttons], ["iPhone 17", "Apple", "Другое"])\n'
if old not in t:
    raise SystemExit("old catalog ordering assertion missing")
t = t.replace(old, new, 1)

old = '        self.assertEqual(self.state.get("catalog_layout_version"), 3)\n'
new = '        self.assertEqual(self.state.get("catalog_layout_version"), 4)\n'
if old not in t:
    raise SystemExit("old catalog layout version assertion missing")
t = t.replace(old, new, 1)

# The first visible button is now iPhone 17, so the Dyson link assertion must
# locate its button by label instead of relying on array position.
old = '        self.assertEqual(buttons[0]["url"], f"https://t.me/c/777/{ordered[0][\'id\']}")\n'
new = '        dyson_button = next(button for button in buttons if button["text"] == "Другое")\n        self.assertEqual(dyson_button["url"], f"https://t.me/c/777/{ordered[0][\'id\']}")\n'
if old not in t:
    raise SystemExit("old first-button URL assertion missing")
t = t.replace(old, new, 1)

p.write_text(t, encoding="utf-8")
