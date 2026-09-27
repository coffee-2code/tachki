"""Настоящий браузер для площадок, которые не пускают обычные запросы (Авто.ру, Авито).

Открывается окно Chrome (или встроенного Chromium) со своим профилем в папке browser_profile —
куки сохраняются, поэтому капча появляется реже. Если площадка всё-таки спросила «я не робот»,
бот пишет об этом и ждёт, пока вы решите капчу в этом окне, затем продолжает.
"""
from __future__ import annotations

import asyncio
import logging
import random
from pathlib import Path
from typing import Awaitable, Callable, Optional

from .config import Settings
from .listings import SiteUnavailable

log = logging.getLogger(__name__)
PROFILE_DIR = Path("browser_profile")
Notify = Callable[[str], Awaitable[None]]
IsCaptcha = Callable[[str, str], bool]  # (url, html) → капча?


class Browser:
    def __init__(self, s: Settings):
        self.s = s
        self.notify: Optional[Notify] = None
        self._pw = None
        self._ctx = None
        self._pages: dict[str, object] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._start_lock = asyncio.Lock()
        self._failed = ""  # браузер не запустился — второй раз не пробуем

    async def _say(self, text: str) -> None:
        log.warning(text)
        if self.notify:
            await self.notify(text)

    async def _ensure(self):
        async with self._start_lock:
            if self._ctx:
                return self._ctx
            if self._failed:
                raise SiteUnavailable(self._failed)
            try:
                from playwright.async_api import async_playwright
            except ImportError as exc:
                raise SiteUnavailable("не установлен браузерный модуль: pip install playwright") from exc
            self._pw = await async_playwright().start()
            opts = dict(
                user_data_dir=str(PROFILE_DIR), headless=self.s.browser_headless, locale="ru-RU",
                viewport={"width": 1366, "height": 900},
                args=["--disable-blink-features=AutomationControlled"],
            )
            errors = []
            for channel in ([self.s.browser_channel] if self.s.browser_channel else []) + [None]:
                try:
                    self._ctx = await self._pw.chromium.launch_persistent_context(
                        **opts, **({"channel": channel} if channel else {}))
                    break
                except Exception as exc:  # noqa: BLE001 — Chrome не установлен → пробуем встроенный Chromium
                    first = next((ln for ln in str(exc).splitlines() if ln.strip()), type(exc).__name__)
                    errors.append(f"{channel or 'chromium'}: {first[:160]}")
            if not self._ctx:
                log.warning("Браузер не запустился:\n%s", "\n".join(errors))
                self._failed = "не запустился браузер (выполните: python -m playwright install chromium)"
                raise SiteUnavailable("не запустился браузер (выполните: python -m playwright install chromium)")
            return self._ctx

    async def get(self, site: str, url: str, is_captcha: IsCaptcha) -> str:
        ctx = await self._ensure()
        lock = self._locks.setdefault(site, asyncio.Lock())
        async with lock:  # на каждой площадке — одна вкладка и по одному запросу
            page = self._pages.get(site)
            if page is None or page.is_closed():
                page = await ctx.new_page()
                self._pages[site] = page
            await asyncio.sleep(random.uniform(self.s.browser_delay_min, self.s.browser_delay_max))
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                await page.wait_for_timeout(random.randint(1200, 2500))
                await page.mouse.wheel(0, random.randint(800, 1600))  # как человек: прокрутили список
                await page.wait_for_timeout(random.randint(500, 1200))
                html = await page.content()
            except Exception as exc:  # noqa: BLE001
                raise SiteUnavailable(f"не открылась страница ({type(exc).__name__})") from exc

            if is_captcha(page.url, html):
                if self.s.browser_headless:
                    raise SiteUnavailable("просит капчу, а браузер скрыт (BROWSER_HEADLESS=true)")
                await self._say(f"{site}: решите капчу в окне браузера на компьютере — жду до "
                                f"{self.s.captcha_wait_sec // 60} мин.")
                waited = 0
                while waited < self.s.captcha_wait_sec:
                    await asyncio.sleep(3)
                    waited += 3
                    try:
                        if not is_captcha(page.url, await page.content()):
                            break
                    except Exception:  # noqa: BLE001 — страница перезагружается после капчи
                        continue
                else:
                    raise SiteUnavailable("капчу не решили вовремя")
                await self._say(f"{site}: капча пройдена, продолжаю.")
                await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                await page.wait_for_timeout(2000)
                html = await page.content()
                if is_captcha(page.url, html):
                    raise SiteUnavailable("снова капча")
            return html

    async def close(self) -> None:
        try:
            if self._ctx:
                await self._ctx.close()
        finally:
            if self._pw:
                await self._pw.stop()
            self._ctx = self._pw = None
