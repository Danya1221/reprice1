import re
import runpy

_original_subn = re.subn


def _literal_subn(pattern, repl, string, count=0, flags=0):
    if isinstance(repl, str):
        return _original_subn(pattern, lambda _match: repl, string, count=count, flags=flags)
    return _original_subn(pattern, repl, string, count=count, flags=flags)


re.subn = _literal_subn
runpy.run_path("apply_price_layout_finish2.py", run_name="__main__")
