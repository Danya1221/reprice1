"""Price parsing/rendering without Telegram or network side effects."""
import hashlib
import html
import re
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

SPACE = re.compile(r"\s+")
FLAGS = re.compile(r"[\U0001F1E6-\U0001F1FF]{2}")
CLOSED = re.compile(r"(?:мы\s+)?(?:сейчас\s+|временно\s+)?закрыты|продажи\s+закрыты|"
                    r"рабочий\s+день\s+окончен|магазин\s+закрыт|не\s+работаем|"
                    r"при[её]м\s+заказов\s+(?:заверш[её]н|закрыт)", re.I)
UNAVAILABLE = re.compile(r"нет\s+в\s+наличии|нет\s+на\s+складе|закончились|продано|❌", re.I)
WHOLESALE = re.compile(r"\b(?:от|from)\s*\d+\s*(?:шт|штук|pcs)\b", re.I)
PRICE = re.compile(
    r"(?P<prefix>[$€₽])?\s*"
    r"(?P<amount>(?:\d{1,3}(?:[ ,.]\d{3})+|\d{3,7})(?:[.,]\d{1,2})?)"
    r"\s*(?P<currency>₽|руб\.?|р\.|RUB|USD|\$|EUR|€)?"
    r"\s*(?P<flags>(?:[\U0001F1E6-\U0001F1FF]{2}\s*)*)[✅🔥‼️!]*$",
    re.I,
)
ACCESSORY = re.compile(r"акс(?:ессуар|ис)|чехол|стекло|кабел[ья]|кабель|адаптер|зарядк|"
                       r"ремеш|бампер|case\b|charger\b|cable\b", re.I)
ASIS = re.compile(r"\bAS[\s-]?IS\b|\bASIS\b|асис", re.I)
CPO = re.compile(r"\bCPO\b", re.I)
INACTIVE = re.compile(r"\bне[\s-]*актив\w*|\binactive\b|not[\s-]*activated|не[\s-]*активирован\w*", re.I)
ACTIVE = re.compile(r"\bактив\w*|\bactive\b|\bactivated\b|pre[\s-]*activated|предактив\w*", re.I)
IPHONE = re.compile(r"\b(?:iphone|айфон)\s*:?\s*(\d{1,2}\s*(?:e\b|pro\s*max\b|pro\b|"
                    r"plus\b|mini\b|air\b)?|air\b|se(?:\s*\d)?)", re.I)
SHORT_IPHONE = re.compile(r"^(\d{1,2}(?:e)?(?:\s+(?:pro\s+max|pro|plus|mini))?)\s+(?=\d{2,4}\s*(?:gb|tb|гб|тб)?\b)", re.I)
BRANDS = (
    ("Ray-Ban Meta", r"ray[\s-]?ban|wayfarer|skyler"),
    ("LEGO", r"\blego\b|лего"),
    ("Dyson", r"\bdyson\b|дайсон"),
    ("Canon", r"\bcanon\b|кэнон|канон"),
    ("DJI / Insta360", r"\bdji\b|\binsta\s*360\b"),
    ("Kodak / Fujifilm", r"\bkodak\b|\bfujifilm\b"),
    ("Bowers & Wilkins", r"bowers|b&w|\bpx[78]\b"),
    ("Harman Kardon / Bose", r"harman\s*kardon|\bbose\b|\baura\s+studio\b|\bonyx\b|soundsticks"),
    ("Google", r"\bgoogle\b|\bpixel\b|\bfitbit\b"),
    ("Samsung", r"\bsamsung\b|\bgalaxy\b|самсунг"),
    ("Apple", r"\bapple\b|\biphone\b|айфон|\bipad\b|\bmacbook\b|\bairpods\b|\bimac\b|apple\s*watch"),
    ("Sony", r"\bsony\b"),
    ("Xiaomi", r"\bxiaomi\b|\bredmi\b|\bpoco\b"),
    ("Huawei", r"\bhuawei\b"),
    ("Honor", r"\bhonor\b"),
    ("JBL", r"\bjbl\b"),
    ("Nintendo", r"\bnintendo\b|\bswitch\b"),
    ("PlayStation", r"playstation|\bps5\b"),
    ("Xbox", r"\bxbox\b"),
)
SIM_LABELS = {
    "sim": "SIM",
    "hybrid": "SIM + eSIM",
    "esim": "eSIM",
    "dual": "2 SIM",
    "unknown": "",
}
# Unlabelled items precede named SIM sections so they cannot appear under a
# misleading eSIM / physical SIM heading.
SIM_ORDER = {"unknown": -1, "hybrid": 0, "dual": 1, "esim": 2, "sim": 3}
CONDITION_LABELS = {"inactive": "Неактив", "active": "Актив", "unknown": "Статус не указан"}
CONDITION_ORDER = {"inactive": 0, "active": 1, "unknown": 2}

# Apple: iPhone 17-family devices bought in these markets are eSIM-only.
# This fallback is used only when the supplier did not explicitly specify SIM.
ESIM_ONLY_17_FLAGS = {
    "🇺🇸", "🇻🇮", "🇬🇺", "🇨🇦", "🇯🇵", "🇦🇪",
    "🇸🇦", "🇶🇦", "🇰🇼", "🇴🇲", "🇧🇭", "🇲🇽",
}


def clean(text):
    return SPACE.sub(" ", unicodedata.normalize("NFKC", text).replace("\u200b", "")).strip()


def strip_decoration(text):
    return clean(text).strip(" \t•▫▪◽◾🔹🔸📱📦🎧⌚🔥✅*_-—–")


def currency_name(value, default="RUB"):
    return {"₽": "RUB", "РУБ": "RUB", "РУБ.": "RUB", "Р.": "RUB",
            "$": "USD", "€": "EUR"}.get(value.upper(), value.upper()) if value else default


def amount_value(value):
    value = value.replace(" ", "")
    fraction = re.search(r"([.,]\d{1,2})$", value)
    if fraction:
        whole = value[:fraction.start()].replace(",", "").replace(".", "")
        return Decimal(whole + "." + fraction[0][1:])
    return Decimal(value.replace(",", "").replace(".", ""))


def split_price(line, default_currency):
    original = strip_decoration(line)
    if UNAVAILABLE.search(original):
        return None
    wholesale = WHOLESALE.search(original)
    if wholesale:
        original = original[:wholesale.start()].rstrip(" ,;/—-")
    match = PRICE.search(original)
    if not match:
        return None
    title = original[:match.start()].strip()
    separated = bool(re.search(r"[-—–=:]\s*$", title))
    amount = amount_value(match["amount"])
    if not separated and not (match["currency"] or match["prefix"]) and amount < 1000:
        return None
    title = title.rstrip(" -—–=:|")
    if not title or not re.search(r"[A-Za-zА-Яа-яЁё]", title):
        return None
    if re.search(r"https?://|@\w+|доставка|гарантия|телефон:|заказ(?:ы|ов)?\s*:|"
                 r"итого|скидка|минимальн|курс\s|обновл[её]н", title, re.I):
        return None
    if amount <= 0 or amount > 10_000_000:
        return None
    flags = match["flags"].strip()
    if flags:
        title += " " + flags
    currency = currency_name(match["currency"] or match["prefix"], default_currency)
    return title, amount, currency


def sim_type(text):
    text = clean(text).lower()
    text = re.sub(r"\be[\s-]+sim\b", "esim", text)
    if re.search(
        r"\b(?:nano\s*)?sim\s*(?:\+|/|&|and|и)\s*esim\b|"
        r"\besim\s*(?:\+|/|&|and|и)\s*(?:nano\s*)?sim\b",
        text,
    ):
        return "hybrid"
    if re.search(r"\b(?:2|dual)\s*(?:nano\s*)?sim\b|две\s*sim|\b2x\s*sim\b", text):
        return "dual"
    if re.search(r"\besim\b|есим", text):
        return "esim"
    if re.search(r"\b(?:1\s*)?(?:nano\s*)?sim\b|physical\s*sim|физическ\w*\s*sim", text):
        return "sim"
    return "unknown"


def iphone_model(title):
    match = IPHONE.search(title)
    if not match:
        return ""
    suffix = clean(match[1])
    suffix = re.sub(r"\s+e\b", "e", suffix, flags=re.I)
    words = [x if re.fullmatch(r"\d+e?", x, re.I) else x.title() for x in suffix.split()]
    return "iPhone " + " ".join(words)


def iphone17_family(model):
    model = clean(model).casefold()
    return model == "iphone air" or model.startswith("iphone 17")


def infer_iphone17_sim(title, model, current="unknown"):
    """Infer the iPhone 17 SIM layout from Apple regional rules and price flags."""
    if current != "unknown" or not iphone17_family(model):
        return current
    lower_model = clean(model).casefold()
    if lower_model == "iphone air":
        return "esim"
    flags = set(FLAGS.findall(title))
    if not flags:
        return current
    if flags & ESIM_ONLY_17_FLAGS:
        return "esim"
    # Mainland-China iPhone 17 / Pro / Pro Max use two physical nano-SIMs.
    # iPhone 17e is the exception and supports physical nano-SIM/eSIM.
    if "🇨🇳" in flags and not lower_model.startswith("iphone 17e"):
        return "dual"
    return "hybrid"


def brand_of(text):
    for name, pattern in BRANDS:
        if re.search(pattern, text, re.I):
            return name
    return ""


def normal_title(title, context=""):
    title = strip_decoration(title)
    title = re.sub(r"\bSM-[A-Za-z0-9/]+\b", "", title, flags=re.I)
    title = re.sub(r"\bайфон\b", "iPhone", title, flags=re.I)
    plain = FLAGS.sub("", title).strip()
    if SHORT_IPHONE.match(plain):
        pos = title.find(plain)
        title = title[:pos] + "iPhone " + title[pos:]
    elif context and not brand_of(title):
        title = context + " " + title
    title = re.sub(r"\b(\d+)\s*(?:GB|ГБ)\b", r"\1GB", title, flags=re.I)
    title = re.sub(r"\b(\d+)\s*(?:TB|ТБ)\b", r"\1TB", title, flags=re.I)
    if re.search(r"ray[\s-]?ban|wayfarer|skyler", title, re.I):
        title = re.sub(r"\bS\s*50\b", "M", title, flags=re.I)
        title = re.sub(r"\bS\s*53\b", "L", title, flags=re.I)
        if not re.search(r"ray[\s-]?ban", title, re.I):
            title = "Ray-Ban Meta " + title
    return clean(title)


def special_block(text):
    if ASIS.search(text):
        return "ASIS"
    if CPO.search(text):
        return "CPO"
    return ""


def activation_state(text):
    if INACTIVE.search(text):
        return "inactive"
    if ACTIVE.search(text):
        return "active"
    return "unknown"


def identity(title, sim, currency):
    flags = "".join(sorted(FLAGS.findall(title)))
    body = FLAGS.sub("", clean(title).lower())
    body = re.sub(r"(\d+)\s*(?:gb|гб)\b", r"\1", body)
    body = re.sub(r"[^\w+/]+", " ", body)
    return clean(body) + "|" + flags + "|" + sim + "|" + currency


@dataclass(frozen=True)
class Item:
    title: str
    price: Decimal
    currency: str
    block: str
    sim: str = "unknown"
    accessory: bool = False

    @property
    def key(self):
        return identity(self.title, self.sim, self.currency)

    def to_dict(self):
        return dict(title=self.title, price=str(self.price), currency=self.currency,
                    block=self.block, sim=self.sim, accessory=self.accessory)

    @classmethod
    def from_dict(cls, value):
        return cls(**{**value, "price": Decimal(value["price"])})


@dataclass
class ParseResult:
    items: list
    rejected: list
    closed: bool = False


def parse_documents(documents, default_currency="RUB"):
    items = OrderedDict()
    rejected = []
    context = ""
    section = ""
    section_sim = "unknown"
    closed = False
    for document in documents:
        for raw_line in document.splitlines():
            line = strip_decoration(raw_line)
            if not line:
                continue
            if CLOSED.search(line):
                closed = True
                continue
            price = split_price(line, default_currency)
            if price is None:
                model = iphone_model(line)
                brand = brand_of(line)
                special = special_block(line)
                is_header = len(line) <= 80 and not re.search(r"https?://|@\w+|[-—=]\s*\d{3}", line)
                if is_header and (model or brand or ACCESSORY.search(line) or special):
                    context = model or (brand if brand != "Apple" else line.strip(":"))
                    if ACCESSORY.search(line):
                        section = "Аксессуары"
                    elif special:
                        section = special
                    else:
                        section = model or brand
                    section_sim = sim_type(line)
                elif is_header and re.fullmatch(r"(?:2\s*)?(?:e[\s-]?)?sim(?:\s*[+/]\s*(?:e[\s-]?)?sim)?", line, re.I):
                    section_sim = sim_type(line)
                elif re.search(r"\d", line) and not re.search(r"https?://|@\w+|^\+?\d[\d ()-]{8,}$", line):
                    rejected.append(line)
                continue
            title, amount, currency = price
            title = normal_title(title, context)
            own_brand = brand_of(title)
            accessory = bool(ACCESSORY.search(title)) or (section == "Аксессуары" and not own_brand)
            model = iphone_model(title)
            special = special_block(title)
            if not special and section in {"ASIS", "CPO"}:
                special = section
            block = "Аксессуары" if accessory else (special or model or own_brand or section)
            if not block:
                rejected.append(line)
                continue
            sim = sim_type(title)
            if sim == "unknown":
                sim = section_sim if model else "unknown"
            if model:
                sim = infer_iphone17_sim(title, model, sim)
            item = Item(title, amount, currency, block, sim, accessory)
            items[item.key] = item
    return ParseResult(list(items.values()), rejected, closed and not items)


def matches_any(text, patterns):
    return any(clean(p).casefold() in clean(text).casefold() for p in patterns)


def select_items(items, settings, overrides=None):
    overrides = overrides or {}
    sim_filter = overrides.get("sim_filter", settings.sim_filter)
    disabled = set(overrides.get("disabled_blocks", []))
    result = []
    for item in items:
        if item.block in disabled:
            continue
        if settings.include_blocks and not matches_any(item.block, settings.include_blocks):
            continue
        if matches_any(item.block, settings.exclude_blocks):
            continue
        if settings.include_items and not matches_any(item.title, settings.include_items):
            continue
        if matches_any(item.title, settings.exclude_items):
            continue
        if item.accessory and not settings.allow_accessories:
            continue
        if iphone_model(item.title) and sim_filter != "all":
            if sim_filter == "sim" and item.sim not in {"sim", "dual", "hybrid"}:
                continue
            if sim_filter == "esim" and item.sim not in {"esim", "hybrid"}:
                continue
            if sim_filter not in {"sim", "esim"} and item.sim != sim_filter:
                continue
        result.append(item)
    return result


def merge_sources(sources):
    """First source wins, but differing country/SIM/condition/storage stay distinct."""
    merged = OrderedDict()
    for items in sources:
        for item in items:
            merged.setdefault(item.key, item)
    return list(merged.values())


def marked_price(item, settings, overrides=None):
    overrides = overrides or {}
    fixed = Decimal(str(overrides.get("markup", settings.markup)))
    percent = Decimal(str(overrides.get("markup_percent", settings.markup_percent)))
    value = item.price * (1 + percent / 100) + fixed
    if value <= 0:
        raise ValueError("После наценки получилась нулевая или отрицательная цена")
    return value.quantize(Decimal("1") if item.currency == "RUB" else Decimal("0.01"), rounding=ROUND_HALF_UP)


def price_text(value, currency):
    places = 0 if currency == "RUB" or value == value.to_integral() else 2
    number = f"{value:,.{places}f}".replace(",", " ")
    if currency == "RUB":
        return number
    return number + " " + {"USD": "$", "EUR": "€"}[currency]


def units(text):
    return len(text.encode("utf-16-le")) // 2


def item_sort(item):
    size = 0 if re.search(r"\bM\b", item.title) else 1 if re.search(r"\bL\b", item.title) else 2
    return size, clean(item.title).casefold()


def display_title(item):
    """Copyable model attributes, with the SIM kind only when it is known."""
    title = item.title
    if iphone_model(item.title):
        explicit = sim_type(item.title)
        if explicit == "unknown" and SIM_LABELS.get(item.sim):
            title += " · " + SIM_LABELS[item.sim]
    return title


def render_blocks(items, settings, overrides=None, closed=False):
    """Return stable page keys and HTML; each full product+price row is copyable."""
    overrides = overrides or {}
    groups = OrderedDict()
    for item in items:
        groups.setdefault(item.block, []).append(item)
    order = overrides.get("block_order", [])
    names = sorted(groups, key=lambda x: (order.index(x) if x in order else len(order), x.casefold()))
    pages = OrderedDict()
    for block in names:
        header = "<b>— " + html.escape(block) + " —</b>"
        if closed:
            chunks = [["Продажи закрыты"]]
        else:
            block_items = groups[block]
            has_activation = any(activation_state(item.title) != "unknown" for item in block_items)
            lines = []
            statuses = sorted(
                {activation_state(item.title) for item in block_items},
                key=lambda status: CONDITION_ORDER[status],
            )
            for status in statuses:
                status_items = [item for item in block_items if activation_state(item.title) == status]
                if has_activation:
                    lines.append("<b>— " + CONDITION_LABELS[status] + " —</b>")
                last_sim = None
                for item in sorted(status_items, key=lambda i: (SIM_ORDER.get(i.sim, 99), item_sort(i))):
                    if iphone_model(item.title) and item.sim != last_sim:
                        label = SIM_LABELS.get(item.sim)
                        if label:
                            lines.append("<b>— " + label + " —</b>")
                        last_sim = item.sim
                    row = display_title(item) + " — " + price_text(marked_price(item, settings, overrides), item.currency)
                    line = "<code>" + html.escape(row) + "</code>"
                    if units(line) > 3000:
                        raise ValueError("Слишком длинное наименование товара")
                    lines.append(line)
            chunks, chunk, size = [], [], units(header) + 50
            for line in lines:
                if size + units(line) + 1 > 3800 and chunk:
                    chunks.append(chunk)
                    chunk, size = [], units(header) + 50
                chunk.append(line)
                size += units(line) + 1
            if chunk:
                chunks.append(chunk)
        for index, chunk in enumerate(chunks):
            key = hashlib.sha256(block.encode()).hexdigest()[:16] + ":" + str(index)
            part = f"\nЧасть {index + 1}" if index else ""
            pages[key] = header + part + "\n\n" + "\n".join(chunk)
    return pages
