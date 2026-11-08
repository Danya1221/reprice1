import unittest

from config import Settings
from prices import build_physical_message, display_title, parse_documents, render_blocks


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
        self.assertIn("<code>iPhone 17 256 Black eSIM — 60 000</code>", content)
        self.assertNotIn("₽", content)

    def test_unknown_sim_keeps_copyable_row_without_placeholder(self):
        items = parse_documents(["iPhone 17 256 Black — 60000\niPhone 17 256 White eSIM — 61000"]).items
        content = next(iter(render_blocks(items, Settings()).values()))
        row = "<code>iPhone 17 256 Black — 60 000</code>"
        self.assertIn(row, content)
        self.assertNotIn("SIM не указан", content)
        self.assertNotIn("SIM-карта не определена", content)
        self.assertNotIn("<b>—  —</b>", content)
        self.assertIn("<code>iPhone 17 256 White eSIM — 61 000</code>", content)
        self.assertLess(content.index(row), content.index("<b>— eSIM —</b>"))

    def test_iphone_flags_hidden_and_known_sim_has_one_consistent_label(self):
        items = parse_documents(["""iPhone 17 256 Black (1Sim+eSim) 🇮🇳 — 62000
Apple iPhone 17 256GB SIM+eSIM Black — 64000
iPhone 17 Pro 512 Blue eSim ASIS 🇯🇵 — 96000
iPhone 17 Pro Max 1TB Blue 🇯🇵 — 147600
Dyson HS08 🇪🇺 — 40000"""]).items
        titles = [display_title(item) for item in items]
        self.assertEqual(titles[0], "iPhone 17 256 Black SIM")
        self.assertEqual(titles[1], "iPhone 17 256GB Black SIM")
        self.assertEqual(titles[2], "iPhone 17 Pro 512 Blue ASIS eSIM")
        self.assertEqual(titles[3], "iPhone 17 Pro Max 1TB Blue eSIM")
        self.assertIn("🇪🇺", titles[4])
        self.assertEqual(items[3].sim, "esim")
        self.assertIn("🇯🇵", items[3].title)

    def test_air_is_sorted_by_storage_with_spacing_only_between_capacities(self):
        items = parse_documents(["""Apple iPhone 17 Air 512GB eSIM Blue — 79800
iPhone 17 Air 1TB Black (eSim) — 90200
iPhone 17 Air 1TB Blue (eSim) — 85500
iPhone 17 Air 256 Black (eSim) — 75000
iPhone 17 Air 256 Blue (eSim) — 73500
iPhone 17 Air 512 Black (eSim) — 87500"""]).items
        content = "\n".join(render_blocks(items, Settings()).values())
        self.assertIn("256 Black eSIM — 75 000</code>\n<code>iPhone 17 Air 256 Blue", content)
        self.assertIn("256 Blue eSIM — 73 500</code>\n\n<code>", content)
        self.assertIn("512GB Blue eSIM — 79 800</code>\n<code>iPhone 17 Air 512 Black", content)
        self.assertIn("512 Black eSIM — 87 500</code>\n\n<code>iPhone 17 Air 1TB", content)
        self.assertNotIn("· eSIM", content)

    def test_apple_prefix_is_removed_from_iphone_rows(self):
        items = parse_documents(["""Apple iPhone 17 Pro Max 1TB SIM+eSIM Orange — 157100
Apple iPhone 17 Pro Max 2TB SIM+eSIM Silver — 173100
iPhone 17 Pro Max 1TB Blue (1Sim+eSim) — 161300"""]).items
        content = "\n".join(render_blocks(items, Settings()).values())
        self.assertIn("iPhone 17 Pro Max 1TB Orange SIM — 157 100", content)
        self.assertIn("iPhone 17 Pro Max 2TB Silver SIM — 173 100", content)
        self.assertNotIn("Apple iPhone", content)

    def test_macbook_same_configuration_colours_are_adjacent(self):
        items = parse_documents(["""Air 15 MC6J4 Silver (M4 24/512) — 138000
Air 15 MC7C4 Sky Blue (M4 16/512) — 111500
Air 15 MC6K4 Starlight (M4 24/512) — 129500
Air 15 MC6L4 Midnight (M4 24/512) — 132500"""]).items
        content = "\n".join(render_blocks(items, Settings()).values())
        self.assertIn("MC6J4 Silver (M4 24/512) — 138 000</code>\n<code>", content)
        self.assertIn("MC6K4 Starlight (M4 24/512) — 129 500</code>\n<code>", content)
        self.assertIn("MC6L4 Midnight (M4 24/512) — 132 500", content)

    def test_adjacent_macbook_chunks_share_one_heading_in_same_post(self):
        content = build_physical_message([
            {"title": "MacBook / iMac", "body": "<code>MacBook Air 1</code>", "part": 0},
            {"title": "MacBook / iMac", "body": "<code>MacBook Air 2</code>", "part": 1},
        ])
        self.assertEqual(content.count("— MacBook / iMac —"), 1)
        self.assertIn("<code>MacBook Air 1</code>\n<code>MacBook Air 2</code>", content)


if __name__ == "__main__":
    unittest.main()
