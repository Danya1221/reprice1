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
    '''def iphone_publish_block(model):
    """Publish every iPhone model/variant as its own Telegram block."""
    return model or ""
''',
    '''def iphone_publish_block(model):
    """Pair base+Plus and Pro+Pro Max from the same generation in one Telegram post."""
    if not model:
        return ""
    match = re.fullmatch(r"iPhone\\s+(\\d{1,2})", model, re.I)
    if match:
        return f"iPhone {match.group(1)} / {match.group(1)} Plus"
    match = re.fullmatch(r"iPhone\\s+(\\d{1,2})\\s+Plus", model, re.I)
    if match:
        return f"iPhone {match.group(1)} / {match.group(1)} Plus"
    match = re.fullmatch(r"iPhone\\s+(\\d{1,2})\\s+Pro(?:\\s+Max)?", model, re.I)
    if match:
        return f"iPhone {match.group(1)} Pro / {match.group(1)} Pro Max"
    return model


def iphone_models_for_block(block):
    match = re.fullmatch(r"iPhone\\s+(\\d{1,2})\\s*/\\s*\\1\\s+Plus", block, re.I)
    if match:
        n = match.group(1)
        return [f"iPhone {n}", f"iPhone {n} Plus"]
    match = re.fullmatch(r"iPhone\\s+(\\d{1,2})\\s+Pro\\s*/\\s*\\1\\s+Pro Max", block, re.I)
    if match:
        n = match.group(1)
        return [f"iPhone {n} Pro", f"iPhone {n} Pro Max"]
    return [block] if block.startswith("iPhone") else []


def iphone_storage_key(item):
    title = FLAGS.sub("", clean(item.title))
    match = re.search(r"\\b(\\d{2,4})\\s*(GB|TB)?\\b", title, re.I)
    if not match:
        return ""
    value = int(match.group(1))
    unit = (match.group(2) or "GB").upper()
    return f"{value}{unit}"
''',
    "iPhone paired publication blocks",
)

old_render = '''            block_items = groups[block]
            has_activation = any(activation_state(item.title) != "unknown" or iphone_model(item.title) for item in block_items)
            def condition(item):
                value = activation_state(item.title)
                return "inactive" if value == "unknown" else value
            lines = []
            statuses = sorted(
                {condition(item) for item in block_items},
                key=lambda status: CONDITION_ORDER[status],
            )
            for status in statuses:
                status_items = [item for item in block_items if condition(item) == status]
                if has_activation:
                    lines.append("<b>— " + CONDITION_LABELS[status] + " —</b>")
                last_sim = None
                last_samsung_section = None
                last_model_key = None
                sorter = samsung_a_s25_sort if block == "Samsung A + S25" else item_sort
                for item in sorted(status_items, key=lambda i: (SIM_ORDER.get(i.sim, 99), sorter(i))):
                    if block == "Samsung A + S25":
                        samsung_section = "Galaxy S25" if re.search(r"\\bS\\s*25\\b", item.title, re.I) else "Galaxy A"
                        if samsung_section != last_samsung_section:
                            if last_samsung_section is not None and lines and lines[-1] != "":
                                lines.append("")
                            lines.append("<b>— " + samsung_section + " —</b>")
                            last_samsung_section = samsung_section
                    if iphone_model(item.title) and item.sim != last_sim:
                        label = SIM_LABELS.get(item.sim)
                        if label:
                            lines.append("<b>— " + label + " —</b>")
                        last_sim = item.sim
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
'''
new_render = '''            block_items = groups[block]
            has_activation = any(activation_state(item.title) != "unknown" or iphone_model(item.title) for item in block_items)
            def condition(item):
                value = activation_state(item.title)
                return "inactive" if value == "unknown" else value
            lines = []
            paired_iphone_models = iphone_models_for_block(block)
            is_paired_iphone = len(paired_iphone_models) == 2
            model_batches = []
            if is_paired_iphone:
                for model_name in paired_iphone_models:
                    subset = [item for item in block_items if iphone_model(item.title) == model_name]
                    if subset:
                        model_batches.append((model_name, subset))
            else:
                model_batches = [("", block_items)]

            for model_index, (model_name, model_items) in enumerate(model_batches):
                if model_index and lines and lines[-1] != "":
                    lines.append("")
                if is_paired_iphone:
                    lines.append("<b>— " + html.escape(model_name) + " —</b>")
                statuses = sorted(
                    {condition(item) for item in model_items},
                    key=lambda status: CONDITION_ORDER[status],
                )
                for status_index, status in enumerate(statuses):
                    status_items = [item for item in model_items if condition(item) == status]
                    if status_index and lines and lines[-1] != "":
                        lines.append("")
                    if has_activation:
                        lines.append("<b>— " + CONDITION_LABELS[status] + " —</b>")
                    last_sim = None
                    last_samsung_section = None
                    last_model_key = None
                    last_storage = None
                    sorter = samsung_a_s25_sort if block == "Samsung A + S25" else item_sort
                    for item in sorted(status_items, key=lambda i: (SIM_ORDER.get(i.sim, 99), sorter(i))):
                        if block == "Samsung A + S25":
                            samsung_section = "Galaxy S25" if re.search(r"\\bS\\s*25\\b", item.title, re.I) else "Galaxy A"
                            if samsung_section != last_samsung_section:
                                if last_samsung_section is not None and lines and lines[-1] != "":
                                    lines.append("")
                                lines.append("<b>— " + samsung_section + " —</b>")
                                last_samsung_section = samsung_section
                        if iphone_model(item.title) and item.sim != last_sim:
                            label = SIM_LABELS.get(item.sim)
                            if label:
                                lines.append("<b>— " + label + " —</b>")
                            last_sim = item.sim
                        storage = iphone_storage_key(item) if iphone_model(item.title) else ""
                        if (storage and last_storage is not None and storage != last_storage
                                and lines and lines[-1] != "" and not lines[-1].startswith("<b>")):
                            lines.append("")
                        if storage:
                            last_storage = storage
                        model_key = model_group_key(item)
                        if (not iphone_model(item.title) and last_model_key is not None and model_key != last_model_key
                                and lines and lines[-1] != "" and not lines[-1].startswith("<b>")):
                            lines.append("")
                        last_model_key = model_key
                        row = display_title(item) + " — " + price_text(marked_price(item, settings, overrides), item.currency)
                        line = "<code>" + html.escape(row) + "</code>"
                        if units(line) > 3000:
                            raise ValueError("Слишком длинное наименование товара")
                        lines.append(line)
'''
s = rep(s, old_render, new_render, "paired iPhone rendering")
p.write_text(s, encoding="utf-8")

cp = Path("catalog_publisher.py")
c = cp.read_text(encoding="utf-8")
c = rep(
    c,
    '''def catalog_group(title):
    """Keep iPhone models directly navigable while grouping the rest compactly."""
    name = title.casefold().strip()
    if name.startswith("iphone"):
        return title.strip()
''',
    '''def catalog_labels(title):
    """Return one or more customer-facing buttons for one physical price message."""
    clean_title = title.strip()
    match = re.fullmatch(r"iPhone\\s+(\\d{1,2})\\s*/\\s*\\1\\s+Plus", clean_title, re.I)
    if match:
        n = match.group(1)
        return [f"iPhone {n}", f"iPhone {n} Plus"]
    match = re.fullmatch(r"iPhone\\s+(\\d{1,2})\\s+Pro\\s*/\\s*\\1\\s+Pro Max", clean_title, re.I)
    if match:
        n = match.group(1)
        return [f"iPhone {n} Pro", f"iPhone {n} Pro Max"]
    return [catalog_group(clean_title)]


def catalog_group(title):
    """Keep iPhone models directly navigable while grouping the rest compactly."""
    name = title.casefold().strip()
    if name.startswith("iphone"):
        return title.strip()
''',
    "catalog labels for paired iPhones",
)
old_loop = '''            group = catalog_group(page_title(content))
            if group in seen_groups:
                continue
            link = message_link(self.target, manifest[key]["id"])
            if link:
                seen_groups.add(group)
                buttons.append({"text": group, "url": link})
'''
new_loop = '''            labels = catalog_labels(page_title(content))
            link = message_link(self.target, manifest[key]["id"])
            if not link:
                continue
            for group in labels:
                if group in seen_groups:
                    continue
                seen_groups.add(group)
                buttons.append({"text": group, "url": link})
'''
c = rep(c, old_loop, new_loop, "catalog paired buttons")
cp.write_text(c, encoding="utf-8")

# Add focused tests without rewriting unrelated expectations.
tp = Path("tests/test_prices.py")
t = tp.read_text(encoding="utf-8")
marker = '    def test_blank_line_is_added_when_model_changes(self):\n'
extra = '''    def test_iphone_base_plus_and_pro_pairs_share_messages(self):\n        items = self.parse("""iPhone 16 128 Black — 60000\niPhone 16 256 Blue — 65000\niPhone 16 Plus 128 Pink — 70000\niPhone 16 Plus 512 White — 82000\niPhone 16 Pro 256 Black — 90000\niPhone 16 Pro Max 256 Natural — 100000""").items\n        self.assertEqual({item.block for item in items}, {\n            "iPhone 16 / 16 Plus", "iPhone 16 Pro / 16 Pro Max"\n        })\n        pages = render_blocks(items, Settings())\n        contents = list(pages.values())\n        base = next(page for page in contents if page.startswith("<b>iPhone 16 / 16 Plus</b>"))\n        pro = next(page for page in contents if page.startswith("<b>iPhone 16 Pro / 16 Pro Max</b>"))\n        self.assertIn("— iPhone 16 —", base)\n        self.assertIn("— iPhone 16 Plus —", base)\n        self.assertIn("— iPhone 16 Pro —", pro)\n        self.assertIn("— iPhone 16 Pro Max —", pro)\n        self.assertIn("128 Black", base)\n        self.assertIn("\\n\\n<code>iPhone 16 256 Blue", base)\n        self.assertIn("\\n\\n<code>iPhone 16 Plus 512 White", base)\n\n'''
if marker not in t:
    raise SystemExit("test marker missing")
t = t.replace(marker, extra + marker, 1)
tp.write_text(t, encoding="utf-8")

cat = Path("tests/test_catalog.py")
ct = cat.read_text(encoding="utf-8")
insert = '    async def test_closed_catalog_keeps_buttons_and_removes_prices(self):\n'
extra_cat = '''    async def test_paired_iphone_models_have_separate_buttons_to_same_message(self):\n        items = parse_documents(["iPhone 16 128 Black — 60000\\niPhone 16 Plus 128 Pink — 70000"]).items\n        await self.publisher.publish(render_blocks(items, Settings()))\n        manifest = self.state.get("published")["messages"]\n        self.assertEqual(len(manifest), 1)\n        message_id = next(iter(manifest.values()))["id"]\n        buttons = [button for row in self.catalog_edit()["reply_markup"]["inline_keyboard"] for button in row]\n        iphone_buttons = {button["text"]: button["url"] for button in buttons if button["text"].startswith("iPhone 16")}\n        self.assertEqual(set(iphone_buttons), {"iPhone 16", "iPhone 16 Plus"})\n        self.assertEqual(set(iphone_buttons.values()), {f"https://t.me/c/777/{message_id}"})\n\n'''
if insert not in ct:
    raise SystemExit("catalog test marker missing")
ct = ct.replace(insert, extra_cat + insert, 1)
cat.write_text(ct, encoding="utf-8")
