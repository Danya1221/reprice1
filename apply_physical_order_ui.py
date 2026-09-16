from pathlib import Path


def replace_between(text, start, end, replacement, label):
    a = text.find(start)
    if a < 0:
        raise SystemExit(f"{label}: start not found")
    b = text.find(end, a)
    if b < 0:
        raise SystemExit(f"{label}: end not found")
    return text[:a] + replacement + text[b:]


# prices.py: order final physical Telegram messages, not old logical product blocks.
p = Path("prices.py")
s = p.read_text(encoding="utf-8")
old = '''def render_blocks(items, settings, overrides=None, closed=False):
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
        # Apple has many logical subcategories. Smaller chunks let the physical
        # packer fill Apple posts with several sections instead of orphaning a
        # one-line Mac mini or Apple TV message after a nearly-full MacBook page.
        chunk_limit = 1800 if physical_section_family(block) == "apple" else 3200
        logical_sections.extend(section_chunks(block, lines, limit=chunk_limit))

    pages = OrderedDict()
    for bundle, sections in pack_physical_sections(logical_sections):
        content = build_physical_message(sections, bundle)
        seed = bundle + "|" + "|".join(f'{section["title"]}#{section["part"]}' for section in sections)
        key = hashlib.sha256(seed.encode()).hexdigest()[:16] + ":0"
        pages[key] = content
    return pages
'''
new = '''def rendered_page_title(content):
    """Visible top heading of one final Telegram price message."""
    match = re.match(r"<b>\\s*(?:—\\s*)?(.*?)(?:\\s*—)?\\s*</b>", content or "")
    return html.unescape(match.group(1)).strip() if match else "Прайс"


def render_blocks(items, settings, overrides=None, closed=False):
    """Render logical sections, pack them, then order the real Telegram messages."""
    overrides = overrides or {}
    groups = OrderedDict()
    for item in items:
        groups.setdefault(item.block, []).append(item)

    # Old block_order contained parser-level names such as Mac mini, AirPods or
    # Samsung S26. Those are no longer Telegram messages, so it must not drive
    # publication order. The operator now orders the final physical headings.
    names = ordered_blocks(groups, [])
    logical_sections = []
    for block in names:
        lines = render_block_lines(block, groups[block], settings, overrides, closed=closed)
        chunk_limit = 1800 if physical_section_family(block) == "apple" else 3200
        logical_sections.extend(section_chunks(block, lines, limit=chunk_limit))

    built = []
    for bundle, sections in pack_physical_sections(logical_sections):
        content = build_physical_message(sections, bundle)
        seed = bundle + "|" + "|".join(f'{section["title"]}#{section["part"]}' for section in sections)
        key = hashlib.sha256(seed.encode()).hexdigest()[:16] + ":0"
        built.append((key, content, rendered_page_title(content)))

    preferred = [clean(value) for value in overrides.get("physical_order", []) if clean(value)]
    if preferred:
        rank = {name.casefold(): index for index, name in enumerate(preferred)}
        built = [entry for _, entry in sorted(
            enumerate(built),
            key=lambda pair: (rank.get(pair[1][2].casefold(), len(rank)), pair[0]),
        )]

    pages = OrderedDict()
    for key, content, _title in built:
        pages[key] = content
    return pages
'''
if old not in s:
    raise SystemExit("prices render_blocks shape changed")
s = s.replace(old, new, 1)
p.write_text(s, encoding="utf-8")


# control_catalog.py: show final rendered messages and move by typed position.
p = Path("control_catalog.py")
s = p.read_text(encoding="utf-8")
s = s.replace("from prices import Item, ordered_blocks\n", "from prices import render_blocks, rendered_page_title, select_items\n", 1)

s = replace_between(
    s,
    "    def known_blocks(self):\n",
    "    async def _edit_or_send",
    '''    def known_blocks(self):
        """Current physical Telegram message headings, exactly as they are published."""
        catalog = self.service.cached_items(include_closed=True)
        options = self.service.options()
        selected = select_items(catalog, self.service.settings, options)
        pages = render_blocks(selected, self.service.settings, options)
        result = []
        for content in pages.values():
            title = rendered_page_title(content)
            if title and title not in result:
                result.append(title)
        return result

''',
    "physical known blocks",
)

s = replace_between(
    s,
    "    def _draft_order(self, user_id):\n",
    "    async def _refresh_result",
    '''    def _draft_order(self, user_id):
        known = self.known_blocks()
        preferred = self.order_drafts.get(user_id, self.service.options().get("physical_order", []))
        order = [name for name in preferred if name in known]
        order.extend(name for name in known if name not in order)
        self.order_drafts[user_id] = order
        return order

    async def show_order(self, chat_id, user_id, page=0, message_id=None, notice=""):
        order = self._draft_order(user_id)
        selected = self.order_selected.get(user_id)
        if selected not in order:
            selected = None
            self.order_selected.pop(user_id, None)

        rows = []
        buttons = []
        for index, block in enumerate(order):
            buttons.append({
                "text": ("✅ " if block == selected else "") + f"{index + 1}. {block}",
                "callback_data": f"order:select:{block_id(block)}",
            })
        for start in range(0, len(buttons), 2):
            rows.append(buttons[start:start + 2])
        if order:
            rows.append([{"text": "↩️ По умолчанию", "callback_data": "order:reset"}])
        rows.append([{"text": "⬅️ Назад", "callback_data": "order:back"}])

        if order:
            if selected:
                hint = f"\n\nВыбран: {selected}\nОтправь одним сообщением номер позиции от 1 до {len(order)}."
            else:
                hint = "\n\nНажми на нужное сообщение, затем просто отправь номер места."
            text = (
                f"↕️ Порядок сообщений · всего {len(order)}\n"
                "Здесь только реальные сообщения, которые сейчас публикуются в прайсе."
                + hint
            )
        else:
            text = "Сначала запроси прайс — здесь появятся текущие сообщения прайса."
        if notice:
            text = notice + "\n\n" + text
        await self._edit_or_send(chat_id, message_id, text, {"inline_keyboard": rows})

    def _move_selected_to(self, user_id, position):
        order = self._draft_order(user_id)
        selected = self.order_selected.get(user_id)
        if selected not in order:
            raise ValueError("Сначала выбери сообщение из списка")
        if not 1 <= position <= len(order):
            raise ValueError(f"Номер должен быть от 1 до {len(order)}")
        order.remove(selected)
        order.insert(position - 1, selected)
        self.order_drafts[user_id] = order
        return order, selected

    async def _refresh_order_result(self, chat_id, user_id, message_id):
        try:
            count, changes = await self.service.refresh_format()
            await self.show_order(
                chat_id, user_id, message_id=message_id,
                notice=f"✅ Порядок применён. Прайс обновлён: {count} позиций.",
            )
        except Exception as exc:
            await self.show_order(
                chat_id, user_id, message_id=message_id,
                notice="⚠️ Порядок сохранён, но обновление не завершилось: " + str(exc),
            )

    async def refresh_order(self, chat_id, user_id, message_id=None):
        if self.task and not self.task.done():
            await self.show_order(
                chat_id, user_id, message_id=message_id,
                notice="⏳ Обновление уже идёт. Новый порядок сохранён.",
            )
            return
        await self.show_order(chat_id, user_id, message_id=message_id, notice="⏳ Порядок сохранён, обновляю прайс…")
        self.task = asyncio.create_task(self._refresh_order_result(chat_id, user_id, message_id))

''',
    "new order interaction",
)

old_callbacks = '''            if data.startswith("order:show:"):
                await self.show_order(chat_id, user_id, message_id=message_id)
            elif data.startswith("order:select:"):
                ident = data.rsplit(":", 1)[1]
                order = self._draft_order(user_id)
                block = next((block for block in order if block_id(block) == ident), None)
                if block:
                    self.order_selected[user_id] = block
                await self.show_order(chat_id, user_id, message_id=message_id)
            elif data.startswith("order:move:"):
                self._move_selected(user_id, data.rsplit(":", 1)[1])
                await self.show_order(chat_id, user_id, message_id=message_id)
            # Backward compatibility for old inline keyboards already sent to Telegram.
            elif data.startswith(("order:up:", "order:down:")):
                _, direction, ident, _page = data.split(":")
                order = self._draft_order(user_id)
                block = next((block for block in order if block_id(block) == ident), None)
                if block:
                    self.order_selected[user_id] = block
                    self._move_selected(user_id, "up" if direction == "up" else "down")
                await self.show_order(chat_id, user_id, message_id=message_id)
            elif data == "order:reset":
                order = ordered_blocks(self.known_blocks(), [])
                self.order_drafts[user_id] = order
                self.order_selected.pop(user_id, None)
                await self.show_order(chat_id, user_id, message_id=message_id, notice="↩️ Вернул порядок по умолчанию.")
            elif data == "order:apply":
                order = self.order_drafts.get(user_id)
                if order is None:
                    await self.show_order(chat_id, user_id, message_id=message_id)
                    return
                self.service.set_option("block_order", ordered_blocks(self.known_blocks(), order))
                await self.refresh_order(chat_id, user_id, message_id)
            elif data == "order:back":
'''
new_callbacks = '''            if data.startswith("order:show:"):
                await self.show_order(chat_id, user_id, message_id=message_id)
            elif data.startswith("order:select:"):
                ident = data.rsplit(":", 1)[1]
                order = self._draft_order(user_id)
                block = next((block for block in order if block_id(block) == ident), None)
                if block:
                    self.order_selected[user_id] = block
                    await self.show_order(
                        chat_id, user_id, message_id=message_id,
                        notice=f"Выбран «{block}». Теперь отправь номер его места.",
                    )
                else:
                    await self.show_order(chat_id, user_id, message_id=message_id)
            # Old keyboards may still be visible in Telegram. Open the new screen
            # instead of applying their obsolete logical-block movement actions.
            elif data.startswith(("order:move:", "order:up:", "order:down:")) or data == "order:apply":
                await self.show_order(
                    chat_id, user_id, message_id=message_id,
                    notice="Эта старая кнопка больше не используется. Выбери текущее сообщение и отправь его номер.",
                )
            elif data == "order:reset":
                self.order_drafts.pop(user_id, None)
                self.order_selected.pop(user_id, None)
                self.service.set_option("physical_order", [])
                self.service.set_option("block_order", [])
                await self.refresh_order(chat_id, user_id, message_id)
            elif data == "order:back":
'''
if old_callbacks not in s:
    raise SystemExit("callback order block changed")
s = s.replace(old_callbacks, new_callbacks, 1)

start = s.find("    async def handle_message(self, message):\n")
if start < 0:
    raise SystemExit("handle_message not found")
new_handle = '''    async def handle_message(self, message):
        text = (message.get("text") or "").strip()
        user_id = message.get("from", {}).get("id")
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        chat_type = chat.get("type", "private")

        # Login codes/passwords and the custom first-message flow always win.
        if user_id in self.login_flows or user_id in getattr(self, "first_message_waiting", set()):
            await super().handle_message(message)
            return

        if user_id in self.order_selected and text and not text.startswith("/"):
            if not self.allowed(user_id, chat_type):
                await self.explain_access(chat_id, user_id, chat_type)
                return
            if not text.isdigit():
                await self.send(chat_id, "Отправь только номер позиции, например 3. Для отмены нажми другой пункт меню.")
                return
            try:
                order, selected = self._move_selected_to(user_id, int(text))
            except ValueError as exc:
                await self.send(chat_id, str(exc))
                return
            self.service.set_option("physical_order", order)
            # Remove the obsolete parser-level order so old Mac mini/AirPods/S26
            # names can never affect the new physical-message layout again.
            self.service.set_option("block_order", [])
            self.order_selected.pop(user_id, None)
            await self.send(chat_id, f"✅ {selected} → место №{int(text)}. Обновляю прайс…")
            await self.refresh_order(chat_id, user_id)
            return

        words = text.split(maxsplit=1)
        command, _, recipient = (words[0].lower() if words else "").partition("@")
        if command != "/order":
            await super().handle_message(message)
            return
        if recipient and self.username and recipient != self.username:
            return
        if not self.allowed(user_id, chat_type):
            await self.explain_access(chat_id, user_id, chat_type)
            return
        if len(words) == 1:
            await self.show_order(chat_id, user_id)
            return

        # Optional direct command: /order Apple 2
        name, separator, raw_position = words[1].rpartition(" ")
        if not separator or not raw_position.isdigit():
            await self.send(chat_id, "Открой /order, выбери текущее сообщение и отправь номер его места.")
            return
        available = self._draft_order(user_id)
        block = next((value for value in available if value.casefold() == name.strip().casefold()), None)
        if not block:
            await self.send(chat_id, "Нет такого текущего сообщения. Открой /order и выбери его кнопкой.")
            return
        self.order_selected[user_id] = block
        try:
            order, selected = self._move_selected_to(user_id, int(raw_position))
        except ValueError as exc:
            await self.send(chat_id, str(exc))
            return
        self.service.set_option("physical_order", order)
        self.service.set_option("block_order", [])
        self.order_selected.pop(user_id, None)
        await self.send(chat_id, f"✅ {selected} → место №{int(raw_position)}. Обновляю прайс…")
        await self.refresh_order(chat_id, user_id)
'''
s = s[:start] + new_handle
p.write_text(s, encoding="utf-8")


# Tests ----------------------------------------------------------------------
p = Path("tests/test_catalog.py")
s = p.read_text(encoding="utf-8")
s = s.replace(
    '            options=lambda: self.options, state=self.state, set_option=set_option,\n            refresh_format=AsyncMock(return_value=(2, 2)))',
    '            options=lambda: self.options, state=self.state, set_option=set_option, settings=Settings(),\n            refresh_format=AsyncMock(return_value=(2, 2)))',
    1,
)
old_test = '''    async def test_order_changes_only_after_apply(self):
        await self.controller.handle_callback(self.callback("order:show:0"))
        await self.controller.handle_callback(self.callback(f"order:up:{block_id('Dyson')}:0"))
        self.assertEqual(self.options, {})
        await self.controller.handle_callback(self.callback("order:apply"))
        await self.controller.task
        self.assertEqual(self.options["block_order"], ["Dyson", "iPhone 17"])
        self.service.refresh_format.assert_awaited_once()

    async def test_order_screen_includes_blocks_from_all_raw_supplier_caches(self):
        raw = parse_documents(["Samsung Galaxy S26 12/256 Black — 70000\\nVivo V70 12/256 Grey — 46300\\nOura Ring 4 Silver — 35000"]).items
        self.state.set("sources", {"extra": {"items": [item.to_dict() for item in raw]}})
        await self.controller.show_order(42, 42, 0)
        text = self.controller.send.await_args.args[1]
        keyboard = self.controller.send.await_args.args[2]["inline_keyboard"]
        labels = [button["text"] for row in keyboard for button in row]
        self.assertIn("Samsung", " ".join(labels))
        self.assertIn("Vivo", " ".join(labels))
        self.assertIn("Oura Ring", " ".join(labels))
        self.assertIn("всего 5", text)
'''
new_test = '''    async def test_order_moves_selected_physical_message_by_typed_number(self):
        await self.controller.handle_callback(self.callback("order:show:0"))
        order = self.controller._draft_order(42)
        self.assertIn("Dyson", order)
        dyson = next(name for name in order if name == "Dyson")
        await self.controller.handle_callback(self.callback(f"order:select:{block_id(dyson)}"))
        await self.controller.handle_message({"text": "1", "from": {"id": 42}, "chat": {"id": 42, "type": "private"}})
        await self.controller.task
        self.assertEqual(self.options["physical_order"][0], "Dyson")
        self.assertEqual(self.options["block_order"], [])
        self.service.refresh_format.assert_awaited_once()

    async def test_order_screen_uses_current_physical_message_titles(self):
        items = parse_documents([
            "iPhone 17 256 Black — 60000\\n"
            "Mac Mini (MU9D3) M4/16/256 Silver — 68500\\n"
            "Apple TV 4K 128GB — 15000\\n"
            "Dyson HS08 — 40000"
        ]).items
        self.service.cached_items = lambda **kw: items
        await self.controller.show_order(42, 42, 0)
        text = self.controller.send.await_args.args[1]
        keyboard = self.controller.send.await_args.args[2]["inline_keyboard"]
        labels = [button["text"] for row in keyboard for button in row]
        joined = " ".join(labels)
        self.assertIn("iPhone", joined)
        self.assertIn("Apple", joined)
        self.assertIn("Dyson", joined)
        self.assertNotIn("Mac mini", joined)
        self.assertNotIn("Apple TV", joined)
        self.assertIn("реальные сообщения", text)
'''
if old_test not in s:
    raise SystemExit("catalog order tests changed")
s = s.replace(old_test, new_test, 1)
p.write_text(s, encoding="utf-8")

p = Path("tests/test_prices.py")
s = p.read_text(encoding="utf-8")
marker = '''    def test_esim_separators_are_not_physical_sim(self):
'''
new_price_test = '''    def test_physical_order_reorders_final_messages_not_logical_apple_sections(self):
        items = self.parse(
            "iPhone 17 256 Black — 60000\\n"
            "Mac Mini (MU9D3) M4/16/256 Silver — 68500\\n"
            "Apple TV 4K 128GB — 15000\\n"
            "Dyson HS08 — 40000"
        ).items
        pages = render_blocks(items, Settings(), {"physical_order": ["Dyson", "Apple"]})
        titles = [re.match(r"<b>(.*?)</b>", page).group(1) for page in pages.values()]
        self.assertEqual(titles[:2], ["Dyson", "Apple"])
        self.assertNotIn("Mac mini", titles)
        self.assertNotIn("Apple TV", titles)

'''
if new_price_test not in s:
    if marker not in s:
        raise SystemExit("price test insertion point missing")
    s = s.replace(marker, new_price_test + marker, 1)
p.write_text(s, encoding="utf-8")
