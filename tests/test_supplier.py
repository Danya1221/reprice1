import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from config import Settings, Source
from supplier import SupplierReader, SupplierTimeout, button_label


def message(number, text):
    return SimpleNamespace(id=number, raw_text=text, buttons=[], document=None, out=False)


class FakeSource:
    def __init__(self):
        self.rows = [message(1, "Старое меню")]
        self.handlers = []

    async def get_messages(self, *args, **kwargs):
        return list(reversed(self.rows))

    def add_event_handler(self, handler, builder):
        self.handlers.append(handler)

    def remove_event_handler(self, handler, builder):
        self.handlers.remove(handler)


class SupplierTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = FakeSource()
        self.reader = SupplierReader(self.client, Settings(
            action_delay=0.001, quiet_seconds=0.006, response_timeout=0.04), Source("@supplier"))
        self.reader.entity = "@supplier"

    async def test_stale_menu_is_never_a_reply(self):
        async def action():
            pass
        with self.assertRaises(SupplierTimeout):
            await self.reader.collect_action(action)
        self.assertEqual(self.client.handlers, [])

    async def test_same_message_edit_is_fresh(self):
        async def action():
            self.client.rows[0].raw_text = "iPhone 17 256 Black — 60000"
        replies = await self.reader.collect_action(action)
        self.assertEqual([m.id for m in replies], [1])
        self.assertIn("60000", replies[0].raw_text)

    async def test_new_reply_wins_over_old_menu(self):
        async def action():
            self.client.rows.append(message(2, "iPhone 17 256 Black — 60000"))
        replies = await self.reader.collect_action(action)
        self.assertEqual([m.id for m in replies], [2])

    async def test_collects_all_multipart_replies(self):
        async def action():
            self.client.rows.extend([message(2, "iPhone 17 256 Black — 60000"),
                                     message(3, "Dyson HS08 — 40000")])
        replies = await self.reader.collect_action(action)
        self.assertEqual([m.id for m in replies], [2, 3])

    async def test_deleted_reply_is_retained_from_event(self):
        async def action():
            event = SimpleNamespace(message=message(2, "iPhone 17 256 Black — 60000"))
            await self.client.handlers[0](event)
            # The message never reaches history: supplier deleted it immediately.
        replies = await self.reader.collect_action(action)
        self.assertEqual([m.id for m in replies], [2])

    async def test_cleanup_after_action_error(self):
        async def action():
            raise ConnectionError("disconnected")
        with self.assertRaises(ConnectionError):
            await self.reader.collect_action(action)
        self.assertEqual(self.client.handlers, [])

    async def test_feed_ignores_photo_and_video_with_price_captions(self):
        self.reader.source = Source("@supplier", mode="feed")
        photo = message(2, "Dyson HS09 — 10000")
        photo.photo = SimpleNamespace(id=101)
        video = message(3, "Dyson HS10 — 12000")
        video.document = SimpleNamespace(
            size=8 * 1024 * 1024, mime_type="video/mp4",
            attributes=[SimpleNamespace(file_name="pretend_price.xlsx")])
        self.client.rows = [message(1, "Dyson HS08 — 40000"), photo, video]
        result = await self.reader.fetch()
        self.assertEqual([item.price for item in result.items], [40000])
        self.assertEqual(self.reader.last_read_stats["messages"], 3)
        self.assertEqual(self.reader.last_read_stats["ignored_media"], 2)
        self.assertEqual(self.reader.last_read_stats["price_files"], 0)

    async def test_diagnostics_count_duplicate_price_rows_and_skipped_captions(self):
        self.reader.source = Source("@supplier", mode="feed")
        photo = message(5, "Dyson Airwrap HS08 — 39900")
        photo.photo = SimpleNamespace(id=55)
        video = message(6, "Dyson HS09 — 48000")
        video.document = SimpleNamespace(
            mime_type="video/mp4", size=9 * 1024 * 1024,
            attributes=[SimpleNamespace(file_name="video.mp4")],
        )
        self.client.rows = [
            message(1, "Dyson HS08 — 40000"),
            message(2, "Dyson HS08 — 39000"),
            message(3, "Dyson HS09 — 42000"),
            photo, video,
        ]
        result = await self.reader.fetch()
        self.assertEqual(len(result.items), 2)
        self.assertEqual(self.reader.last_read_stats["messages"], 5)
        self.assertEqual(self.reader.last_read_stats["text_posts"], 3)
        self.assertEqual(self.reader.last_read_stats["price_rows"], 3)
        self.assertEqual(self.reader.last_read_stats["parsed"], 2)
        self.assertEqual(self.reader.last_read_stats["ignored_caption_posts"], 2)
        self.assertEqual(self.reader.last_read_stats["ignored_photo_posts"], 1)
        self.assertEqual(self.reader.last_read_stats["ignored_video_posts"], 1)

    async def test_caption_opt_in_parses_text_without_downloading_media(self):
        self.reader.source = Source("@supplier", mode="feed", read_captions=True)
        photo = message(2, "Dyson HS09 — 47000")
        photo.photo = SimpleNamespace(id=101)
        video = message(3, "Dyson HS10 — 52000")
        video.document = SimpleNamespace(
            size=12 * 1024 * 1024, mime_type="video/mp4",
            attributes=[SimpleNamespace(file_name="movie.mp4")])
        self.client.download_media = AsyncMock(side_effect=AssertionError("Media must never download"))
        self.client.rows = [message(1, "Dyson HS08 — 40000"), photo, video]
        result = await self.reader.fetch()
        self.assertEqual(len(result.items), 3)
        self.assertEqual(self.reader.last_read_stats["read_caption_posts"], 2)
        self.assertEqual(self.reader.last_read_stats["ignored_caption_posts"], 0)
        self.assertEqual(self.reader.last_read_stats["ignored_media"], 2)
        self.client.download_media.assert_not_awaited()

    async def test_dyson_feed_categorizes_shorthand_models(self):
        self.reader.source = Source("@dyson", mode="feed", label="Канал: R Dyson")
        self.client.rows = [message(1, "🇬🇧 V15 SV47 Detect Absolute Yellow/Nickel 52500 📷"),
                            message(2, "🇪🇺 HS07 Corrale Copper/Nickel с чехлом 38000 📷")]
        result = await self.reader.fetch()
        self.assertEqual(len(result.items), 2)
        self.assertEqual([item.block for item in result.items], ["Dyson", "Dyson"])
        self.assertEqual(self.reader.last_read_stats["price_rows"], 2)

    async def test_video_file_is_never_downloaded_as_spreadsheet(self):
        self.reader.source = Source("@supplier", mode="feed")
        video = message(1, "Dyson HS10 — 12000")
        video.document = SimpleNamespace(size=12 * 1024 * 1024, mime_type="video/mp4",
            attributes=[SimpleNamespace(file_name="fake.xlsx")])
        self.client.download_media = AsyncMock(side_effect=AssertionError("Do not download media"))
        self.client.rows = [video, message(2, "Dyson HS08 — 40000")]
        result = await self.reader.fetch()
        self.assertEqual(len(result.items), 1)
        self.client.download_media.assert_not_awaited()

    def test_button_labels_ignore_emoji(self):
        self.assertEqual(button_label("📦 Прайс"), button_label("Прайс"))

    async def test_custom_closed_reply_is_recognized(self):
        self.reader.source = Source("@supplier", closed_text="Заказы на сегодня завершены")
        self.reader.messages = AsyncMock(return_value=[message(2, "ЗАКАЗЫ НА СЕГОДНЯ\nЗАВЕРШЕНЫ")])
        result = await self.reader.fetch()
        self.assertTrue(result.closed)
        self.assertEqual(result.items, [])

    async def test_custom_feed_closure_does_not_reuse_older_prices(self):
        self.reader.source = Source("@supplier", mode="feed", closed_text="Прайс будет завтра")
        self.client.rows = [message(1, "iPhone 17 256 Black — 60000"),
                            message(2, "Прайс будет завтра")]
        result = await self.reader.fetch()
        self.assertTrue(result.closed)
        self.assertEqual(result.items, [])

    async def test_custom_closed_reply_stops_button_navigation(self):
        self.reader.source = Source("@supplier", buttons=("Прайс",), closed_text="Прайс будет завтра")
        self.reader.collect_action = AsyncMock(return_value=[message(2, "Прайс будет завтра")])
        result = await self.reader.fetch()
        self.assertTrue(result.closed)
        self.reader.collect_action.assert_awaited_once()

    def test_generic_closure_still_works_with_custom_phrase(self):
        self.reader.source = Source("@supplier", closed_text="Прайс будет завтра")
        self.assertTrue(self.reader.is_closed_text("В данный момент мы закрыты"))
        self.assertFalse(self.reader.is_closed_text("Выберите категорию"))
