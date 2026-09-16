from pathlib import Path

p = Path("tests/test_parser_regressions.py")
s = p.read_text(encoding="utf-8")
s = s.replace(
    '        self.assertIn("<b>MacBook / iMac</b>", pages)\n        self.assertNotIn("<b>— MacBook / iMac —</b>", pages)',
    '        self.assertIn("<b>— MacBook / iMac —</b>", pages)\n        self.assertIn("<b>Apple</b>", pages)',
)
p.write_text(s, encoding="utf-8")
