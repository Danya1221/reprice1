"""Price parsing/rendering without Telegram or network side effects."""
import hashlib
import html
import re
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass, replace
from decimal import Decimal, ROUND_HALF_UP

SPACE = re.compile(r"\s+")
FLAGS = re.compile(r"[\U0001F1E6-\U0001F1FF]{2}")
APPLE_WARRANTY_NOTICE = re.compile(
    r"^\s*(?:⚠\ufe0f?\s*)*важная\s+информация\s+об\s+обновл[её]нной\s+"
    r"гарантийной\s+политик[ие]\s+apple(?:\s*[.:;—–-]+\s*|\s*$)", re.I,
)
CLOSED = re.compile(r"(?:мы\s+)?(?:сейчас\s+|временно\s+)?закрыты|продажи\s+закрыты|"
                    r"рабочий\s+день\s+окончен|магазин\s+закрыт|не\s+работаем|"
                    r"при[её]м\s+заказов\s+(?:заверш[её]н|закрыт)", re.I)
UNAVAILABLE = re.compile(r"нет\s+в\s+наличии|нет\s+на\s+складе|закончились|продано|❌", re.I)
MARKED_DOWN = re.compile(r"(?<![а-яё])уцен(?:к\w*|[её]н\w*)?\b", re.I)
WHOLESALE = re.compile(r"\b(?:от|from)\s*\d+\s*(?:шт|штук|pcs)\b", re.I)
PRICE = re.compile(
    r"(?P<prefix>[$€₽])?\s*"
    r"(?P<amount>(?:\d{1,3}(?:[ ,.]\d{3})+|\d{1,7})(?:[.,]\d{1,2})?)"
    r"\s*(?P<currency>₽|руб\.?|р\.|RUB|USD|\$|EUR|€)?"
    r"\s*(?P<flags>(?:[\U0001F1E6-\U0001F1FF]{2}\s*)*)[✅🔥‼️!]*$",
    re.I,
)
# Supplier texts often end a numeric price with a camera/availability marker.
# These are textual markers, not media; ignore them when extracting the price.
# A parenthesized note after 📷 (e.g. colour) is preserved in the item title.
PRICE_STATUS_SUFFIX = re.compile(
    r"(?:\s*(?:📷|📸|📹|🎥|🏎️?|🚛|🔌)\s*)+"
    r"(?P<note>\([^()\n]{1,60}\))?\s*$"
)

def strip_price_status_suffix(value):
    match = PRICE_STATUS_SUFFIX.search(value)
    if match is None:
        return value, ""
    return value[:match.start()].rstrip(), (match.group("note") or "").strip()


ACCESSORY = re.compile(r"акс(?:ессуар|ис)|чехол|стекло|кабел[ья]|кабель|адаптер|[АA]dapter|переходник|зарядк|"
                       r"ремеш|бампер|case\b|charger\b|cable\b|"
                       r"\b(?:tempered\s+glass|screen\s+protector)\b|"
                       r"\bgadgess\b.*\bglass\b|^\s*(?:premium\s+)?corning\s+glass\b", re.I)
DJI_MIC_KIT = re.compile(
    r"^\s*DJI\s+Mic\b.*?(?P<bundle>\((?=[^)]*\b\d+\s*TX\b)(?=[^)]*\b\d+\s*RX\b)[^)]*\))",
    re.I,
)
PIXEL_WATCH_CONFIGURATION = re.compile(
    r"^\s*(?:Google\s+)?Pixel\s+Watch\s+\d+\b(?=[^\n]*\b\d{2}\s*mm\b)"
    r"(?=[^\n]*\bCase\s*/[^\n]*\bBand\b)", re.I,
)
WATCH_BODY_CASE = re.compile(
    r"\b(?:Aluminum|Aluminium|Titanium|Stainless\s+Steel|Steel|Silver|Gold|Ceramic|Polymer)\s+Case\b",
    re.I,
)
ASIS = re.compile(r"\bAS[\s-]?IS\b|\bASIS\b|асис", re.I)
CPO = re.compile(r"\bCPO\b", re.I)
PACKAGING_NOTE = r"ориг(?:инальная)?\.?\s*упак(?:овка)?\.?"
ORIGINAL_PACKAGING = re.compile(
    r"\(\s*" + PACKAGING_NOTE + r"(?:\s*iphone)?\s*\)|\b" + PACKAGING_NOTE + r"(?!\w)", re.I,
)
STOCK_ANNOTATION = r"(?:остаток(?:\s+на\s+складе)?|в\s+наличии|на\s+складе)\b(?:\s*[:=]?\s*\d+\s*(?:шт(?:ук[аи]?)?\b\.?|pcs\b))?"
SUPPLIER_STOCK_NOTE = re.compile(
    r"\s*(?:\(\s*" + STOCK_ANNOTATION + r"\s*\)|\[\s*" + STOCK_ANNOTATION + r"\s*\]"
    r"|\b" + STOCK_ANNOTATION + r"(?=\s*(?:" + FLAGS.pattern + r"\s*)*$))",
    re.I,
)
INACTIVE = re.compile(r"\bне[\s-]*актив\w*|\binactive\b|not[\s-]*activated|не[\s-]*активирован\w*", re.I)
ACTIVE = re.compile(r"\bактив\w*|\bactive\b(?![\s-]+(?:band|strap|noise)\b)|"
                    r"\bactivated\b|pre[\s-]*activated|предактив\w*", re.I)
IPHONE = re.compile(r"\b(?:iphone|айфон)\s*:?\s*(\d{1,2}\s*(?:e\b|pro\s*max\b|pro\b|"
                    r"plus\b|mini\b|air\b|duo\b)?|air\b|duo\b|se(?:\s*\d)?)", re.I)
SHORT_IPHONE = re.compile(r"^(\d{1,2}(?:e)?(?:\s+(?:pro\s+max|pro|plus|mini|air|duo))?)\s+(?=\d{1,4}\s*(?:gb|tb|гб|тб)?\b)", re.I)
BRANDS = (
    ("Ray-Ban Meta", r"ray[\s-]?ban|wayfarer|skyler"),
    ("Oakley Meta", r"\boakley\b"),
    ("Oura Ring", r"\boura(?:\s+ring)?\b"),
    ("LEGO", r"\blego\b|лего"),
    ("Dyson", r"\bdyson\b|дайсон"),
    ("Canon", r"\bcanon\b|кэнон|канон"),
    ("Rode", r"\br(?:o|ø)de\b"),
    ("COROS", r"\bcoros\b"),
    ("DJI / Insta360", r"\bdji\b|\binsta\s*360\b"),
    ("Kodak / Fujifilm", r"\bkodak\b|\bfujifilm\b|\binstax\b"),
    ("Bowers & Wilkins", r"bowers|b&w|\bpx[78]\b"),
    ("Harman Kardon / Bose", r"harman\s*kardon|\bbose\b|\baura\s+studio\b|\bonyx\b|soundsticks"),
    ("Sennheiser", r"\bsennheiser\b"),
    ("Bang & Olufsen", r"\bbang\s*&\s*olufsen\b|\bbeoplay\b"),
    ("Amazon Kindle", r"\b(?:amazon\s+)?kindle\b"),
    ("Logitech", r"\blogitech\b"),
    ("Medicube", r"\bmedicube\b"),
    ("Meta Quest", r"\b(?:meta\s+)?(?:oculus\s+)?quest\s*\d"),
    ("Miele", r"\bmiele\b"),
    ("Steam", r"\bsteam\s+(?:controller|deck)\b"),
    ("XREAL", r"\bxreal\b"),
    ("PLAUD", r"\bplaud\b"),
    ("Яндекс", r"\bяндекс\b|\byandex\b"),
    ("Google", r"\bgoogle\b|\bpixel\b|\bfitbit\b"),
    ("Samsung", r"\bsamsung\b|\bgalaxy\b|самсунг"),
    ("Apple", r"\bapple\b|\biphone\b|айфон|\bipad\b|\bmacbook\b|\bairpods\b|\bimac\b|"
              r"\bmac\s+mini\b|\bmac\s+studio\b|\bapple\s*tv\b|\bairtag\b|apple\s*watch"),
    ("Sony", r"\bsony\b"),
    ("Xiaomi", r"\bxiaomi\b|\bredmi\b|\bpoco\b|^\s*(?:redmi\s+)?note\s+\d{1,2}[a-z]*\b|"
               r"^\s*x8\s+pro\s+max\.?\s+\d{1,2}\s*/\s*\d{2,4}\b"),
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
    ("GoPro", r"\bgopro\b|^\s*hero\s*\d{1,2}\s+black\b"),
    ("Marshall", r"\bmarshall\b"),
    ("Anker", r"\banker\b|\bsoundcore\b"),
    ("JBL", r"\bjbl\b"),
    ("Nintendo", r"\bnintendo\b|\bswitch\b"),
    ("PlayStation", r"playstation|\bps5\b"),
    ("Xbox", r"\bxbox\b"),
)
SIM_LABELS = {
    "sim": "SIM",
    "hybrid": "SIM",
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


def strip_supplier_annotations(title):
    """Remove notes that describe the seller's stock rather than a variant."""
    title = re.sub(r"[\u200b-\u200f\u202a-\u202e\u2060\ufeff]", "", title)
    title = ORIGINAL_PACKAGING.sub("", title)
    return clean(SUPPLIER_STOCK_NOTE.sub(" ", title))


COLOR_TRANSLATIONS = (
    (r"серо[- ]+зел[её]н(?:ый|ая|ое)", "Gray Green"),
    (r"т[её]мно[- ]+син(?:ий|яя|ее)", "Dark Blue"),
    (r"светло[- ]+розов(?:ый|ая|ое)", "Light Pink"),
    (r"светло[- ]+син(?:ий|яя|ее)", "Light Blue"),
    (r"ч[её]рн(?:ый|ая|ое)", "Black"),
    (r"бел(?:ый|ая|ое)", "White"),
    (r"зел[её]н(?:ый|ая|ое)", "Green"),
    (r"син(?:ий|яя|ее)", "Blue"),
    (r"сер(?:ый|ая|ое)|grey", "Gray"),
    (r"серебрист(?:ый|ая|ое)|серебро", "Silver"),
    (r"золотист(?:ый|ая|ое)|золот(?:ой|ая|ое)", "Gold"),
    (r"фиолетов(?:ый|ая|ое)", "Violet"),
    (r"сиренев(?:ый|ая|ое)", "Lilac"),
    (r"розов(?:ый|ая|ое)", "Pink"),
    (r"графит(?:овый|овая|овое)?", "Graphite"),
    (r"бежев(?:ый|ая|ое)", "Beige"),
    (r"оранжев(?:ый|ая|ое)", "Orange"),
    (r"красн(?:ый|ая|ое)", "Red"),
    (r"ж[её]лт(?:ый|ая|ое)", "Yellow"),
    (r"коричнев(?:ый|ая|ое)", "Brown"),
)


def offer_color_spelling(title):
    """Translate colour spellings while retaining the shade and other attributes."""
    for pattern, color in COLOR_TRANSLATIONS:
        title = re.sub(r"\b(?:" + pattern + r")\b", color, title, flags=re.I)
    title = re.sub(r"\b(?:navy(?:\s+blue)?|нейви|нави)\b", "Navy", title, flags=re.I)
    # A./А. is shorthand for Samsung's Awesome colours. Only consume the
    # prefix in front of a known colour, so model identifiers are untouched.
    title = re.sub(
        r"\b(?:awesome\s+|[aа]\s*\.\s*)(?=(?:navy|gr[ae]y(?:\s*green)?|"
        r"(?:dark|light)\s+blue|ic(?:e|y)\s*blue|lilac|lavender|charcoal|white|black|silver|blue|green|pink|violet)\b)",
        "", title, flags=re.I,
    )
    # A37 has a single green factory colour, Awesome Graygreen. Only
    # match the plain colour after memory, preserving qualifiers like Mint.
    if re.match(r"^\s*(?:samsung\s+)?(?:galaxy\s+)?a\s*(?:37|376)\b", FLAGS.sub("", title), re.I):
        title = re.sub(
            r"(\b\d{1,2}\s*/\s*\d{2,4}(?:\s*(?:gb|гб))?\s+\(?\s*)(?:gr[ae]y[\s-]*)?green\b",
            r"\g<1>Gray Green", title, flags=re.I,
        )
    return title


# For these phone ranges, 5G is a redundant part of the model description.
# Other models can have distinct LTE and 5G editions with the same base name.
MODELS_WITH_REDUNDANT_5G = re.compile(
    r"^(?:(?:samsung\s+)?(?:galaxy\s+)?(?:a\s*(?:26|27|36|37|56|57)\b|(?:z\s*)?fold\s*7\b)"
    r"|(?:google\s+)?pixel\s+(?:10a|11(?:\s+pro(?:\s+(?:xl|fold))?)?)\b"
    r"|honor\s+600(?:\s+pro)?(?=\s+5\s*[gг]\b|\s+\d|\s*$)"
    r"|one\s*plus\s+(?:15r?|nord\s+5)\b)",
    re.I,
)


def offer_network_spelling(title):
    # Requiring a known model avoids interpreting quantities or dropping
    # the network label from phones where it identifies a separate version.
    if MODELS_WITH_REDUNDANT_5G.match(FLAGS.sub("", title).strip()):
        return clean(re.sub(r"\b5\s*[gг]\b", " ", title, flags=re.I))
    return title


def samsung_market_parts(title):
    """Return a title and explicit market flags without guessing conflicts."""
    flags = FLAGS.findall(title)
    bare = clean(FLAGS.sub(" ", title))
    markers = r"IN|IND|MY|MALAYSIA|KZ|EU|HK|HONG\s+KONG|ARG|ARAB"
    market = re.search(
        r"(?:\(\s*(" + markers + r")\s*\)|\b(" + markers + r")\b)"
        r"(?=\s*(?:2\s*sim\s*)?$)", bare, re.I,
    )
    if market:
        code = (market.group(1) or market.group(2)).upper()
        code = re.sub(r"\s+", " ", code)
        flag = {"IN": "🇮🇳", "IND": "🇮🇳", "MY": "🇲🇾", "MALAYSIA": "🇲🇾",
                "KZ": "🇰🇿", "EU": "🇪🇺", "HK": "🇭🇰", "HONG KONG": "🇭🇰",
                "ARG": "🇦🇷", "ARAB": "🇦🇪"}[code]
        if set(flags) == {flag} or (not flags and code != "ARAB"):
            bare = clean(bare[:market.start()] + " " + bare[market.end():])
            if not flags:
                flags.append(flag)
    return bare, flags


SAMSUNG_S_OFFER_CODES = {
    25: {"": "S931", "+": "S936", "fe": "S731", "ultra": "S938"},
    26: {"": "S942", "+": "S947", "fe": "S741", "ultra": "S948"},
}

TABLET_NETWORK_ALIASES = {
    "Wi-Fi": r"\b(?:wi[\s-]*fi|wf)\b",
    "LTE": r"\b(?:lte|4\s*[gг])\b",
    "5G": r"\b5\s*[gг]\b",
}
TABLET_MEMORY = re.compile(r"\b(\d{1,2})\s*/\s*(\d{2,4}(?:\s*(?:TB|ТБ))?)(?:\s*(?:GB|ГБ))?\b", re.I)
SAMSUNG_TAB_CODES = {
    "A11": ("8.7", {"X130": "Wi-Fi", "X133": "Wi-Fi", "X135": "LTE"}),
    "A11+": ("11", {"X230": "Wi-Fi", "X236": "5G"}),
}
SAMSUNG_NETWORK_BLOCKS = ("Samsung Tab Wi-Fi", "Samsung Tab LTE", "Samsung Tab 5G",
                          "Samsung Watch Bluetooth", "Samsung Watch LTE")


def tablet_networks(title):
    return [network for network, pattern in TABLET_NETWORK_ALIASES.items() if re.search(pattern, title, re.I)]


def tablet_memory_label(match):
    storage = re.sub(r"\s+", "", match[2].upper()).replace("ТБ", "TB")
    storage = {"1024": "1TB", "2048": "2TB"}.get(storage, storage)
    return f"{match[1]}/{storage}"


def samsung_tab_name(title):
    model = re.match(r"^(?:Samsung\s+(?:Galaxy\s+)?|Galaxy\s+)?Tab\s+A\s*11(\+|\s+Plus\b)?(?=\s|$)", title, re.I)
    if not model:
        if re.match(r"^Samsung\s+Tab\s+S\s*\d", title, re.I):
            for network, pattern in TABLET_NETWORK_ALIASES.items():
                title = re.sub(pattern, network, title, flags=re.I)
        return title
    variant = "A11+" if model[1] else "A11"
    diagonal, codes = SAMSUNG_TAB_CODES[variant]
    details = title[model.end():].strip()
    size = re.match(r"(\d+(?:[.,]\d+)?)\s*(?:\"|″|''|”)(?=\s|$)", details)
    if size and size[1].replace(",", ".") != diagonal:
        return title
    if size:
        details = details[size.end():]
    networks = tablet_networks(details)
    code = re.search(r"\b(?:SM-)?(X\d{3})[FBGN]?\b", details, re.I)
    network = codes.get(code[1].upper()) if code else None
    if len(networks) > 1 or (code and (not network or (networks and networks != [network]))):
        return title
    if code:
        details = details[:code.start()] + " " + details[code.end():]
    if networks:
        network = networks[0]
        details = re.sub(TABLET_NETWORK_ALIASES[network], " ", details, flags=re.I)
    details = clean(re.sub(r"\(\s*\)", " ", details))
    details = TABLET_MEMORY.sub(tablet_memory_label, details)
    details = standard_colour_case(offer_color_spelling(details))
    return clean(f'Samsung Tab {variant} {diagonal}" ' + (network or "") + " " + details)


def samsung_s_offer_spelling(title, markets):
    match = re.match(
        r"^(?:samsung\s+)?s\s*(25|26)(?:\s*(\+|plus\b|fe\b|ultra\b|edge\b))?(?=\s|$)",
        title, re.I,
    )
    if not match:
        return title
    generation = int(match[1])
    variant = (match[2] or "").casefold().replace("plus", "+")
    suffix = variant if variant in {"", "+"} else " " + variant.title()
    if variant == "fe":
        suffix = " FE"
    title = f"Samsung S{generation}{suffix}" + title[match.end():]
    code = SAMSUNG_S_OFFER_CODES[generation].get(variant)
    markets = set(markets)
    east_asian = {"🇭🇰", "🇨🇳"}
    if code and not markets.intersection(east_asian):
        title = re.sub(r"\b" + code + r"B\b", " ", title, flags=re.I)
    elif code and markets and markets <= east_asian:
        title = re.sub(r"\b" + code + r"0\b", " ", title, flags=re.I)
    title = re.sub(r"\b5\s*[gг]\b", " ", title, flags=re.I)
    title = re.sub(r"\b(\d{1,2})\s*/\s*(1024|2048)(?:\s*(?:GB|ГБ))?\b",
                   lambda m: f"{m[1]}/{int(m[2]) // 1024}TB", title, flags=re.I)
    if generation == 26 and variant != "fe":
        title = re.sub(r"\b(?:sky\s+)?blue\b|\bголуб(?:ой|ая|ое)\b", "Sky Blue", title, flags=re.I)
        title = re.sub(r"\b(?:cobalt\s+)?violet\b", "Cobalt Violet", title, flags=re.I)
        title = re.sub(r"\bpink\s*gold\b", "Pink Gold", title, flags=re.I)
    elif generation == 26:
        title = re.sub(r"\bphistachio\b", "Pistachio", title, flags=re.I)
    elif variant in {"", "+", "fe"}:
        title = re.sub(r"\bic(?:e|y)\s*blue\b", "Icyblue", title, flags=re.I)
        if variant in {"", "+"}:
            title = re.sub(r"\bsilver(?:\s+shadow)?\b", "Silver Shadow", title, flags=re.I)
    elif variant == "ultra":
        ultracolors = (
            (r"(?:silver[\s-]*)?blue", "Silver Blue"),
            (r"gr[ae]y", "Gray"),
            (r"white(?:[\s-]*silver)?", "White Silver"),
            (r"black", "Black"),
            (r"jet[\s-]*black", "Jet Black"),
        )
        for pattern, color in ultracolors:
            title = re.sub(r"(?<=\s)(?:titanium\s+)?(?:" + pattern + r")\b", color, title, flags=re.I)
    title = re.sub(r"\b2\s*sim\b", "2 SIM", title, flags=re.I)
    title = re.sub(r"\(\s*([A-Za-z]+(?:\s+[A-Za-z]+){0,2})\s*\)", r"\1", title)
    return clean(title)


def samsung_watch_name(title):
    """Normalise watch descriptions while preserving connectivity and generation."""
    title = title.replace("\ufe0f", "")
    watch = re.match(
        r"^Samsung\s+(?:Galaxy\s+)?(?:L(\d{3})\s+)?Watch\s*"
        r"(Ultra(?:\s*2\b)?|[6-9](?:\s+Classic)?)\s+"
        r"(?:(?:\((20\d{2})\)|(20\d{2}))\s+)?"
        r"(?:(LTE|4G)\s+)?(40|43|44|46|47)(?:\s*(?:mm|мм))?\b"
        r"(?:\s+(LTE|4G|Bluetooth|BT|Wi[ -]?Fi))?\b(.*)$", title, re.I,
    )
    if not watch:
        return title
    sku, model, bracket_year, plain_year, before, size, after, details = watch.groups()
    model = re.sub(r"\bUltra\s*2\b", "Ultra 2", clean(model).title())
    ultra = model.startswith("Ultra")
    lte_label = re.compile(r"\b(?:LTE|4G)\b", re.I)
    lte = bool(before or lte_label.fullmatch(after or "") or lte_label.search(details) or (ultra and not after))
    details = lte_label.sub(" ", details)
    known = {"325": ("8", "40", True), "330": ("8", "44", False),
             "345": ("9", "40", True), "355": ("9", "44", True),
             "705": ("Ultra", "47", True), "715": ("Ultra 2", "47", True)}
    if sku and known.get(sku) != (model, size, lte):
        return title
    year = bracket_year or plain_year
    if ultra:
        in_details = re.search(r"\(\s*(20\d{2})\s*\)", details)
        if in_details:
            if year and year != in_details[1]:
                return title
            year = in_details[1]
            details = details[:in_details.start()] + " " + details[in_details.end():]
        if model == "Ultra 2" and year == "2026":
            year = None  # Suppliers also list this generation simply as Ultra 2.
        details = re.sub(r"^\s*Titanium\s+", " ", details, flags=re.I)
    details = re.sub(r"\s*/\s*EU\s*/\s*PA\b", " ", details, flags=re.I)
    details = offer_color_spelling(clean(details))
    if model == "8":
        details = re.sub(r"^Black\b", "Graphite", details, flags=re.I)
    details = re.sub(r"^(black|blue|cream|graphite|gray|silver|white)\b",
                     lambda m: m[0].title(), details, flags=re.I)
    return clean(f"Samsung Watch {model} {size}mm" + (" LTE" if lte else "")
                 + (f" ({year})" if year else "") + " " + details)


def standard_colour_case(title):
    colours = r"black|white|gray|silver|graphite|purple|violet|mint|blue|cyan|green|gold|red|orange|pink|beige|cream"
    return re.sub(r"\b(?:" + colours + r")\b", lambda m: m[0].title(), title, flags=re.I)


HUAWEI_NAME_ALIASES = (
    (r"^Huawei\s+(?:FB\s*Neo|Free\s*Buds\s*Neo)\b", "Huawei FreeBuds Neo"),
    (r"^Huawei\s+Free\s*Buds\s+(SE|Pro)\s*(\d+)\b", lambda m: f"Huawei FreeBuds {m[1].upper() if m[1].lower() == 'se' else 'Pro'} {m[2]}"),
    (r"^Huawei\s+Free\s*Clip\s*2\s*S\b", "Huawei FreeClip 2 S"),
    (r"^Huawei\s+Free\s*(Buds|Clip)\s*(?=\d)", lambda m: f"Huawei Free{m[1].title()} "),
    (r"^Huawei\s+Watch\s+Fit\s*(?=\d)", "Huawei Watch Fit "),
    (r"\b(\d+)\s*(?:mm|мм)\b", r"\1mm"),
    (r"^Huawei\s+M[- ]?Pencil\b", "Huawei M-Pencil"),
)
HUAWEI_FINISH_ALIASES = {
    "huawei freeclip 2": {"gold": "Rose Gold", "rose gold": "Rose Gold", "purple": "Berry Purple", "berry purple": "Berry Purple"},
    "huawei freeclip 2 s": {"blue": "Deepsea Blue", "deepsea blue": "Deepsea Blue", "space silver": "Space Silver"},
    "huawei nova 15": {"green": "Mint Green", "mint": "Mint Green", "mint green": "Mint Green"},
    "huawei nova 15 max": {"blue": "Lake Cyan", "cyan": "Lake Cyan", "lake cyan": "Lake Cyan"},
    "huawei nova 16s": {"blue": "Isle Blue", "isle blue": "Isle Blue", "black": "Starry Black", "starry black": "Starry Black",
                        "white": "Sky White", "sky white": "Sky White", "breathing crystal": "Breathing Crystal"},
    "huawei nova 16s pro": {"blue": "Isle Blue", "isle blue": "Isle Blue", "breathing crystal": "Breathing Crystal"},
    "huawei pura 90s pro": {
        "black": "Mulberry Black", "mulberry black": "Mulberry Black", "white": "Coconut White", "coconut white": "Coconut White",
        "orange": "Orange Soda", "orange soda": "Orange Soda", "green": "Guava Soda", "pink green": "Guava Soda", "guava soda": "Guava Soda",
    },
    "huawei pura 90s pro max": {
        "black": "Graphite Black", "graphite": "Graphite Black", "graphite black": "Graphite Black",
        "gold": "Blush Gold", "blush gold": "Blush Gold", "orange": "Orange Ocean", "orange blue": "Orange Ocean", "orange ocean": "Orange Ocean",
        "purple": "Blaze Purple", "beige violet": "Blaze Purple", "blaze purple": "Blaze Purple",
    },
}
HUAWEI_MATEPAD_BUNDLES = {
    "с клавиатурой": r"(?:\+\s*)?\b(?:kb|keyboard|клавиатура|с\s+клавиатурой)\b",
    "со стилусом": r"(?:\+\s*)?(?<![\w-])(?:pen|stylus|стилус|со\s+стилусом)\b",
    "с чехлом": r"(?:\+\s*)?\b(?:cover|с\s+чехлом)\b",
}


def huawei_matepad_name(title):
    match = re.match(r"^Huawei\s+MatePad(?:\s+(Pro\s+Max|SE\s*11|11[.,]5s?|Air|Mini|Pro))?(?=\s|$)", title, re.I)
    if not match:
        return title
    variant = clean(match[1] or "").replace(",", ".").title().replace("Se", "SE").replace("11.5S", "11.5s")
    variant = re.sub(r"^SE\s*11$", "SE 11", variant)
    details = title[match.end():].strip()
    memory = TABLET_MEMORY.search(details)
    networks = tablet_networks(details)
    if not memory or len(networks) > 1:
        return title
    fields = [tablet_memory_label(memory)]
    details = details[:memory.start()] + " " + details[memory.end():]
    size = re.search(r"\b(\d{1,2}[.,]\d|11|12)\s*(?:[\"″”]|''|(?=\s|$))", details)
    diagonal = size[1].replace(",", ".") if size else ""
    if size:
        details = details[:size.start()] + " " + details[size.end():]
    if not variant and diagonal == "11.5":
        variant, diagonal = "11.5", ""
    if (variant, diagonal) in {("SE 11", "11"), ("11.5", "11.5"), ("11.5s", "11.5"), ("Mini", "8.8")}:
        diagonal = ""
    matte = re.compile(r"\bpaper[ -]*matte(?:\s+edition)?\b", re.I)
    air_code = re.search(r"\bWKI-W09\b", details, re.I)
    # Only a verified model code can resolve a conflicting diagonal.
    verified_air = variant == "Air" and air_code and networks in ([], ["Wi-Fi"])
    if verified_air:
        diagonal = "12"
        details = details[:air_code.start()] + " " + details[air_code.end():]
    if matte.search(details) or verified_air:
        fields.append("PaperMatte")
        details = matte.sub(" ", details)
    network = networks[0] if networks else ""
    if network:
        details = re.sub(TABLET_NETWORK_ALIASES[network], " ", details, flags=re.I)
    elif fields[-1] == "PaperMatte" and variant in {"11.5", "11.5s"}:
        network = "Wi-Fi"
    elif verified_air:
        network = "Wi-Fi"
    bundle = []
    for label, pattern in HUAWEI_MATEPAD_BUNDLES.items():
        if re.search(pattern, details, re.I):
            bundle.append(label)
            details = re.sub(pattern, " ", details, flags=re.I)
    if (variant == "Air" and not diagonal and fields == ["8/256", "PaperMatte"]
            and network == "Wi-Fi" and "с клавиатурой" in bundle):
        diagonal = "12"
    size_label = f'{diagonal}"' if diagonal else ""
    return clean(" ".join(["Huawei MatePad", variant, size_label, *fields, network, clean(details).strip(" +"), *bundle]))


def huawei_offer_name(title):
    flags = FLAGS.findall(title)
    bare = clean(FLAGS.sub(" ", title))
    if not re.match(r"^Huawei\b", bare, re.I):
        return title
    bare = "Huawei" + bare[6:]
    for pattern, name in HUAWEI_NAME_ALIASES:
        bare = re.sub(pattern, name, bare, flags=re.I)
    bare = standard_colour_case(offer_color_spelling(bare))
    bare = re.sub(r"\bбежевые\b", "Beige", bare, flags=re.I)
    bare = TABLET_MEMORY.sub(tablet_memory_label, bare)
    bare = huawei_matepad_name(bare)
    bare = re.sub(r"^Huawei\s+(Nova|Pura|Mate)\s+([\da-z]+)(?:\s+(Pro\s+Max|Pro|Max|Ultra)\b)?",
                  lambda m: f"Huawei {m[1].title()} {m[2].upper() if m[1].lower() in {'pura', 'mate'} else m[2].lower()}"
                            + (" " + clean(m[3]).title() if m[3] else ""), bare, flags=re.I)
    if re.match(r"^Huawei Pura 90S Pro(?: Max)?(?=\s+(?:5\s*[gг]\s+)?\d{1,2}/)", bare, re.I):
        bare = clean(re.sub(r"\b5\s*[gг]\b", " ", bare, flags=re.I))
    for model, finishes in sorted(HUAWEI_FINISH_ALIASES.items(), key=lambda alias: -len(alias[0])):
        if bare.casefold().startswith(model + " "):
            rest = bare[len(model):].strip()
            memory = TABLET_MEMORY.match(rest)
            finish = (rest[memory.end():] if memory else rest).strip(" ()")
            if canon := finishes.get(finish.casefold()):
                bare = clean(bare[:len(model)] + " " + (memory[0] if memory else "") + " " + canon)
            break
    return clean(bare + " " + " ".join(dict.fromkeys(flags)))


SAMSUNG_CHARGER = re.compile(r"^(?:🔌\ufe0f?\s*)?Samsung\s+EP[- ]?T(2510|4510|4511|6530)\b", re.I)


def samsung_charger_name(title):
    charger = SAMSUNG_CHARGER.match(title)
    if not charger:
        return title
    part = charger[1]
    power, ports = {"2510": ("25", "USB-C"), "4510": ("45", "USB-C"),
                    "4511": ("45", "USB-C"), "6530": ("65", "USB-A/C")}[part]
    details = clean(title[charger.end():])
    details = re.sub(r"^" + power + r"\s*(?:W|Вт)\b[\s,]*", "", details, flags=re.I)
    port = r"(?:(?:USB\s*)?Type[-\s]*)?[AА]/[CС]|USB[-\s]*[AА]/[CС]" if part == "6530" else r"(?:USB|Type)[-\s]*[CС]"
    details = re.sub(r"^(?:" + port + r")\b[\s,]*", "", details, flags=re.I)
    details = offer_color_spelling(details)
    details = re.sub(r"\b(black|white)\b", lambda m: m[0].title(), details, flags=re.I)
    details = re.sub(r"\b(?:with\s+(?:a\s+)?cable|кабель\s+в\s+комплекте)\b", "с кабелем", details, flags=re.I)
    return clean(f"Samsung EP-T{part} {power}W {ports} " + details)


def is_accessory(title):
    if re.search(r"\bmonarch\b.*\bsamsung\b", title, re.I):
        return True
    # A charger with an included cable is still the Samsung charger offer.
    if SAMSUNG_CHARGER.match(FLAGS.sub("", title).strip()):
        return False
    # A TX/RX kit is a microphone system. Its included Charging Case does not
    # turn it into an accessory; a case sold on its own still is an accessory.
    kit = DJI_MIC_KIT.search(title)
    if kit:
        title = title[:kit.start("bundle")] + title[kit.end("bundle"):]
    if PIXEL_WATCH_CONFIGURATION.search(title):
        # In a watch size/case/band configuration, Case describes the housing.
        title = WATCH_BODY_CASE.sub("", title)
    return bool(ACCESSORY.search(title))


def strip_decoration(text):
    text = re.sub(r"^\s*[▫▪◽◾]\ufe0f?\s*", "", text)
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
    original, trailing_note = strip_price_status_suffix(original)
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
    if trailing_note:
        title += " " + trailing_note
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


def infer_iphone_sim(title):
    """Infer only from an unambiguous supplier market flag and supported model.

    Apple: support.apple.com/en-ie/118569 and support.apple.com/en-ca/108898.
    A flag is treated as the market version, not the seller's location.
    """
    model = iphone_model(title).casefold()
    if model == "iphone air":
        return "esim"
    flags = set(FLAGS.findall(title))
    if len(flags) != 1:
        return "unknown"
    flag = next(iter(flags))
    # ISO 3166-1 alpha-2, plus the supplier's EU flag. This validates flags
    # rather than treating any pair of regional-indicator letters as a market.
    codes = set(("AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ "
                 "CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR "
                 "GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM JO JP "
                 "KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ "
                 "NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ "
                 "TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW EU").split())
    country = "".join(chr(ord(ch) - 0x1F1E6 + ord("A")) for ch in flag)
    if country not in codes:
        return "unknown"
    esim17 = {"US", "PR", "VI", "CA", "MX", "JP", "GU", "AE", "SA", "BH", "KW", "QA", "OM"}
    if model in {"iphone 17", "iphone 17 pro", "iphone 17 pro max", "iphone 17e", "iphone 18 pro", "iphone 18 pro max"}:
        if country in esim17:
            return "esim"
        if country == "CN":
            return "hybrid" if model in {"iphone 17e", "iphone 18 pro", "iphone 18 pro max"} else "dual"
        return "hybrid"
    valid_older = {f"iphone {number}{suffix}" for number, suffixes in {
        11: ("", " pro", " pro max"), 12: ("", " mini", " pro", " pro max"),
        13: ("", " mini", " pro", " pro max"), 14: ("", " plus", " pro", " pro max"),
        15: ("", " plus", " pro", " pro max"), 16: ("", "e", " plus", " pro", " pro max")
    }.items() for suffix in suffixes}
    if model not in valid_older:
        return "unknown"
    generation = int(re.match(r"iphone (\d+)", model).group(1))
    if country in {"US", "PR"} and generation >= 14:
        return "esim"
    if country in {"CN", "HK", "MO"}:
        if model == "iphone 16e":
            return "dual" if country == "CN" else "hybrid"
        if "mini" in model:
            return "unknown" if country == "CN" else "hybrid"
        return "dual"
    return "hybrid"


def resolve_iphone_sim(item):
    if item.sim != "unknown" or item.accessory:
        return item
    sim = infer_iphone_sim(item.title)
    return replace(item, sim=sim) if sim != "unknown" else item


def iphone_publish_block(model):
    """Logical iPhone block. Physical Telegram messages are grouped later."""
    return model or ""


def iphone_storage_key(item):
    title = FLAGS.sub("", clean(item.title))
    model = iphone_model(title)
    if model:
        # iPhone 17 Air is grouped as "iPhone Air", but the original text
        # still contains 17; remove the actual match before finding storage.
        title = IPHONE.sub("", title, count=1).strip()
    match = re.search(r"\b(\d{1,4})\s*(GB|TB)?\b", title, re.I)
    if not match:
        return ""
    value = int(match.group(1))
    unit = (match.group(2) or "GB").upper()
    return f"{value}{unit}"

def brand_of(text):
    for name, pattern in BRANDS:
        if re.search(pattern, text, re.I):
            return name
    return ""


def apple_watch_block(title):
    plain = FLAGS.sub("", clean(title)).strip()

    # Explicitly branded non-Apple watches always win over generic watch rules.
    if re.search(
        r"\b(?:galaxy|samsung|one\s*plus|oneplus|xiaomi|redmi|huawei|honor|"
        r"garmin|coros|pixel|fitbit|amazfit|oppo|vivo|realme|nothing)\b",
        plain,
        re.I,
    ):
        return ""

    # Supplier shorthand for Apple Watch. A Series shorthand must be followed
    # by a real watch case size so Galaxy S-series phones are never mistaken
    # for Apple Watch.
    watch_size = r"(?:38|40|41|42|44|45|46|49)(?:\s*mm)?"
    if re.search(r"^S\s*\d{1,2}\s+" + watch_size + r"\b", plain, re.I):
        return "Apple Watch"
    if re.search(r"^SE\s*\d*\s+" + watch_size + r"\b", plain, re.I):
        return "Apple Watch"
    if re.search(r"^SE\s*\(\s*20\d{2}\s*\)\s+" + watch_size + r"\b", plain, re.I):
        return "Apple Watch"
    if re.search(r"^(?:UL|ULTRA)\s*\d{1,2}\b", plain, re.I):
        return "Apple Watch"

    if re.search(r"\bapple\s*watch\b|^watch\b", plain, re.I):
        return "Apple Watch"
    return ""


def samsung_block(title):
    plain = FLAGS.sub("", clean(title)).strip()
    has_brand = bool(re.search(r"\bsamsung\b|\bgalaxy\b|самсунг", plain, re.I))

    # Supplier often omits "Samsung" from every product row.
    if re.search(r"^(?:samsung\s+|galaxy\s+)?buds\s*[34]\b", plain, re.I):
        return "Samsung Buds"
    if re.search(r"^(?:(?:samsung\s+)(?:galaxy\s+)?|galaxy\s+)a\s*\d{2,3}[a-z]?\b", plain, re.I) or re.search(r"^a\s*(?:17|27|37|57)\b", plain, re.I):
        return "Samsung A + S25"
    if re.search(r"^(?:samsung\s+|galaxy\s+)?s\s*25(?:\s*(?:fe|edge|ultra|\+|plus))?\b", plain, re.I):
        return "Samsung A + S25"
    if re.search(r"^(?:samsung\s+|galaxy\s+)?s\s*26(?:\s*(?:fe|edge|ultra|\+|plus))?\b", plain, re.I):
        return "Samsung S26"
    if re.search(r"^(?:samsung\s+|galaxy\s+)?tab\s*s\s*\d*", plain, re.I):
        return "Samsung Tab S"
    if re.search(r"^(?:samsung\s+(?:galaxy\s+)?|galaxy\s+)?tab\s+a\s*11\b", plain, re.I):
        return "Samsung"
    if re.search(r"^(?:samsung\s+)?(?:galaxy\s+)?(?:z\s*)?(?:fold|flip)(?=\s|\d|$)", plain, re.I):
        return "Samsung Fold / Flip"

    if not has_brand:
        return ""
    if re.search(r"\b(?:galaxy\s*)?tab\s*s\s*\d*", plain, re.I):
        return "Samsung Tab S"
    if re.search(r"\b(?:galaxy\s*)?(?:z\s*)?(?:fold|flip)(?=\s|\d|$)", plain, re.I):
        return "Samsung Fold / Flip"
    if re.search(r"\b(?:galaxy\s*)?buds\s*[34]\b", plain, re.I):
        return "Samsung Buds"
    if re.search(r"\b(?:galaxy\s+|samsung\s+(?:galaxy\s+)?)a\s*\d{2,3}[a-z]?\b", plain, re.I):
        return "Samsung A + S25"
    if re.search(r"\bgalaxy\s+s\s*25\b", plain, re.I):
        return "Samsung A + S25"
    if re.search(r"\bgalaxy\s+s\s*26\b", plain, re.I):
        return "Samsung S26"
    return "Samsung"


def apple_ipad_block(title):
    """Recognize supplier iPad rows that omit the word iPad."""
    plain = FLAGS.sub("", clean(title)).strip()
    if re.search(r"\bApple\s+Pencil\b", plain, re.I):
        return "Apple Accessories"
    if re.search(r"\biPad\b", plain, re.I):
        return "iPad"
    # Supplier examples:
    # MINI 7 128 ... Wi-Fi
    # AIR 11/13 M3/M4 128/256 ... Wi-Fi
    # PRO 11/13 M4/M5 256/1TB ... Wi-Fi/LTE
    # PRO 12.9 M2 128 ... LTE
    if re.search(r"^MINI\s+\d+\s+(?:\d{2,4}|\d+TB)\b.*\b(?:Wi[ -]?Fi|LTE|Cellular)\b", plain, re.I):
        return "iPad"
    if re.search(r"^(?:AIR|PRO)\s+(?:11|12\.9|13)\s+M\d+\s+(?:\d{2,4}|\d+TB)\b.*\b(?:Wi[ -]?Fi|LTE|Cellular)\b", plain, re.I):
        return "iPad"
    return ""


def apple_computer_block(title):
    """Recognize supplier MacBook rows even when the word MacBook is omitted."""
    plain = FLAGS.sub("", clean(title)).strip()
    if re.search(r"\b(?:MacBook|iMac)\b", plain, re.I):
        return "MacBook / iMac"

    # Supplier examples: Neo 13 MHFD4 ... (A18 Pro 8/256),
    # Air 13/15 MDH... (M5 ...), Pro 14/16 ... (M4/M5 Pro ...).
    sku = r"[A-Z0-9]{4,}"
    chip = r"(?:A18\s*Pro|M\d+(?:\s*(?:Pro|Max))?)"
    if re.search(r"^(?:Neo|Air)\s+(?:13|15)\s+" + sku + r"\b.*\(" + chip + r"\b", plain, re.I):
        return "MacBook / iMac"
    if re.search(r"^Pro\s+(?:14|16)\s+" + sku + r"\b.*\(" + chip + r"\b", plain, re.I):
        return "MacBook / iMac"
    return ""


def apple_accessory_block(title):
    """Apple power adapters / MacBook transitions belong to the common Apple post."""
    plain = FLAGS.sub("", clean(title)).strip()
    if re.search(r"^(?:Apple\s+)?Magic\s+(?:Mouse|Keyboard|Trackpad)\b", plain, re.I):
        return "Apple Accessories"
    mixed_adapter = r"(?:Adapter|Аdapter|адаптер)"
    if "" in title and re.search(mixed_adapter, plain, re.I):
        return "Apple Accessories"
    if re.search(r"\bпереходник\s+для\s+MacBook\b", plain, re.I):
        return "Apple Accessories"
    if re.search(r"^" + mixed_adapter + r"\s+(?:universal|20W|USB[ -]?C\s+to\s+USB)\b", plain, re.I):
        return "Apple Accessories"
    return ""


def product_block(title):
    plain = FLAGS.sub("", clean(title))
    if re.match(r"^\s*(?:hd|hs|ht)\s*\d{2}(?:[- ]?[a-z])?\b", plain, re.I):
        return "Dyson"
    if re.match(r"^\s*5\s*(?:💿\s*)?(?:portal\b|дисковод\b)", plain, re.I):
        return "PlayStation"
    if re.search(r"\bpoco\b|^\s*x8\s+pro\s+max\b", plain, re.I):
        return "POCO"
    if re.search(r"\bredmi\b|^\s*note\s+\d{1,2}[a-z]*\b", plain, re.I):
        return "Redmi"
    watch = apple_watch_block(title)
    if watch:
        return watch
    samsung = samsung_block(title)
    if samsung:
        return samsung
    ipad = apple_ipad_block(title)
    if ipad:
        return ipad
    apple_accessory = apple_accessory_block(title)
    if apple_accessory:
        return apple_accessory
    computer = apple_computer_block(title)
    if computer:
        return computer
    for label in ("AirPods", "iPad", "Mac mini", "Mac Studio", "Apple TV", "AirTag"):
        if re.search(r"\b" + re.escape(label) + r"\b", title, re.I):
            return label
    return brand_of(title)


def ordered_blocks(blocks, preferred=()):
    defaults = ["iPhone", "AirPods", "Apple Accessories", "Mac mini", "Apple TV", "AirTag",
                "Apple Watch", "iPad", "MacBook / iMac", "Mac Studio", "Apple", "Ray-Ban Meta", "Samsung", "Honor", "Realme", "Huawei", "Tecno",
                "Xiaomi", "Redmi", "POCO", "Google", "COROS", "Rode", "Dyson", "Oura Ring", "CPO", "ASIS", "Аксессуары", "Товары"]
    def order_key(name):
        if name in preferred:
            return (-1, preferred.index(name), (), "")
        model = iphone_model(name)
        family = "iPhone" if model else ("Apple Watch" if name.startswith("Apple Watch") else ("Samsung" if name.startswith("Samsung") else name))
        rank = defaults.index(family) if family in defaults else len(defaults) - 4
        samsung_order = ("Samsung Buds", "Samsung A + S25", "Samsung S26", "Samsung Tab S",
                         "Samsung Fold / Flip", "Samsung", *SAMSUNG_NETWORK_BLOCKS)
        samsung_rank = samsung_order.index(name) if name in samsung_order else 0
        numbered = model if model else (name if family == "Apple Watch" else "")
        number = re.search(r"\d+", numbered)
        return (rank, samsung_rank, -int(number[0]) if number else 0, tuple(int(x) if x.isdigit() else x for x in re.split(r"(\d+)", name.casefold())), name)
    return sorted(set(blocks), key=order_key)


def normal_title(title, context=""):
    title = strip_decoration(APPLE_WARRANTY_NOTICE.sub("", strip_decoration(title)))
    context = APPLE_WARRANTY_NOTICE.sub("", strip_decoration(context))
    # Explicit supplier shorthand must win over a preceding brand header.
    if re.match(r"^\s*(?:hd|hs|ht)\s*\d{2}(?:[- ]?[a-z])?\b", FLAGS.sub("", title), re.I):
        title = "Dyson " + title
    elif re.match(r"^\s*5\s*(?:💿\s*)?(?:portal\b|дисковод\b)", FLAGS.sub("", title), re.I):
        title = "PlayStation " + title
    if re.search(r"\bTab\s+A\s*11\b", title, re.I):
        title = re.sub(r"\bSM-(X\d{3}[A-Z0-9]*)\b", r"\1", title, flags=re.I)
    title = re.sub(r"\bSM-[A-Za-z0-9/]+\b", "", title, flags=re.I)
    title = re.sub(r"\bайфон\b", "iPhone", title, flags=re.I)
    # A supplier's Apple Air rows can inherit an unrelated Samsung heading.
    # Match only the observed phone-shaped eSIM rows with iPhone Air colours.
    if (sim_type(title) == "esim" and
            re.search(r"\b(?:cloud\s+white|light\s+gold|sky\s+blue|space\s+black)\b", title, re.I)):
        title = re.sub(r"^((?:[\U0001F1E6-\U0001F1FF]{2}\s*)*)Samsung\s+Air(?=\s+\d{2,4}\s*(?:GB|TB|ГБ|ТБ)?\b)",
                       r"\1iPhone Air", title, flags=re.I)
    # A supplier abbreviates iPhone Pro Max as "17 Max" / "18 Max".
    # A preceding Samsung section used to prepend Samsung to those rows.
    # Recognize both the raw abbreviation and already-cached wrong titles,
    # but require an iPhone-style SIM marker and a capacity.
    if sim_type(title) in {"hybrid", "esim"}:
        flag_prefix = r"((?:[\U0001F1E6-\U0001F1FF]{2}\s*)*)"
        title = re.sub(r"^" + flag_prefix + r"(?:Samsung\s+)?(17|18)\s+Max(?=\s+\d{1,4}\s*(?:GB|TB|ГБ|ТБ)?\b)",
                       r"\1iPhone \2 Pro Max", title, count=1, flags=re.I)
    # Supplier rows may use Cyrillic Е instead of Latin e in iPhone 16e/17e.
    # Restrict the substitution to the model prefix, not product descriptions.
    title = re.sub(r"^((?:iPhone\s*:?\s*)?\d{1,2})\s*[eе]\b", r"\1e", title, flags=re.I)
    plain = FLAGS.sub("", title).strip()
    if SHORT_IPHONE.match(plain):
        # A flag in the middle means the flag-free text is not a substring.
        # Insert before the model, never at find()'s -1 position near the end.
        pos = re.search(r"\d", title).start()
        title = title[:pos] + "iPhone " + title[pos:]
    elif context and not brand_of(title) and not apple_ipad_block(title) and not apple_computer_block(title) and not apple_accessory_block(title) and not is_accessory(title):
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
    title = re.sub(r"^UL\s*(\d{1,2})\b", r"Ultra \1", title, flags=re.I)
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


def offer_qualifier_suffix(item):
    # Condition can be supplied by a section heading instead of the title.
    condition = item.block.upper() if item.block.upper() in {"CPO", "ASIS"} else ""
    return ("|condition:" + condition if condition else "") + ("|accessory" if item.accessory else "")


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
        return identity(self.title, self.sim, self.currency) + offer_qualifier_suffix(self)

    def to_dict(self):
        return dict(title=self.title, price=str(self.price), currency=self.currency,
                    block=self.block, sim=self.sim, accessory=self.accessory)

    @classmethod
    def from_dict(cls, value):
        data = {**value, "price": Decimal(value["price"])}
        data["title"] = normal_title(data["title"])
        if data.get("block") not in {"ASIS", "CPO"}:
            model = iphone_model(data["title"])
            known = iphone_publish_block(model) if model else product_block(data["title"])
            complete_device = (DJI_MIC_KIT.search(data["title"]) or PIXEL_WATCH_CONFIGURATION.search(data["title"])
                               or SAMSUNG_CHARGER.match(FLAGS.sub("", data["title"]).strip()))
            if known == "Apple Accessories" or (known and complete_device and not is_accessory(data["title"])):
                data.update(block=known, accessory=False)
            elif is_accessory(data["title"]):
                data.update(block="Аксессуары", accessory=True)
            elif known and data.get("block") != "Аксессуары":
                data["block"] = known
        return cls(**data)


@dataclass
class ParseResult:
    items: list
    rejected: list
    closed: bool = False


def _price_lines_with_priority(documents, document_priorities=None):
    """Join split rows without losing the timestamp of either part."""
    pending = None
    pending_priority = None
    for index, document in enumerate(documents):
        priority = document_priorities[index] if document_priorities is not None else (index,)
        for raw in document.splitlines():
            line = strip_decoration(raw)
            if not line:
                continue
            # An article on its own is metadata, never a product with a price.
            # Its final hyphenated component can otherwise look like a price.
            if re.fullmatch(r"(?:арт(?:икул)?\.?|sku)\s*[:№#]?\s*[A-Za-zА-Яа-я0-9][A-Za-zА-Яа-я0-9./\-]*", line, re.I):
                continue
            tier = WHOLESALE.search(line)
            if tier and not FLAGS.sub("", line[:tier.start()]).strip():
                if not re.search(r"\b(?:от|from)\s*1\s", tier[0], re.I):
                    continue
                line = line[:tier.start()] + line[tier.end():]
            else:
                line = re.sub(r"\b(?:от|from)\s*1\s*(?:шт|штук|pcs)\b", "", line, flags=re.I)
            candidate = FLAGS.sub("", line).strip(" —-–=:•")
            candidate, _ = strip_price_status_suffix(candidate)
            if pending and PRICE.fullmatch(candidate) and not UNAVAILABLE.search(line):
                flags = " ".join(FLAGS.findall(line))
                yield (pending + (" " + flags if flags and flags not in pending else "") + " — " + candidate,
                       max(pending_priority, priority))
                pending = None
                pending_priority = None
            else:
                if pending is not None:
                    yield pending, pending_priority
                pending = line
                pending_priority = priority
    if pending is not None:
        yield pending, pending_priority


def price_lines(documents):
    """Join product titles with a following retail price, including across messages."""
    for line, _ in _price_lines_with_priority(documents):
        yield line


def parse_documents(documents, default_currency="RUB", fallback_block="", document_priorities=None):
    items = OrderedDict()
    item_priorities = {}
    rejected = []
    context = ""
    section = ""
    section_sim = "unknown"
    closed = False
    for raw_line, priority in _price_lines_with_priority(documents, document_priorities):
        line = strip_decoration(APPLE_WARRANTY_NOTICE.sub("", strip_decoration(raw_line)))
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
            if is_header and (model or brand or is_accessory(line) or special):
                context = model or (brand if brand != "Apple" else line.strip(":"))
                if is_accessory(line):
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
        apple_accessory = own_brand == "Apple Accessories"
        accessory = (is_accessory(title) or (section == "Аксессуары" and not own_brand)) and not apple_accessory
        model = iphone_model(title)
        special = special_block(title)
        if not special and section in {"ASIS", "CPO"}:
            special = section
        publish_model = iphone_publish_block(model) if model else ""
        block = "Аксессуары" if accessory else (special or publish_model or own_brand or fallback_block or section or "Товары")
        sim = sim_type(title)
        if sim == "unknown":
            sim = section_sim if model else "unknown"
        item = Item(title, amount, currency, block, sim, accessory)
        item = resolve_iphone_sim(item)
        previous_priority = item_priorities.get(item.key)
        # Repeated identical rows in the same supplier document are
        # offers for the same variant; keep the lower price. A newer document
        # still replaces an older one even if its price has gone up.
        same_document_cheaper = (previous_priority == priority and item.price < items[item.key].price)
        if previous_priority is None or priority > previous_priority or same_document_cheaper:
            items[item.key] = item
            item_priorities[item.key] = priority
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
        item = resolve_iphone_sim(item)
        if MARKED_DOWN.search(strip_supplier_annotations(item.title)):
            continue
        if re.search(r"\bmonarch\b.*\bsamsung\b", item.title, re.I):
            continue
        # LEGO is excluded from the published price regardless of its source or block.
        if re.search(r"\blego\b", item.title, re.I) or item.block.casefold() == "lego":
            continue
        parent = "Xiaomi" if item.block in {"POCO", "Redmi"} else item.block
        if item.block in disabled or (parent != item.block and parent in disabled):
            continue
        if include_blocks and not (matches_any(item.block, include_blocks) or matches_any(parent, include_blocks)):
            continue
        if matches_any(item.block, exclude_blocks) or matches_any(parent, exclude_blocks):
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


def fixed_markup_for_item(item, settings, overrides=None):
    """Most specific matching category/model rule replaces the base fixed markup."""
    overrides = overrides or {}
    base = Decimal(str(overrides.get("markup", settings.markup)))
    if item.currency != "RUB":
        return base
    base = Decimal(str(overrides.get("markup_base_rub", base)))
    haystack = clean(" ".join((item.title, item.block, brand_of(item.title)))).casefold()
    best = None
    for index, rule in enumerate(overrides.get("markup_rules", [])):
        if not isinstance(rule, dict):
            continue
        pattern = str(rule.get("pattern", "")).strip()
        tokens = re.findall(r"[\w]+", pattern.casefold())
        if not tokens:
            continue
        # Device rules do not include accessories unless the rule names them.
        if (item.accessory or is_accessory(item.title)) and not is_accessory(pattern):
            continue
        if not all(re.search(r"(?<!\w)" + re.escape(token) + r"(?!\w)", haystack) for token in tokens):
            continue
        score = (len(tokens), sum(len(token) for token in tokens), index)
        if best is None or score > best[0]:
            best = (score, Decimal(str(rule["amount"])))
    return best[1] if best else base


def marked_price(item, settings, overrides=None):
    overrides = overrides or {}
    fixed = fixed_markup_for_item(item, settings, overrides)
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
    plain = FLAGS.sub("", clean(item.title)).strip().casefold()
    return size, plain


def dyson_model(title):
    """Identify the model named in a Dyson row, without guessing unknown models."""
    text = FLAGS.sub("", clean(title))
    # A part's product name starts with "насадка" (possibly after Dyson and a
    # modifier). Words in the middle can describe a complete appliance's kit,
    # e.g. "HD18 ... (насадки)" or "V15s ... без лазерной насадки".
    if re.search(
        r"^\s*(?:(?:отдельно|отдельная)\s+)?(?:dyson\s+)?"
        r"(?:(?:щелев\w*|лазерн\w*|сменн\w*|дополнительн\w*)\s+)?"
        r"(?:насадк\w*|attachments?\b|nozzles?\b)", text, re.I,
    ):
        return "Насадки"
    if re.search(r"подставк|\b(?:stand|dock)\b", text, re.I):
        return "Подставки"
    if re.search(r"фильтр|\bfilters?\b", text, re.I):
        return "Фильтры"
    if re.search(r"\b(?:travel\s+bag|storage\s+bag)\b|сумк", text, re.I):
        return "Сумки"
    match = re.search(r"\bgen\s*(\d+)\b", text, re.I)
    if match:
        return "Gen" + match.group(1)
    # HS09 V2 is an HS09 variant, not a V2 vacuum cleaner.
    match = re.search(r"\b(HS|HD|HT|TP|BP|PH|HU|CY)\s*(\d{1,2})([A-Z]?)\b", text, re.I)
    if match:
        if match.group(1).upper() == "BP" and match.group(2) in {"02", "03", "04", "06"}:
            return "Очистители воздуха"
        return match.group(1).upper() + match.group(2) + match.group(3).upper()
    # V15 SV47 is a V15 variant: use the consumer model before its SV code.
    match = re.search(r"\bV\s*(\d{1,2})(s?)\b", text, re.I)
    if match:
        return "V" + match.group(1) + match.group(2).lower()
    match = re.search(r"\b(SV)\s*(\d{1,2})([A-Z]?)\b", text, re.I)
    if match:
        return match.group(1).upper() + match.group(2) + match.group(3).upper()
    if re.search(r"\bG\s*1\b", text, re.I):
        return "G1"
    if re.search(r"\bvis\s*nav\b", text, re.I):
        return "Vis Nav"
    if re.search(r"\bpencilvac\b", text, re.I):
        return "Pencilvac"
    if re.search(r"\bclean\s*\+\s*wash\b", text, re.I):
        return "Clean+Wash"
    if re.search(r"\bspot\s*\+\s*scrub\b", text, re.I):
        return "Spot+Scrub"
    if re.search(r"\bpencil\s*wash\b", text, re.I):
        return "Pencil Wash"
    if re.search(r"\bbig\s*ball\b", text, re.I):
        return "Big Ball"
    if re.search(r"\bhush\s*jet\b", text, re.I):
        return "HushJet"
    accessory_names = r"hair\s*clips?|crest|comb|brush|paddle|гребен|расч[её]ск|щ[её]тк"
    if "+" in text and len(re.findall(accessory_names, text, re.I)) >= 2:
        return "Наборы аксессуаров"
    if re.search(r"\bhair\s*clips?\b|заколк", text, re.I):
        return "Заколки"
    if re.search(r"\bround\b.*(?:brush)?|кругл\w*\s+щ[её]тк", text, re.I):
        return "Круглые щётки"
    if re.search(r"\bpaddle\b|\bbrush\b|щ[её]тк", text, re.I):
        return "Щётки Paddle" if re.search(r"\bpaddle\b", text, re.I) else "Щётки"
    if re.search(r"\b(?:crest|comb)\b|гребен|расч[её]ск", text, re.I):
        return "Расчёски"
    return ""


def dyson_model_sort(title):
    model = dyson_model(title)
    natural = tuple(int(part) if part.isdigit() else part.casefold()
                    for part in re.split(r"(\d+)", model))
    return (bool(model), natural)


def samsung_a_s25_sort(item):
    plain = FLAGS.sub("", clean(item.title)).strip()
    a = samsung_a_model(plain)
    if a:
        return 0, a[0] % 10, (a[0], a[1], samsung_memory_sort(plain)), plain.casefold()
    if re.search(r"\bS\s*25\s*FE\b", plain, re.I):
        return 1, 0, 0, plain.casefold()
    if re.search(r"\bS\s*25\s*Edge\b", plain, re.I):
        return 1, 2, 0, plain.casefold()
    if re.search(r"\bS\s*25\s*Ultra\b", plain, re.I):
        return 1, 3, 0, plain.casefold()
    if re.search(r"\bS\s*25\b", plain, re.I):
        return 1, 1, 0, plain.casefold()
    return 9, 0, 0, plain.casefold()


def samsung_a_model(title):
    plain = FLAGS.sub("", clean(title)).strip()
    match = re.match(r"^(?:samsung\s+)?(?:galaxy\s+)?a\s*(\d{2,3})([a-z]?)\b", plain, re.I)
    if not match:
        return None
    number = int(match.group(1))
    return {175: 17, 376: 37, 576: 57}.get(number, number), match.group(2).casefold()


def samsung_a_lte(title):
    return bool(re.search(r"\b(?:4\s*[gг]|lte|a175)\b", title, re.I))


def samsung_a_offer_spelling(title):
    model = samsung_a_model(title)
    if model is None:
        return title
    # A trailing quantity in a supplier's row is their available stock.
    title = re.sub(r"\s+\d{2,}\s*шт\.?\s*$", "", title, flags=re.I)
    if model == (17, "") and samsung_a_lte(title):
        title = re.sub(r"\b(?:l\.\s*|light\s+)?blue\b", "Light Blue", title, flags=re.I)
        title = re.sub(r"\b(?:4\s*[gг]|lte)\b", "4G", title, flags=re.I)
    if model == (18, "") and not re.search(r"\b5\s*[gг]\b", title, re.I):
        title = re.sub(r"\b(?:gray|grey|silver)\b", "Silver", title, flags=re.I)
    if model == (27, ""):
        title = re.sub(r"\b(?:light\s+)?pink\b", "Light Pink", title, flags=re.I)
    if model == (37, ""):
        title = re.sub(r"\b(?:black|charcoal)\b", "Charcoal", title, flags=re.I)
    return title


def samsung_memory_sort(title):
    match = re.search(r"\b(\d{1,2})\s*/\s*(\d{1,4})\s*(tb|тб|gb|гб)?\b", title, re.I)
    if not match:
        return (999, 99999)
    ram, storage = int(match.group(1)), int(match.group(2))
    if (match.group(3) or "").casefold() in {"tb", "тб"}:
        storage *= 1024
    return (ram, storage)


def samsung_s_model(title):
    plain = FLAGS.sub("", clean(title))
    match = re.match(r"^(?:samsung\s+)?(?:galaxy\s+)?s\s*(\d{2,3})\s*(ultra\b|plus\b|\+|fe\b|edge\b)?", plain, re.I)
    if not match:
        return None
    variant = (match.group(2) or "").casefold()
    return (int(match.group(1)), {"": 0, "fe": 1, "+": 2, "plus": 2, "edge": 3, "ultra": 4}[variant])


def samsung_z_model(title):
    plain = FLAGS.sub("", clean(title))
    match = re.match(r"^(?:samsung\s+)?(?:galaxy\s+)?(?:z\s*)?(fold|flip)\s*(\d+)\b", plain, re.I)
    return (match.group(1).casefold(), int(match.group(2))) if match else None


def samsung_phone_group(title):
    model = samsung_a_model(title)
    if model:
        return ("a", model[0] % 10, *model)
    model = samsung_s_model(title)
    if model:
        return ("s", *model)
    model = samsung_z_model(title)
    if model:
        return ("z", *model)
    return None


def samsung_colour_sort(title):
    """Visual grouping only: never use approximate colour names to merge stock."""
    text = FLAGS.sub("", clean(title)).casefold().replace("ё", "е")
    colours = (
        ("black", r"\bblack\b|черн\w*|jet\s*black"),
        ("blue", r"\bblue\b|\bblueberry\b|голуб\w*|сини\w*"),
        ("green", r"\bgreen\b|\bpistachio\b|зелен\w*|фисташков\w*"),
        ("gray", r"\bgr[ae]y\b|\bgraphite\b|сер\w*|графит\w*"),
        ("purple", r"\bviolet\b|\bpurple\b|\blilac\b|фиолетов\w*|сиренев\w*"),
        ("white", r"\bwhite\b|бел\w*"),
        ("silver", r"\bsilver\b|серебр\w*"),
        ("gold", r"\bgold\b|золот\w*"),
        ("pink", r"\bpink\b|розов\w*"),
    )
    for label, pattern in colours:
        if re.search(pattern, text):
            return label
    return "~"


def model_group_key(item):
    """A stable model family key used only for visual spacing in rendered price lists."""
    plain = FLAGS.sub("", normal_title(item.title)).strip()

    if item.block.startswith("Samsung"):
        if re.search(r"\bWatch\b", plain, re.I):
            watch = re.match(r"^Samsung Watch (Ultra(?: 2)?|[6-9](?: Classic)?)\b",
                             display_title(item), re.I)
            if watch:
                return f"samsung watch {watch[1].casefold()}"
        samsung_model = samsung_phone_group(plain)
        if samsung_model:
            group = "samsung " + " ".join(map(str, samsung_model))
            if samsung_model[0] == "a" and samsung_a_lte(plain):
                return group + " 4g"
            return group

    if item.block == "MacBook / iMac":
        group = macbook_configuration(plain)
        if group:
            return "macbook " + " ".join(group)

    model = iphone_model(plain)
    if model:
        return model.casefold()

    watch = re.search(r"\b(?:Series\s*\d{1,2}|SE(?:\s*\d{1,2})?|Ultra(?:\s*\d{1,2})?)\b", plain, re.I)
    if watch and re.search(r"\bWatch\b", plain, re.I):
        return ("apple watch " + watch.group(0)).casefold()

    # Most phone/tablet price rows put RAM/storage immediately after the model.
    storage = re.search(r"\b(?:\d{1,2}/\d{2,4}(?:GB|TB)?|\d{2,4}(?:GB|TB))\b", plain, re.I)
    if storage:
        prefix = plain[:storage.start()].strip(" -—–/·")
        if prefix:
            return clean(prefix).casefold()

    known = [
        r"\b(?:Samsung\s+)?Buds\s*\d+(?:\s*FE)?\b",
        r"\b(?:Samsung\s+)?A\s*\d{2,3}[A-Za-z]*\b",
        r"\b(?:Samsung\s+)?S\s*\d{2}(?:\s*(?:FE|Edge|Ultra|\+|Plus))?\b",
        r"\b(?:Samsung\s+)?Tab\s*S\s*\d+(?:\s*Ultra)?\b",
        r"\b(?:Z\s*)?(?:Fold|Flip)\s*\d+\b",
        r"\b(?:Oura\s+Ring|Oura)\s*\d+\b",
        r"\bCoros\s+Pace\s*\d+\b",
        r"\bDJI\s+Osmo\s+(?:Pocket|Action)\s*\d+\b",
        r"\bInsta\s*360\s*[A-Z]*\d+\b|\bInsta360\s*[A-Z]*\d+\b",
        r"\b(?:Ray-Ban\s+Meta\s+)?(?:Wayfarer|Skyler)\b",
        r"\bR(?:O|Ø)DE\s+Wireless\s+(?:Me|Pro|Micro|GO(?:\s*\d+)?)\b",
        r"\b(?:AirPods|iPad)\s+[A-Za-z]*\s*\d+\b",
    ]
    for pattern in known:
        match = re.search(pattern, plain, re.I)
        if match:
            return clean(match.group(0)).casefold()

    # Generic numbered product families: keep through the first real model number,
    # plus a short modifier such as Pro/Ultra/FE/Edge/S3 when present.
    tokens = plain.split()
    for index, token in enumerate(tokens):
        if not re.search(r"\d", token):
            continue
        if index == 0 and re.fullmatch(r"Insta360", token, re.I):
            continue
        end = index + 1
        while end < len(tokens) and end <= index + 2 and re.fullmatch(
            r"(?:Pro|Max|Ultra|Plus|FE|Edge|Mini|Air|S\d+[A-Za-z]*|Gen\d+)", tokens[end], re.I
        ):
            end += 1
        return clean(" ".join(tokens[:end])).casefold()

    # If there is no reliable model signal, keep rows together rather than
    # inserting random gaps based on colour/accessory wording.
    return item.block.casefold()


def macbook_configuration(title):
    """Group clearly specified MacBooks by model, size, chip, RAM and storage."""
    model = re.search(r"\b(?:macbook\s+)?(air|pro|neo)\s*(13|14|15|16)\b", title, re.I)
    chip = re.search(r"\b(a18\s*pro|m\d+(?:\s*(?:pro|max))?)\b", title, re.I)
    memory = re.search(r"\b(\d{1,2})\s*/\s*(\d{3,4}|\d+\s*tb)(?:\s*gb)?\b", title, re.I)
    if not (model and chip and memory):
        return None
    return (model.group(1).casefold(), model.group(2), clean(chip.group()).casefold(),
            memory.group(1), re.sub(r"\s+", "", memory.group(2)).casefold())


def normalize_iphone_18_pro_max_color(title):
    if iphone_model(title).casefold() != "iphone 18 pro max":
        return title
    return re.sub(r"\b(?:glacier(?:\s+blue)?|blue)\b", "Glacier", title, flags=re.I)


def display_title(item):
    """Copyable model attributes, with the SIM kind only when it is known."""
    # Apply at render time so previously saved prices lose the note as well.
    title = normal_title(strip_supplier_annotations(item.title))
    if item.block.startswith("Samsung"):
        title, markets = samsung_market_parts(title)
        flags = r"((?:[\U0001F1E6-\U0001F1FF]{2}\s*)*)"
        title = re.sub(r"^" + flags + r"(?:samsung\s+galaxy|galaxy)\s+",
                       r"\1Samsung ", title, count=1, flags=re.I)
        title = re.sub(r"^" + flags + r"(?=[AS]\s*\d{2,3}[a-z]?\b)",
                       r"\1Samsung ", title, count=1, flags=re.I)
        title = re.sub(r"^(?=(?:z\s*)?(?:fold|flip)\s*\d)", "Samsung ", title, flags=re.I)
        title = re.sub(r"\bZ\s*(?=fold|flip)", "Z ", title, flags=re.I)
        for sku, name in (("A175", "A17 4G"), ("A376", "A37 5G"), ("A576", "A57 5G")):
            title = re.sub(r"\bSamsung\s+" + sku + r"\b", "Samsung " + name, title, flags=re.I)
        title = samsung_tab_name(samsung_charger_name(samsung_watch_name(title)))
        if re.match(r"^Samsung\s+A\s*(?:27|37|57)\b", title, re.I):
            title = offer_network_spelling(offer_color_spelling(title))
            if re.match(r"^Samsung\s+A\s*57\b", title, re.I):
                title = re.sub(r"\bDark Blue\b", "Navy", title, flags=re.I)
        title = samsung_a_offer_spelling(title)
        if samsung_s_model(title) and not item.accessory:
            title = samsung_s_offer_spelling(offer_color_spelling(title), markets)
        if samsung_z_model(title) and not item.accessory:
            title = re.sub(r"\(\s*([A-Za-z]+(?:\s+[A-Za-z]+){0,2})\s*\)", r"\1", offer_network_spelling(title))
        title = re.sub(r"\b(\d{1,2}\s*/\s*\d{2,4})\s*(?:GB|ГБ)\b", r"\1", title, flags=re.I)
        title = clean(re.sub(r"\(\s*\)", "", title))
        if markets:
            title += " " + " ".join(dict.fromkeys(markets))
    if item.block == "Huawei" and not item.accessory:
        title = huawei_offer_name(title)
    if iphone_model(title) and not item.accessory:
        title = normalize_iphone_18_pro_max_color(title)
        title = FLAGS.sub("", title)
        title = re.sub(r"\bapple\s+(?=iphone\b)", "", title, flags=re.I)
        # Display storage consistently; 256GB and 256 identify the same size.
        title = re.sub(r"\b(64|128|256|512)\s*(?:gb|гб)\b", r"\1", title, flags=re.I)
        # The supplier may place the SIM marker anywhere in the name.
        sim_marker = (r"(?<!\w)(?:(?:1\s*)?(?:nano\s*)?sim\s*(?:\+|/|&|and|и)\s*e[\s-]*sim|"
                      r"e[\s-]*sim\s*(?:\+|/|&|and|и)\s*(?:1\s*)?(?:nano\s*)?sim|"
                      r"(?:2|dual|2x)\s*(?:nano\s*)?sim|две\s*sim|"
                      r"e[\s-]*sim|есим|(?:1\s*)?(?:nano\s*)?sim|physical\s*sim|физическ\w*\s*sim)(?!\w)")
        title = re.sub(sim_marker, "", title, flags=re.I)
        title = re.sub(r"\(\s*\)", "", title)
        title = clean(title).strip(" ·-")
        if SIM_LABELS.get(item.sim):
            title += " " + SIM_LABELS[item.sim]
    return title


def iphone_bundle_key(block):
    """Stable physical iPhone generation key.

    11–15 intentionally remain one legacy message. Every generation from 16
    onward gets its own message automatically, so iPhone 18/19/etc require no
    code change when suppliers start listing them.
    """
    if block == "iPhone Air":
        return "iphone17"
    match = re.fullmatch(
        r"iPhone\s+(\d{1,2})(?:e)?(?:\s+(?:Plus|Pro(?:\s+Max)?|Mini|Air|Duo))?",
        block, re.I,
    )
    if not match:
        return ""
    number = int(match.group(1))
    if 11 <= number <= 15:
        return "iphone11-15"
    if number >= 16:
        return f"iphone{number}"
    return ""


def iphone_bundle_section_rank(title):
    """Predictable model order inside any iPhone generation message."""
    name = clean(title).casefold()
    if name == "iphone air":
        return (1, name)
    match = re.fullmatch(
        r"iphone\s+(\d{1,2})(e)?(?:\s+(mini|air|plus|pro(?:\s+max)?))?",
        name, re.I,
    )
    if not match:
        return (20, name)
    suffix = (match.group(3) or "").casefold()
    if match.group(2):
        suffix = "e"
    rank = {
        "e": 0,
        "air": 1,
        "": 2,
        "mini": 3,
        "plus": 4,
        "pro": 5,
        "pro max": 6,
    }.get(suffix, 10)
    return (rank, name)


def iphone_bundle_header(bundle, titles):
    if bundle == "iphone11-15":
        return "iPhone 11 / 12 / 13 / 14 / 15"
    if bundle == "iphone16":
        base = "iPhone 16 / 16 Plus / 16 Pro / 16 Pro Max"
        if any(title.casefold() == "iphone 16e" for title in titles):
            base += " / 16e"
        return base
    if bundle.startswith("iphone") and bundle[6:].isdigit():
        generation = int(bundle[6:])
        ordered = sorted(titles, key=iphone_bundle_section_rank)
        labels = []
        for title in ordered:
            name = clean(title)
            lower = name.casefold()
            if lower == "iphone air" and generation == 17:
                labels.append("17 Air")
            elif lower.startswith("iphone "):
                labels.append(name[7:])
            else:
                labels.append(name)
        return "iPhone " + " / ".join(labels)
    return " / ".join(titles)


def physical_brand_label(title):
    name = clean(title)
    lower = name.casefold()
    if lower.startswith("iphone"):
        return "iPhone"
    if lower.startswith(("airpods", "apple watch", "ipad", "macbook", "imac", "mac mini", "mac studio", "apple tv", "airtag", "apple")):
        return "Apple"
    if lower.startswith("samsung"):
        return "Samsung"
    if lower.startswith("ray-ban"):
        return "Ray-Ban Meta"
    if lower.startswith("dji") or lower.startswith("insta360"):
        return "DJI / Insta360"
    if lower.startswith("harman") or lower.startswith("bose"):
        return "Harman Kardon / Bose"
    if lower.startswith("kodak") or lower.startswith("fujifilm"):
        return "Kodak / Fujifilm"
    return name


def product_storage_key(item):
    """Storage part for RAM/storage products such as Galaxy S26 12/256 or 16/1TB."""
    title = FLAGS.sub("", clean(item.title))
    match = re.search(r"\b\d{1,2}\s*/\s*(\d{1,4})\s*(GB|TB)?\b", title, re.I)
    if not match:
        return ""
    factor = 1024 if (match.group(2) or "").upper() == "TB" else 1
    return f"{int(match.group(1)) * factor}GB"


def iphone_storage_rank(item):
    key = iphone_storage_key(item)
    match = re.fullmatch(r"(\d+)(GB|TB)", key, re.I)
    if not match:
        return 10**9
    value = int(match.group(1))
    if match.group(2).upper() == "TB":
        value *= 1024
    return value


def render_block_lines(block, block_items, settings, overrides, closed=False):
    if closed:
        return ["Продажи закрыты"]

    has_activation = any(activation_state(item.title) != "unknown" or iphone_model(item.title) for item in block_items)

    def condition(item):
        value = activation_state(item.title)
        return "inactive" if value == "unknown" else value

    lines = []
    statuses = sorted({condition(item) for item in block_items}, key=lambda status: CONDITION_ORDER[status])
    for status_index, status in enumerate(statuses):
        status_items = [item for item in block_items if condition(item) == status]
        if status_index and lines and lines[-1] != "":
            lines.append("")
        # Ordinary/non-activated stock is the default and needs no noisy heading.
        # Only explicitly active stock gets its own visible section.
        if has_activation and status == "active":
            lines.append("<b>— Актив —</b>")
            lines.append("")

        last_sim = None
        last_samsung_section = None
        last_dyson_model = None
        last_model_key = None
        last_iphone_model = None
        last_storage = None
        sorter = samsung_a_s25_sort if block == "Samsung A + S25" else item_sort

        def full_sort(item):
            if block in SAMSUNG_NETWORK_BLOCKS or block == "Huawei":
                return item_sort(replace(item, title=display_title(item)))
            if block == "Dyson":
                return (dyson_model_sort(item.title), item_sort(item))
            if block == "MacBook / iMac":
                group = macbook_configuration(FLAGS.sub("", item.title))
                return (0 if group else 1, group or (), item_sort(item))
            if block == "Samsung":
                model = samsung_a_model(item.title)
                if model:
                    return (0, (model[0] % 10, *model), samsung_a_lte(item.title), samsung_memory_sort(item.title),
                            samsung_colour_sort(item.title), item_sort(item))
                return (1, (), False, (0, 0), "", item_sort(item))
            if block in {"Samsung A + S25", "Samsung S26", "Samsung Fold / Flip"}:
                model = samsung_phone_group(item.title)
                lte = bool(model and model[0] == "a" and samsung_a_lte(item.title))
                title = display_title(item) if model and model[0] == "s" else item.title
                return (0 if model else 1, model or (), lte,
                        samsung_memory_sort(title), samsung_colour_sort(title), item_sort(item))
            if block == "Honor":
                memory = re.search(r"\b(\d{1,2})\s*/\s*(\d{2,4})\s*(GB|TB|ГБ|ТБ)?\b", item.title, re.I)
                if memory:
                    storage = int(memory.group(2)) * (1024 if (memory.group(3) or "").upper() in {"TB", "ТБ"} else 1)
                    model = clean(FLAGS.sub("", item.title[:memory.start()])).casefold()
                    return (0, model, storage, int(memory.group(1)), item_sort(item))
                return (1, "", 0, 0, item_sort(item))
            if block in {"CPO", "ASIS"}:
                name = iphone_model(item.title).casefold()
                natural = tuple(int(part) if part.isdigit() else part
                                for part in re.split(r"(\d+)", name))
                return (SIM_ORDER.get(item.sim, 99), natural, iphone_storage_rank(item), item_sort(item))
            if iphone_model(item.title):
                return (SIM_ORDER.get(item.sim, 99), iphone_storage_rank(item), sorter(item))
            return (SIM_ORDER.get(item.sim, 99), 0, sorter(item))

        for item in sorted(status_items, key=full_sort):
            if block == "Dyson":
                current_model = dyson_model(item.title)
                if current_model != last_dyson_model:
                    if current_model:
                        if lines and lines[-1] != "":
                            lines.append("")
                        lines.append("<b>— " + html.escape(current_model) + " —</b>")
                        lines.append("")
                    last_dyson_model = current_model

            if block == "Samsung A + S25":
                samsung_section = "Samsung S25" if re.search(r"\bS\s*25\b", item.title, re.I) else "Samsung A"
                if samsung_section != last_samsung_section:
                    if last_samsung_section is not None and lines and lines[-1] != "":
                        lines.append("")
                    lines.append("<b>— " + samsung_section + " —</b>")
                    lines.append("")
                    last_samsung_section = samsung_section

            if iphone_model(item.title) and item.sim != last_sim:
                label = SIM_LABELS.get(item.sim)
                if label:
                    if lines and lines[-1] != "":
                        lines.append("")
                    lines.append("<b>— " + label + " —</b>")
                    lines.append("")
                last_storage = None
                last_iphone_model = None
                last_sim = item.sim

            if iphone_model(item.title):
                storage = iphone_storage_key(item)
                model = iphone_model(item.title).casefold()
                changed = ((last_iphone_model is not None and model != last_iphone_model)
                           or (storage and last_storage is not None and storage != last_storage))
                if (changed
                        and lines and lines[-1] != "" and not lines[-1].startswith("<b>")):
                    lines.append("")
                last_iphone_model = model
                if storage:
                    last_storage = storage
            else:
                model_key = model_group_key(item)
                model_changed = last_model_key is not None and model_key != last_model_key
                if (model_changed and lines and lines[-1] != "" and not lines[-1].startswith("<b>")):
                    lines.append("")
                if model_changed:
                    last_storage = None
                if block.startswith("Samsung"):
                    storage = product_storage_key(item)
                    if (storage and last_storage is not None and storage != last_storage
                            and lines and lines[-1] != "" and not lines[-1].startswith("<b>")):
                        lines.append("")
                    if storage:
                        last_storage = storage
                last_model_key = model_key

            value = ("По запросу" if item.key in overrides.get("_request_only_keys", set())
                     else price_text(marked_price(item, settings, overrides), item.currency))
            row = display_title(item) + " — " + value
            line = "<code>" + html.escape(row) + "</code>"
            if units(line) > 3000:
                raise ValueError("Слишком длинное наименование товара")
            lines.append(line)

    while lines and lines[-1] == "":
        lines.pop()
    return lines


def section_chunks(title, lines, limit=3200):
    """Split a logical block only when Telegram forces us to; never add Part labels."""
    result = []
    chunk = []
    size = 0
    dyson_heading = None
    previous_samsung_model = None
    for index, line in enumerate(lines):
        # Keep an entire Samsung A-model together when it fits a message.
        if title in {"Samsung", "Samsung A + S25", "Samsung S26", "Samsung Fold / Flip"} and line.startswith("<code>"):
            current = samsung_phone_group(html.unescape(re.sub(r"^<code>", "", line)))
            if current and current != previous_samsung_model:
                end = index + 1
                while end < len(lines):
                    next_line = lines[end]
                    if next_line.startswith("<code>"):
                        next_model = samsung_phone_group(html.unescape(re.sub(r"^<code>", "", next_line)))
                        if next_model != current:
                            break
                    elif next_line.startswith("<b>"):
                        break
                    end += 1
                group_size = sum(units(value) + 1 for value in lines[index:end])
                if chunk and group_size <= limit and size + group_size > limit:
                    while chunk and chunk[-1] == "":
                        chunk.pop()
                    result.append({"title": title, "body": "\n".join(chunk), "part": len(result)})
                    chunk, size = [], 0
            previous_samsung_model = current
        if title == "Dyson" and line.startswith("<b>— "):
            dyson_heading = line
        line_size = units(line) + 1
        if chunk and size + line_size > limit:
            # Keep a Dyson category heading with its first product when the
            # Telegram message limit falls between the heading and that row.
            tail = []
            if title == "Dyson" and line.startswith("<code>"):
                last = len(chunk) - 1
                while last >= 0 and chunk[last] == "":
                    last -= 1
                if last > 0 and chunk[last].startswith("<b>— "):
                    tail = chunk[last:]
                    chunk = chunk[:last]
                elif dyson_heading:
                    # If one model spans several posts, every continuation
                    # still identifies the model for the reader.
                    tail = [dyson_heading, ""]
            while chunk and chunk[-1] == "":
                chunk.pop()
            result.append({"title": title, "body": "\n".join(chunk), "part": len(result)})
            chunk = tail
            size = sum(units(value) + 1 for value in chunk)
        chunk.append(line)
        size += line_size
    if chunk:
        while chunk and chunk[-1] == "":
            chunk.pop()
        result.append({"title": title, "body": "\n".join(chunk), "part": len(result)})
    return result or [{"title": title, "body": "", "part": 0}]


def build_physical_message(sections, bundle=""):
    if not sections:
        return ""
    if len(sections) == 1 and not bundle:
        section = sections[0]
        heading = physical_brand_label(section["title"])
        if heading != section["title"]:
            return (
                "<b>" + html.escape(heading) + "</b>\n\n"
                + "<b>— " + html.escape(section["title"]) + " —</b>\n\n"
                + section["body"]
            )
        return "<b>" + html.escape(section["title"]) + "</b>\n\n" + section["body"]

    titles = []
    for section in sections:
        if section["title"] not in titles:
            titles.append(section["title"])

    if bundle:
        heading = iphone_bundle_header(bundle, titles)
    else:
        brands = []
        for title in titles:
            label = physical_brand_label(title)
            if label not in brands:
                brands.append(label)
        heading = " • ".join(brands)

    bodies = []
    previous_title = None
    for section in sections:
        if section["title"] == previous_title:
            bodies[-1] += "\n" + section["body"]
        else:
            bodies.append("<b>— " + html.escape(section["title"]) + " —</b>\n\n" + section["body"])
        previous_title = section["title"]
    # Three newlines = a visible empty line between logical sections.
    return "<b>" + html.escape(heading) + "</b>\n\n" + "\n\n\n".join(bodies)


def split_bundle_sections(sections, bundle, limit=3950):
    batches = []
    current = []
    for section in sections:
        candidate = current + [section]
        if current and units(build_physical_message(candidate, bundle)) > limit:
            batches.append(current)
            current = [section]
        else:
            current = candidate
    if current:
        batches.append(current)
    return batches


def physical_section_family(title):
    """Keep major ecosystems together instead of mixing them with unrelated brands."""
    if title in {"CPO", "ASIS", "Аксессуары"}:
        return "isolated"
    label = physical_brand_label(title)
    if label == "Apple":
        return "apple"
    if label == "Samsung":
        return "samsung"
    return "other"


def pack_physical_sections(sections, limit=3950):
    """Pack by ecosystem: Apple together, Samsung together, other small brands separately."""
    # Saved custom block order may interleave Apple/Samsung with unrelated brands.
    # Compact these ecosystems at their first occurrence before packing so a tiny
    # Apple TV/Mac mini section can never be stranded behind Honor/Oura/etc.
    compacted = []
    emitted_families = set()
    for section in sections:
        if iphone_bundle_key(section["title"]):
            compacted.append(section)
            continue
        family = physical_section_family(section["title"])
        if family in {"apple", "samsung"}:
            if family in emitted_families:
                continue
            emitted_families.add(family)
            compacted.extend([
                entry for entry in sections
                if not iphone_bundle_key(entry["title"]) and physical_section_family(entry["title"]) == family
            ])
        else:
            compacted.append(section)
    sections = compacted

    physical = []
    pending = []
    pending_family = ""
    seen_iphone_bundles = set()

    def flush_pending():
        nonlocal pending, pending_family
        if pending:
            physical.append(("", pending))
            pending = []
            pending_family = ""

    index = 0
    while index < len(sections):
        section = sections[index]
        bundle = iphone_bundle_key(section["title"])
        if bundle:
            flush_pending()
            if bundle not in seen_iphone_bundles:
                seen_iphone_bundles.add(bundle)
                bundle_sections = [entry for entry in sections if iphone_bundle_key(entry["title"]) == bundle]
                if bundle.startswith("iphone"):
                    bundle_sections.sort(key=lambda entry: iphone_bundle_section_rank(entry["title"]))
                for batch in split_bundle_sections(bundle_sections, bundle, limit):
                    physical.append((bundle, batch))
            index += 1
            continue

        family = physical_section_family(section["title"])
        if family == "isolated":
            flush_pending()
            physical.append(("", [section]))
            index += 1
            continue

        # Never let Apple/Samsung spill into Honor, Kodak, Marshall, Oura, etc.
        if pending and family != pending_family:
            flush_pending()

        candidate = pending + [section]
        if pending and units(build_physical_message(candidate)) > limit:
            flush_pending()
            pending = [section]
            pending_family = family
        else:
            pending = candidate
            pending_family = family
        index += 1

    flush_pending()
    return physical


def physical_order_label(title):
    """Stable name of one *physical* Telegram price message.

    A multi-model iPhone heading is represented by its generation (iPhone 17,
    iPhone 18, ...). A split overflow message containing one model keeps that
    exact model name (for example iPhone 17 Pro Max), so it can be moved and
    linked independently.
    """
    name = clean(title)
    if not name:
        return ""

    # Preserve occurrence suffixes used when one model itself spans 2+ posts.
    suffix = ""
    occurrence = re.fullmatch(r"(.+?)\s+·\s+(\d+)", name)
    if occurrence:
        name = clean(occurrence.group(1))
        suffix = " · " + occurrence.group(2)

    lower = name.casefold()
    if lower.startswith("iphone"):
        numbers = [int(value) for value in re.findall(r"\b(\d{1,2})\b", name)]

        # Only headings with several model sections are collapsed to the
        # generation button/order key. Single-model overflow pages remain exact.
        if "/" in name and numbers:
            if all(11 <= value <= 15 for value in numbers):
                name = "iPhone 11–15"
            else:
                name = f"iPhone {numbers[0]}"
        elif lower == "iphone air":
            name = "iPhone 17"

    return name + suffix


def physical_message_labels(contents):
    """Unique labels for physical posts, in their current physical order."""
    counts = {}
    labels = []
    for content in contents:
        base = physical_order_label(rendered_page_title(content))
        count = counts.get(base, 0) + 1
        counts[base] = count
        labels.append(base if count == 1 else f"{base} · {count}")
    return labels


class PhysicalPages(OrderedDict):
    """Keep occurrence labels attached to their pages when pages are moved."""

    def __init__(self):
        super().__init__()
        self.labels = {}


def physical_page_labels(pages):
    labels = getattr(pages, "labels", {})
    if all(key in labels for key in pages):
        return [labels[key] for key in pages]
    return physical_message_labels(list(pages.values()))


def merge_physical_order(previous, current):
    """Prepend new pages, retain missing slots and match stock-dependent headings.

    Exact matches win before matching a renamed packed heading. This prevents a
    new overflow page from taking an existing page's place just because both
    pages contain the same brand or iPhone generation.
    """
    def unique(values):
        result, seen = [], set()
        for value in values:
            name = physical_order_label(value)
            if name and name.casefold() not in seen:
                result.append(name)
                seen.add(name.casefold())
        return result

    previous, current = unique(previous), unique(current)
    ranks = {name.casefold(): index for index, name in enumerate(previous)}
    matched = {}
    pending = []
    for name in current:
        index = ranks.get(name.casefold())
        if index is None:
            pending.append(name)
        else:
            matched[index] = name

    def members(name):
        occurrence = re.fullmatch(r"(.+?)\s+·\s+(\d+)", name)
        base = occurrence.group(1) if occurrence else name
        part = occurrence.group(2) if occurrence else "1"
        bundle = iphone_bundle_key(base)
        names = {bundle} if bundle else {value.strip().casefold() for value in base.split("•")}
        return names, part

    new = []
    for name in pending:
        names, part = members(name)
        candidates = []
        for index, old in enumerate(previous):
            if index in matched:
                continue
            old_names, old_part = members(old)
            common = len(names & old_names) if part == old_part else 0
            if common:
                candidates.append((common / len(names | old_names), common, -index, index))
        if candidates:
            matched[max(candidates)[-1]] = name
        else:
            new.append(name)
    ordered = [matched.get(index, name) for index, name in enumerate(previous)]

    def family(name):
        base = re.sub(r"\s+·\s+\d+$", "", name).strip()
        bundle = iphone_bundle_key(base)
        if bundle:
            return bundle
        lower = base.casefold()
        if lower.startswith("samsung"):
            return "samsung"
        if lower.startswith("apple"):
            return "apple"
        return lower

    unrelated = []
    for name in new:
        relatives = [i for i, existing in enumerate(ordered) if family(existing) == family(name)]
        if relatives:
            ordered.insert(relatives[-1] + 1, name)
        else:
            unrelated.append(name)
    return unrelated + ordered


def rendered_page_title(content):
    """Visible top heading of one final Telegram price message."""
    match = re.match(r"<b>\s*(?:—\s*)?(.*?)(?:\s*—)?\s*</b>", content or "")
    return html.unescape(match.group(1)).strip() if match else "Прайс"


def samsung_network_block(item):
    if item.block not in {"Samsung", "Samsung Tab S"} or item.accessory:
        return item.block
    title = display_title(item)
    networks = tablet_networks(title)
    if re.match(r"^Samsung Watch (?:Ultra(?: 2)?|[6-9](?: Classic)?) \d+mm\b", title):
        return "Samsung Watch LTE" if "LTE" in networks else "Samsung Watch Bluetooth"
    if title.startswith("Samsung Tab ") and len(networks) == 1:
        return f"Samsung Tab {networks[0]}"
    return item.block


def render_blocks(items, settings, overrides=None, closed=False):
    """Render logical sections, pack them, then order the real Telegram messages."""
    overrides = overrides or {}
    groups = OrderedDict()
    for item in items:
        groups.setdefault(item.block if closed else samsung_network_block(item), []).append(item)

    # Old block_order contained parser-level names such as Mac mini, AirPods or
    # Samsung S26. Those are no longer Telegram messages, so it must not drive
    # publication order. The operator now orders the final physical headings.
    names = ordered_blocks(groups, [])
    logical_sections = []
    for block in names:
        lines = render_block_lines(block, groups[block], settings, overrides, closed=closed)
        chunk_limit = 1800 if physical_section_family(block) == "apple" else (3700 if block == "Samsung" else 3200)
        logical_sections.extend(section_chunks(block, lines, limit=chunk_limit))

    built = []
    for bundle, sections in pack_physical_sections(logical_sections):
        content = build_physical_message(sections, bundle)
        seed = bundle + "|" + "|".join(f'{section["title"]}#{section["part"]}' for section in sections)
        key = hashlib.sha256(seed.encode()).hexdigest()[:16] + ":0"
        built.append((key, content, rendered_page_title(content)))

    # Assign a unique stable label to every physical post *before* custom
    # ordering. This keeps duplicate overflow pages addressable as "· 2", "· 3".
    labels = physical_message_labels([entry[1] for entry in built])
    built = [
        (key, content, title, label)
        for (key, content, title), label in zip(built, labels)
    ]

    preferred = merge_physical_order(overrides.get("physical_order", []), labels)
    if preferred:
        rank = {name.casefold(): index for index, name in enumerate(preferred)}
        built = [entry for _, entry in sorted(
            enumerate(built),
            key=lambda pair: (
                rank.get(pair[1][3].casefold(), len(rank)),
                pair[0],
            ),
        )]

    pages = PhysicalPages()
    for key, content, _title, label in built:
        pages[key] = content
        pages.labels[key] = label
    return pages
