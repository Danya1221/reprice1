import unittest
from decimal import Decimal

from config import Settings
from prices import (Item, amount_value, merge_sources, parse_documents, render_blocks,
                    select_items, units, marked_price)


class PriceTests(unittest.TestCase):
    def parse(self, text):
        return parse_documents([text])

    def test_iphone_variants_country_condition_and_markup(self):
        result = self.parse("""iPhone: 17
🇺🇸 17e 256GB Black - 58 800
🇮🇳 17e 256GB Black - 62 900
16 Pro 256 Natural CPO 🇺🇸 — 79 000
15 Pro Max 256 Black 🇯🇵 — 85 000
12 128 White 🇮🇳 — 33 000""")
        self.assertEqual(len(result.items), 5)
        self.assertEqual(result.items[2].block, "CPO")
        self.assertIn("CPO", result.items[2].title)
        self.assertIn("🇺🇸", result.items[2].title)
        self.assertEqual(result.items[0].price, Decimal("58800"))
        self.assertEqual(marked_price(result.items[0], Settings(markup=Decimal(500))), Decimal(59300))

    def test_pro_max_is_separate_from_base(self):
        items = self.parse("iPhone 17 256 Black — 60000\niPhone 17 Pro Max 256 Black — 90000").items
        self.assertEqual([i.block for i in items], ["iPhone 17 / 17 Plus", "iPhone 17 Pro / 17 Pro Max"])

    def test_sim_header_and_explicit_variants(self):
        items = self.parse("""iPhone 17
eSIM
17 256 Black — 60000
SIM
17 256 Black — 62000
17 256 Black 2 SIM — 65000""").items
        self.assertEqual([i.sim for i in items], ["esim", "sim", "dual"])
        self.assertEqual(len(select_items(items, Settings(sim_filter="sim"))), 2)
        self.assertEqual(len(select_items(items, Settings(sim_filter="esim"))), 1)

    def test_country_flag_does_not_infer_iphone17_sim(self):
        item = self.parse("🇺🇸 iPhone 17 256GB Black — 58800").items[0]
        self.assertEqual(item.sim, "unknown")
        self.assertEqual(len(select_items([item], Settings(sim_filter="esim"))), 0)

    def test_supplier_sim_markers_drive_iphone17_variants(self):
        items = self.parse("""iPhone 17
SIM + eSIM
17 256 Black 🇮🇳 — 62000
eSIM
17 Pro 256 Black 🇺🇸 — 70000
2 SIM
17 Pro Max 256 Black 🇨🇳 — 80000""").items
        self.assertEqual([item.sim for item in items], ["hybrid", "esim", "dual"])

    def test_explicit_sim_marker_is_used(self):
        item = self.parse("🇺🇸 iPhone 17 256 Black SIM + eSIM — 60000").items[0]
        self.assertEqual(item.sim, "hybrid")

    def test_google_plus_and_samsung_sm_code(self):
        result = self.parse("Samsung Galaxy S26+ SM-S947B 12/256 Cobalt Violet 🇦🇪 — 68300")
        self.assertEqual(result.items[0].block, "Samsung S26")
        self.assertIn("S26+", result.items[0].title)
        self.assertNotIn("SM-", result.items[0].title)
        self.assertIn("🇦🇪", result.items[0].title)

    def test_brands_are_not_misclassified(self):
        items = self.parse("""LEGO Star Wars 75313 — 78000
Dyson Airwrap HS08 — 42000
Canon EOS R50 — 51000
DJI Osmo Pocket 3 — 38000
Insta360 X5 — 41000
Google Fitbit Air Obsidian — 10700
Ray-Ban Meta Wayfarer S50 Black — 32000
Ray-Ban Meta Skyler S53 Brown — 34000""").items
        self.assertEqual([i.block for i in items], [
            "LEGO", "Dyson", "Canon", "DJI / Insta360", "DJI / Insta360", "Google",
            "Ray-Ban Meta", "Ray-Ban Meta"])
        self.assertIn(" M ", items[-2].title)
        self.assertIn(" L ", items[-1].title)

    def test_note_14s_and_rode_do_not_inherit_dji_section(self):
        items = self.parse("""DJI / Insta360
Note 14S 8/256 Aurora Purple 🇪🇺 — 17800
Note 14S 8/256 Midnight Black 🇪🇺 — 17800
RODE Wireless Me Dual Set 🇷🇺 — 12700
RODE Wireless Pro 🇷🇺 — 23300
DJI Osmo Pocket 4 Creator Combo — 44800
Insta360 X6 Standard Bundle — 51100""").items
        self.assertEqual([item.block for item in items], [
            "Xiaomi", "Xiaomi", "Rode", "Rode", "DJI / Insta360", "DJI / Insta360"
        ])
        self.assertTrue(items[0].title.startswith("Note 14S"))
        self.assertTrue(items[2].title.startswith("RODE Wireless"))
        self.assertNotIn("DJI / Insta360 Note", items[0].title)
        self.assertNotIn("DJI / Insta360 RODE", items[2].title)

    def test_samsung_series_are_read_and_split_into_catalog_blocks(self):
        items = self.parse("""Samsung
Galaxy A
A27 8/256 Black 🇪🇺 — 24000
A37 8/256 Awesome Graphite 🇪🇺 — 35000
A57 12/256 Blue 🇪🇺 — 42000
Galaxy S
S26 12/256 Black 🇦🇪 — 65000
S26+ 12/256 Cobalt Violet 🇦🇪 — 68300
S26 Ultra 12/512 Titanium Black 🇦🇪 — 99000
Galaxy Z
Z Fold 7 12/256 Black 🇦🇪 — 120000
Z Flip 7 12/256 Mint 🇦🇪 — 78000""").items
        self.assertEqual([item.block for item in items], [
            "Samsung A + S25", "Samsung A + S25", "Samsung A + S25",
            "Samsung S26", "Samsung S26", "Samsung S26",
            "Samsung Fold / Flip", "Samsung Fold / Flip",
        ])
        pages = render_blocks(items, Settings())
        headers = [content.split("\n", 1)[0] for content in pages.values()]
        self.assertIn("<b>Samsung A + S25</b>", headers)
        self.assertIn("<b>Samsung S26</b>", headers)
        self.assertIn("<b>Samsung Fold / Flip</b>", headers)

    def test_coros_does_not_inherit_xiaomi_context(self):
        items = self.parse("""Xiaomi
Coros Pace 4 Black Nylon 🇨🇳 — 21000
Coros Pace 4 Black Silicone 🇨🇳 — 22000
Coros Pace 4 Ice Crystal Black Silicone 🇨🇳 — 22700
Coros Pace 4 Jacob&Co collaboration fabric strap version 🇨🇳 — 25300""").items
        self.assertEqual([item.block for item in items], ["COROS"] * 4)
        self.assertTrue(all(item.title.startswith("Coros ") for item in items))
        self.assertTrue(all(not item.title.startswith("Xiaomi ") for item in items))

    def test_requested_samsung_phone_split_and_tab_tablets(self):
        items = self.parse("""Samsung
Galaxy A
A37 8/256 Black — 35000
A57 12/256 Blue — 42000
Galaxy S25
S25 12/256 Black — 65000
S25 Ultra 12/512 Titanium — 90000
Galaxy S26
S26 12/256 Black — 68000
S26+ 12/256 Cobalt Violet — 71000
S26 Ultra 12/512 Titanium Black — 99000
Galaxy Tab S
Tab S10 12/256 Grey — 65000
Tab S11 Ultra 12/512 Silver — 98000""").items
        self.assertEqual([item.block for item in items], [
            "Samsung A + S25", "Samsung A + S25",
            "Samsung A + S25", "Samsung A + S25",
            "Samsung S26", "Samsung S26", "Samsung S26",
            "Samsung Tab S", "Samsung Tab S",
        ])
        self.assertTrue(all(not item.title.startswith("Samsung A +") for item in items))
        pages = render_blocks(items, Settings())
        headers = [content.split("\n", 1)[0] for content in pages.values()]
        self.assertIn("<b>Samsung A + S25</b>", headers)
        self.assertIn("<b>Samsung S26</b>", headers)
        self.assertIn("<b>Samsung Tab S</b>", headers)

    def test_supplier_style_samsung_sections(self):
        items = self.parse("""Buds 3 FE Black 🇦🇪 - 6400
Buds 4 White 🇰🇿 - 10400
🇷🇺A17 6/128GB Gray — 15000
🇷🇺A27 8/256GB Blue — 23800
🇷🇺A57 12/512GB Navy — 40500
S25 FE 8/128 Navy 🇮🇳 - 37400
S25 12/128 Navy 🇪🇺 - 45000
S25 Edge 12/256 Jetblack 🇪🇺 - 49100
S25 Ultra 12/1TB Gray 🇨🇱 - 79400
S26 FE 8/128 Graphite 🇿🇦 - 46500
S26+ 12/256 White 🇰🇿 - 69500
S26 Ultra 16/1TB Black 🇨🇱 - 117500""").items
        self.assertEqual([item.block for item in items], [
            "Samsung Buds", "Samsung Buds",
            "Samsung A + S25", "Samsung A + S25", "Samsung A + S25",
            "Samsung A + S25", "Samsung A + S25", "Samsung A + S25", "Samsung A + S25",
            "Samsung S26", "Samsung S26", "Samsung S26",
        ])
        pages = render_blocks(items, Settings())
        text = "\n".join(pages.values())
        self.assertIn("<b>Samsung Buds</b>", text)
        self.assertIn("<b>— Galaxy A —</b>", text)
        self.assertIn("<b>— Galaxy S25 —</b>", text)
        self.assertIn("<b>Samsung S26</b>", text)
        combined = next(page for page in pages.values() if page.startswith("<b>Samsung A + S25</b>"))
        self.assertLess(combined.index("Galaxy A"), combined.index("Galaxy S25"))

    def test_all_apple_watches_share_one_block(self):
        items = self.parse("""Apple Watch
Series 11 46mm Black — 40000
SE 3 44mm Silver — 30000
Ultra 3 49mm Black — 70000""").items
        self.assertEqual([item.block for item in items], ["Apple Watch"] * 3)
        pages = render_blocks(items, Settings())
        self.assertEqual(len(pages), 1)
        self.assertTrue(next(iter(pages.values())).startswith("<b>Apple Watch</b>"))

    def test_only_requested_galaxy_a_models_are_auto_detected(self):
        items = self.parse("""A17 6/128GB Gray — 15000
A27 6/128GB Black — 20800
A37 8/128GB Lavender — 23900
A57 8/256GB Navy — 31800""").items
        self.assertEqual([item.block for item in items], ["Samsung A + S25"] * 4)
        self.assertEqual([item.title.split()[0] for item in items], ["A17", "A27", "A37", "A57"])

    def test_other_bare_a_models_are_not_assumed_to_be_samsung(self):
        items = self.parse("""A15 8/256 Black — 19900
A55 8/256 Blue — 29900""").items
        self.assertEqual([item.block for item in items], ["Товары", "Товары"])

    def test_iphone_base_plus_and_pro_pairs_share_messages(self):
        items = self.parse("""iPhone 16 128 Black — 60000
iPhone 16 256 Blue — 65000
iPhone 16 Plus 128 Pink — 70000
iPhone 16 Plus 512 White — 82000
iPhone 16 Pro 256 Black — 90000
iPhone 16 Pro Max 256 Natural — 100000""").items
        self.assertEqual({item.block for item in items}, {
            "iPhone 16 / 16 Plus", "iPhone 16 Pro / 16 Pro Max"
        })
        pages = render_blocks(items, Settings())
        contents = list(pages.values())
        base = next(page for page in contents if page.startswith("<b>iPhone 16 / 16 Plus</b>"))
        pro = next(page for page in contents if page.startswith("<b>iPhone 16 Pro / 16 Pro Max</b>"))
        self.assertIn("— iPhone 16 —", base)
        self.assertIn("— iPhone 16 Plus —", base)
        self.assertIn("— iPhone 16 Pro —", pro)
        self.assertIn("— iPhone 16 Pro Max —", pro)
        self.assertIn("128 Black", base)
        self.assertIn("\n\n<code>iPhone 16 256 Blue", base)
        self.assertIn("\n\n<code>iPhone 16 Plus 512 White", base)

    def test_blank_line_is_added_when_model_changes(self):
        items = self.parse("""A17 6/128GB Gray — 15000
A17 8/256GB Blue — 17800
A27 6/128GB Black — 20800
A27 8/256GB Blue — 23800
S25 12/256 Navy — 50500
S25 Ultra 12/256 Black — 65000""").items
        page = next(value for value in render_blocks(items, Settings()).values() if value.startswith("<b>Samsung A + S25</b>"))
        self.assertIn("A17 8/256GB Blue — 17 800</code>\n\n<code>A27", page)
        self.assertIn("<b>— Galaxy S25 —</b>", page)
        self.assertIn("S25 12/256 Navy — 50 500</code>\n\n<code>S25 Ultra", page)

    def test_iphone_models_are_separate_and_keep_activation_sections(self):
        items = self.parse("""iPhone 16
16 128GB Black 🇮🇳 — 63700
16 128GB Black 🇮🇳 Актив — 60800
16 Plus 128GB Black 🇮🇳 — 73700
16 Plus 128GB Pink 🇮🇳 Актив — 72000
16 Pro 256GB Natural 🇦🇪 — 83600""").items
        self.assertEqual({item.block for item in items}, {"iPhone 16 / 16 Plus", "iPhone 16 Pro / 16 Pro Max"})
        pages = render_blocks(items, Settings())
        iphone16 = next(page for page in pages.values() if page.startswith("<b>iPhone 16 / 16 Plus</b>"))
        self.assertIn("— iPhone 16 —", iphone16)
        self.assertIn("— iPhone 16 Plus —", iphone16)
        self.assertGreaterEqual(iphone16.count("— Не активированное —"), 2)
        self.assertGreaterEqual(iphone16.count("— Актив —"), 2)

    def test_preserve_explicit_condition_and_original_packaging(self):
        item = self.parse("iPhone 16 128 Black Актив (Ориг. Упаковка) — 45000").items[0]
        self.assertIn("Актив (Ориг. Упаковка)", item.title)

    def test_asis_and_cpo_are_separate_messages(self):
        items = self.parse("""iPhone 16 Pro 256 Natural CPO 🇺🇸 — 79000
ASIS
iPhone 16 Pro Max 256 Black 🇺🇸 — 80000
iPhone 17 256 Black 🇮🇳 — 60000""").items
        self.assertEqual(items[0].block, "CPO")
        self.assertEqual(items[1].block, "ASIS")
        pages = render_blocks(items, Settings())
        contents = list(pages.values())
        self.assertTrue(any("<b>CPO</b>" in page for page in contents))
        self.assertTrue(any("<b>ASIS</b>" in page for page in contents))

    def test_active_and_inactive_are_sorted_inside_message(self):
        items = self.parse("""iPhone 17 256 Black Актив 🇮🇳 — 60000
iPhone 17 256 White Неактив 🇮🇳 — 61000
iPhone 17 256 Blue 🇮🇳 — 62000""").items
        content = next(iter(render_blocks(items, Settings()).values()))
        self.assertIn("— Не активированное —", content)
        self.assertIn("— Актив —", content)
        self.assertNotIn("Статус не указан", content)
        self.assertLess(content.index("— Не активированное —"), content.index("— Актив —"))
        self.assertLess(content.index("256 Blue"), content.index("— Актив —"))

    def test_accessories_filter(self):
        items = self.parse("Чехол iPhone 17 Black — 500\niPhone 17 256 Black — 60000").items
        self.assertEqual(len(items), 2)
        selected = select_items(items, Settings(allow_accessories=False))
        self.assertEqual(len(selected), 1)
        self.assertFalse(selected[0].accessory)

    def test_wholesale_tier_does_not_replace_retail(self):
        items = self.parse("iPhone 17 256 Black — 60000 от 6 шт — 58000").items
        self.assertEqual(items[0].price, Decimal(60000))
        self.assertEqual(self.parse("от 6 шт iPhone 17 256 Black — 58000").items, [])

    def test_menus_and_unavailable_are_not_products(self):
        self.assertEqual(self.parse("Прайс\nАктуальный прайс\nВыберите категорию\nОбновлён 2026").items, [])
        self.assertEqual(self.parse("iPhone 17 256 Black — 60000 ❌").items, [])

    def test_unclassified_priced_product_is_retained(self):
        result = self.parse("Something 256GB Green — 88000")
        self.assertEqual(result.items[0].block, "Товары")
        self.assertEqual(result.items[0].price, Decimal(88000))

    def test_currency_and_decimal_price(self):
        result = self.parse("iPhone 17 256 Black — 799.50 €\niPhone 17 256 White — $ 850")
        self.assertEqual([i.currency for i in result.items], ["EUR", "USD"])
        self.assertEqual(result.items[0].price, Decimal("799.50"))
        for value in ["79 000", "79,000", "79.000"]:
            self.assertEqual(amount_value(value), Decimal(79000))

    def test_multi_document_headers_and_duplicates(self):
        result = parse_documents(["iPhone 17\n17 256 Black — 60000", "17 256 Black — 61000"])
        self.assertEqual(len(result.items), 1)
        self.assertEqual(result.items[0].price, Decimal(61000))

    def test_source_priority_keeps_countries_separate(self):
        one = self.parse("🇺🇸 iPhone 17 256GB Black — 60000").items
        two = self.parse("iPhone 17 256 Black 🇺🇸 — 61000\n🇮🇳 iPhone 17 256 Black — 62000").items
        merged = merge_sources([one, two])
        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[0].price, Decimal(60000))

    def test_escaped_html_copyable_name_and_closed_prices(self):
        items = self.parse("B&W Px8 Black <Special> — 50000").items
        pages = render_blocks(items, Settings())
        content = next(iter(pages.values()))
        self.assertIn("<code>B&amp;W", content)
        self.assertIn("&lt;Special&gt;", content)
        closed = next(iter(render_blocks(items, Settings(), closed=True).values()))
        self.assertNotIn("50 000", closed)
        self.assertIn("Продажи закрыты", closed)

    def test_pages_fit_telegram_limit(self):
        items = [Item(f"iPhone 17 256GB Black {i} 🇺🇸", Decimal(60000), "RUB", "iPhone 17", "esim")
                 for i in range(220)]
        pages = render_blocks(items, Settings())
        self.assertGreater(len(pages), 1)
        self.assertTrue(all(units(p) <= 4096 for p in pages.values()))
        self.assertEqual(sum(p.count("<code>") for p in pages.values()), 220)

    def test_split_pages_have_no_part_labels(self):
        items = [Item(f"iPhone 17 256GB Black {i} 🇺🇸", Decimal(60000), "RUB", "iPhone 17", "esim")
                 for i in range(220)]
        pages = render_blocks(items, Settings())
        self.assertGreater(len(pages), 1)
        self.assertTrue(all("Часть " not in page for page in pages.values()))

    def test_closure_and_block_filter(self):
        self.assertTrue(self.parse("В данный момент мы закрыты. Ждём завтра").closed)
        items = self.parse("iPhone 17 256 Black — 60000\nDyson HS08 — 40000").items
        self.assertEqual([i.block for i in select_items(items, Settings(), {"disabled_blocks": ["Dyson"]})], ["iPhone 17 / 17 Plus"])

    def test_esim_separators_are_not_physical_sim(self):
        for label in ("e-SIM", "e SIM", "eSIM"):
            item = self.parse(f"iPhone 17 256 Black {label} — 60000").items[0]
            self.assertEqual(item.sim, "esim")


if __name__ == "__main__":
    unittest.main()
