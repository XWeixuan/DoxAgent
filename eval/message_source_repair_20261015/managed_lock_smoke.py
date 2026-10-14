"""Isolated real-Chromium A/B, no production Profile, network, or browser attach."""
import asyncio
import json
import os
import subprocess
import tempfile
from pathlib import Path

from playwright.async_api import async_playwright
from doxagent.site_strategy.runtime import OwnerFileLock, clear_profile_runtime_links


async def main():
    os.environ['DISPLAY'] = ':97'
    display = subprocess.Popen(['/usr/bin/Xvfb', ':97', '-screen', '0', '1440x1000x24', '-nolisten', 'tcp'])
    await asyncio.sleep(0.5)
    with tempfile.TemporaryDirectory(prefix='dox-managed-redeploy-') as temporary:
        root = Path(temporary)
        profile = root / 'profile'
        profile.mkdir()
        for name in ('SingletonLock', 'SingletonSocket', 'SingletonCookie'):
            (profile / name).symlink_to('retired-container-198484')
        lock = OwnerFileLock(root / '.writer.lock')
        lock.acquire()
        try:
            async with async_playwright() as playwright:
                try:
                    context = await playwright.chromium.launch_persistent_context(
                        str(profile), headless=False, chromium_sandbox=True, timeout=10000)
                except Exception as exc:
                    message = str(exc)
                    print(json.dumps({'before': type(exc).__name__, 'singleton_error':
                        'ProcessSingleton' in message or 'another computer' in message
                        or 'profile appears to be in use' in message}), flush=True)
                else:
                    await context.close()
                    raise AssertionError('stale-lock baseline unexpectedly launched')
                clear_profile_runtime_links(profile)
                context = await playwright.chromium.launch_persistent_context(
                    str(profile), headless=False, chromium_sandbox=True, timeout=10000)
                page = await context.new_page()
                await page.set_content('<html><body>isolated-recovery-ok</body></html>')
                assert await page.locator('body').inner_text() == 'isolated-recovery-ok'
                await context.close()
                print(json.dumps({'after': 'browser_started_and_closed',
                    'production_profile_used': False}), flush=True)
        finally:
            lock.release()
            display.terminate()
            display.wait(timeout=5)


asyncio.run(main())
