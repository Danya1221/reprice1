import unittest

from config import Settings
from prices import display_title, parse_documents, render_blocks


class DisplayFormatTests(unittest.TestCase):
    def test_full_row_including_price_is_copyable_and_generic_header_removed(self):
        items = parse_documents(["AirPods 4 — 9800"]).items
        content = next(iter(render_blocks(items, Settings()).values()))
        self.assertIn("<code>AirPods 4 — 9 800</code>", content)
        self.assertNotIn("₽", content)
        self.assertNotIn("АКТУАЛЬНЫЙ ПРАЙС", content)
        self.assertTrue(content.startswith("<b>Apple</b>"))
        self.assertIn("<b>— AirPods —</b>", content)

    def test_section_sim_is_written_into_copyable_iphone_row(self):
        items = parse_documents(["iPhone 17\neSIM\n17 256 Black — 60000"]).items
        content = next(iter(render_blocks(items, Settings()).values()))
        self.assertIn("<b>— eSIM —</b>", content)
        self.assertIn("<code>iPhone 17 256 Black · eSIM — 60 000</code>", content)
        self.assertNotIn("₽", content)

    def test_unknown_sim_keeps_copyable_row_without_placeholder(self):
        items = parse_documents(["iPhone 17 256 Black — 60000\niPhone 17 256 White eSIM — 61000"]).items
        content = next(iter(render_blocks(items, Settings()).values()))
        row = "<code>iPhone 17 256 Black — 60 000</code>"
        self.assertIn(row, content)
        self.assertNotIn("SIM не указан", content)
        self.assertNotIn("SIM-карта не определена", content)
        self.assertNotIn("<b>—  —</b>", content)
        self.assertIn("<code>iPhone 17 256 White · eSIM — 61 000</code>", content)
        self.assertLess(content.index(row), content.index("<b>— eSIM —</b>"))

    def test_iphone_flags_hidden_and_known_sim_has_one_consistent_label(self):
        items = parse_documents(["""iPhone 17 256 Black (1Sim+eSim) 🇮🇳 — 62000
Apple iPhone 17 256GB SIM+eSIM Black — 64000
iPhone 17 Pro 512 Blue eSim ASIS 🇯🇵 — 96000
iPhone 17 Pro Max 1TB Blue 🇯🇵 — 147600
Dyson HS08 🇪🇺 — 40000"""]).items
        titles = [display_title(item) for item in items]
        self.assertEqual(titles[0], "iPhone 17 256 Black · SIM + eSIM")
        self.assertEqual(titles[1], "Apple iPhone 17 256GB Black · SIM + eSIM")
        self.assertEqual(titles[2], "iPhone 17 Pro 512 Blue ASIS · eSIM")
        self.assertEqual(titles[3], "iPhone 17 Pro Max 1TB Blue")
        self.assertIn("🇪🇺", titles[4])
        self.assertEqual(items[3].sim, "unknown")
        self.assertIn("🇯🇵", items[3].title)


if __name__ == "__main__":
    unittest.main()
