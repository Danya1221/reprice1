import unittest

from prices import parse_documents


class WatchBrandingTests(unittest.TestCase):
    def test_galaxy_and_oneplus_watches_never_become_apple_watch(self):
        items = parse_documents(["""Galaxy Watch Ultra (2025) 47mm LTE Silver 🇦🇪 — 27300
Galaxy Watch Ultra (2025) 47mm LTE White 🇦🇪 — 26800
Galaxy Watch Ultra (2025) 47mm LTE White 🇦🇪 — 26800
OnePlus Watch Lite Black Steel OPWWE262 🇪🇺 — 10300
OnePlus Watch Lite Silver Steel OPWWE262 🇪🇺 — 10300"""]).items
        self.assertEqual([item.block for item in items], ["Samsung", "Samsung", "OnePlus", "OnePlus"])

    def test_real_apple_watch_still_reads_with_and_without_apple_prefix(self):
        direct = parse_documents(["Apple Watch Series 11 46mm Black — 40000\nWatch S10 46mm Silver — 35000"]).items
        self.assertEqual([item.block for item in direct], ["Apple Watch", "Apple Watch"])

        sectioned = parse_documents(["""Apple Watch
Series 11 46mm Black — 40000
SE 3 44mm Silver — 30000
Ultra 3 49mm Black — 70000"""]).items
        self.assertEqual([item.block for item in sectioned], ["Apple Watch"] * 3)


if __name__ == "__main__":
    unittest.main()
