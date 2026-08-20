import html
import os

from aiohttp import web
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from telethon.sessions import StringSession

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "").strip()
SETUP_KEY = os.getenv("SETUP_KEY", "").strip()
PORT = int(os.getenv("PORT", "8080"))

if not API_ID or not API_HASH:
    raise RuntimeError("Заполни API_ID и API_HASH в Railway Variables")
if not SETUP_KEY:
    raise RuntimeError("Добавь SETUP_KEY в Railway Variables")

client = TelegramClient(StringSession(), API_ID, API_HASH)
state = {"phone": None, "phone_code_hash": None}

PAGE = """<!doctype html><html lang='ru'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Telegram Session Setup</title><style>body{font-family:system-ui,-apple-system,sans-serif;max-width:680px;margin:50px auto;padding:0 20px}input,button{font-size:16px;padding:12px;margin:6px 0;width:100%;box-sizing:border-box}button{cursor:pointer}.card{border:1px solid #ddd;border-radius:14px;padding:20px;margin:16px 0}.ok{background:#eefbef}.err{background:#fff1f1}pre{white-space:pre-wrap;word-break:break-all}.small{color:#666;font-size:14px}</style></head><body><h1>Telegram Session Setup</h1>{body}</body></html>"""

def page(body, status=200):
    return web.Response(text=PAGE.format(body=body), content_type="text/html", status=status)

def esc(x):
    return html.escape(str(x or ""))

async def ensure_client():
    if not client.is_connected():
        await client.connect()

async def index(request):
    return page("""<div class='card'><p>Введи SETUP_KEY и номер Telegram.</p><form method='post' action='/phone'><input name='key' type='password' placeholder='SETUP_KEY' required><input name='phone' placeholder='+31612345678' required><button type='submit'>Отправить код</button></form></div><p class='small'>После получения SESSION_STRING выключи SETUP_MODE и удали SETUP_KEY.</p>""")

async def phone(request):
    data = await request.post()
    if data.get('key', '') != SETUP_KEY:
        return page("<div class='card err'>Неверный SETUP_KEY.</div>", 403)
    phone = data.get('phone', '').strip()
    await ensure_client()
    try:
        sent = await client.send_code_request(phone)
        state['phone'] = phone
        state['phone_code_hash'] = sent.phone_code_hash
    except Exception as e:
        return page(f"<div class='card err'>Ошибка: {esc(e)}</div>")
    return page(f"""<div class='card ok'><p>Код отправлен на <b>{esc(phone)}</b>.</p><form method='post' action='/code'><input name='key' type='password' placeholder='SETUP_KEY' required><input name='code' placeholder='Код из Telegram' required><button type='submit'>Подтвердить</button></form></div>""")

async def code(request):
    data = await request.post()
    if data.get('key', '') != SETUP_KEY:
        return page("<div class='card err'>Неверный SETUP_KEY.</div>", 403)
    if not state['phone'] or not state['phone_code_hash']:
        return page("<div class='card err'>Сначала запроси код заново.</div>")
    await ensure_client()
    try:
        await client.sign_in(
            phone=state['phone'],
            code=data.get('code', '').replace(' ', ''),
            phone_code_hash=state['phone_code_hash'],
        )
    except SessionPasswordNeededError:
        return page("""<div class='card'><p>Включена 2FA. Введи облачный пароль Telegram.</p><form method='post' action='/password'><input name='key' type='password' placeholder='SETUP_KEY' required><input name='password' type='password' placeholder='Пароль 2FA' required><button type='submit'>Войти</button></form></div>""")
    except Exception as e:
        return page(f"<div class='card err'>Ошибка входа: {esc(e)}</div>")
    return await show_session()

async def password(request):
    data = await request.post()
    if data.get('key', '') != SETUP_KEY:
        return page("<div class='card err'>Неверный SETUP_KEY.</div>", 403)
    await ensure_client()
    try:
        await client.sign_in(password=data.get('password', ''))
    except Exception as e:
        return page(f"<div class='card err'>Ошибка 2FA: {esc(e)}</div>")
    return await show_session()

async def show_session():
    if not await client.is_user_authorized():
        return page("<div class='card err'>Авторизация не завершена.</div>")
    session_string = client.session.save()
    return page(f"""<div class='card ok'><h2>Готово ✅</h2><p>Скопируй всю строку ниже в Railway Variable <b>SESSION_STRING</b>:</p><pre>{esc(session_string)}</pre><p>Затем поставь <code>SETUP_MODE=false</code>, удали <code>SETUP_KEY</code> и сделай redeploy.</p></div>""")

app = web.Application()
app.add_routes([
    web.get('/', index),
    web.post('/phone', phone),
    web.post('/code', code),
    web.post('/password', password),
])

if __name__ == '__main__':
    web.run_app(app, host='0.0.0.0', port=PORT)
