from __future__ import annotations

import asyncio
import base64
import contextlib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from browser_use.browser.events import DomUpdatedEvent
from browser_use.vision import AnnotatedScreenshot, BoundingBox


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


class AnnotationConfig(BaseModel):
    """Visual styling for DOM annotation overlays and image fallbacks."""

    border_color: str = "#ff2d55"
    fill_color: str = "#ffd60a"
    fill_opacity: float = 0.22
    label_background: str = "#ff2d55"
    label_color: str = "#ffffff"
    font_size: int = 12
    stroke_width: int = 3
    overlay_id: str = "__browser_use_dom_annotation__"


class DomAnnotationResult(AnnotatedScreenshot):
    """Annotated screenshot result returned by the Pillow fallback."""


class DomAnnotator:
    """Draw and manage visual annotations for indexed DOM elements."""

    def __init__(self, page: Any | None, config: AnnotationConfig | None = None) -> None:
        self.page = page
        self.config = config or AnnotationConfig()
        self.visible = False
        self._last_elements: list[dict[str, Any]] = []

    async def highlight_range(self, elements: list[Any], count: int) -> None:
        selected = self._normalize_elements(elements)[: max(0, count)]
        await self._render_overlay(selected)

    async def highlight_element(self, elements: list[Any], index: int) -> None:
        selected = [element for element in self._normalize_elements(elements) if element.get("index") == index]
        await self._render_overlay(selected)

    async def highlight_all(self, elements: list[Any]) -> None:
        await self._render_overlay(self._normalize_elements(elements))

    async def show(self) -> None:
        if not self._last_elements:
            self.visible = False
            return
        await self._inject_overlay(self._last_elements)
        self.visible = True

    async def hide(self) -> None:
        if self.page is not None:
            await self.page.evaluate(
                _DOM_ANNOTATION_HIDE_SCRIPT,
                {"overlay_id": self.config.overlay_id},
            )
        self.visible = False

    async def extract_bounding_boxes(self, elements: list[Any]) -> list[BoundingBox]:
        normalized = self._normalize_elements(elements)
        if self.page is None:
            return [self._bounding_box_from_element(element) for element in normalized]

        raw_boxes = await self.page.evaluate(
            _DOM_ANNOTATION_EXTRACT_SCRIPT,
            {"mode": "extract", "elements": normalized},
        )
        return [BoundingBox.model_validate(box) for box in raw_boxes or []]

    async def annotate_image_path(
        self,
        image_path: str | Path,
        elements: list[Any],
        output_path: str | Path | None = None,
    ) -> DomAnnotationResult:
        source = Path(image_path)
        target = Path(output_path) if output_path is not None else source.with_name(f"{source.stem}-annotated{source.suffix}")
        boxes = [self._bounding_box_from_element(element) for element in self._normalize_elements(elements)]
        width, height = self._draw_with_pillow(source, target, boxes)
        image_bytes = target.read_bytes()
        return DomAnnotationResult(
            image_path=str(target),
            content_type=self._content_type(target),
            base64_data=base64.b64encode(image_bytes).decode("ascii"),
            mode="viewport",
            width=width,
            height=height,
            bounding_boxes=boxes,
        )

    async def _render_overlay(self, elements: list[dict[str, Any]]) -> None:
        self._last_elements = elements
        await self._inject_overlay(elements)
        self.visible = True

    async def _inject_overlay(self, elements: list[dict[str, Any]]) -> None:
        if self.page is None:
            raise ValueError("DomAnnotator requires a page for in-page annotation")
        await self.page.evaluate(
            _DOM_ANNOTATION_SHOW_SCRIPT,
            {
                "config": self.config.model_dump(),
                "elements": elements,
            },
        )

    def _normalize_elements(self, elements: list[Any]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for element in elements:
            if isinstance(element, BaseModel):
                data = element.model_dump()
            elif isinstance(element, dict):
                data = dict(element)
            else:
                data = {
                    "index": getattr(element, "index"),
                    "tag_name": getattr(element, "tag_name", ""),
                    "text": getattr(element, "text", ""),
                    "x": getattr(element, "x", 0),
                    "y": getattr(element, "y", 0),
                    "width": getattr(element, "width", 0),
                    "height": getattr(element, "height", 0),
                }
            data["index"] = int(data.get("index", 0))
            data["x"] = float(data.get("x", 0) or 0)
            data["y"] = float(data.get("y", 0) or 0)
            data["width"] = float(data.get("width", 0) or 0)
            data["height"] = float(data.get("height", 0) or 0)
            data["label"] = data.get("label") or data.get("text") or data.get("tag_name") or str(data["index"])
            normalized.append(data)
        return normalized

    def _bounding_box_from_element(self, element: dict[str, Any]) -> BoundingBox:
        return BoundingBox(
            index=int(element["index"]),
            x=float(element.get("x", 0) or 0),
            y=float(element.get("y", 0) or 0),
            width=float(element.get("width", 0) or 0),
            height=float(element.get("height", 0) or 0),
            label=str(element.get("label") or element.get("text") or element.get("tag_name") or element["index"]),
            text=element.get("text"),
        )

    def _draw_with_pillow(self, source: Path, target: Path, boxes: list[BoundingBox]) -> tuple[int, int]:
        from PIL import Image, ImageDraw, ImageFont

        target.parent.mkdir(parents=True, exist_ok=True)
        image = Image.open(source).convert("RGB")
        draw = ImageDraw.Draw(image)
        border = self._rgb(self.config.border_color)
        fill = self._blend(self._rgb(self.config.fill_color), self.config.fill_opacity)
        label_background = self._rgb(self.config.label_background)
        label_color = self._rgb(self.config.label_color)
        font = ImageFont.load_default()

        for box in boxes:
            x1 = max(0, float(box.x))
            y1 = max(0, float(box.y))
            x2 = max(x1 + 1, x1 + float(box.width))
            y2 = max(y1 + 1, y1 + float(box.height))
            draw.rectangle(
                (x1, y1, x2, y2),
                fill=fill,
                outline=border,
                width=max(1, int(self.config.stroke_width)),
            )

            label = str(box.index)
            try:
                text_box = draw.textbbox((0, 0), label, font=font)
                text_width = text_box[2] - text_box[0]
                text_height = text_box[3] - text_box[1]
            except AttributeError:
                text_width, text_height = draw.textsize(label, font=font)
            padding = 3
            label_height = max(self.config.font_size + 4, text_height + padding * 2)
            label_width = max(label_height, text_width + padding * 2)
            label_y = y1 if y1 < label_height else y1 - label_height
            draw.rectangle(
                (x1, label_y, x1 + label_width, label_y + label_height),
                fill=label_background,
            )
            draw.text(
                (x1 + padding, label_y + max(1, (label_height - text_height) / 2 - 1)),
                label,
                fill=label_color,
                font=font,
            )

        image.save(target)
        return image.size

    def _content_type(self, path: Path) -> str:
        suffix = path.suffix.lower()
        if suffix in {".jpg", ".jpeg"}:
            return "image/jpeg"
        if suffix == ".png":
            return "image/png"
        return "application/octet-stream"

    def _rgb(self, color: str) -> tuple[int, int, int]:
        if color.startswith("#") and len(color) == 7:
            return tuple(int(color[index : index + 2], 16) for index in (1, 3, 5))  # type: ignore[return-value]
        named = {"white": (255, 255, 255), "black": (0, 0, 0), "red": (255, 0, 0)}
        return named.get(color.lower(), (0, 0, 0))

    def _blend(self, color: tuple[int, int, int], opacity: float) -> tuple[int, int, int]:
        alpha = min(1.0, max(0.0, float(opacity)))
        return tuple(round((channel * alpha) + (255 * (1 - alpha))) for channel in color)


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

_DOM_ANNOTATION_SHOW_SCRIPT = r"""
(payload) => {
  const config = payload.config || {};
  const overlayId = config.overlay_id || '__browser_use_dom_annotation__';
  const namespace = 'http://www.w3.org/2000/svg';
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

  const existing = document.getElementById(overlayId);
  if (existing && existing.__browserUseDomAnnotationObserver) {
    existing.__browserUseDomAnnotationObserver.disconnect();
  }
  if (existing) existing.remove();

  const svg = document.createElementNS(namespace, 'svg');
  svg.id = overlayId;
  svg.setAttribute('aria-hidden', 'true');
  svg.style.position = 'absolute';
  svg.style.left = '0';
  svg.style.top = '0';
  svg.style.width = `${Math.max(document.documentElement.scrollWidth, document.body?.scrollWidth || 0, window.innerWidth)}px`;
  svg.style.height = `${Math.max(document.documentElement.scrollHeight, document.body?.scrollHeight || 0, window.innerHeight)}px`;
  svg.style.pointerEvents = 'none';
  svg.style.zIndex = '2147483647';
  svg.style.overflow = 'visible';
  document.documentElement.appendChild(svg);

  const byIndex = new Map((payload.elements || []).map((element) => [Number(element.index), element]));

  const findLiveRect = (index) => {
    const candidates = Array.from(document.querySelectorAll(interactiveSelector));
    const live = candidates[index];
    if (!live || !live.isConnected) return null;
    const rect = live.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return null;
    return {
      x: rect.left + window.scrollX,
      y: rect.top + window.scrollY,
      width: rect.width,
      height: rect.height
    };
  };

  const draw = () => {
    svg.replaceChildren();
    svg.style.width = `${Math.max(document.documentElement.scrollWidth, document.body?.scrollWidth || 0, window.innerWidth)}px`;
    svg.style.height = `${Math.max(document.documentElement.scrollHeight, document.body?.scrollHeight || 0, window.innerHeight)}px`;

    for (const element of byIndex.values()) {
      const liveRect = findLiveRect(Number(element.index));
      const rect = liveRect || {
        x: Number(element.x || 0) + (Math.abs(Number(element.x || 0)) < window.innerWidth ? window.scrollX : 0),
        y: Number(element.y || 0) + (Math.abs(Number(element.y || 0)) < window.innerHeight ? window.scrollY : 0),
        width: Number(element.width || 0),
        height: Number(element.height || 0)
      };
      if (rect.width <= 0 || rect.height <= 0) continue;

      const group = document.createElementNS(namespace, 'g');
      const box = document.createElementNS(namespace, 'rect');
      box.setAttribute('x', String(Math.max(0, rect.x)));
      box.setAttribute('y', String(Math.max(0, rect.y)));
      box.setAttribute('width', String(Math.max(1, rect.width)));
      box.setAttribute('height', String(Math.max(1, rect.height)));
      box.setAttribute('rx', '2');
      box.setAttribute('fill', config.fill_color || '#ffd60a');
      box.setAttribute('fill-opacity', String(config.fill_opacity ?? 0.22));
      box.setAttribute('stroke', config.border_color || '#ff2d55');
      box.setAttribute('stroke-width', String(config.stroke_width || 3));
      group.appendChild(box);

      const labelText = String(element.index);
      const fontSize = Number(config.font_size || 12);
      const labelWidth = Math.max(20, labelText.length * fontSize * 0.62 + 8);
      const labelHeight = fontSize + 8;
      const labelY = rect.y < labelHeight ? rect.y : rect.y - labelHeight;
      const label = document.createElementNS(namespace, 'rect');
      label.setAttribute('x', String(Math.max(0, rect.x)));
      label.setAttribute('y', String(Math.max(0, labelY)));
      label.setAttribute('width', String(labelWidth));
      label.setAttribute('height', String(labelHeight));
      label.setAttribute('rx', '3');
      label.setAttribute('fill', config.label_background || '#ff2d55');
      group.appendChild(label);

      const text = document.createElementNS(namespace, 'text');
      text.textContent = labelText;
      text.setAttribute('x', String(Math.max(0, rect.x) + 4));
      text.setAttribute('y', String(Math.max(0, labelY) + labelHeight - 5));
      text.setAttribute('fill', config.label_color || '#ffffff');
      text.setAttribute('font-size', String(fontSize));
      text.setAttribute('font-family', 'system-ui, -apple-system, BlinkMacSystemFont, sans-serif');
      text.setAttribute('font-weight', '700');
      group.appendChild(text);

      svg.appendChild(group);
    }
  };

  let frame = 0;
  const scheduleDraw = () => {
    if (frame) return;
    frame = window.requestAnimationFrame(() => {
      frame = 0;
      draw();
    });
  };

  const observer = new MutationObserver(scheduleDraw);
  observer.observe(document.documentElement, {
    attributes: true,
    childList: true,
    subtree: true,
    characterData: true
  });
  window.addEventListener('resize', scheduleDraw, {passive: true});
  window.addEventListener('scroll', scheduleDraw, {passive: true});
  svg.__browserUseDomAnnotationObserver = observer;
  svg.__browserUseDomAnnotationCleanup = () => {
    observer.disconnect();
    window.removeEventListener('resize', scheduleDraw);
    window.removeEventListener('scroll', scheduleDraw);
    if (frame) window.cancelAnimationFrame(frame);
  };
  draw();
  return {ok: true, count: byIndex.size};
}
"""

_DOM_ANNOTATION_HIDE_SCRIPT = r"""
(payload) => {
  const overlayId = payload.overlay_id || '__browser_use_dom_annotation__';
  const overlay = document.getElementById(overlayId);
  if (overlay && overlay.__browserUseDomAnnotationCleanup) {
    overlay.__browserUseDomAnnotationCleanup();
  }
  if (overlay) overlay.remove();
  return {ok: true};
}
"""

_DOM_ANNOTATION_EXTRACT_SCRIPT = r"""
(payload) => {
  const elements = payload.elements || [];
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
  const candidates = Array.from(document.querySelectorAll(interactiveSelector));
  const clean = (value) => (value || '').replace(/\s+/g, ' ').trim();
  return elements.map((element) => {
    const index = Number(element.index);
    const live = candidates[index];
    if (live && live.isConnected) {
      const rect = live.getBoundingClientRect();
      return {
        index,
        x: rect.left + window.scrollX,
        y: rect.top + window.scrollY,
        width: rect.width,
        height: rect.height,
        label: clean(live.innerText || live.textContent) || element.label || element.text || live.tagName.toLowerCase()
      };
    }
    return {
      index,
      x: Number(element.x || 0),
      y: Number(element.y || 0),
      width: Number(element.width || 0),
      height: Number(element.height || 0),
      label: element.label || element.text || element.tag_name || String(index)
    };
  });
}
"""

__all__ = [
    "AnnotationConfig",
    "DOMElement",
    "DOMState",
    "DOMTreeSerializer",
    "DomAnnotationResult",
    "DomAnnotator",
    "DomService",
    "DomWatchdog",
]
