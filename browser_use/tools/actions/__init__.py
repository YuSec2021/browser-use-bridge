from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from pydantic import BaseModel, ConfigDict

from browser_use.dom import DomService


class NavigateParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str


class ClickParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int


class InputTextParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int
    text: str


class ScrollParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: int = 0


class ExtractContentParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GoBackParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DoneParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    success: bool = True
    text: str = ""


async def navigate(browser_session: Any, url: str) -> dict[str, Any]:
    await browser_session.navigate(url)
    return {"ok": True, "url": await browser_session.get_current_url()}


async def click(browser_session: Any, index: int) -> dict[str, Any]:
    page = _active_page(browser_session)
    download_future = _download_future(page)
    point = await page.evaluate(_CLICK_SCRIPT, index)
    await page.mouse.click(point["x"], point["y"])
    if download_future is not None:
        with contextlib.suppress(Exception):
            download = await asyncio.wait_for(download_future, timeout=1)
            await _save_download(page, download)
    await _settle_page(page)
    return {"ok": True, "index": index}


async def input_text(browser_session: Any, index: int, text: str) -> dict[str, Any]:
    page = _active_page(browser_session)
    await page.evaluate(_INPUT_TEXT_SCRIPT, {"index": index, "text": text})
    await _settle_page(page)
    return {"ok": True, "index": index, "text": text}


async def scroll(browser_session: Any, amount: int = 0) -> dict[str, Any]:
    page = _active_page(browser_session)
    await page.evaluate("(amount) => window.scrollBy(0, amount)", amount)
    return {"ok": True, "amount": amount}


async def extract_content(browser_session: Any) -> dict[str, Any]:
    page = _active_page(browser_session)
    dom_state = await DomService(browser_session).get_state()
    content = await page.evaluate(_EXTRACT_CONTENT_SCRIPT)
    return {
        "ok": True,
        "url": dom_state.url,
        "title": dom_state.title,
        "content": content,
        "elements": [element.model_dump() for element in dom_state.elements],
    }


async def go_back(browser_session: Any) -> dict[str, Any]:
    page = _active_page(browser_session)
    await page.go_back(wait_until="load")
    await browser_session.session_manager.refresh_tab(browser_session.session_manager.get_active_tab())
    return {"ok": True, "url": await browser_session.get_current_url()}


async def done(success: bool = True, text: str = "") -> dict[str, Any]:
    return {"ok": True, "done": True, "success": success, "text": text}


def _active_page(browser_session: Any) -> Any:
    browser_session._ensure_started()
    return browser_session.session_manager.get_active_tab().page


async def _settle_page(page: Any) -> None:
    with contextlib.suppress(Exception):
        await page.wait_for_load_state("load", timeout=3000)


def _download_future(page: Any) -> asyncio.Future[Any] | None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    future: asyncio.Future[Any] = loop.create_future()

    def on_download(download: Any) -> None:
        if not future.done():
            future.set_result(download)

    with contextlib.suppress(Exception):
        page.once("download", on_download)
    return future


async def _save_download(page: Any, download: Any) -> None:
    session = getattr(page, "_browser_use_session", None)
    downloads_path = getattr(getattr(session, "profile", None), "downloads_path", None)
    if not downloads_path:
        return
    from pathlib import Path

    target = Path(downloads_path) / download.suggested_filename
    target.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(Exception):
        await download.save_as(str(target))


_CLICK_SCRIPT = r"""
(index) => {
  const element = elementAtIndex(index);
  if (!element) throw new Error(`No element at index ${index}`);
  element.scrollIntoView({block: 'center', inline: 'center'});
  const rect = element.getBoundingClientRect();
  return {x: rect.left + rect.width / 2, y: rect.top + rect.height / 2};

  function elementAtIndex(index) {
    const selector = [
      'button',
      'a[href]',
      'input:not([type="hidden"])',
      'textarea',
      'select',
      'summary',
      '[contenteditable="true"]',
      '[role="button"]',
      '[role="link"]',
      '[role="textbox"]',
      '[role="checkbox"]',
      '[role="radio"]',
      '[role="switch"]',
      '[role="combobox"]',
      '[onclick]',
      '[tabindex]:not([tabindex="-1"])'
    ].join(',');
    const visible = (element) => {
      if (!element.isConnected) return false;
      if (element.closest('[hidden], [aria-hidden="true"], template')) return false;
      const style = window.getComputedStyle(element);
      if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
      const rect = element.getBoundingClientRect();
      return rect.width > 0 && rect.height > 0 && rect.bottom >= 0 && rect.right >= 0 &&
        rect.top <= window.innerHeight && rect.left <= window.innerWidth;
    };
    return Array.from(document.querySelectorAll(selector)).filter(visible)[index] || null;
  }
}
"""

_INPUT_TEXT_SCRIPT = r"""
({index, text}) => {
  const element = elementAtIndex(index);
  if (!element) throw new Error(`No input element at index ${index}`);
  element.scrollIntoView({block: 'center', inline: 'center'});
  element.focus();
  if ('value' in element) {
    element.value = text;
  } else {
    element.textContent = text;
  }
  element.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: 'insertText', data: text}));
  element.dispatchEvent(new Event('change', {bubbles: true}));

  function elementAtIndex(index) {
    const selector = [
      'button',
      'a[href]',
      'input:not([type="hidden"])',
      'textarea',
      'select',
      'summary',
      '[contenteditable="true"]',
      '[role="button"]',
      '[role="link"]',
      '[role="textbox"]',
      '[role="checkbox"]',
      '[role="radio"]',
      '[role="switch"]',
      '[role="combobox"]',
      '[onclick]',
      '[tabindex]:not([tabindex="-1"])'
    ].join(',');
    const visible = (element) => {
      if (!element.isConnected) return false;
      if (element.closest('[hidden], [aria-hidden="true"], template')) return false;
      const style = window.getComputedStyle(element);
      if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
      const rect = element.getBoundingClientRect();
      return rect.width > 0 && rect.height > 0 && rect.bottom >= 0 && rect.right >= 0 &&
        rect.top <= window.innerHeight && rect.left <= window.innerWidth;
    };
    return Array.from(document.querySelectorAll(selector)).filter(visible)[index] || null;
  }
}
"""

_EXTRACT_CONTENT_SCRIPT = r"""
() => {
  const clean = (value) => (value || '').replace(/\s+/g, ' ').trim();
  const fields = Array.from(document.querySelectorAll('input, textarea, select'))
    .map((element) => clean(element.value || element.getAttribute('aria-label') || element.name || element.id))
    .filter(Boolean);
  return clean([document.title, document.body?.innerText || '', ...fields].join('\n'));
}
"""


__all__ = [
    "ClickParams",
    "DoneParams",
    "ExtractContentParams",
    "GoBackParams",
    "InputTextParams",
    "NavigateParams",
    "ScrollParams",
    "click",
    "done",
    "extract_content",
    "go_back",
    "input_text",
    "navigate",
    "scroll",
]
