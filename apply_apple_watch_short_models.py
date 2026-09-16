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

# UL is the supplier abbreviation for Ultra. Normalize it before identity is
# built so UL 2/3 and Ultra 2/3 are the same model for sorting/deduplication.
old_normal = '''    if re.search(r"ray[\\s-]?ban|wayfarer|skyler", title, re.I):
        title = re.sub(r"\\bS\\s*50\\b", "M", title, flags=re.I)
        title = re.sub(r"\\bS\\s*53\\b", "L", title, flags=re.I)
        if not re.search(r"ray[\\s-]?ban", title, re.I):
            title = "Ray-Ban Meta " + title
    return clean(title)
'''
new_normal = '''    if re.search(r"ray[\\s-]?ban|wayfarer|skyler", title, re.I):
        title = re.sub(r"\\bS\\s*50\\b", "M", title, flags=re.I)
        title = re.sub(r"\\bS\\s*53\\b", "L", title, flags=re.I)
        if not re.search(r"ray[\\s-]?ban", title, re.I):
            title = "Ray-Ban Meta " + title
    title = re.sub(r"^UL\\s*(\\d{1,2})\\b", r"Ultra \\1", title, flags=re.I)
    return clean(title)
'''
if s.count(old_normal) != 1:
    raise SystemExit(f"normal_title insertion count={s.count(old_normal)}")
s = s.replace(old_normal, new_normal, 1)
p.write_text(s, encoding="utf-8")

p = Path("tests/test_watch_branding.py")
t = p.read_text(encoding="utf-8")
insert = '''
    def test_supplier_apple_watch_shorthand_is_recognized(self):
        items = parse_documents(["""SE3 40 Midnight(S/M) 2025 🇺🇸 - 19600
SE3 44 Midnight(M/L) 2025 🇺🇸 - 21400
S7 41 Midnight 🇺🇸 - 18000
S8 45 Starlight 🇺🇸 - 22000
S9 41 Pink 🇺🇸 - 24000
S10 46 Silver 🇺🇸 - 27000
S11 42 Jet Black(S/M) 🇺🇸 - 28300
S11 46 Space Gray(M/L) 🇺🇸 - 30800
UL 2 White - OB 🇺🇸 - 53600
UL 3 Natural / Blue/Bright Blue TL(S/M) 🇺🇸 - 57800"""]).items
        self.assertEqual(len(items), 10)
        self.assertTrue(all(item.block == "Apple Watch" for item in items))
        self.assertTrue(any(item.title.startswith("Ultra 2 ") for item in items))
        self.assertTrue(any(item.title.startswith("Ultra 3 ") for item in items))

    def test_ul_and_ultra_are_the_same_model_name(self):
        items = parse_documents(["""UL 3 Black / Black AL(S) 🇺🇸 - 57800
Ultra 3 Black / Black AL(S) 🇺🇸 - 57800"""]).items
        self.assertEqual(len(items), 1)
        self.assertTrue(items[0].title.startswith("Ultra 3 "))
        self.assertEqual(items[0].block, "Apple Watch")

    def test_s_series_phone_is_not_mistaken_for_apple_watch(self):
        items = parse_documents(["""Samsung Galaxy S10 8/128 Black — 25000
S11 12/256 Black — 50000"""]).items
        self.assertNotEqual(items[0].block, "Apple Watch")
        self.assertNotEqual(items[1].block, "Apple Watch")
'''
marker = '\n\nif __name__ == "__main__":\n'
if marker not in t:
    raise SystemExit("watch test marker missing")
t = t.replace(marker, insert + marker, 1)
p.write_text(t, encoding="utf-8")
