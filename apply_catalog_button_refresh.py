from pathlib import Path


def rep(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected 1 match, got {count}")
    return text.replace(old, new, 1)

p = Path("catalog_publisher.py")
s = p.read_text(encoding="utf-8")

old = '''def catalog_labels(content):
    """Buttons represented by one physical Telegram price message."""
    plain = html.unescape(re.sub(r"<[^>]+>", " ", content))
    iphone_pattern = re.compile(
'''
new = '''def catalog_labels(content):
    """Buttons represented by one physical Telegram price message."""
    # Major ecosystems are defined by the final physical post heading.  Do not
    # let an old logical section name (Mac mini, Apple TV, Samsung S26, etc.)
    # leak back into the public catalog buttons.
    title = page_title(content)
    if title.casefold().strip() == "apple":
        return ["Apple"]
    if title.casefold().strip() == "samsung":
        return ["Samsung"]

    plain = html.unescape(re.sub(r"<[^>]+>", " ", content))
    iphone_pattern = re.compile(
'''
s = rep(s, old, new, "physical heading catalog labels")

s = rep(
    s,
    '''    title = page_title(content)
    labels = []
''',
    '''    labels = []
''',
    "remove duplicate title lookup",
)

old = '''            count = 1
            records = await self._prepare_catalog(count)
            changes += await self._update_catalog(pages, records)
            return changes
'''
new = '''            count = 1
            records = await self._prepare_catalog(count)

            # Existing Telegram catalog messages may carry a hash produced by an
            # older button-layout algorithm.  Force one in-place keyboard rewrite
            # when this layout version changes; keep the same message ID.
            force_catalog = int(self.state.get("catalog_layout_version", 0) or 0) < 3
            if force_catalog:
                for record in records:
                    record["hash"] = ""
                self.save_catalog(records)

            changes += await self._update_catalog(pages, records)
            if force_catalog:
                self.state.set("catalog_layout_version", 3)
            return changes
'''
s = rep(s, old, new, "catalog layout version refresh")
p.write_text(s, encoding="utf-8")

p = Path("tests/test_catalog.py")
t = p.read_text(encoding="utf-8")
marker = '    async def test_samsung_series_share_one_compact_catalog_button(self):\n'
extra = '''    async def test_mac_mini_uses_apple_catalog_button(self):
        items = parse_documents(["Mac mini M4 16/256 Silver — 68800"]).items
        pages = render_blocks(items, Settings())
        content = next(iter(pages.values()))
        self.assertTrue(content.startswith("<b>Apple</b>"))
        await self.publisher.publish(pages)
        buttons = [button for row in self.catalog_edit()["reply_markup"]["inline_keyboard"] for button in row]
        self.assertEqual([button["text"] for button in buttons], ["Apple"])

    async def test_catalog_layout_version_forces_existing_keyboard_edit(self):
        items = parse_documents(["Mac mini M4 16/256 Silver — 68800"]).items
        pages = render_blocks(items, Settings())
        await self.publisher.publish(pages)
        catalog_id = self.state.get("catalog")["messages"][0]["id"]
        self.state.set("catalog_layout_version", 2)
        self.publisher.calls.clear()
        await self.publisher.publish(pages)
        self.assertEqual(self.state.get("catalog")["messages"][0]["id"], catalog_id)
        self.assertEqual(self.state.get("catalog_layout_version"), 3)
        edits = [payload for method, payload in self.publisher.calls if method == "editMessageText" and payload.get("reply_markup")]
        self.assertTrue(edits)
        buttons = [button for row in edits[-1]["reply_markup"]["inline_keyboard"] for button in row]
        self.assertEqual([button["text"] for button in buttons], ["Apple"])
        self.assertFalse(any(method == "sendMessage" for method, _ in self.publisher.calls))

'''
if marker not in t:
    raise SystemExit("catalog test marker missing")
t = t.replace(marker, extra + marker, 1)
p.write_text(t, encoding="utf-8")
