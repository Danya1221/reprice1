from pathlib import Path
import runpy

runpy.run_path("apply_dual_telegram_accounts.py", run_name="__main__")

p = Path("main.py")
s = p.read_text(encoding="utf-8")
old = '''        if service.reload_requested:
            continue
        service.wake.clear()
        try:
            await asyncio.wait_for(service.wake.wait(), timeout=retry_seconds)
        except asyncio.TimeoutError:
            pass
'''
new = '''        if service.reload_requested:
            continue
        service.wake.clear()
        wake_task = asyncio.create_task(service.wake.wait())
        stop_task = asyncio.create_task(service.stop_event.wait())
        try:
            done, pending = await asyncio.wait(
                {wake_task, stop_task}, timeout=retry_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            if stop_task in done and service.stop_event.is_set():
                return
        finally:
            for task in (wake_task, stop_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(wake_task, stop_task, return_exceptions=True)
'''
if s.count(old) != 1:
    raise SystemExit(f"retry wait block count={s.count(old)}")
s = s.replace(old, new, 1)
p.write_text(s, encoding="utf-8")
