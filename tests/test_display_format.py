import unittest

from config import Settings
from prices import parse_documents, render_blocks


class DisplayFormatTests(unittest.TestCase):
    def test_full_row_including_price_is_copyable_and_generic_header_removed(self):
        items = parse_documents(["AirPods 4 — 9800"]).items
        content = next(iter(render_blocks(items, Settings()).values()))
        self.assertIn("<code>AirPods 4 — 9 800 ₽</code>", content)
        self.assertNotIn("АКТУАЛЬНЫЙ ПРАЙС", content)
        self.assertTrue(content.startswith("<b>— Apple —</b>"))

    def test_section_sim_is_written_into_copyable_iphone_row(self):
        items = parse_documents(["iPhone 17\neSIM\n17 256 Black — 60000"]).items
        content = next(iter(render_blocks(items, Settings()).values()))
        self.assertIn("<b>— eSIM —</b>", content)
        self.assertIn("<code>iPhone 17 256 Black · eSIM — 60 000 ₽</code>", content)

    def test_unknown_sim_is_visible_in_iphone_row(self):
        items = parse_documents(["iPhone 17 256 Black — 60000"]).items
        content = next(iter(render_blocks(items, Settings()).values()))
        self.assertIn("SIM не указан", content)


if __name__ == "__main__":
    unittest.main()
