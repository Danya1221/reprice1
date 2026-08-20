import asyncio
import hashlib
import html as html_lib
from contextlib import suppress

from telethon import TelegramClient, events, Button
from telethon.errors import FloodWaitError
from telethon.sessions import StringSession

from config import (
    API_ID,
    API_HASH,
    SESSION_STRING,
    SUPPLIER_BOT,
    TARGET_CHANNEL,
    REQUEST_TEXT,
    BUTTON_PATH,
    POLL_SECONDS,
    AFTER_ACTION_DELAY,
    RESPONSE_TIMEOUT,
    PRICE_HEADER,
    STATE_FILE,
)
from state import StateStore


state = StateStore(STATE_FILE)

client = TelegramClient(
    StringSession(SESSION_STRING),
    API_ID,
    API_HASH,
)

sync_lock = asyncio.Lock()


def validate_config():
    missing = []

    if not API_ID:
        missing.append("API_ID")
    if not API_HASH:
        missing.append("API_HASH")
    if not SESSION_STRING:
        missing.append("SESSION_STRING")
    if not SUPPLIER_BOT:
        missing.append("SUPPLIER_BOT")
    if not TARGET_CHANNEL:
        missing.append("TARGET_CHANNEL")

    if missing:
        raise RuntimeError(
            "Не заполнены переменные: " + ", ".join(missing)
        )


def fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def get_latest_incoming(limit: int = 10):
    messages = await client.get_messages(SUPPLIER_BOT, limit=limit)

    for msg in messages:
        if not msg.out:
            return msg

    return None


async def click_button_by_text(message, wanted_text: str):
    if not message or not message.buttons:
        raise RuntimeError(
            f"Нет кнопок для нажатия '{wanted_text}'"
        )

    wanted = wanted_text.casefold().strip()

    for row in message.buttons:
        for button in row:
            label = (button.text or "").casefold().strip()

            if label == wanted:
                print(f"🔘 Нажимаю кнопку: {button.text}")
                await button.click()
                return

    available = [
        button.text
        for row in message.buttons
        for button in row
        if getattr(button, "text", None)
    ]

    raise RuntimeError(
        f"Кнопка '{wanted_text}' не найдена. "
        f"Доступные: {available}"
    )


async def wait_and_refresh(message_id=None):
    await asyncio.sleep(AFTER_ACTION_DELAY)

    if message_id:
        with suppress(Exception):
            refreshed = await client.get_messages(
                SUPPLIER_BOT,
                ids=message_id,
            )
            if refreshed:
                return refreshed

    return await get_latest_incoming()


async def request_price():
    print(f"📡 Запрашиваю прайс у {SUPPLIER_BOT}")

    before = await get_latest_incoming()
    before_id = before.id if before else None

    sent = await client.send_message(
        SUPPLIER_BOT,
        REQUEST_TEXT,
    )

    # Даём боту время ответить или отредактировать своё сообщение.
    response = None

    for _ in range(max(1, int(RESPONSE_TIMEOUT / max(AFTER_ACTION_DELAY, 0.5)))):
        await asyncio.sleep(AFTER_ACTION_DELAY)
        candidate = await get_latest_incoming()

        if candidate and (
            candidate.id != before_id
            or candidate.date >= sent.date
        ):
            response = candidate
            break

    if response is None:
        response = await get_latest_incoming()

    if response is None:
        raise RuntimeError("Бот поставщика не прислал ответ")

    # Проходим по цепочке кнопок.
    for button_name in BUTTON_PATH:
        current_id = response.id

        await click_button_by_text(
            response,
            button_name,
        )

        response = await wait_and_refresh(current_id)

        if response is None:
            raise RuntimeError(
                f"После кнопки '{button_name}' нет ответа"
            )

    # Последний refresh важен для ботов, которые после клика
    # не присылают новое сообщение, а редактируют старое.
    response = await wait_and_refresh(response.id)

    return response


def build_target_text(source_text: str) -> str:
    source_text = source_text.strip()

    if PRICE_HEADER:
        return f"{PRICE_HEADER}\n\n{source_text}"

    return source_text


async def find_existing_target_message():
    saved_id = state.get("target_message_id")

    if saved_id:
        with suppress(Exception):
            msg = await client.get_messages(
                TARGET_CHANNEL,
                ids=int(saved_id),
            )
            if msg:
                return msg

    if not PRICE_HEADER:
        return None

    async for msg in client.iter_messages(
        TARGET_CHANNEL,
        limit=100,
    ):
        text = msg.raw_text or ""

        if text.startswith(PRICE_HEADER):
            state.set("target_message_id", msg.id)
            return msg

    return None


async def publish_or_update(source_message):
    source_text = source_message.raw_text or ""

    if not source_text.strip():
        raise RuntimeError(
            "Ответ поставщика не содержит текста"
        )

    final_text = build_target_text(source_text)
    current_hash = fingerprint(final_text)

    if state.get("last_content") == current_hash:
        print("✅ Прайс без изменений")
        return

    async with sync_lock:
        target = await find_existing_target_message()

        if target:
            await client.edit_message(
                TARGET_CHANNEL,
                target.id,
                final_text,
                link_preview=False,
            )
            target_id = target.id
            print(f"♻️ Прайс обновлён: message_id={target_id}")
        else:
            target = await client.send_message(
                TARGET_CHANNEL,
                final_text,
                link_preview=False,
            )
            target_id = target.id
            print(f"✅ Создан прайс: message_id={target_id}")

        state.set("target_message_id", target_id)
        state.set(
            "last_supplier_message_id",
            source_message.id,
        )
        state.set("last_content", current_hash)


async def sync_once():
    try:
        source_message = await request_price()
        await publish_or_update(source_message)

    except FloodWaitError as e:
        print(f"⏳ FloodWait: {e.seconds} сек.")
        await asyncio.sleep(e.seconds + 3)

    except Exception as e:
        print(f"❌ Ошибка синхронизации: {e}")


@client.on(events.MessageEdited(chats=SUPPLIER_BOT))
async def supplier_message_edited(event):
    last_id = state.get("last_supplier_message_id")

    if last_id and event.message.id == int(last_id):
        print("✏️ Поставщик отредактировал текущий прайс")

        try:
            await publish_or_update(event.message)
        except Exception as e:
            print(f"❌ Ошибка обновления после редактирования: {e}")


async def periodic_sync():
    while True:
        await sync_once()
        await asyncio.sleep(POLL_SECONDS)


async def main():
    validate_config()

    await client.start()

    me = await client.get_me()
    print(
        f"✅ Price Sync запущен от аккаунта "
        f"{me.username or me.id}"
    )
    print(
        f"📦 {SUPPLIER_BOT} → {TARGET_CHANNEL}"
    )

    task = asyncio.create_task(periodic_sync())

    try:
        await client.run_until_disconnected()
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


if __name__ == "__main__":
    asyncio.run(main())
