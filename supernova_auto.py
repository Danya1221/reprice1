"""Open Supernova's Mini App and request its Excel export without logging auth data."""
import asyncio
import json
import re
from urllib.parse import urlsplit

from telethon import functions, types, utils


class SupernovaAutoError(RuntimeError):
    pass


DOWNLOAD_LABEL = re.compile(
    r"скачать\s+(?:прайс[\s\-–—]*лист\s*(?:\(\s*(?:excel|xlsx)\s*\)|(?:в\s+)?(?:excel|xlsx))"
    r"|(?:excel|xlsx)(?:\s+прайс[\s\-–—]*лист)?)", re.I,
)
PRICE_LABEL = re.compile(r"^\s*(?:🛒\s*)?прайс(?:\s*\d[\d\s]*)?\s*[›❯>→]?\s*$", re.I)

# Minimal Telegram mobile WebView bridge. It supplies viewport/theme events and
# read-only cloud storage; it never accepts popups, payments or permissions.
WEBVIEW_BRIDGE = r"""(() => {
  window.__supernovaBlockedEvent = '';
  function reply(name, value) {
    setTimeout(() => {
      if (window.Telegram && window.Telegram.WebView)
        window.Telegram.WebView.receiveEvent(name, value);
    }, 0);
  }
  window.TelegramWebviewProxy = {postEvent: function(type, data) {
    if (type === 'web_app_request_theme')
      reply('theme_changed', {theme_params: {bg_color:'#ffffff',text_color:'#000000',
        hint_color:'#999999',link_color:'#2481cc',button_color:'#2481cc',
        button_text_color:'#ffffff',secondary_bg_color:'#f1f1f1'}});
    else if (type === 'web_app_request_viewport')
      reply('viewport_changed', {height:window.innerHeight,width:window.innerWidth,
        is_state_stable:true,is_expanded:true});
    else if (type === 'web_app_request_safe_area')
      reply('safe_area_changed', {top:0,bottom:0,left:0,right:0});
    else if (type === 'web_app_request_content_safe_area')
      reply('content_safe_area_changed', {top:0,bottom:0,left:0,right:0});
    else if (type === 'web_app_invoke_custom_method') {
      let request;
      try { request = JSON.parse(data); } catch (_) { return; }
      if (!request || typeof request.req_id !== 'string') return;
      if (request.method !== 'getStorageValues' && request.method !== 'getStorageKeys') {
        window.__supernovaBlockedEvent = type;
        reply('custom_method_invoked', {req_id: request.req_id, error: 'METHOD_UNSUPPORTED'});
        return;
      }
      window.__supernovaReadStorage(request).then(
        result => reply('custom_method_invoked', Object.assign({req_id: request.req_id}, result)),
        () => reply('custom_method_invoked', {req_id: request.req_id, error: 'REQUEST_FAILED'})
      );
    }
    else if (/^(web_app_open_popup|web_app_request_phone|web_app_open_invoice|web_app_open_link|web_app_open_tg_link|web_app_data_send)$/.test(type))
      window.__supernovaBlockedEvent = type;
  }};
})();"""


async def supernova_webview(client, bot):
    """Ask Telegram for a fresh, account-authorized Mini App URL in memory."""
    if not getattr(bot, "bot", False):
        raise SupernovaAutoError("Supernova не является Telegram-ботом")
    peer = utils.get_input_peer(bot)
    bot_user = utils.get_input_user(bot)
    full = await client(functions.users.GetFullUserRequest(id=bot_user))
    info = getattr(getattr(full, "full_user", None), "bot_info", None)
    menu = getattr(info, "menu_button", None)
    if isinstance(menu, types.BotMenuButton):
        if urlsplit(menu.url).scheme != "https":
            raise SupernovaAutoError("Адрес Mini App должен использовать HTTPS")
        request = functions.messages.RequestWebViewRequest(
            peer=peer, bot=bot_user, platform="android", from_bot_menu=True, url=menu.url,
        )
    elif getattr(bot, "bot_has_main_app", False):
        request = functions.messages.RequestMainWebViewRequest(peer=peer, bot=bot_user, platform="android")
    else:
        # Some suppliers launch the app from a keyboard button rather than
        # the menu or main app. Only use an unambiguous Mini App button.
        messages = await client.get_messages(bot, limit=40)
        candidates = []
        for message in messages:
            if getattr(message, "out", False) or getattr(message, "sender_id", None) != bot.id:
                continue
            for row in (getattr(message, "buttons", None) or []):
                for button in row:
                    raw = getattr(button, "button", button)
                    if isinstance(raw, (types.KeyboardButtonWebView, types.KeyboardButtonSimpleWebView)):
                        if not any(existing.url == raw.url for existing in candidates):
                            candidates.append(raw)
        preferred = [button for button in candidates if re.search(r"прайс|прилож|открыть|open|каталог", button.text, re.I)]
        chosen = preferred[0] if len(preferred) == 1 else candidates[0] if len(candidates) == 1 else None
        if chosen is None:
            if candidates:
                raise SupernovaAutoError("В чате несколько кнопок Mini App: не удалось выбрать нужную")
            request = functions.messages.RequestMainWebViewRequest(peer=peer, bot=bot_user, platform="android")
        elif urlsplit(chosen.url).scheme != "https":
            raise SupernovaAutoError("Адрес Mini App должен использовать HTTPS")
        elif isinstance(chosen, types.KeyboardButtonSimpleWebView):
            request = functions.messages.RequestSimpleWebViewRequest(bot=bot_user, platform="android", url=chosen.url)
        else:
            request = functions.messages.RequestWebViewRequest(peer=peer, bot=bot_user, platform="android", url=chosen.url)
    result = await client(request)
    url = getattr(result, "url", "")
    if urlsplit(url).scheme != "https" or not urlsplit(url).hostname:
        raise SupernovaAutoError("Telegram не выдал корректный адрес Mini App")
    return url


async def read_cloud_storage(client, bot, source, request, allowed_origin):
    """Forward only read-only Telegram storage calls from the supplier's main frame."""
    try:
        frame = source["frame"]
        if frame != frame.page.main_frame:
            return {"error": "UNTRUSTED_FRAME"}
        url = urlsplit(frame.url)
        if (url.scheme, url.hostname, url.port) != allowed_origin:
            return {"error": "UNTRUSTED_ORIGIN"}
        if not isinstance(request, dict) or request.get("method") not in ("getStorageValues", "getStorageKeys"):
            return {"error": "METHOD_UNSUPPORTED"}
        params = request.get("params")
        if not isinstance(params, dict):
            return {"error": "INVALID_PARAMS"}
        if request["method"] == "getStorageKeys":
            if params:
                return {"error": "INVALID_PARAMS"}
        else:
            keys = params.get("keys")
            if set(params) != {"keys"} or not isinstance(keys, list) or len(keys) > 50 or not all(
                    isinstance(key, str) and len(key) <= 128 for key in keys):
                return {"error": "INVALID_PARAMS"}
        response = await asyncio.wait_for(client(functions.bots.InvokeWebViewCustomMethodRequest(
            bot=utils.get_input_user(bot), custom_method=request["method"],
            params=types.DataJSON(data=json.dumps(params, ensure_ascii=False)))), timeout=10)
        if len(response.data) > 65536:
            return {"error": "RESULT_TOO_LARGE"}
        return {"result": json.loads(response.data)}
    except Exception:
        return {"error": "REQUEST_FAILED"}


async def page_markers(page, diagnostics=None):
    """Collect fixed indicators only; never collect text, URLs or Telegram credentials."""
    try:
        markers = await page.evaluate("""() => {
          const text = document.body ? document.body.innerText : '';
          const known = ['web_app_open_popup', 'web_app_request_phone', 'web_app_open_invoice',
            'web_app_open_link', 'web_app_open_tg_link', 'web_app_invoke_custom_method', 'web_app_data_send'];
          const event = known.includes(window.__supernovaBlockedEvent) ? window.__supernovaBlockedEvent : '';
          return {length: text.trim().length, loading: /загруз|loading/i.test(text),
            error: /ошибк|error|не удалось/i.test(text), login: /авторизац|войти|вход|log.?in|sign.?in/i.test(text),
            frames: document.querySelectorAll('iframe').length, event};
        }""")
        diagnostics = diagnostics or {}
        event = markers.get("event") or "нет"
        return (f"Текст: {min(markers.get('length', 0), 9999)} симв.; "
                f"загрузка/ошибка/вход: {int(markers.get('loading', False))}/"
                f"{int(markers.get('error', False))}/{int(markers.get('login', False))}; "
                f"iframe: {markers.get('frames', 0)}; Telegram: {event}; "
                f"сеть/401-403/5xx/JS: {diagnostics.get('failed', 0)}/{diagnostics.get('auth', 0)}/"
                f"{diagnostics.get('server', 0)}/{diagnostics.get('js', 0)}")
    except Exception:
        return "Не удалось проверить страницу"


async def click_export_page(page, diagnostics=None):
    """Click only the known price navigation and Excel-export controls."""
    export = page.get_by_text(DOWNLOAD_LABEL).filter(visible=True).first
    try:
        await export.wait_for(state="visible", timeout=4000)
    except Exception:
        price = page.get_by_text(PRICE_LABEL).filter(visible=True).first
        try:
            await price.wait_for(state="visible", timeout=15000)
            await price.click(timeout=5000)
        except Exception as exc:
            raise SupernovaAutoError("Не найдена или не открылась кнопка «Прайс». " + await page_markers(page, diagnostics)) from exc
        try:
            await export.wait_for(state="visible", timeout=15000)
        except Exception as exc:
            raise SupernovaAutoError("Открыл «Прайс», но не нашёл кнопку Excel. " + await page_markers(page, diagnostics)) from exc
    if await page.evaluate("Boolean(window.__supernovaBlockedEvent)"):
        raise SupernovaAutoError("Mini App запросил дополнительное подтверждение в Telegram")
    try:
        await export.click(timeout=5000)
        await page.wait_for_timeout(1500)
    except Exception as exc:
        raise SupernovaAutoError("Не удалось нажать скачивание прайса") from exc
    if await page.evaluate("Boolean(window.__supernovaBlockedEvent)"):
        raise SupernovaAutoError("Mini App запросил дополнительное подтверждение в Telegram")


async def request_supernova_export(client, bot, wait_for_delivery, progress=None):
    """Use an ephemeral browser; no session URL, cookies or screenshots are saved."""
    progress = progress or (lambda value: None)
    progress("Получаю адрес Mini App через Telegram")
    url = await supernova_webview(client, bot)
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as playwright:
            progress("Запускаю браузер")
            browser = await playwright.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            try:
                context = await browser.new_context(
                    viewport={"width": 412, "height": 800}, device_scale_factor=1,
                    user_agent=("Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 "
                                "(KHTML, like Gecko) Chrome/140.0.0.0 Mobile Safari/537.36"),
                    accept_downloads=False,
                )
                allowed = urlsplit(url)
                allowed_origin = (allowed.scheme, allowed.hostname, allowed.port)
                async def read_storage(source, request):
                    return await read_cloud_storage(client, bot, source, request, allowed_origin)
                await context.expose_binding("__supernovaReadStorage", read_storage)
                await context.add_init_script(WEBVIEW_BRIDGE)
                page = await context.new_page()
                diagnostics = {"failed": 0, "auth": 0, "server": 0, "js": 0}

                def request_failed(_):
                    diagnostics["failed"] += 1

                def response_received(response):
                    if response.status in (401, 403):
                        diagnostics["auth"] += 1
                    elif 500 <= response.status < 600:
                        diagnostics["server"] += 1

                def page_error(_):
                    diagnostics["js"] += 1

                page.on("requestfailed", request_failed)
                page.on("response", response_received)
                page.on("pageerror", page_error)
                progress("Открываю Mini App")
                await page.goto(url, wait_until="domcontentloaded", timeout=25000)
                if urlsplit(page.url).scheme != "https":
                    raise SupernovaAutoError("Mini App открыл небезопасный адрес")
                progress("Ищу кнопки прайса и Excel")
                await click_export_page(page, diagnostics)
                progress("Кнопка нажата, жду Excel в чате")
                # Keep the page alive until the bot has actually sent the file;
                # closing it early can cancel the app's pending export request.
                await wait_for_delivery()
            finally:
                await browser.close()
    except asyncio.CancelledError:
        raise
    except SupernovaAutoError:
        raise
    except Exception as exc:
        # Browser exceptions may contain the full URL with Telegram initData.
        raise SupernovaAutoError("Ошибка браузера (" + type(exc).__name__ + ")") from exc
