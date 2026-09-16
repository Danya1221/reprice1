from pathlib import Path


def replace_once(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected 1 match, got {count}")
    return text.replace(old, new, 1)


# runtime.py: account 1 reads every supplier; account 2 reads only primary/HI.
p = Path("runtime.py")
s = p.read_text(encoding="utf-8")
s = replace_once(
    s,
    '''    def _slot_sources(self, slot):
        # Read every configured supplier from both accounts. A static feed is safe
        # to read twice; a supplier bot may intentionally return account-specific prices.
        return self.settings.sources
''',
    '''    def _slot_sources(self, slot):
        sources = tuple(self.settings.sources)
        if int(slot) == 1:
            return sources
        # Account 2 exists only to compare the account-specific HI price.
        # Never query the remaining suppliers from the second Telegram account.
        return sources[:1]
''',
    "slot source selection",
)
s = replace_once(
    s,
    '''            for slot in (1, 2):
                if slot == 2 and not self.account_configured(2):
                    continue
                value = cache.get(self.source_cache_key(source, slot), {})
''',
    '''            for slot in (1, 2):
                if slot == 2 and not self.account_configured(2):
                    continue
                if source not in self._slot_sources(slot):
                    continue
                value = cache.get(self.source_cache_key(source, slot), {})
''',
    "ignore stale secondary source cache",
)
p.write_text(s, encoding="utf-8")


# control_botapi.py: make the scope explicit in the UI.
p = Path("control_botapi.py")
s = p.read_text(encoding="utf-8")
s = replace_once(
    s,
    '''            "Один и тот же бот поставщика читается с обоих аккаунтов. "
            "Если цены отличаются, в итоговый прайс попадёт меньшая закупочная цена."
''',
    '''            "Аккаунт 1 читает все источники. Аккаунт 2 читает только HI (Поставщик 1). "
            "Если цена HI отличается, в итоговый прайс попадёт меньшая закупочная цена."
''',
    "accounts menu scope",
)
p.write_text(s, encoding="utf-8")


# Regression test: account 2 must never query supplier 2.
p = Path("tests/test_dual_accounts.py")
s = p.read_text(encoding="utf-8")
marker = '''    async def test_same_hi_variant_from_two_accounts_uses_lower_price(self):
'''
test = '''    async def test_second_account_reads_only_primary_hi_supplier(self):
        other = Source("@other", label="OTHER")
        self.settings.sources = (self.settings.sources[0], other)
        service = SyncService(self.client1, self.settings, self.state)
        service.attach_client(self.client2, 2)

        primary = [reader.source.peer for reader in service.readers_for_slot(1)]
        secondary = [reader.source.peer for reader in service.readers_for_slot(2)]
        active = [(slot, reader.source.peer) for slot, reader in service.active_reader_entries()]

        self.assertEqual(primary, ["@hi", "@other"])
        self.assertEqual(secondary, ["@hi"])
        self.assertEqual(active, [(1, "@hi"), (1, "@other"), (2, "@hi")])

'''
if test not in s:
    if marker not in s:
        raise SystemExit("dual account test insertion point missing")
    s = s.replace(marker, test + marker, 1)
p.write_text(s, encoding="utf-8")
