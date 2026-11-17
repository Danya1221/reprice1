import unittest
from decimal import Decimal

from config import Settings
from prices import parse_documents, render_blocks


class GarminArticleTest(unittest.TestCase):
    def test_article_rows_are_not_products_and_names_keep_prices(self):
        text = '''Fenix 9 pro
▫️ Garmin Fenix 9 Pro 51mm balck inReach — 113 000 ₽
      арт. 010-04337-11
▫️ Garmin Fenix 9 Pro 51 mm Titanium grey — 102 000 ₽
      арт. 010-04337-26
▫️ Garmin Fenix 9 Pro 51 mm Black — 107 000 ₽
      арт. 010-04337-16
▫️ Garmin Fenix 9 Pro 47 mm Black inReach — 99 000 ₽
      арт. 010-04336-11
▫️ Garmin Fenix 9 Pro 47 mm Titanium grey — 95 000 ₽
      арт. 010-04336-26
▫️ Garmin Fenix 9 Pro 47 mm Titanium grey inReach — 99 000 ₽
      арт. 010-04336-21
▫️ Garmin Fenix 9 Pro 43 mm Shell Pink/Autumn Orange inReach — 92 000 ₽
      арт. 010-04335-01'''
        items = parse_documents([text]).items
        self.assertEqual(len(items), 7)
        self.assertEqual([item.price for item in items], list(map(Decimal, ['113000', '102000', '107000', '99000', '95000', '99000', '92000'])))
        self.assertTrue(all(item.title.startswith('Garmin Fenix 9 Pro') and item.block == 'Garmin' for item in items))
        output = '\n'.join(render_blocks(items, Settings()).values())
        self.assertIn('Garmin Fenix 9 Pro 51mm balck inReach', output)
        self.assertNotIn('арт. 010-', output)

    def test_article_between_name_and_price_does_not_break_join(self):
        result = parse_documents(['Garmin Fenix 9 Pro 47mm Black\nарт. 010-04336-11\n99 000 ₽'])
        self.assertEqual([(item.title, item.price) for item in result.items],
                         [('Garmin Fenix 9 Pro 47mm Black', Decimal('99000'))])
