import unittest
from decimal import Decimal

from prices import display_title, parse_documents
from runtime import merge_lowest, select_samsung_regions


class SamsungS26UserRowsTest(unittest.TestCase):
    def test_user_rows_are_merged_without_losing_requested_markets(self):
        text = '''Samsung S26 12/256 S942B Black 🇨🇱 — 61 000
Samsung S26 12/256 S942B Black 🇷🇺 — 61 300
Samsung S26 12/256 (Black) 🇮🇳 — 59 000
Samsung S26 12/256 Black 🇨🇳 — 62 000
Samsung S26 12/256 Черный 🇷🇺 — 61 500
Samsung S26 12/256 S9420 Blue 🇭🇰 — 59 500
Samsung S26 12/256 S942B Blue 🇮🇳 — 58 500
Samsung S26 12/256 S942B Blue 🇷🇺 — 60 000
Samsung S26 12/256 Sky Blue 🇨🇳 — 60 500
Samsung S26 12/256 Sky Blue 🇰🇿 — 58 500
Samsung S26 12/256 Голубой 🇷🇺 — 60 000
Samsung S26 12/256 Blue 🇲🇾 — 58 300
Samsung S26 12/256 Blue HK 2sim 🇭🇰 — 58 300
Samsung S26 12/256 S942B Pink Gold 🇮🇳 — 59 000
Samsung S26 12/256 PinK Gold Malaysia 🇲🇾 — 58 300
Samsung S26 12/256 S942B Violet 🇰🇿 — 59 000
Samsung S26 12/256 S942B Violet 🇷🇺 — 61 000
Samsung S26 12/256 (Cobalt Violet) 🇰🇿 — 58 800
Samsung S26 12/256 Фиолетовый 🇷🇺 — 61 500
Samsung S26 12/256 S942B White 🇨🇱 — 60 000
Samsung S26 12/256 White 🇵🇦 — 60 000'''
        rows = parse_documents([text]).items
        selected = select_samsung_regions(merge_lowest([rows]))
        self.assertEqual(len(rows), 21)
        self.assertEqual(len(selected), 8)
        self.assertEqual({display_title(row): row.price for row in selected}, {
            'Samsung S26 12/256 Black 🇮🇳': Decimal('59000'),
            'Samsung S26 12/256 Black 🇨🇳': Decimal('62000'),
            'Samsung S26 12/256 Sky Blue 2 SIM 🇭🇰': Decimal('58300'),
            'Samsung S26 12/256 Sky Blue 🇨🇳': Decimal('60500'),
            'Samsung S26 12/256 Sky Blue 🇰🇿': Decimal('58500'),
            'Samsung S26 12/256 Pink Gold 🇲🇾': Decimal('58300'),
            'Samsung S26 12/256 Cobalt Violet 🇰🇿': Decimal('58800'),
            'Samsung S26 12/256 White 🇨🇱': Decimal('60000'),
        })

    def test_different_memory_condition_and_conflicting_markets_stay_separate(self):
        text = '''Samsung S26 12/256 Pink Gold 🇲🇾 — 58 300
Samsung S26 12/512 Pink Gold 🇲🇾 — 65 000
Samsung S26 12/256 Pink Gold Уценка 🇲🇾 — 50 000
Samsung S26 12/256 Pink Gold Malaysia 🇷🇺 — 59 000'''
        self.assertEqual(len(merge_lowest([parse_documents([text]).items])), 4)
