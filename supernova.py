"""Read the Supernova XLSX export as a separate, short-lived supplier snapshot."""
import io
import re
import zipfile
from decimal import Decimal, InvalidOperation

from openpyxl import load_workbook

from prices import Item, iphone_model, iphone_publish_block, is_accessory, normal_title, product_block, sim_type, special_block

MAX_BYTES = 5_000_000
MAX_UNCOMPRESSED = 20_000_000
FRESH_SECONDS = 600


def parse_supernova(data):
    if not isinstance(data, bytes) or not data or len(data) > MAX_BYTES:
        raise ValueError("Пустой Excel-файл или размер больше 5 МБ")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if sum(entry.file_size for entry in archive.infolist()) > MAX_UNCOMPRESSED:
                raise ValueError("Распакованный Excel больше 20 МБ")
        book = load_workbook(io.BytesIO(data), read_only=True, data_only=True, keep_links=False)
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
        raise ValueError("Не удалось открыть Excel Supernova") from exc
    try:
        if "Прайс" not in book.sheetnames:
            raise ValueError("В Excel нет листа «Прайс»")
        columns = None
        items = {}
        seen = set()
        unavailable = set()
        count = 0
        for number, row in enumerate(book["Прайс"].iter_rows(values_only=True), 1):
            if number > 5000:
                raise ValueError("В Excel больше 5000 строк")
            if columns is None:
                cells = {str(value).strip().casefold(): index for index, value in enumerate(row) if value is not None}
                if {"наименование", "кол-во", "цена от 1 шт"} <= cells.keys():
                    columns = cells
                continue
            def cell(name):
                index = columns[name]
                return row[index] if index < len(row) else None
            title, stock, price = cell("наименование"), cell("кол-во"), cell("цена от 1 шт")
            if title is None and stock is None and price is None:
                continue
            if isinstance(title, str) and title.strip().casefold().startswith("файл выгружен для:") and stock is None and price is None:
                continue
            if not isinstance(title, str) or not 2 <= len(title.strip()) <= 240:
                raise ValueError(f"Строка {number}: неверное название")
            quantity = str(stock).strip() if stock is not None else ""
            if not re.fullmatch(r"\d+\+?", quantity) or int(quantity.rstrip("+")) > 1_000_000:
                raise ValueError(f"Строка {number}: неверный остаток")
            try:
                if isinstance(price, bool):
                    raise ValueError("boolean price")
                amount = Decimal(str(price).strip().replace(" ", "").replace("\u00a0", "").replace(",", "."))
                if not amount.is_finite() or not 0 < amount <= 10_000_000:
                    raise ValueError("invalid price")
            except (InvalidOperation, ValueError, TypeError) as exc:
                raise ValueError(f"Строка {number}: неверная цена от 1 шт") from exc
            name = normal_title(title.strip())
            own_brand = product_block(name)
            accessory = is_accessory(name) and own_brand != "Apple Accessories"
            iphone = iphone_model(name)
            block = ("Аксессуары" if accessory else
                     special_block(name) or (iphone_publish_block(iphone) if iphone else "") or own_brand or "Товары")
            item = Item(name, amount, "RUB", block, sim_type(name), accessory)
            if item.key in seen:
                raise ValueError(f"Строка {number}: повтор товара")
            seen.add(item.key)
            count += 1
            if int(quantity.rstrip("+")) > 0:
                items[item.key] = item
            else:
                unavailable.add(item.key)
        if columns is None or not count or not items:
            raise ValueError("Не найдены товары и нужные колонки Supernova")
        return list(items.values()), unavailable
    finally:
        book.close()
