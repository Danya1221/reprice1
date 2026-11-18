import unittest
from decimal import Decimal

from prices import parse_documents
from runtime import merge_lowest, select_samsung_regions


class SamsungDeduplicationTest(unittest.TestCase):
    def test_a08_supplier_examples_keep_lowest_per_memory_and_colour(self):
        text = '''Samsung A08 4/128GB Dark Blue 🇷🇺 — 14 100
🇷🇺Samsung A08 4/128 blue — 14 800
Samsung A08 4/128GB Green 🇷🇺 — 14 100
Samsung A08 4/128GB Silver 🇷🇺 — 14 100
Samsung A08 4/128GB Navy 🇷🇺 — 13 500
Samsung A08 6/128GB Dark Blue 🇷🇺 — 16 000
🇷🇺Samsung A08 6/128 blue — 16 700
Samsung A08 6/128GB Green 🇷🇺 — 16 000
Samsung A08 6/128GB Silver 🇷🇺 — 16 000'''
        items = select_samsung_regions(merge_lowest([parse_documents([text]).items]))
        self.assertEqual(sorted(i.price for i in items), list(map(Decimal, ['13500', '14100', '14100', '16000', '16000', '16000'])))
        self.assertEqual(len(items), 6)

    def test_translated_colours_merge_across_series_but_specifications_stay_separate(self):
        text = '''Samsung S26 12/256GB White 🇷🇺 — 60 000
Galaxy S26 12/256 Белый 🇷🇺 — 59 000
Samsung Z Fold7 12/256GB Gray 🇷🇺 — 90 000
Galaxy Z Fold7 12/256 Grey 🇷🇺 — 89 000
Samsung A08 4/128 Dark Blue 🇷🇺 — 14 100
Samsung A08 6/128 Blue 🇷🇺 — 16 000
Samsung A08 4/128 Dark Blue уценка 🇷🇺 — 12 000
Samsung A08 4/128 Dark Blue 🇰🇿 — 13 000
Samsung S26 12/256 Dark Blue 🇷🇺 — 61 000
Samsung S26 12/256 Blue 🇷🇺 — 62 000'''
        items = merge_lowest([parse_documents([text]).items])
        self.assertEqual(sorted(i.price for i in items), list(map(Decimal, ['12000', '13000', '14100', '16000', '59000', '61000', '62000', '89000'])))

    def test_cheapest_plus_requested_regions_survive(self):
        text = '''Samsung A08 4/128 Blue 🇷🇺 — 14 100
Samsung A08 4/128 Navy 🇰🇿 — 14 000
Samsung A08 4/128 Dark Blue 🇨🇳 — 14 200
Samsung A08 4/128 Dark Blue 🇭🇰 — 14 300
Samsung A08 4/128 Blue 🇦🇪 — 13 500
Samsung A08 4/128 Navy 🇮🇳 — 13 700'''
        items = select_samsung_regions(merge_lowest([parse_documents([text]).items]))
        self.assertEqual(sorted(i.price for i in items), list(map(Decimal, ['13500', '14000', '14100', '14200', '14300'])))
