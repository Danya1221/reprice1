import re
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from config import Settings, Source
from prices import Item, parse_documents
from runtime import SyncService, merge_lowest
from state import StateStore


class GlobalDuplicateTests(unittest.IsolatedAsyncioTestCase):
    def test_brand_spellings_merge_at_lowest_price(self):
        text = '''Apple iPad Pro 11 M4 1TB Silver CA — 104 000
iPad Pro 11 M4 1 TB Silver CA — 99 000
Marshall Major V (Black) 🇪🇺 — 5 400
Marshall Major 5 Black 🇪🇺 — 5 200
Redmi Note 15 Pro+ 5G 12/512GB Black 🇪🇺 — 34 800
Note 15 Pro+ 5G 12/512 Black 🇪🇺 — 33 900
Sony WH-1000XM6 Black 🇪🇺 — 25 000
Sony WH-1000XM6 Черный 🇪🇺 — 24 000'''
        rows = merge_lowest([parse_documents([text]).items])
        self.assertEqual(sorted(i.price for i in rows), list(map(Decimal, ['5200', '24000', '33900', '99000'])))

    def test_conditions_and_accessories_do_not_merge(self):
        text = '''iPhone 17 256 Black eSIM — 70 000
CPO
iPhone 17 256 Black eSIM — 65 000
ASIS
iPhone 17 256 Black eSIM — 60 000'''
        rows = parse_documents([text]).items
        self.assertEqual(len(rows), 3)
        self.assertEqual({r.block for r in merge_lowest([rows])}, {'CPO', 'ASIS', 'iPhone 17'})
        normal = Item('Sony device 100 Black', Decimal('10000'), 'RUB', 'Sony')
        accessory = Item('Sony device 100 Black', Decimal('2000'), 'RUB', 'Аксессуары', accessory=True)
        self.assertEqual(len(merge_lowest([[normal, accessory]])), 2)

    def test_condition_on_row_matches_condition_in_heading_only(self):
        cpo1 = Item('iPhone 17 256 Black eSIM CPO', Decimal('65000'), 'RUB', 'CPO', 'esim')
        cpo2 = Item('iPhone 17 256 Black eSIM', Decimal('64000'), 'RUB', 'CPO', 'esim')
        new = Item('iPhone 17 256 Black eSIM', Decimal('70000'), 'RUB', 'iPhone 17', 'esim')
        merged = merge_lowest([[cpo1, cpo2, new]])
        self.assertEqual(sorted(i.price for i in merged), [Decimal('64000'), Decimal('70000')])

    def test_same_document_uses_cheapest_but_newer_document_still_wins(self):
        for brand in ('Honor 600 8/256 Black', 'Sony WH-1000XM6 Black', 'iPad Pro 11 M4 1TB Silver'):
            text = brand + ' — 30 000\n' + brand + ' — 31 000'
            self.assertEqual(parse_documents([text]).items[0].price, Decimal('30000'))
            self.assertEqual(parse_documents([text, brand + ' — 32 000']).items[0].price, Decimal('32000'))

    def test_material_variants_are_never_merged(self):
        text = '''iPhone 17 256 Black SIM — 70 000
iPhone 17 256 Black eSIM — 65 000
iPhone 17 512 Black SIM — 80 000
Redmi Note 15 8/256 Black NFC 🇪🇺 — 20 000
Redmi Note 15 8/256 Black NO NFC 🇪🇺 — 19 000
Sony WH-1000XM6 Black 🇪🇺 — 25 000
Sony WH-1000XM6 Black 🇪🇺 дефект коробки — 23 000
Dyson HS09 Pink с кейсом 🇬🇧 — 40 000
Dyson HS09 Pink без кейса 🇬🇧 — 39 000
Marshall Major 5 Black 🇪🇺 — 5 000
Marshall Major 5 Black 🇬🇧 — 5 100'''
        rows = parse_documents([text]).items
        self.assertEqual(len(merge_lowest([rows])), 11)

    async def test_direct_publication_also_merges_duplicates(self):
        text = '''Apple iPad Pro 11 M4 1TB Silver CA — 104 000
iPad Pro 11 M4 1TB Silver CA — 99 000
Samsung A57 12/512 Gray 🇷🇺 — 42 800
Samsung A57 5G 12/512 A.Gray 🇷🇺 — 42 000'''
        with tempfile.TemporaryDirectory() as folder:
            state = StateStore(Path(folder) / 'state.json')
            settings = Settings(sources=(Source('@hi'), Source('@supplier', mode='feed')), off_hours=False)
            service = SyncService(SimpleNamespace(), settings, state)
            service.publisher.publish = AsyncMock(return_value=0)
            service.publisher.hide_existing = AsyncMock(return_value=0)
            count, _ = await service.render(items=parse_documents([text]).items)
            content = '\n'.join(service.publisher.publish.await_args.args[0].values())
            self.assertEqual(count, 2)
            self.assertEqual(len(re.findall(r'<code>', content)), 2)
            self.assertIn('99 000', content)
            self.assertIn('42 000', content)
            self.assertNotIn('104 000', content)
            self.assertNotIn('42 800', content)
