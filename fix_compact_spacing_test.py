from pathlib import Path

p = Path("apply_compact_catalog_spacing.py")
text = p.read_text(encoding="utf-8")
text = text.replace(
    'self.assertIn("A17 8/256GB Blue — 17 800</code>\\n\\n<code>Samsung A27", page)',
    'self.assertIn("A17 8/256GB Blue — 17 800</code>\\n\\n<code>A27", page)',
)
text = text.replace(
    'self.assertIn("S25 12/256 Navy — 50 500</code>\\n\\n<code>Samsung S25 Ultra", page)',
    'self.assertIn("S25 12/256 Navy — 50 500</code>\\n\\n<code>S25 Ultra", page)',
)
p.write_text(text, encoding="utf-8")
