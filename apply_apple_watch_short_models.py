from pathlib import Path

p = Path("prices.py")
s = p.read_text(encoding="utf-8")
old = '''def apple_watch_block(title):
    plain = FLAGS.sub("", clean(title)).strip()
    if not re.search(r"\\bwatch\\b", plain, re.I):
        return ""
    # Never classify another manufacturer's watch as Apple just because the
    # product name contains the generic word "Watch".
    if re.search(
        r"\\b(?:galaxy|samsung|one\\s*plus|oneplus|xiaomi|redmi|huawei|honor|"
        r"garmin|coros|pixel|fitbit|amazfit|oppo|vivo|realme|nothing)\\b",
        plain,
        re.I,
    ):
        return ""
    if re.search(r"\\bapple\\s*watch\\b|^watch\\b", plain, re.I):
        return "Apple Watch"
    return ""
'''
new = '''def apple_watch_block(title):
    plain = FLAGS.sub("", clean(title)).strip()

    # Explicitly branded non-Apple watches always win over generic watch rules.
    if re.search(
        r"\\b(?:galaxy|samsung|one\\s*plus|oneplus|xiaomi|redmi|huawei|honor|"
        r"garmin|coros|pixel|fitbit|amazfit|oppo|vivo|realme|nothing)\\b",
        plain,
        re.I,
    ):
        return ""

    # Supplier shorthand for Apple Watch. A Series shorthand must be followed
    # by a real watch case size so Galaxy S-series phones are never mistaken
    # for Apple Watch.
    watch_size = r"(?:38|40|41|42|44|45|46|49)(?:\\s*mm)?"
    if re.search(r"^S\\s*\\d{1,2}\\s+" + watch_size + r"\\b", plain, re.I):
        return "Apple Watch"
    if re.search(r"^SE\\s*\\d*\\s+" + watch_size + r"\\b", plain, re.I):
        return "Apple Watch"
    if re.search(r"^(?:UL|ULTRA)\\s*\\d{1,2}\\b", plain, re.I):
        return "Apple Watch"

    if re.search(r"\\bapple\\s*watch\\b|^watch\\b", plain, re.I):
        return "Apple Watch"
    return ""
'''
if s.count(old) != 1:
    raise SystemExit(f"apple_watch_block old block count={s.count(old)}")
s = s.replace(old, new, 1)
p.write_text(s, encoding="utf-8")

p = Path("tests/test_watch_branding.py")
t = p.read_text(encoding="utf-8")
insert = '''\n    def test_supplier_apple_watch_shorthand_is_recognized(self):\n        items = parse_documents(["""SE3 40 Midnight(S/M) 2025 🇺🇸 - 19600\nSE3 44 Midnight(M/L) 2025 🇺🇸 - 21400\nS7 41 Midnight 🇺🇸 - 18000\nS8 45 Starlight 🇺🇸 - 22000\nS9 41 Pink 🇺🇸 - 24000\nS10 46 Silver 🇺🇸 - 27000\nS11 42 Jet Black(S/M) 🇺🇸 - 28300\nS11 46 Space Gray(M/L) 🇺🇸 - 30800\nUL 2 White - OB 🇺🇸 - 53600\nUL 3 Natural / Blue/Bright Blue TL(S/M) 🇺🇸 - 57800"""]).items\n        self.assertEqual(len(items), 10)\n        self.assertTrue(all(item.block == "Apple Watch" for item in items))\n\n    def test_s_series_phone_is_not_mistaken_for_apple_watch(self):\n        items = parse_documents(["Samsung Galaxy S10 8/128 Black — 25000\nS11 12/256 Black — 50000"]).items\n        self.assertNotEqual(items[0].block, "Apple Watch")\n        self.assertNotEqual(items[1].block, "Apple Watch")\n'''
marker = '\n\nif __name__ == "__main__":\n'
if marker not in t:
    raise SystemExit("watch test marker missing")
t = t.replace(marker, insert + marker, 1)
p.write_text(t, encoding="utf-8")
