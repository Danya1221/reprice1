import unittest
from decimal import Decimal
from prices import Item, display_title, parse_documents
from runtime import merge_lowest, select_samsung_regions


class SamsungInventoryDedupTest(unittest.TestCase):
    def test_examples_from_supplier_prices(self):
        text = '''Samsung A27 6/128GB Black 🇷🇺 — 21 600
Samsung A27 5G 6/128GB Black 🇷🇺 — 21 300
🇷🇺Samsung A27 8/256 Pink — 26 300
Samsung A27 8/256GB Light Pink 🇷🇺 — 26 100
🇷🇺Samsung A57 12/512 Gray — 42 800
Samsung A57 5G 12/512GB A.Gray 🇷🇺 — 42 100
🇷🇺Samsung A57 12/512 Gray — 42 000
Samsung A57 5G 12/512GB A.Gray 🇲🇾 — 42 000
Samsung A17 4G 8/256 Black (MY) — 20 000
Samsung A17 4G 8/256GB Black 🇲🇾 — 20 400
🇰🇿Samsung S26+ 12/256 S947B Blue — 69 500
Samsung S26+ 12/256GB Sky Blue 🇰🇿 — 69 500
🇷🇺Samsung S26 Ultra 12/256 S948B White — 80 000
Samsung S26 Ultra 12/256GB Белый 🇷🇺 — 79 500
Samsung ️L355 Watch 9 44 LTE silver🇷🇺 — 23 700
🇷🇺Samsung Watch 9 LTE 44mm Silver — 24 000
Samsung Z Fold 8 12/512 Lavender (KZ) — 121 000
Samsung Z Fold8 12/512 Lavender 🇰🇿 — 122 000'''
        items = select_samsung_regions(merge_lowest([parse_documents([text]).items]))
        self.assertEqual(sorted(i.price for i in items), list(map(Decimal, ['20000','21300','23700','26100','42000','69500','79500','121000'])))
        for item in items:
            self.assertTrue(display_title(item).startswith('Samsung '))
        self.assertTrue(all(display_title(i).endswith(('🇷🇺', '🇲🇾', '🇰🇿')) for i in items))

    def test_regional_and_condition_variants_remain_distinct(self):
        text = '''Samsung S26+ 12/256 Sky Blue 🇮🇳 — 60 000
Samsung S26+ 12/256 Blue 🇰🇿 — 61 000
Samsung S26+ 12/256 Blue 🇨🇳 — 62 000
Samsung S26+ 12/256 Blue 🇭🇰 — 63 000
Samsung S26+ 12/256 Blue 🇷🇺 — 64 000
Samsung S26+ 12/256 Sky Blue Уценка 🇮🇳 — 55 000
Samsung A17 4G 8/256 Black 🇲🇾 — 20 000
Samsung A17 5G 8/256 Black 🇲🇾 — 22 000
Samsung A17 8/256 Black 🇲🇾 — 21 000
Samsung Watch 9 44mm LTE Silver 🇷🇺 — 23 700
Samsung Watch 9 44mm Silver 🇷🇺 — 22 000
Samsung Z Fold8 12/512 Lavender 🇰🇿 — 121 000
Samsung Z Fold8 16/512 Lavender 🇰🇿 — 122 000'''
        items = select_samsung_regions(merge_lowest([parse_documents([text]).items]))
        self.assertEqual(len(items), 13)

    def test_same_source_same_title_keeps_cheapest_but_later_document_can_raise_price(self):
        rows = ['Samsung A57 12/512 Gray 🇷🇺 — 42 000\nSamsung A57 12/512 Gray 🇷🇺 — 42 800']
        self.assertEqual(parse_documents(rows).items[0].price, Decimal('42000'))
        rows.append('Samsung A57 12/512 Gray 🇷🇺 — 43 000')
        self.assertEqual(parse_documents(rows).items[0].price, Decimal('43000'))

    def test_aliases_and_model_codes_are_only_applied_to_matching_models(self):
        text = """Samsung A07s 4/64 violet 🇰🇿 — 10 700
Samsung A07s 4/64GB Light Violet 🇰🇿 — 10 800
Samsung A18 8/256GB Light Blue 🇷🇺 — 23 400
🇷🇺Samsung A18 8/256 blue — 23 300
Samsung A37 5G 8/256GB A.Greygreen 🇰🇿 — 29 400
Samsung A376 8/256 Gray Green 🇰🇿 — 29 300
Samsung S26 12/256 Cobalt Violet (KZ) — 58 800
🇰🇿Samsung S26 12/256 S942B Violet — 58 500
Samsung S26 FE 5G 8/256GB Blueberry 🇷🇺 — 45 000
🇷🇺Samsung S26 FE 8/256 S741B Blueberry — 44 500
Samsung S26 FE 8/256 Blueberry 2SIM 🇷🇺 — 44 000
Samsung S26+ 12/256 S942B Blue 🇰🇿 — 50 000"""
        items = select_samsung_regions(merge_lowest([parse_documents([text]).items]))
        self.assertEqual(sorted(i.price for i in items), list(map(Decimal, ['10700', '23300', '29300', '44000', '44500', '50000', '58500'])))

    def test_mislabelled_iphone_air_is_not_in_samsung(self):
        for title in ['Samsung Air 256GB Cloud White eSim 🇺🇸', 'Samsung Air 512GB Light Gold eSim 🇯🇵']:
            item = Item.from_dict({'title': title, 'price': '73500', 'currency': 'RUB', 'block': 'Samsung', 'sim': 'esim', 'accessory': False})
            self.assertEqual(item.block, 'iPhone Air')
            self.assertTrue(display_title(item).startswith('iPhone Air '))
