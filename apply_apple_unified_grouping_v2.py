from pathlib import Path
import runpy

runpy.run_path("apply_apple_unified_grouping.py", run_name="__main__")

p = Path("prices.py")
s = p.read_text(encoding="utf-8")
old = '''    computer = apple_computer_block(title)
    if computer:
        return computer
    apple_accessory = apple_accessory_block(title)
    if apple_accessory:
        return apple_accessory
'''
new = '''    apple_accessory = apple_accessory_block(title)
    if apple_accessory:
        return apple_accessory
    computer = apple_computer_block(title)
    if computer:
        return computer
'''
if s.count(old) != 1:
    raise SystemExit(f"product priority block count={s.count(old)}")
s = s.replace(old, new, 1)
p.write_text(s, encoding="utf-8")
