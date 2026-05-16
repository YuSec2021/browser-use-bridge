from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from browser_use_bridge.browser.events import DomUpdatedEvent


class DOMElement(BaseModel):
    model_config = ConfigDict(extra="allow")

    index: int
    tag_name: str
    text: str = ""
    is_interactive: bool = True
    attributes: dict[str, str] = Field(default_factory=dict)
    x: float = 0
    y: float = 0
    width: float = 0
    height: float = 0


class DOMState(BaseModel):
    model_config = ConfigDict(extra="allow")

    url: str = ""
    title: str = ""
    elements: list[DOMElement] = Field(default_factory=list)


class DOMTreeSerializer:
    """Compact serializer for LLM-facing element references."""

    def serialize(self, state: DOMState) -> str:
        rows = self._stage_render_elements(
            self._stage_limit_noise(
                self._stage_sort_elements(
                    self._stage_keep_interactive(
                        self._stage_normalize_labels(state.elements)
                    )
                )
            )
        )
        return "\n".join(rows)

    def _stage_normalize_labels(self, elements: list[DOMElement]) -> list[DOMElement]:
        normalized: list[DOMElement] = []
        for element in elements:
            label = " ".join(element.text.split())
            if label:
                normalized.append(element.model_copy(update={"text": label}))
        return normalized

    def _stage_keep_interactive(self, elements: list[DOMElement]) -> list[DOMElement]:
        return [element for element in elements if element.is_interactive]

    def _stage_sort_elements(self, elements: list[DOMElement]) -> list[DOMElement]:
        return sorted(elements, key=lambda element: element.index)

    def _stage_limit_noise(self, elements: list[DOMElement]) -> list[DOMElement]:
        return elements[:200]

    def _stage_render_elements(self, elements: list[DOMElement]) -> list[str]:
        rows: list[str] = []
        for element in elements:
            rows.append(f"[{element.index}] <{element.tag_name}> {element.text}")
        return rows


class DomWatchdog:
    def __init__(self, session: Any) -> None:
        self.session = session
        self.is_running = False
        self._attached_pages: set[int] = set()

    async def start(self) -> None:
        self.start_now()

    async def stop(self) -> None:
        self.is_running = False

    def start_now(self) -> None:
        if self.is_running:
            return
        self.is_running = True
        context = getattr(self.session, "_context", None)
        if context is not None:
            context.on("page", self._attach_page)
            for page in context.pages:
                self._attach_page(page)

    def _attach_page(self, page: Any) -> None:
        page_key = id(page)
        if page_key in self._attached_pages:
            return
        self._attached_pages.add(page_key)
        page.on("framenavigated", lambda frame: self._schedule_update(page, frame))
        page.on("load", lambda: self._schedule_update(page, None))

    def _schedule_update(self, page: Any, frame: Any | None) -> None:
        if not self.is_running:
            return
        if frame is not None and frame != getattr(page, "main_frame", None):
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._emit_update(page))

    async def _emit_update(self, page: Any) -> None:
        await asyncio.sleep(0)
        title = ""
        with contextlib.suppress(Exception):
            title = await page.title()
        self.session.event_bus.emit(
            DomUpdatedEvent(session=self.session, url=getattr(page, "url", ""), title=title)
        )


class DomService:
    def __init__(self, session: Any, watch: bool = False) -> None:
        self.session = session
        self._watchdog: DomWatchdog | None = None
        if watch:
            self._watchdog = DomWatchdog(session)
            self._watchdog.start_now()

    async def get_state(self) -> DOMState:
        page = self._active_page()
        with contextlib.suppress(Exception):
            await page.wait_for_load_state("load", timeout=5000)
        title = await page.title()
        url = getattr(page, "url", "")
        cdp_payload = await self._capture_cdp_state(page)
        raw_elements = await page.evaluate(_ELEMENT_EXTRACTION_SCRIPT)
        elements = [
            DOMElement.model_validate({**raw, "index": index})
            for index, raw in enumerate(raw_elements)
        ]
        return DOMState(
            url=url,
            title=title,
            elements=elements,
            accessibility_tree=cdp_payload.get("accessibility_tree"),
            dom_snapshot=cdp_payload.get("dom_snapshot"),
        )

    async def capture_screenshot(self, highlight_elements: bool = False) -> bytes:
        page = self._active_page()
        if not highlight_elements:
            return await page.screenshot(type="png")

        state = await self.get_state()
        await page.evaluate(_ADD_HIGHLIGHT_SCRIPT, [element.model_dump() for element in state.elements])
        try:
            return await page.screenshot(type="png")
        finally:
            with contextlib.suppress(Exception):
                await page.evaluate(_REMOVE_HIGHLIGHT_SCRIPT)

    async def _capture_cdp_state(self, page: Any) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        context = getattr(page, "context", None)
        if context is None:
            return payload

        session = None
        with contextlib.suppress(Exception):
            session = await context.new_cdp_session(page)
        if session is None:
            return payload

        with contextlib.suppress(Exception):
            payload["accessibility_tree"] = await session.send("Accessibility.getFullAXTree")
        with contextlib.suppress(Exception):
            payload["dom_snapshot"] = await session.send(
                "DOMSnapshot.captureSnapshot",
                {"computedStyles": ["display", "visibility", "opacity"]},
            )
        with contextlib.suppress(Exception):
            await session.detach()
        return payload

    def _active_page(self) -> Any:
        self.session._ensure_started()
        return self.session.session_manager.get_active_tab().page


_ELEMENT_EXTRACTION_SCRIPT = r"""
() => {
  const interactiveSelector = [
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

  const clean = (value) => (value || '').replace(/\s+/g, ' ').trim();

  const labelledByText = (element) => {
    const ids = clean(element.getAttribute('aria-labelledby')).split(' ').filter(Boolean);
    return clean(ids.map((id) => document.getElementById(id)?.textContent || '').join(' '));
  };

  const associatedLabel = (element) => {
    if (element.id) {
      const explicit = document.querySelector(`label[for="${CSS.escape(element.id)}"]`);
      if (explicit) return clean(explicit.textContent);
    }
    const label = element.closest('label');
    return label ? clean(label.textContent) : '';
  };

  const labelFor = (element) => {
    const tag = element.tagName.toLowerCase();
    const aria = clean(element.getAttribute('aria-label'));
    if (aria) return aria;
    const labelledBy = labelledByText(element);
    if (labelledBy) return labelledBy;
    const label = associatedLabel(element);
    if (label) return label;
    const text = clean(element.innerText || element.textContent);
    if (text) return text;
    const placeholder = clean(element.getAttribute('placeholder'));
    if (placeholder) return placeholder;
    const title = clean(element.getAttribute('title'));
    if (title) return title;
    const alt = clean(element.getAttribute('alt'));
    if (alt) return alt;
    if (['input', 'textarea', 'select'].includes(tag)) {
      const value = clean(element.value);
      if (value) return value;
    }
    return clean(element.getAttribute('name') || element.id || tag);
  };

  const isVisible = (element) => {
    if (!element.isConnected) return false;
    if (element.closest('[hidden], [aria-hidden="true"], template')) return false;
    const style = window.getComputedStyle(element);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rects = element.getClientRects();
    if (!rects.length) return false;
    const rect = element.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return false;
    if (rect.bottom < 0 || rect.right < 0) return false;
    if (rect.top > window.innerHeight || rect.left > window.innerWidth) return false;
    return true;
  };

  const elements = Array.from(document.querySelectorAll(interactiveSelector));
  return elements
    .filter(isVisible)
    .map((element) => {
      const rect = element.getBoundingClientRect();
      const attributes = {};
      for (const name of ['id', 'name', 'role', 'type', 'href', 'aria-label', 'placeholder', 'title']) {
        const value = element.getAttribute(name);
        if (value) attributes[name] = value;
      }
      return {
        tag_name: element.tagName.toLowerCase(),
        text: labelFor(element),
        is_interactive: true,
        attributes,
        x: rect.x,
        y: rect.y,
        width: rect.width,
        height: rect.height
      };
    })
    .filter((element) => element.text.length > 0);
}
"""

_ADD_HIGHLIGHT_SCRIPT = r"""
(elements) => {
  const existing = document.getElementById('__browser_use_dom_highlights__');
  if (existing) existing.remove();
  const layer = document.createElement('div');
  layer.id = '__browser_use_dom_highlights__';
  layer.style.position = 'fixed';
  layer.style.inset = '0';
  layer.style.zIndex = '2147483647';
  layer.style.pointerEvents = 'none';
  layer.style.contain = 'strict';
  document.documentElement.appendChild(layer);

  for (const element of elements) {
    const box = document.createElement('div');
    box.style.position = 'absolute';
    box.style.left = `${Math.max(0, element.x)}px`;
    box.style.top = `${Math.max(0, element.y)}px`;
    box.style.width = `${Math.max(1, element.width)}px`;
    box.style.height = `${Math.max(1, element.height)}px`;
    box.style.border = '3px solid #ff2d55';
    box.style.background = 'rgba(255, 214, 10, 0.22)';
    box.style.boxSizing = 'border-box';

    const badge = document.createElement('div');
    badge.textContent = String(element.index);
    badge.style.position = 'absolute';
    badge.style.left = '0';
    badge.style.top = '0';
    badge.style.transform = 'translateY(-100%)';
    badge.style.background = '#ff2d55';
    badge.style.color = '#ffffff';
    badge.style.font = '700 12px/16px sans-serif';
    badge.style.minWidth = '18px';
    badge.style.height = '18px';
    badge.style.textAlign = 'center';
    badge.style.padding = '1px 4px';
    box.appendChild(badge);
    layer.appendChild(box);
  }
}
"""

_REMOVE_HIGHLIGHT_SCRIPT = r"""
() => {
  const layer = document.getElementById('__browser_use_dom_highlights__');
  if (layer) layer.remove();
}
"""


__all__ = ["DOMElement", "DOMState", "DOMTreeSerializer", "DomService", "DomWatchdog"]
