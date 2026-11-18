import re
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

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

    async def test_publication_merges_a37_and_cross_brand_colour_and_network_aliases(self):
        text = '''Samsung A37 6/128 White 🇿🇦 — 24 700
Samsung A37 5G 6/128 White 🇿🇦 — 24 700
Samsung A37 5Г 6/128 Белый 🇿🇦 — 24 700
Samsung A57 12/512 Navy 🇷🇺 — 42 900
Samsung A57 5G 12/512 A.Navy 🇷🇺 — 43 000
Samsung A57 5Г 12/512 А.Тёмно-синий 🇷🇺 — 42 500
Honor 600 12/256 Orange 🇷🇺 — 40 000
Honor 600 5Г 12/256 Оранжевый 🇷🇺 — 39 000
Google Pixel 10a 8/128 Black 🇪🇺 — 38 000
Google Pixel 10a 5G 8/128 Чёрный 🇪🇺 — 37 500
Redmi Note 15 4G 8/256 White 🇪🇺 — 20 000
Redmi Note 15 5G 8/256 Белый 🇪🇺 — 21 000'''
        with tempfile.TemporaryDirectory() as folder:
            state = StateStore(Path(folder) / 'state.json')
            service = SyncService(SimpleNamespace(), Settings(), state)
            service.publisher.publish = AsyncMock(return_value=0)
            with patch.dict('os.environ', {'RAILWAY_GIT_COMMIT_SHA': 'a' * 40}):
                count, _ = await service.render(items=parse_documents([text]).items)
                status = service.status()
            content = '\n'.join(service.publisher.publish.await_args.args[0].values())
        self.assertEqual(count, 6)
        self.assertEqual(content.count('Samsung A37 6/128 White 🇿🇦 — 24 700'), 1)
        self.assertIn('Samsung A57 12/512 Navy 🇷🇺 — 42 500', content)
        self.assertIn('39 000', content)
        self.assertIn('37 500', content)
        self.assertIn('20 000', content)
        self.assertIn('21 000', content)
        self.assertIn('Версия последней публикации: aaaaaaa', status)

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
