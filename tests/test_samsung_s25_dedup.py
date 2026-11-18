import unittest
from decimal import Decimal

from prices import Item, display_title, parse_documents
from runtime import merge_lowest, select_samsung_regions


class SamsungS25DedupTest(unittest.TestCase):
    def test_user_supplier_rows_keep_correct_variants_and_prices(self):
        sample = 'Samsung S25 12/256GB Ice Blue 🇮🇳 — 50 500\n🇮🇳S25 12/256 S931B Silver — 50 300\nSamsung S25 12/256 silver🇮🇳 — 50 300\nSamsung S25 12/256GB Silver Shadow 🇮🇳 — 48 800\n🇭🇰S25 12/256 S9310 Mint — 49 500\n🇮🇳S25 12/256 S931B Icyblue — 50 300\n🇮🇳S25 12/256 S931B Mint — 50 300\n🇮🇳S25 12/256 S931B Navy — 50 300\nSamsung S25 12/256 (Icyblue) IN — 49 100\nSamsung S25 12/256 Blueblack (IN) — 52 800\nSamsung S25 12/256 Mint (IN) — 48 400\nSamsung S25 12/256GB Icyblue 🇮🇳 — 50 400\nSamsung S25 12/256GB Mint 🇨🇳 — 49 500\nSamsung S25 12/256GB Navy 🇮🇳 — 50 400\nSamsung S25 12/256GB Mint 🇮🇳 — 48 800\n🇮🇳S25 FE 8/128 S731B Jetblack — 38 200\nSamsung S25 FE 8/128GB Jet Black 🇮🇳 — 37 500\nSamsung S25 FE 8/128GB White 🇮🇳 — 38 000\n🇮🇳S25 FE 8/128 S731B Navy — 38 000\nSamsung S25 FE 5G 8/128GB Navy 🇮🇳 — 37 800\nSamsung S25 FE 8/128GB Navy 🇦🇪 — 37 500\n🇮🇳S25 FE 8/256 S731B Jetblack — 43 000\n🇷🇺S25 FE 8/256 S731B Jetblack — 43 300\nSamsung S25 FE 8/256 (Jetblack) IN — 42 200\nSamsung S25 FE 5G 8/256GB JetBlack 🇮🇳 — 43 000\nSamsung S25 FE 8/256GB Jet Black 🇮🇳 — 42 800\nSamsung S25 FE 8/256GB Ice Blue 🇦🇪 — 42 800\n🇸🇬S25 FE 8/256 S731B Icyblue — 43 000\n🇮🇳S25 FE 8/256 S731B Navy — 43 000\nSamsung S25 FE 5G 8/256GB IceBlue 🇲🇾 — 43 000\nSamsung S25 FE 5G 8/256GB Navy 🇮🇳 — 43 000\nSamsung S25 FE 8/256 iceblue🇷🇺 — 43 000\nSamsung S25 FE 8/256GB Navy 🇦🇪 — 42 800\n🇦🇪S25 Ultra 12/256 S938B Blue — 67 400\n🇦🇪S25 Ultra 12/256 S938B Gray — 67 400\nSamsung S25 Ultra 12/256GB Gray 🇲🇾 — 66 500\nSamsung S25 Ultra 12/512GB Black — 72 000\nSamsung S25 Ultra 12/512GB White Silver 🇻🇳 — 72 000\n🇨🇱S25 Ultra 12/1024 S938B Blue — 79 000\nSamsung S25 Ultra 12/1TB Silver Blue Arg — 78 700\nSamsung S25 Ultra 12/1TB Grey Arab 🇦🇪 — 79 000\nSamsung S25 Ultra 12/1TB White Silver Arg — 77 900\n'
        items = select_samsung_regions(merge_lowest([parse_documents([sample]).items]))
        prices = [item.price for item in items]
        self.assertEqual(len(items), 23)
        for value in (49100, 48800, 48400, 49500, 37500, 42200, 43300, 42800):
            self.assertIn(Decimal(value), prices)
        for value in (50500, 50400, 38200):
            self.assertNotIn(Decimal(value), prices)
        silver = [i for i in items if 'Silver Shadow' in i.title]
        self.assertEqual([i.price for i in silver], [Decimal('48800')])
        indian_mint = [i for i in items if 'Mint (IN)' in i.title]
        self.assertEqual([i.price for i in indian_mint], [Decimal('48400')])
        self.assertIn('🇮🇳', display_title(indian_mint[0]))

    def test_differing_hardware_market_condition_and_memory_remain_distinct(self):
        sample = """Samsung S25 12/256 S931B Mint 🇮🇳 — 50 000
Samsung S25 12/256 S9310 Mint 🇮🇳 — 51 000
Samsung S25 12/256 S931B Mint уценка 🇮🇳 — 45 000
Samsung S25 12/512 S931B Mint 🇮🇳 — 60 000
Samsung S25 FE 8/256 S731B Mint 🇮🇳 — 43 000
Samsung S25 12/256 Mint 🇭🇰 — 52 000
Samsung S25 12/256 Mint 🇨🇳 — 53 000
Samsung S26 12/256 4G Black 🇮🇳 — 40 000
Samsung S26 12/256 5G Black 🇮🇳 — 42 000"""
        items = select_samsung_regions(merge_lowest([parse_documents([sample]).items]))
        self.assertEqual(len(items), 9)

    def test_s25_ultra_memory_and_known_region_variants(self):
        sample = """🇨🇱S25 Ultra 12/1024 S938B Blue — 79 000
Samsung S25 Ultra 12/1TB Blue 🇨🇱 — 78 000
Samsung S25 Ultra 12/512 Blue 🇨🇱 — 70 000
Samsung S25 Ultra 12/1TB Blue 🇭🇰 — 80 000"""
        items = select_samsung_regions(merge_lowest([parse_documents([sample]).items]))
        self.assertEqual(sorted(i.price for i in items), [Decimal('70000'), Decimal('78000'), Decimal('80000')])
        self.assertTrue(display_title(items[0]).startswith('🇨🇱Samsung ') or display_title(items[0]).startswith('Samsung '))
