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
    r"(?P<amount>(?:\d{1,3}(?:[ ,.]\d{3})+|\d{1,7})(?:[.,]\d{1,2})?)"
    r"\s*(?P<currency>₽|руб\.?|р\.|RUB|USD|\$|EUR|€)?"
    r"\s*(?P<flags>(?:[\U0001F1E6-\U0001F1FF]{2}\s*)*)[✅🔥‼️!]*$",
    re.I,
)
ACCESSORY = re.compile(r"акс(?:ессуар|ис)|чехол|стекло|кабел[ья]|кабель|адаптер|зарядк|"
                       r"ремеш|бампер|case\b|charger\b|cable\b", re.I)
ASIS = re.compile(r"\bAS[\s-]?IS\b|\bASIS\b|асис", re.I)
CPO = re.compile(r"\bCPO\b", re.I)
PACKAGING_NOTE = r"ориг(?:инальная)?\.?\s*упак(?:овка)?\.?"
ORIGINAL_PACKAGING = re.compile(
    r"\(\s*" + PACKAGING_NOTE + r"(?:\s*iphone)?\s*\)|\b" + PACKAGING_NOTE + r"(?!\w)", re.I,
)
INACTIVE = re.compile(r"\bне[\s-]*актив\w*|\binactive\b|not[\s-]*activated|не[\s-]*активирован\w*", re.I)
ACTIVE = re.compile(r"\bактив\w*|\bactive\b|\bactivated\b|pre[\s-]*activated|предактив\w*", re.I)
IPHONE = re.compile(r"\b(?:iphone|айфон)\s*:?\s*(\d{1,2}\s*(?:e\b|pro\s*max\b|pro\b|"
                    r"plus\b|mini\b|air\b)?|air\b|se(?:\s*\d)?)", re.I)
SHORT_IPHONE = re.compile(r"^(\d{1,2}(?:e)?(?:\s+(?:pro\s+max|pro|plus|mini|air))?)\s+(?=\d{1,4}\s*(?:gb|tb|гб|тб)?\b)", re.I)
BRANDS = (
    ("Ray-Ban Meta", r"ray[\s-]?ban|wayfarer|skyler"),
    ("Oura Ring", r"\boura(?:\s+ring)?\b"),
    ("LEGO", r"\blego\b|лего"),
    ("Dyson", r"\bdyson\b|дайсон"),
    ("Canon", r"\bcanon\b|кэнон|канон"),
    ("Rode", r"\br(?:o|ø)de\b"),
    ("COROS", r"\bcoros\b"),
    ("DJI / Insta360", r"\bdji\b|\binsta\s*360\b"),
    ("Kodak / Fujifilm", r"\bkodak\b|\bfujifilm\b"),
    ("Bowers & Wilkins", r"bowers|b&w|\bpx[78]\b"),
    ("Harman Kardon / Bose", r"harman\s*kardon|\bbose\b|\baura\s+studio\b|\bonyx\b|soundsticks"),
    ("Google", r"\bgoogle\b|\bpixel\b|\bfitbit\b"),
    ("Samsung", r"\bsamsung\b|\bgalaxy\b|самсунг"),
    ("Apple", r"\bapple\b|\biphone\b|айфон|\bipad\b|\bmacbook\b|\bairpods\b|\bimac\b|apple\s*watch"),
    ("Sony", r"\bsony\b"),
    ("Xiaomi", r"\bxiaomi\b|\bredmi\b|\bpoco\b|^\s*(?:redmi\s+)?note\s+\d{1,2}[a-z]*\b"),
    ("Huawei", r"\bhuawei\b"),
    ("Honor", r"\bhonor\b"),
    ("Realme", r"\brealme\b|реалми"),
    ("Tecno", r"\btecno\b|текно"),
    ("Infinix", r"\binfinix\b"),
    ("OnePlus", r"\bone\s*plus\b"),
    ("OPPO", r"\boppo\b"),
    ("Vivo", r"\bvivo\b|\biqoo\b"),
    ("Nothing", r"\bnothing\b|\bcmf\b"),
    ("Nubia", r"\bnubia\b|red\s*magic"),
    ("Garmin", r"\bgarmin\b"),
    ("GoPro", r"\bgopro\b"),
    ("Marshall", r"\bmarshall\b"),
    ("Anker", r"\banker\b|\bsoundcore\b"),
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
CONDITION_LABELS = {"inactive": "Не активированное", "active": "Актив", "unknown": "Не активированное"}
CONDITION_ORDER = {"inactive": 0, "active": 1, "unknown": 2}


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
    if UNAVAILABLE.search(original) or IPHONE.fullmatch(original):
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
        r"\b(?:1\s*)?(?:nano\s*)?sim\s*(?:\+|/|&|and|и)\s*esim\b|"
        r"\besim\s*(?:\+|/|&|and|и)\s*(?:1\s*)?(?:nano\s*)?sim\b",
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
    model = " ".join(words)
    if model.casefold() == "17 air":
        return "iPhone Air"
    return "iPhone " + model



def brand_of(text):
    for name, pattern in BRANDS:
        if re.search(pattern, text, re.I):
            return name
    return ""


def apple_watch_block(title):
    if not re.search(r"\b(?:apple\s*)?watch\b", title, re.I):
        return ""
    ultra = re.search(r"\bultra(?:\s*(\d{1,2}))?\b", title, re.I)
    if ultra:
        return "Apple Watch Ultra" + (" " + ultra.group(1) if ultra.group(1) else "")
    series = re.search(r"\b(?:series|s)\s*(\d{1,2})\b", title, re.I)
    if series:
        return "Apple Watch Series " + series.group(1)
    se = re.search(r"\bse(?:\s*(\d{1,2}))?\b", title, re.I)
    if se:
        return "Apple Watch SE" + (" " + se.group(1) if se.group(1) else "")
    return "Apple Watch"


def samsung_block(title):
    if not re.search(r"\bsamsung\b|\bgalaxy\b|самсунг", title, re.I):
        return ""

    # Galaxy Tab S is a tablet family, never a Galaxy S phone.
    if re.search(r"\b(?:galaxy\s*)?tab\s*s\s*\d*", title, re.I):
        return "Samsung Tab S"

    # Keep foldables separate when they are present.
    if re.search(r"\b(?:galaxy\s*)?(?:z\s*)?(?:fold|flip)\b", title, re.I):
        return "Samsung Fold / Flip"

    # The requested phone layout is deliberately balanced into two messages:
    # A-series together with S25, then the complete S26 family.
    if re.search(r"\b(?:galaxy\s*)?a\s*\d{1,3}[a-z]*\b|\bsamsung\s+a\s*\d{1,3}[a-z]*\b", title, re.I):
        return "Samsung A + S25"
    if re.search(r"\b(?:galaxy\s*)?s\s*25(?:\s*(?:\+|plus|ultra|fe))?\b|\bsamsung\s+s\s*25", title, re.I):
        return "Samsung A + S25"
    if re.search(r"\b(?:galaxy\s*)?s\s*26(?:\s*(?:\+|plus|ultra|fe))?\b|\bsamsung\s+s\s*26", title, re.I):
        return "Samsung S26"

    # Header-only sections from supplier menus.
    if re.search(r"\bgalaxy\s+a\b|\bsamsung\s+galaxy\s+a\b", title, re.I):
        return "Samsung A + S25"
    if re.search(r"\bgalaxy\s+s\s*25\b", title, re.I):
        return "Samsung A + S25"
    if re.search(r"\bgalaxy\s+s\s*26\b", title, re.I):
        return "Samsung S26"
    if re.search(r"\b(?:galaxy\s*)?tab\s*s\b", title, re.I):
        return "Samsung Tab S"
    return "Samsung"


def product_block(title):
    watch = apple_watch_block(title)
    if watch:
        return watch
    samsung = samsung_block(title)
    if samsung:
        return samsung
    if re.search(r"\b(?:MacBook|iMac)\b", title, re.I):
        return "MacBook / iMac"
    for label in ("AirPods", "iPad", "Mac mini", "Mac Studio", "Apple TV", "AirTag"):
        if re.search(r"\b" + re.escape(label) + r"\b", title, re.I):
            return label
    return brand_of(title)


def ordered_blocks(blocks, preferred=()):
    defaults = ["iPhone", "AirPods", "Apple Watch", "iPad", "MacBook / iMac", "Apple", "Ray-Ban Meta",
                "Samsung", "Honor", "Realme", "Huawei", "Tecno", "Xiaomi", "Google", "COROS", "Rode", "Dyson",
                "Oura Ring", "CPO", "ASIS", "Аксессуары", "Товары"]
    def order_key(name):
        if name in preferred:
            return (-1, preferred.index(name), (), "")
        model = iphone_model(name)
        family = "iPhone" if model else ("Apple Watch" if name.startswith("Apple Watch") else ("Samsung" if name.startswith("Samsung") else name))
        rank = defaults.index(family) if family in defaults else len(defaults) - 4
        samsung_rank = {"Samsung A + S25": 0, "Samsung S26": 1, "Samsung Tab S": 2, "Samsung Fold / Flip": 3, "Samsung": 4}.get(name, 0) if family == "Samsung" else 0
        numbered = model if model else (name if family == "Apple Watch" else "")
        number = re.search(r"\d+", numbered)
        return (rank, samsung_rank, -int(number[0]) if number else 0, tuple(int(x) if x.isdigit() else x for x in re.split(r"(\d+)", name.casefold())), name)
    return sorted(set(blocks), key=order_key)


def normal_title(title, context=""):
    title = strip_decoration(title)
    title = re.sub(r"\bSM-[A-Za-z0-9/]+\b", "", title, flags=re.I)
    title = re.sub(r"\bайфон\b", "iPhone", title, flags=re.I)
    plain = FLAGS.sub("", title).strip()
    if SHORT_IPHONE.match(plain):
        # A flag in the middle means the flag-free text is not a substring.
        # Insert before the model, never at find()'s -1 position near the end.
        pos = re.search(r"\d", title).start()
        title = title[:pos] + "iPhone " + title[pos:]
    elif context and not brand_of(title):
        # Samsung block labels are navigation names, not product-name prefixes.
        # Prefix bare A/S/Tab rows with the brand only, otherwise rows become e.g.
        # "Samsung A + S25 S25 ...".
        prefix = "Samsung" if context.startswith("Samsung") else context
        title = prefix + " " + title
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
        data = {**value, "price": Decimal(value["price"])}
        data["title"] = normal_title(data["title"])
        if data.get("block") not in {"ASIS", "CPO", "Аксессуары"}:
            known = iphone_model(data["title"]) or product_block(data["title"])
            if known:
                data["block"] = known
        return cls(**data)


@dataclass
class ParseResult:
    items: list
    rejected: list
    closed: bool = False


def price_lines(documents):
    """Join product titles with a following retail price, including across messages."""
    pending = None
    for document in documents:
        for raw in document.splitlines():
            line = strip_decoration(raw)
            if not line:
                continue
            tier = WHOLESALE.search(line)
            if tier and not FLAGS.sub("", line[:tier.start()]).strip():
                if not re.search(r"\b(?:от|from)\s*1\s", tier[0], re.I):
                    continue
                line = line[:tier.start()] + line[tier.end():]
            else:
                line = re.sub(r"\b(?:от|from)\s*1\s*(?:шт|штук|pcs)\b", "", line, flags=re.I)
            candidate = FLAGS.sub("", line).strip(" —-–=:•")
            if pending and PRICE.fullmatch(candidate) and not UNAVAILABLE.search(line):
                flags = " ".join(FLAGS.findall(line))
                yield pending + (" " + flags if flags and flags not in pending else "") + " — " + candidate
                pending = None
            else:
                if pending is not None:
                    yield pending
                pending = line
    if pending is not None:
        yield pending


def parse_documents(documents, default_currency="RUB"):
    items = OrderedDict()
    rejected = []
    context = ""
    section = ""
    section_sim = "unknown"
    closed = False
    for raw_line in price_lines(documents):
        line = strip_decoration(raw_line)
        if not line:
            continue
        if CLOSED.search(line):
            closed = True
            continue
        price = split_price(line, default_currency)
        if price is None:
            model = iphone_model(line)
            brand = product_block(line)
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
            elif is_header and re.fullmatch(r"(?:[12]\s*)?(?:e[\s-]?)?sim(?:\s*[+/&]\s*(?:[12]\s*)?(?:e[\s-]?)?sim)?", line, re.I):
                section_sim = sim_type(line)
            elif re.search(r"\d", line) and not re.search(r"https?://|@\w+|^\+?\d[\d ()-]{8,}$", line):
                rejected.append(line)
            continue
        title, amount, currency = price
        title = normal_title(title, context)
        own_brand = product_block(title)
        accessory = bool(ACCESSORY.search(title)) or (section == "Аксессуары" and not own_brand)
        model = iphone_model(title)
        special = special_block(title)
        if not special and section in {"ASIS", "CPO"}:
            special = section
        block = "Аксессуары" if accessory else (special or model or own_brand or section or "Товары")
        sim = sim_type(title)
        if sim == "unknown":
            sim = section_sim if model else "unknown"
        item = Item(title, amount, currency, block, sim, accessory)
        items[item.key] = item
    return ParseResult(list(items.values()), rejected, closed and not items)


def matches_any(text, patterns):
    return any(clean(p).casefold() in clean(text).casefold() for p in patterns)


def select_items(items, settings, overrides=None):
    overrides = overrides or {}
    sim_filter = overrides.get("sim_filter", settings.sim_filter)
    disabled = set(overrides.get("disabled_blocks", []))
    include_blocks = overrides.get("include_blocks", settings.include_blocks)
    exclude_blocks = overrides.get("exclude_blocks", settings.exclude_blocks)
    include_items = overrides.get("include_items", settings.include_items)
    exclude_items = overrides.get("exclude_items", settings.exclude_items)
    allow_accessories = overrides.get("allow_accessories", settings.allow_accessories)
    result = []
    for item in items:
        if item.block in disabled:
            continue
        if include_blocks and not matches_any(item.block, include_blocks):
            continue
        if matches_any(item.block, exclude_blocks):
            continue
        if include_items and not matches_any(item.title, include_items):
            continue
        if matches_any(item.title, exclude_items):
            continue
        if item.accessory and not allow_accessories:
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
    # Apply at render time so previously saved prices lose the note as well.
    title = normal_title(ORIGINAL_PACKAGING.sub("", item.title))
    if iphone_model(title):
        explicit = sim_type(title)
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
    names = ordered_blocks(groups, order)
    pages = OrderedDict()
    for block in names:
        header = "<b>" + html.escape(block) + "</b>"
        if closed:
            chunks = [["Продажи закрыты"]]
        else:
            block_items = groups[block]
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
            pages[key] = header + "\n\n" + "\n".join(chunk)
    return pages
