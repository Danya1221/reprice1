from pathlib import Path


def rep(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected 1 match, got {count}")
    return text.replace(old, new, 1)

p = Path("tests/test_complete_prices.py")
s = p.read_text(encoding="utf-8")
s = rep(
    s,
    '        self.assertIn("Не активированное", content)\n        self.assertNotIn("Статус не указан", content)\n',
    '        self.assertNotIn("Не активированное", content)\n        self.assertNotIn("Статус не указан", content)\n',
    "inactive heading expectation",
)
p.write_text(s, encoding="utf-8")

p = Path("tests/test_catalog.py")
s = p.read_text(encoding="utf-8")
s = rep(
    s,
    '        self.publisher.calls.clear()\n        await self.publisher.publish(self.pages(["Dyson", "AirPods", "iPhone 17"]))\n        self.assertEqual(self.publisher.calls, [])\n',
    '        self.publisher.calls.clear()\n        await self.publisher.publish(self.pages(["Dyson", "AirPods", "iPhone 17"]))\n        self.assertFalse(any(method == "sendMessage" for method, _ in self.publisher.calls))\n',
    "catalog existence probe expectation",
)
p.write_text(s, encoding="utf-8")

p = Path("tests/test_prices.py")
s = p.read_text(encoding="utf-8")
s = s.replace(
    'self.assertIn("12/128 Cobalt Violet — 57 300</code>\\n\\n<code>Galaxy S26 12/256", content)',
    'self.assertIn("12/128 Cobalt Violet — 57 300</code>\\n\\n<code>S26 12/256", content)',
)
s = s.replace(
    'self.assertIn("12/256 Sky Blue — 63 000</code>\\n\\n<code>Galaxy S26 12/512", content)',
    'self.assertIn("12/256 Sky Blue — 63 000</code>\\n\\n<code>S26 12/512", content)',
)
p.write_text(s, encoding="utf-8")
