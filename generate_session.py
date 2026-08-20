import asyncio
import os

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.sessions import StringSession


load_dotenv()

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "").strip()


async def main():
    if not API_ID or not API_HASH:
        raise RuntimeError(
            "Сначала заполни API_ID и API_HASH в .env"
        )

    client = TelegramClient(
        StringSession(),
        API_ID,
        API_HASH,
    )

    await client.start()

    print("\nSESSION_STRING:")
    print(client.session.save())
    print(
        "\nСкопируй эту строку в .env / Railway "
        "как SESSION_STRING."
    )

    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
