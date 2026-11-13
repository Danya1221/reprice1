import unittest

from config import Settings
from prices import build_physical_message, display_title, parse_documents, render_blocks


class DisplayFormatTests(unittest.TestCase):
    def test_honor_models_sort_by_storage_before_ram(self):
        rows = parse_documents(["\n".join([
            "Honor 600 12/512GB Black 🇬🇧 — 44200",
            "Honor 600 12/512GB Golden White 🇬🇧 — 44200",
            "Honor 600 8/256GB Black 🇷🇺 — 38100",
            "Honor 600 8/256GB Orange 🇷🇺 — 37900",
            "Honor 600 8/512GB Black 🇰🇿 — 42000",
        ])]).items
        content = "\n".join(render_blocks(rows, Settings()).values())
        self.assertLess(content.index("8/256GB Black"), content.index("8/512GB Black"))
        self.assertLess(content.index("8/512GB Black"), content.index("12/512GB Black"))

    def test_iphone_duo_and_samsung_a57_are_recognized(self):
        rows = parse_documents(["iPhone Duo 256 Black — 75000\nSamsung A57 8/256 Black — 32000"]).items
        self.assertEqual([item.block for item in rows], ["iPhone Duo", "Samsung A + S25"])
        content = "\n".join(render_blocks(rows, Settings()).values())
        self.assertIn("iPhone Duo 256 Black", content)
        self.assertIn("Samsung A57", content)

    def test_supplier_duo_and_a57_price_rows_are_published(self):
        text = """18 Duo 256 White (eSim) - 194300
18 Duo 256 Night Sky (eSim) - 195300
18 Duo 512 Night Sky (eSim) Актив - 219300
18 Duo 1TB White (eSim) - 288300
18 Duo 2TB Night Sky (eSim) - 325300
iPhone Duo 256 White (eSim) - 194300
Galaxy A57 8/128 Gray SM-A576B/DS🇰🇿
🇰🇿 От 1 шт - 29 000
Galaxy A57 8/256 Navy SM-A576B/DS🇷🇺
🇷🇺 От 1 шт - 32 800
• A57 5G 12/512GB A.Navy 🇲🇾 - 42000₽"""
        result = parse_documents([text])
        self.assertEqual(len(result.items), 9)
        content = "\n".join(render_blocks(result.items, Settings()).values())
        self.assertIn("iPhone 18 Duo 256 White eSIM — 194 300", content)
        self.assertIn("iPhone 18 Duo 512 Night Sky Актив eSIM — 219 300", content)
        self.assertIn("iPhone 18 Duo 1TB White eSIM — 288 300", content)
        self.assertIn("iPhone Duo 256 White eSIM — 194 300", content)
        self.assertIn("Samsung A57 8/128 Gray 🇰🇿 — 29 000", content)
        self.assertIn("Samsung A57 5G 12/512GB A.Navy 🇲🇾 — 42 000", content)
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
        self.assertEqual(titles[1], "iPhone 17 256 Black SIM")
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
        self.assertIn("512 Blue eSIM — 79 800</code>\n<code>iPhone 17 Air 512 Black", content)
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

    def test_samsung_rows_have_one_brand_name_and_no_empty_parentheses(self):
        items = parse_documents(["""Samsung S25 FE 5G 8/256GB () Navy 🇮🇳 — 42000
Galaxy Z Fold7 12/256 Blue Shadow 🇰🇿 — 97600
Samsung Galaxy Z Fold 7 12/256GB Blue Shadow 🇭🇰 — 98000
Samsung Galaxy Watch 8 40mm Silver 🇮🇳 — 16800"""]).items
        titles = [display_title(item) for item in items]
        self.assertEqual(titles[0], "Samsung S25 FE 5G 8/256GB Navy 🇮🇳")
        self.assertEqual(titles[1], "Samsung Z Fold7 12/256 Blue Shadow 🇰🇿")
        self.assertEqual(titles[2], "Samsung Z Fold 7 12/256GB Blue Shadow 🇭🇰")
        self.assertEqual(titles[3], "Samsung Watch 8 40mm Silver 🇮🇳")

    def test_cpo_groups_models_and_storage_with_single_blank_lines(self):
        items = parse_documents(["""iPhone 16 Pro 256 Black CPO eSIM — 76500
iPhone 14 Plus 128 Yellow CPO eSIM — 42500
iPhone 16 Pro 128 White CPO eSIM — 70500
iPhone 16 Pro 128 Black CPO eSIM — 70500
iPhone 16 Pro 256 White CPO eSIM — 76500"""]).items
        content = "\n".join(render_blocks(items, Settings()).values())
        self.assertIn("14 Plus 128 Yellow CPO eSIM — 42 500</code>\n\n<code>iPhone 16 Pro 128", content)
        self.assertIn("128 Black CPO eSIM — 70 500</code>\n<code>iPhone 16 Pro 128 White", content)
        self.assertIn("128 White CPO eSIM — 70 500</code>\n\n<code>iPhone 16 Pro 256", content)


if __name__ == "__main__":
    unittest.main()
