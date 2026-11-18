import unittest
from decimal import Decimal

from prices import Item, display_title, parse_documents

NOTICE = '⚠️⚠️⚠️Важная Информация об обновленной гарантийной политики Apple.'


class AppleWarrantyNoticeTest(unittest.TestCase):
    def test_notice_does_not_become_prefix_for_products_after_it(self):
        rows = parse_documents([NOTICE + '''
SE (2026) 40 Starlight S/M MJKR4 — 22 000
Apple USB Lightning to camera MD821 — 2 500
Apple USB-C 20W 🇪🇺 — 2 400
SE (2024) 40 Starlight Green Loop MXEH3 — 18 700''']).items
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0].title, 'SE (2026) 40 Starlight S/M MJKR4')
        self.assertEqual(rows[0].block, 'Apple Watch')
        self.assertEqual(rows[0].price, Decimal('22000'))
        self.assertEqual(rows[3].block, 'Apple Watch')
        self.assertTrue(all('гарантийн' not in item.title.lower() for item in rows))

    def test_notice_and_product_on_same_line(self):
        result = parse_documents([NOTICE + ' SE (2026) 44 Starlight ML MJL54 — 24 000'])
        self.assertEqual(len(result.items), 1)
        self.assertEqual(result.items[0].title, 'SE (2026) 44 Starlight ML MJL54')
        self.assertEqual(result.items[0].price, Decimal('24000'))

    def test_cached_product_is_cleaned_and_classified(self):
        item = Item.from_dict({'title': NOTICE + ' SE (2024) 40 Starlight Green Loop MXFX3',
                               'price': '18700', 'currency': 'RUB', 'block': 'Apple',
                               'sim': 'unknown', 'accessory': False})
        self.assertEqual(item.block, 'Apple Watch')
        self.assertEqual(display_title(item), 'SE (2024) 40 Starlight Green Loop MXFX3')

    def test_different_product_notes_are_preserved(self):
        title = 'iPhone 17 256 Black гарантийная пломба'
        item = Item(title, Decimal('70000'), 'RUB', 'iPhone 17')
        self.assertIn('гарантийная пломба', display_title(item))
