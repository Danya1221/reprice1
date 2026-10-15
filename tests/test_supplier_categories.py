"""Regression coverage for the supplier rows reported under «Товары»."""
import unittest
from collections import Counter

from config import Settings
from prices import Item, parse_documents, render_blocks, select_items


SAMPLE = """Meta Oakley HSTN S51 (F Black, L Clear) 00W8002🇺🇸 — 30 300
Meta Oakley HSTN S51 (F Black, L Prizm Black Polarized) 00W8002🇺🇸 — 32 600
Meta Oakley HSTN S51 (F Warm Grey, L Prizm Ruby) 00W8002🇺🇸 — 30 300
16Е 128 Black 🇺🇸 — 43 800
Gadgess Premium Corning Glass - 17 — 470
Gadgess Premium Corning Glass - 17 Pro — 480
Gadgess Premium Corning Glass - 17 Pro Max — 480
Gadgess Premium Corning Glass - Air — 470
Hero 12 Black🇪🇺 — 22 300
Hero 13 Black🇪🇺 — 27 500
Instax mini 13 Candy Pink — 9 800
Instax mini 13 Candy Pink 🇨🇳 — 9 300
Instax mini 13 Dreamy Blue — 9 800
Instax mini 13 Dreamy Blue 🇨🇳 — 9 300
Instax mini 13 Dreamy Purple — 9 800
Instax mini 13 Dreamy Purple 🇨🇳 — 9 300
Instax mini 13 Dreamy White — 9 800
Instax mini 13 Dreamy White 🇨🇳 — 9 300
Instax mini 13 Lagoon Green — 9 800
Instax mini 13 Lagoon Green 🇨🇳 — 9 300
Magic Mouse 3 USB-C (White) 🖱️ — 6 200
Plaud Note Pro Black — 16 300
Plaud Note Pro Silver — 16 300
Sennheiser HD620S Black🇪🇺 — 22 100
X8 Pro Max. 12/256 Blue 🇪🇺 — 40 800
Яндекс Стрит Зеленая Алиса — 8 500
Яндекс Стрит Зеленая Алиса 🇷🇺 — 8 000
Яндекс Стрит Оранжевая Алиса — 8 500
Яндекс Стрит Оранжевая Алиса 🇷🇺 — 8 000"""

EXPECTED_BLOCKS = (
    ["Oakley Meta"] * 3 + ["iPhone 16e"] + ["Аксессуары"] * 4
    + ["GoPro"] * 2 + ["Kodak / Fujifilm"] * 10 + ["Apple Accessories"]
    + ["PLAUD"] * 2 + ["Sennheiser", "Xiaomi"] + ["Яндекс"] * 4
)


class SupplierCategoryTests(unittest.TestCase):
    def test_all_reported_rows_get_categories_without_price_or_country_loss(self):
        result = parse_documents(["Товары\n" + SAMPLE])
        self.assertFalse(result.rejected)
        self.assertEqual(len(result.items), 29)
        self.assertEqual([item.block for item in result.items], EXPECTED_BLOCKS)
        self.assertEqual(result.items[3].title, "iPhone 16e 128 Black 🇺🇸")
        self.assertEqual([str(item.price) for item in result.items[:10]],
                         ["30300", "32600", "30300", "43800", "470", "480", "480", "470", "22300", "27500"])
        instax = [item for item in result.items if item.block == "Kodak / Fujifilm"]
        self.assertEqual(Counter(("🇨🇳" in item.title, str(item.price)) for item in instax),
                         {(False, "9800"): 5, (True, "9300"): 5})
        self.assertTrue(all(not item.accessory for item in result.items if item.block == "Oakley Meta"))
        self.assertEqual([item.accessory for item in result.items[4:8]], [True] * 4)
        self.assertFalse(result.items[20].accessory)

        pages = render_blocks(result.items, Settings())
        self.assertEqual(sum(page.count("<code>") for page in pages.values()), 29)
        self.assertNotIn("Товары", "\n".join(pages.values()))
        self.assertIn("<b>— Apple Accessories —</b>", "\n".join(pages.values()))

    def test_already_cached_goods_are_reclassified_without_refetch(self):
        fresh = parse_documents([SAMPLE]).items
        cached = []
        for row, item in zip(SAMPLE.splitlines(), fresh):
            value = item.to_dict()
            value.update(title=row.rsplit(" — ", 1)[0], block="Товары", accessory=False)
            cached.append(Item.from_dict(value))
        self.assertEqual([item.block for item in cached], EXPECTED_BLOCKS)
        self.assertEqual([item.key for item in cached], [item.key for item in fresh])
        self.assertEqual([item.accessory for item in cached], [item.accessory for item in fresh])

    def test_screen_protectors_obey_accessory_filter_in_fresh_and_cached_prices(self):
        fresh = parse_documents([SAMPLE]).items
        cached = [Item.from_dict(item.to_dict()) for item in fresh]
        for items in (fresh, cached):
            selected = select_items(items, Settings(allow_accessories=False))
            self.assertEqual(len(selected), 25)
            self.assertFalse(any("Gadgess" in item.title for item in selected))
            self.assertTrue(any("Magic Mouse" in item.title for item in selected))
            self.assertEqual(len(select_items(items, Settings(), {"allow_accessories": True})), 29)

    def test_known_rows_do_not_inherit_previous_supplier_section(self):
        result = parse_documents(["Samsung\nS26 12/256 Black — 65000\n" + SAMPLE])
        self.assertEqual([item.block for item in result.items[1:]], EXPECTED_BLOCKS)
        self.assertFalse(any(item.title.startswith("Samsung ") for item in result.items[1:]))

    def test_nearby_names_are_not_assigned_to_unrelated_brands(self):
        rows = [
            ("Meta Quest 3 512GB White — 40000", "Товары", False),
            ("Honor X8 8/128 Black — 20000", "Honor", False),
            ("X8 12/256 Blue — 20000", "Товары", False),
            ("Hero Stand Black — 2000", "Товары", False),
            ("Oakley Meta HSTN Clear Glasses — 30000", "Oakley Meta", False),
            ("PLAUD Note Pro Black — 16000", "PLAUD", False),
        ]
        for row, block, accessory in rows:
            with self.subTest(row=row):
                item = parse_documents([row]).items[0]
                self.assertEqual((item.block, item.accessory), (block, accessory))

    def test_iphone_e_variants_share_one_canonical_name(self):
        for model in ("16Е", "16е", "16E", "16e", "16 Е", "iPhone 16Е", "Айфон 16Е"):
            with self.subTest(model=model):
                item = parse_documents([f"{model} 128 Black 🇺🇸 — 43800"]).items[0]
                self.assertEqual(item.title, "iPhone 16e 128 Black 🇺🇸")
                self.assertEqual(item.block, "iPhone 16e")


if __name__ == "__main__":
    unittest.main()
