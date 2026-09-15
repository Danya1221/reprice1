from pathlib import Path


def rep(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected 1 match, got {count}")
    return text.replace(old, new, 1)

p = Path("prices.py")
s = p.read_text(encoding="utf-8")

s = rep(
    s,
    '    if re.search(r"^(?:samsung\\s+|galaxy\\s+)?a\\s*\\d{2,3}[a-z]*\\b", plain, re.I):\n        return "Samsung A + S25"\n',
    '    if re.search(r"^(?:samsung\\s+|galaxy\\s+)?a\\s*(?:17|27|37|57)\\b", plain, re.I):\n        return "Samsung A + S25"\n',
    "bare exact Galaxy A models",
)

s = rep(
    s,
    '    if re.search(r"\\bgalaxy\\s+a\\b|\\bsamsung\\s+galaxy\\s+a\\b", plain, re.I):\n        return "Samsung A + S25"\n',
    '    if re.search(r"\\b(?:galaxy\\s+|samsung\\s+(?:galaxy\\s+)?)?a\\s*(?:17|27|37|57)\\b", plain, re.I):\n        return "Samsung A + S25"\n',
    "branded exact Galaxy A models",
)

s = rep(
    s,
    '    a = re.search(r"\\bA\\s*(\\d{2,3})\\b", plain, re.I)\n',
    '    a = re.search(r"\\bA\\s*(17|27|37|57)\\b", plain, re.I)\n',
    "Galaxy A sorting",
)

p.write_text(s, encoding="utf-8")

tp = Path("tests/test_prices.py")
t = tp.read_text(encoding="utf-8")
# Old fixtures still used A56 from the previous broad Axx rule. Keep the same
# scenarios but switch that model to A37, which is one of the explicitly allowed models.
t = t.replace("A56 8/256 Awesome Graphite 🇪🇺 — 35000", "A37 8/256 Awesome Graphite 🇪🇺 — 35000")
t = t.replace("A56 8/256 Black — 35000", "A37 8/256 Black — 35000")

marker = '    def test_blank_line_is_added_when_model_changes(self):\n'
extra = '''    def test_only_requested_galaxy_a_models_are_auto_detected(self):\n        items = self.parse("""A17 6/128GB Gray — 15000\nA27 6/128GB Black — 20800\nA37 8/128GB Lavender — 23900\nA57 8/256GB Navy — 31800""").items\n        self.assertEqual([item.block for item in items], ["Samsung A + S25"] * 4)\n        self.assertEqual([item.title.split()[0] for item in items], ["A17", "A27", "A37", "A57"])\n\n    def test_other_bare_a_models_are_not_assumed_to_be_samsung(self):\n        items = self.parse("""A15 8/256 Black — 19900\nA55 8/256 Blue — 29900""").items\n        self.assertEqual([item.block for item in items], ["Товары", "Товары"])\n\n'''
if marker not in t:
    raise SystemExit("test marker missing")
t = t.replace(marker, extra + marker, 1)
tp.write_text(t, encoding="utf-8")
