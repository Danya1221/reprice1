import os
from dotenv import load_dotenv

load_dotenv()

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "").strip()
SESSION_STRING = os.getenv("SESSION_STRING", "").strip()

SUPPLIER_BOT = os.getenv("SUPPLIER_BOT", "").strip()
TARGET_CHANNEL = os.getenv("TARGET_CHANNEL", "").strip()

REQUEST_TEXT = os.getenv("REQUEST_TEXT", "/start").strip()
BUTTON_PATH = [
    x.strip()
    for x in os.getenv("BUTTON_PATH", "").split(">")
    if x.strip()
]

POLL_SECONDS = int(os.getenv("POLL_SECONDS", "600"))
AFTER_ACTION_DELAY = float(os.getenv("AFTER_ACTION_DELAY", "2"))
RESPONSE_TIMEOUT = int(os.getenv("RESPONSE_TIMEOUT", "30"))

PRICE_HEADER = os.getenv("PRICE_HEADER", "📦 АКТУАЛЬНЫЙ ПРАЙС").strip()
STATE_FILE = os.getenv("STATE_FILE", "state.json").strip()
