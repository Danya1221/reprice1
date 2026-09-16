from pathlib import Path
import re


def rep(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected 1 match, got {count}")
    return text.replace(old, new, 1)


p = Path("prices.py")
s = p.read_text(encoding="utf-8")
old = '''def pack_physical_sections(sections, limit=3950):
    """Pack small logical blocks into large Telegram posts without changing their DB blocks."""
    ordered_units = []
    seen_bundles = set()
    for section in sections:
        bundle = iphone_bundle_key(section["title"])
        if bundle:
            if bundle in seen_bundles:
                continue
            seen_bundles.add(bundle)
            bundle_sections = [entry for entry in sections if iphone_bundle_key(entry["title"]) == bundle]
            for batch in split_bundle_sections(bundle_sections, bundle, limit):
                ordered_units.append((bundle, batch))
        else:
            ordered_units.append(("", [section]))

    physical = []
    pending = []
    for bundle, unit_sections in ordered_units:
        if bundle:
            if pending:
                physical.append(("", pending))
                pending = []
            physical.append((bundle, unit_sections))
            continue

        candidate = pending + unit_sections
        if pending and units(build_physical_message(candidate)) > limit:
            physical.append(("", pending))
            pending = list(unit_sections)
        else:
            pending = candidate
    if pending:
        physical.append(("", pending))
    return physical
'''
new = '''def pack_physical_sections(sections, limit=3950):
    """Pack ordinary small brands, while preserving explicitly separated business sections."""
    isolated = {
        "CPO", "ASIS", "Аксессуары",
        "Samsung Buds", "Samsung A + S25", "Samsung S26",
        "Samsung Tab S", "Samsung Fold / Flip",
    }
    ordered_units = []
    seen_bundles = set()
    for section in sections:
        bundle = iphone_bundle_key(section["title"])
        if bundle:
            if bundle in seen_bundles:
                continue
            seen_bundles.add(bundle)
            bundle_sections = [entry for entry in sections if iphone_bundle_key(entry["title"]) == bundle]
            for batch in split_bundle_sections(bundle_sections, bundle, limit):
                ordered_units.append((bundle, batch))
        elif section["title"] in isolated:
            ordered_units.append(("isolated", [section]))
        else:
            ordered_units.append(("", [section]))

    physical = []
    pending = []
    for bundle, unit_sections in ordered_units:
        if bundle:
            if pending:
                physical.append(("", pending))
                pending = []
            if bundle == "isolated":
                physical.append(("", unit_sections))
            else:
                physical.append((bundle, unit_sections))
            continue

        candidate = pending + unit_sections
        if pending and units(build_physical_message(candidate)) > limit:
            physical.append(("", pending))
            pending = list(unit_sections)
        else:
            pending = candidate
    if pending:
        physical.append(("", pending))
    return physical
'''
s = rep(s, old, new, "preserve isolated business sections")
p.write_text(s, encoding="utf-8")

# Fix/align tests after the layout migration.
tp = Path("tests/test_prices.py")
t = tp.read_text(encoding="utf-8")
t = t.replace(
    'iphone16 = next(page for page in pages.values() if page.startswith("<b>iPhone 16 / 16 Plus</b>"))\n        self.assertIn("— iPhone 16 —", iphone16)\n        self.assertIn("— iPhone 16 Plus —", iphone16)\n        self.assertGreaterEqual(iphone16.count("— Не активированное —"), 2)\n        self.assertGreaterEqual(iphone16.count("— Актив —"), 2)',
    'iphone16 = next(page for page in pages.values() if page.startswith("<b>iPhone 16 / 16 Plus / 16 Pro / 16 Pro Max</b>"))\n        self.assertIn("— iPhone 16 —", iphone16)\n        self.assertIn("— iPhone 16 Plus —", iphone16)\n        self.assertGreaterEqual(iphone16.count("— Не активированное —"), 2)\n        self.assertGreaterEqual(iphone16.count("— Актив —"), 2)',
)
# Brand ordering follows block ordering; only require that all participating brands
# are shown at the top and that there is a visible empty line between sections.
t = t.replace(
    'self.assertTrue(content.startswith("<b>Xiaomi • Vivo • Realme</b>"))\n        self.assertIn("</code>\\n\\n\\n<b>— Vivo —</b>", content)',
    'header = content.split("\\n", 1)[0]\n        for brand in ["Xiaomi", "Vivo", "Realme"]:\n            self.assertIn(brand, header)\n        self.assertIn("</code>\\n\\n\\n<b>— ", content)',
)
tp.write_text(t, encoding="utf-8")

rp = Path("tests/test_parser_regressions.py")
r = rp.read_text(encoding="utf-8")
# Repair a literal newline that re.sub inserted inside the source string.
r = r.replace('packed = "\n".join(render_blocks(items, Settings()).values())',
              'packed = "\\n".join(render_blocks(items, Settings()).values())')
rp.write_text(r, encoding="utf-8")
