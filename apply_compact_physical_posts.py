from pathlib import Path
import re


def rep(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected 1 match, got {count}")
    return text.replace(old, new, 1)


# Keep logical product blocks separate. Only the renderer is allowed to combine
# them into larger physical Telegram messages.
p = Path("prices.py")
s = p.read_text(encoding="utf-8")
start = s.index("def iphone_publish_block(model):")
end = s.index("\ndef brand_of(text):", start)
s = s[:start] + '''def iphone_publish_block(model):
    """Logical iPhone block. Physical Telegram messages are grouped later."""
    return model or ""


def iphone_storage_key(item):
    title = FLAGS.sub("", clean(item.title))
    model = iphone_model(title)
    if model:
        title = re.sub(re.escape(model), "", title, count=1, flags=re.I).strip()
    match = re.search(r"\\b(\\d{1,4})\\s*(GB|TB)?\\b", title, re.I)
    if not match:
        return ""
    value = int(match.group(1))
    unit = (match.group(2) or "GB").upper()
    return f"{value}{unit}"

''' + s[end + 1:]

render_start = s.index("def render_blocks(")
new_tail = r'''def iphone_bundle_key(block):
    """Physical iPhone message requested by the operator."""
    if block == "iPhone Air":
        return "iphone17"
    match = re.fullmatch(r"iPhone\s+(\d{1,2})(?:e)?(?:\s+(?:Plus|Pro(?:\s+Max)?))?", block, re.I)
    if not match:
        return ""
    number = int(match.group(1))
    if 11 <= number <= 15:
        return "iphone11-15"
    if number == 16:
        return "iphone16"
    if number == 17:
        return "iphone17"
    return ""


def iphone_bundle_header(bundle, titles):
    if bundle == "iphone11-15":
        return "iPhone 11 / 12 / 13 / 14 / 15"
    if bundle == "iphone16":
        base = "iPhone 16 / 16 Plus / 16 Pro / 16 Pro Max"
        if any(title.casefold() == "iphone 16e" for title in titles):
            base += " / 16e"
        return base
    if bundle == "iphone17":
        base = "iPhone 17 / 17 Pro / 17 Pro Max / Air"
        if any(title.casefold() == "iphone 17e" for title in titles):
            base += " / 17e"
        return base
    return " / ".join(titles)


def physical_brand_label(title):
    name = clean(title)
    lower = name.casefold()
    if lower.startswith("iphone"):
        return "iPhone"
    if lower.startswith(("airpods", "apple watch", "ipad", "macbook", "imac", "mac mini", "mac studio", "apple tv", "airtag", "apple")):
        return "Apple"
    if lower.startswith("samsung"):
        return "Samsung"
    if lower.startswith("ray-ban"):
        return "Ray-Ban Meta"
    if lower.startswith("dji") or lower.startswith("insta360"):
        return "DJI / Insta360"
    if lower.startswith("harman") or lower.startswith("bose"):
        return "Harman Kardon / Bose"
    if lower.startswith("kodak") or lower.startswith("fujifilm"):
        return "Kodak / Fujifilm"
    return name


def iphone_storage_rank(item):
    key = iphone_storage_key(item)
    match = re.fullmatch(r"(\d+)(GB|TB)", key, re.I)
    if not match:
        return 10**9
    value = int(match.group(1))
    if match.group(2).upper() == "TB":
        value *= 1024
    return value


def render_block_lines(block, block_items, settings, overrides, closed=False):
    if closed:
        return ["Продажи закрыты"]

    has_activation = any(activation_state(item.title) != "unknown" or iphone_model(item.title) for item in block_items)

    def condition(item):
        value = activation_state(item.title)
        return "inactive" if value == "unknown" else value

    lines = []
    statuses = sorted({condition(item) for item in block_items}, key=lambda status: CONDITION_ORDER[status])
    for status_index, status in enumerate(statuses):
        status_items = [item for item in block_items if condition(item) == status]
        if status_index and lines and lines[-1] != "":
            lines.append("")
        if has_activation:
            lines.append("<b>— " + CONDITION_LABELS[status] + " —</b>")

        last_sim = None
        last_samsung_section = None
        last_model_key = None
        last_storage = None
        sorter = samsung_a_s25_sort if block == "Samsung A + S25" else item_sort

        def full_sort(item):
            if iphone_model(item.title):
                return (SIM_ORDER.get(item.sim, 99), iphone_storage_rank(item), sorter(item))
            return (SIM_ORDER.get(item.sim, 99), 0, sorter(item))

        for item in sorted(status_items, key=full_sort):
            if block == "Samsung A + S25":
                samsung_section = "Galaxy S25" if re.search(r"\bS\s*25\b", item.title, re.I) else "Galaxy A"
                if samsung_section != last_samsung_section:
                    if last_samsung_section is not None and lines and lines[-1] != "":
                        lines.append("")
                    lines.append("<b>— " + samsung_section + " —</b>")
                    last_samsung_section = samsung_section

            if iphone_model(item.title) and item.sim != last_sim:
                label = SIM_LABELS.get(item.sim)
                if label:
                    if lines and lines[-1] != "" and not lines[-1].startswith("<b>"):
                        lines.append("")
                    lines.append("<b>— " + label + " —</b>")
                last_sim = item.sim

            if iphone_model(item.title):
                storage = iphone_storage_key(item)
                if (storage and last_storage is not None and storage != last_storage
                        and lines and lines[-1] != "" and not lines[-1].startswith("<b>")):
                    lines.append("")
                if storage:
                    last_storage = storage
            else:
                model_key = model_group_key(item)
                if (last_model_key is not None and model_key != last_model_key
                        and lines and lines[-1] != "" and not lines[-1].startswith("<b>")):
                    lines.append("")
                last_model_key = model_key

            row = display_title(item) + " — " + price_text(marked_price(item, settings, overrides), item.currency)
            line = "<code>" + html.escape(row) + "</code>"
            if units(line) > 3000:
                raise ValueError("Слишком длинное наименование товара")
            lines.append(line)

    while lines and lines[-1] == "":
        lines.pop()
    return lines


def section_chunks(title, lines, limit=3200):
    """Split a logical block only when Telegram forces us to; never add Part labels."""
    result = []
    chunk = []
    size = 0
    for line in lines:
        line_size = units(line) + 1
        if chunk and size + line_size > limit:
            while chunk and chunk[-1] == "":
                chunk.pop()
            result.append({"title": title, "body": "\n".join(chunk), "part": len(result)})
            chunk = []
            size = 0
        chunk.append(line)
        size += line_size
    if chunk:
        while chunk and chunk[-1] == "":
            chunk.pop()
        result.append({"title": title, "body": "\n".join(chunk), "part": len(result)})
    return result or [{"title": title, "body": "", "part": 0}]


def build_physical_message(sections, bundle=""):
    if not sections:
        return ""
    if len(sections) == 1 and not bundle:
        section = sections[0]
        return "<b>" + html.escape(section["title"]) + "</b>\n\n" + section["body"]

    titles = []
    for section in sections:
        if section["title"] not in titles:
            titles.append(section["title"])

    if bundle:
        heading = iphone_bundle_header(bundle, titles)
    else:
        brands = []
        for title in titles:
            label = physical_brand_label(title)
            if label not in brands:
                brands.append(label)
        heading = " • ".join(brands)

    bodies = []
    for section in sections:
        bodies.append("<b>— " + html.escape(section["title"]) + " —</b>\n" + section["body"])
    # Three newlines = a visible empty line between logical sections.
    return "<b>" + html.escape(heading) + "</b>\n\n" + "\n\n\n".join(bodies)


def split_bundle_sections(sections, bundle, limit=3950):
    batches = []
    current = []
    for section in sections:
        candidate = current + [section]
        if current and units(build_physical_message(candidate, bundle)) > limit:
            batches.append(current)
            current = [section]
        else:
            current = candidate
    if current:
        batches.append(current)
    return batches


def pack_physical_sections(sections, limit=3950):
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


def render_blocks(items, settings, overrides=None, closed=False):
    """Render logical blocks, then pack them into fewer large physical Telegram messages."""
    overrides = overrides or {}
    groups = OrderedDict()
    for item in items:
        groups.setdefault(item.block, []).append(item)

    order = overrides.get("block_order", [])
    names = ordered_blocks(groups, order)
    logical_sections = []
    for block in names:
        lines = render_block_lines(block, groups[block], settings, overrides, closed=closed)
        logical_sections.extend(section_chunks(block, lines))

    pages = OrderedDict()
    for bundle, sections in pack_physical_sections(logical_sections):
        content = build_physical_message(sections, bundle)
        seed = bundle + "|" + "|".join(f'{section["title"]}#{section["part"]}' for section in sections)
        key = hashlib.sha256(seed.encode()).hexdigest()[:16] + ":0"
        pages[key] = content
    return pages
'''
s = s[:render_start] + new_tail
p.write_text(s, encoding="utf-8")

# Catalog: every iPhone model remains a separate button, even when several
# models share one physical Telegram post. Other groups stay compact.
cp = Path("catalog_publisher.py")
c = cp.read_text(encoding="utf-8")
cat_start = c.index("def catalog_labels(content):")
cat_end = c.index("\n\ndef catalog_group(title):", cat_start)
new_catalog_labels = r'''def catalog_labels(content):
    """Buttons represented by one physical Telegram price message."""
    plain = html.unescape(re.sub(r"<[^>]+>", " ", content))
    iphone_pattern = re.compile(
        r"\biPhone\s+(?:Air|\d{1,2}e?(?:\s+(?:Plus|Pro(?:\s+Max)?))?)\b",
        re.I,
    )
    iphones = []
    for raw in iphone_pattern.findall(plain):
        canonical = iphone_model_label(raw)
        if canonical and canonical not in iphones:
            iphones.append(canonical)
    if iphones:
        return iphones

    title = page_title(content)
    labels = []
    for part in [piece.strip() for piece in title.split("•") if piece.strip()]:
        group = catalog_group(part)
        if group not in labels:
            labels.append(group)
    return labels or [catalog_group(title)]


def iphone_model_label(raw):
    raw = re.sub(r"\s+", " ", raw).strip()
    if raw.casefold() == "iphone air":
        return "iPhone Air"
    match = re.fullmatch(r"iPhone\s+(\d{1,2}e?)(?:\s+(Plus|Pro(?:\s+Max)?))?", raw, re.I)
    if not match:
        return ""
    result = "iPhone " + match.group(1)
    suffix = match.group(2)
    if suffix:
        suffix = re.sub(r"\s+", " ", suffix).title().replace("Pro Max", "Pro Max")
        result += " " + suffix
    return result
'''
c = c[:cat_start] + new_catalog_labels + c[cat_end:]
cp.write_text(c, encoding="utf-8")

# Bring older tests back to logical blocks, then test the new physical packing.
tp = Path("tests/test_prices.py")
t = tp.read_text(encoding="utf-8")
t = t.replace('["iPhone 17 / 17 Plus", "iPhone 17 Pro / 17 Pro Max"]', '["iPhone 17", "iPhone 17 Pro Max"]')
t = t.replace('{"iPhone 16 / 16 Plus", "iPhone 16 Pro / 16 Pro Max"}', '{"iPhone 16", "iPhone 16 Plus", "iPhone 16 Pro"}')
t = t.replace('["iPhone 17 / 17 Plus"]', '["iPhone 17"]')

# Replace the focused pair test with a physical-message test.
pattern = re.compile(r'    def test_iphone_base_plus_and_pro_pairs_share_messages\(self\):.*?(?=    def test_blank_line_is_added_when_model_changes\(self\):)', re.S)
replacement = '''    def test_iphone_16_family_is_one_physical_message_with_storage_gaps(self):\n        items = self.parse("""iPhone 16 128 Black — 60000\niPhone 16 256 Blue — 65000\niPhone 16 Plus 128 Pink — 70000\niPhone 16 Plus 512 White — 82000\niPhone 16 Pro 256 Black — 90000\niPhone 16 Pro Max 256 Natural — 100000""").items\n        self.assertEqual({item.block for item in items}, {\n            "iPhone 16", "iPhone 16 Plus", "iPhone 16 Pro", "iPhone 16 Pro Max"\n        })\n        pages = render_blocks(items, Settings())\n        self.assertEqual(len(pages), 1)\n        content = next(iter(pages.values()))\n        self.assertTrue(content.startswith("<b>iPhone 16 / 16 Plus / 16 Pro / 16 Pro Max</b>"))\n        for model in ["iPhone 16", "iPhone 16 Plus", "iPhone 16 Pro", "iPhone 16 Pro Max"]:\n            self.assertIn("— " + model + " —", content)\n        self.assertIn("128 Black — 60 000</code>\\n\\n<code>iPhone 16 256 Blue", content)\n\n    def test_requested_iphone_generations_are_three_large_messages(self):\n        items = self.parse("""iPhone 11 128 Black — 30000\niPhone 12 128 Blue — 35000\niPhone 13 128 Black — 40000\niPhone 14 128 Black — 45000\niPhone 15 128 Black — 50000\niPhone 16 128 Black — 60000\niPhone 16 Plus 128 Pink — 70000\niPhone 16 Pro 256 Black — 80000\niPhone 16 Pro Max 256 Natural — 90000\niPhone 17 256 Black — 70000\niPhone 17 Pro 256 Blue — 90000\niPhone 17 Pro Max 256 Silver — 100000\niPhone Air 256 Gold — 80000""").items\n        pages = list(render_blocks(items, Settings()).values())\n        self.assertEqual(len(pages), 3)\n        self.assertTrue(any(page.startswith("<b>iPhone 11 / 12 / 13 / 14 / 15</b>") for page in pages))\n        self.assertTrue(any(page.startswith("<b>iPhone 16 / 16 Plus / 16 Pro / 16 Pro Max</b>") for page in pages))\n        self.assertTrue(any(page.startswith("<b>iPhone 17 / 17 Pro / 17 Pro Max / Air</b>") for page in pages))\n\n    def test_small_brand_blocks_are_packed_with_visible_section_gap(self):\n        items = self.parse("""Xiaomi 15 12/256 White — 48000\nVivo V70 12/256 Grey — 46000\nRealme GT 7 12/256 Black — 42000""").items\n        pages = list(render_blocks(items, Settings()).values())\n        self.assertEqual(len(pages), 1)\n        content = pages[0]\n        self.assertTrue(content.startswith("<b>Xiaomi • Vivo • Realme</b>"))\n        self.assertIn("</code>\\n\\n\\n<b>— Vivo —</b>", content)\n\n'''
t, n = pattern.subn(replacement, t, count=1)
if n != 1:
    raise SystemExit(f"focused iPhone pair test: got {n}")

tp.write_text(t, encoding="utf-8")

rp = Path("tests/test_parser_regressions.py")
r = rp.read_text(encoding="utf-8")
r = r.replace('["iPhone 17 Pro / 17 Pro Max", "iPhone 17 Pro / 17 Pro Max", "iPhone Air"]', '["iPhone 17 Pro", "iPhone 17 Pro Max", "iPhone Air"]')
r = r.replace('"iPhone 11 Pro / 11 Pro Max", "iPhone 12 / 12 Plus",\n            "iPhone 13 / 13 Plus", "iPhone 14 / 14 Plus",\n            "iPhone 15 / 15 Plus", "iPhone 15 Pro / 15 Pro Max",', '"iPhone 11 Pro Max", "iPhone 12", "iPhone 13", "iPhone 14",\n            "iPhone 15", "iPhone 15 Plus", "iPhone 15 Pro",')
r = r.replace('self.assertIn("<b>iPhone 13 / 13 Plus</b>", headers)\n        self.assertIn("<b>iPhone 15 / 15 Plus</b>", headers)\n        self.assertIn("<b>iPhone 15 Pro / 15 Pro Max</b>", headers)', 'self.assertTrue(any(page.startswith("<b>iPhone 11 / 12 / 13 / 14 / 15</b>") for page in pages.values()))')
rp.write_text(r, encoding="utf-8")

cat = Path("tests/test_catalog.py")
ct = cat.read_text(encoding="utf-8")
ct = ct.replace('["Dyson", "iPhone 17 / 17 Plus"]', '["Dyson", "iPhone 17"]')
# Physical packing can reduce the number of managed posts, so slot-count tests should
# compare against the actual rendered page count instead of the old per-block count.
ct = ct.replace('self.assertEqual(len(entries), 3)', 'self.assertEqual(len(entries), len(self.pages()))')
cat.write_text(ct, encoding="utf-8")
