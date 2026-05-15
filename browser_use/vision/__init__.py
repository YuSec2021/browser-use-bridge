from __future__ import annotations

import base64
import inspect
import json
import uuid
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


CaptureMode = Literal["full_page", "viewport", "element"]


class BoundingBox(BaseModel):
    """Screen-space coordinates for a DOM element label."""

    model_config = ConfigDict(extra="allow")

    index: int
    x: float
    y: float
    width: float
    height: float
    label: str | None = None
    text: str | None = None
    confidence: float | None = None


class AnnotatedScreenshot(BaseModel):
    """Serializable screenshot payload suitable for vision-capable chat models."""

    model_config = ConfigDict(extra="allow")

    image_path: str
    content_type: str = "image/jpeg"
    base64_data: str
    mode: CaptureMode | str = "viewport"
    width: int
    height: int
    bounding_boxes: list[BoundingBox] = Field(default_factory=list)


class VisionAnalysis(BaseModel):
    """Parsed result from a vision model invocation."""

    model_config = ConfigDict(extra="allow")

    raw_response: Any = None
    bounding_boxes: list[BoundingBox] = Field(default_factory=list)
    confidence_scores: list[float] = Field(default_factory=list)
    annotated_image_path: str | None = None
    refinement_count: int = 0
    refinement_metadata: dict[str, Any] = Field(default_factory=dict)


class VisionModel:
    """Minimal provider-neutral protocol target for vision-capable chat models."""

    async def ainvoke(self, messages: list[dict[str, Any]], **kwargs: Any) -> Any:
        raise NotImplementedError("VisionModel subclasses must implement ainvoke()")

    async def analyze(self, messages: list[dict[str, Any]], **kwargs: Any) -> Any:
        return await self.ainvoke(messages, **kwargs)


class VisionService:
    """Capture, annotate, analyze, and refine browser screenshots."""

    def __init__(
        self,
        browser_session: Any | None = None,
        model: Any | None = None,
        output_dir: str | Path | None = None,
        jpeg_quality: int = 85,
    ) -> None:
        self.browser_session = browser_session
        self.model = model
        self.output_dir = Path(output_dir or "/tmp")
        self.jpeg_quality = jpeg_quality
        self.output_dir.mkdir(parents=True, exist_ok=True)

    async def capture(
        self,
        mode: CaptureMode | str = "viewport",
        bounding_box: BoundingBox | None = None,
        path: str | Path | None = None,
    ) -> AnnotatedScreenshot:
        page = self._active_page()
        image_path = Path(path) if path is not None else self._default_path(f"{mode}.jpg")
        image_path.parent.mkdir(parents=True, exist_ok=True)

        screenshot_kwargs: dict[str, Any] = {
            "path": str(image_path),
            "type": "jpeg",
            "quality": self.jpeg_quality,
        }
        boxes: list[BoundingBox] = []
        if mode == "full_page":
            screenshot_kwargs["full_page"] = True
        elif mode == "element":
            if bounding_box is None:
                raise ValueError("mode='element' requires a bounding_box")
            screenshot_kwargs["clip"] = {
                "x": bounding_box.x,
                "y": bounding_box.y,
                "width": bounding_box.width,
                "height": bounding_box.height,
            }
            boxes = [bounding_box]
        else:
            screenshot_kwargs["full_page"] = False

        raw_bytes = await page.screenshot(**screenshot_kwargs)
        image_bytes = self._image_bytes(image_path, raw_bytes)
        width, height = self._resolve_dimensions(image_path, page, bounding_box)
        return AnnotatedScreenshot(
            image_path=str(image_path),
            content_type="image/jpeg",
            base64_data=base64.b64encode(image_bytes).decode("ascii"),
            mode=str(mode),
            width=width,
            height=height,
            bounding_boxes=boxes,
        )

    async def annotate(
        self,
        screenshot: AnnotatedScreenshot,
        bounding_boxes: list[BoundingBox] | None = None,
        path: str | Path | None = None,
    ) -> AnnotatedScreenshot:
        boxes = list(bounding_boxes if bounding_boxes is not None else screenshot.bounding_boxes)
        image_path = Path(path) if path is not None else self._default_path("annotated.jpg")
        image_path.parent.mkdir(parents=True, exist_ok=True)
        width, height = self._draw_bounding_boxes(Path(screenshot.image_path), image_path, boxes)
        image_bytes = image_path.read_bytes()
        return AnnotatedScreenshot(
            image_path=str(image_path),
            content_type="image/jpeg",
            base64_data=base64.b64encode(image_bytes).decode("ascii"),
            mode=screenshot.mode,
            width=width,
            height=height,
            bounding_boxes=boxes,
        )

    async def annotate_image_path(
        self,
        image_path: str | Path,
        bounding_boxes: list[BoundingBox] | None = None,
        path: str | Path | None = None,
        mode: CaptureMode | str = "viewport",
    ) -> AnnotatedScreenshot:
        source = Path(image_path)
        width, height = self._image_size(source)
        screenshot = AnnotatedScreenshot(
            image_path=str(source),
            content_type="image/jpeg",
            base64_data=base64.b64encode(source.read_bytes()).decode("ascii"),
            mode=mode,
            width=width,
            height=height,
            bounding_boxes=list(bounding_boxes or []),
        )
        return await self.annotate(screenshot, bounding_boxes=bounding_boxes, path=path)

    async def analyze(
        self,
        screenshot: AnnotatedScreenshot,
        prompt: str,
        **kwargs: Any,
    ) -> VisionAnalysis:
        if self.model is None:
            raise ValueError("VisionService.analyze() requires a vision model")
        messages = self._build_vision_messages(screenshot, prompt)
        raw_response = await self._invoke_model(messages, **kwargs)
        return self._parse_analysis(raw_response, screenshot)

    async def refine(
        self,
        screenshot: AnnotatedScreenshot,
        prompt: str,
        confidence_threshold: float = 0.8,
        max_refinements: int = 1,
        **kwargs: Any,
    ) -> VisionAnalysis:
        best = await self.analyze(screenshot, prompt=prompt, **kwargs)
        best_confidence = self._best_confidence(best)
        refinements = 0

        while best_confidence < confidence_threshold and refinements < max_refinements:
            refinements += 1
            refined_prompt = (
                f"{prompt}\n\nPrevious vision result had confidence {best_confidence:.2f}. "
                "Re-check the annotated screenshot and return the most likely DOM index."
            )
            candidate = await self.analyze(screenshot, prompt=refined_prompt, **kwargs)
            candidate_confidence = self._best_confidence(candidate)
            if candidate_confidence >= best_confidence:
                best = candidate
                best_confidence = candidate_confidence
            if best_confidence >= confidence_threshold:
                break

        return best.model_copy(
            update={
                "refinement_count": refinements,
                "refinement_metadata": {
                    "confidence_threshold": confidence_threshold,
                    "best_confidence": best_confidence,
                    "max_refinements": max_refinements,
                },
            }
        )

    def _active_page(self) -> Any:
        if self.browser_session is None:
            raise ValueError("VisionService.capture() requires a browser_session")
        session_manager = getattr(self.browser_session, "session_manager", None)
        if session_manager is not None:
            return session_manager.get_active_tab().page
        page = getattr(self.browser_session, "page", None)
        if page is not None:
            return page
        raise RuntimeError("Unable to resolve active page from browser_session")

    def _default_path(self, suffix: str) -> Path:
        return self.output_dir / f"vision-{uuid.uuid4().hex}-{suffix}"

    @staticmethod
    def _image_bytes(path: Path, raw_bytes: Any) -> bytes:
        if path.exists():
            return path.read_bytes()
        if isinstance(raw_bytes, bytes):
            return raw_bytes
        if isinstance(raw_bytes, bytearray):
            return bytes(raw_bytes)
        return b""

    def _resolve_dimensions(
        self,
        image_path: Path,
        page: Any,
        bounding_box: BoundingBox | None,
    ) -> tuple[int, int]:
        if bounding_box is not None:
            return int(round(bounding_box.width)), int(round(bounding_box.height))
        try:
            return self._image_size(image_path)
        except Exception:
            viewport = getattr(page, "viewport_size", None) or {}
            return int(viewport.get("width", 0)), int(viewport.get("height", 0))

    @staticmethod
    def _image_size(image_path: Path) -> tuple[int, int]:
        from PIL import Image

        with Image.open(image_path) as image:
            return int(image.width), int(image.height)

    def _draw_bounding_boxes(
        self,
        source_path: Path,
        output_path: Path,
        boxes: list[BoundingBox],
    ) -> tuple[int, int]:
        from PIL import Image, ImageDraw, ImageFont

        with Image.open(source_path) as source:
            image = source.convert("RGB")
        draw = ImageDraw.Draw(image)
        font = ImageFont.load_default()
        palette = ["#e11d48", "#2563eb", "#16a34a", "#d97706", "#7c3aed", "#0891b2"]

        for offset, box in enumerate(boxes):
            color = palette[offset % len(palette)]
            left = float(box.x)
            top = float(box.y)
            right = left + float(box.width)
            bottom = top + float(box.height)
            draw.rectangle((left, top, right, bottom), outline=color, width=3)

            label = f"[{box.index}]"
            text_bbox = draw.textbbox((left, top), label, font=font)
            text_width = text_bbox[2] - text_bbox[0]
            text_height = text_bbox[3] - text_bbox[1]
            label_bottom = max(0, top - text_height - 6)
            draw.rectangle(
                (left, label_bottom, left + text_width + 8, label_bottom + text_height + 6),
                fill=color,
            )
            draw.text((left + 4, label_bottom + 3), label, fill="white", font=font)

        image.save(output_path, format="JPEG", quality=self.jpeg_quality)
        return int(image.width), int(image.height)

    def _build_vision_messages(
        self,
        screenshot: AnnotatedScreenshot,
        prompt: str,
    ) -> list[dict[str, Any]]:
        box_lines = [
            (
                f"[{box.index}] {box.label or box.text or ''} "
                f"x={box.x} y={box.y} width={box.width} height={box.height}"
            ).strip()
            for box in screenshot.bounding_boxes
        ]
        text = "\n".join(
            part
            for part in [
                prompt,
                f"Annotated screenshot content type: {screenshot.content_type}",
                "DOM-indexed bounding boxes:",
                "\n".join(box_lines),
            ]
            if part
        )
        return [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": text},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{screenshot.content_type};base64,{screenshot.base64_data}"
                        },
                    },
                ],
            }
        ]

    async def _invoke_model(self, messages: list[dict[str, Any]], **kwargs: Any) -> Any:
        if hasattr(self.model, "analyze"):
            result = self.model.analyze(messages, **kwargs)
        elif hasattr(self.model, "ainvoke"):
            result = self.model.ainvoke(messages, **kwargs)
        else:
            raise TypeError("Vision model must expose analyze() or ainvoke()")
        if inspect.isawaitable(result):
            return await result
        return result

    def _parse_analysis(self, response: Any, screenshot: AnnotatedScreenshot) -> VisionAnalysis:
        payload = self._coerce_payload(response)
        if isinstance(payload, VisionAnalysis):
            return payload.model_copy(update={"annotated_image_path": screenshot.image_path})
        if not isinstance(payload, dict):
            return VisionAnalysis(raw_response=payload, annotated_image_path=screenshot.image_path)

        raw_response = payload.get("raw_response", payload)
        boxes_payload = payload.get("bounding_boxes") or []
        boxes = [
            box if isinstance(box, BoundingBox) else BoundingBox.model_validate(box)
            for box in boxes_payload
        ]
        scores = [float(score) for score in (payload.get("confidence_scores") or [])]
        if not scores:
            scores = [box.confidence for box in boxes if box.confidence is not None]
        return VisionAnalysis(
            raw_response=raw_response,
            bounding_boxes=boxes,
            confidence_scores=[float(score) for score in scores],
            annotated_image_path=screenshot.image_path,
        )

    @staticmethod
    def _coerce_payload(response: Any) -> Any:
        if isinstance(response, str):
            try:
                return json.loads(response)
            except json.JSONDecodeError:
                return response
        content = getattr(response, "content", None)
        if content is not None:
            return VisionService._coerce_payload(content)
        return response

    @staticmethod
    def _best_confidence(analysis: VisionAnalysis) -> float:
        if analysis.confidence_scores:
            return max(float(score) for score in analysis.confidence_scores)
        confidences = [
            float(box.confidence)
            for box in analysis.bounding_boxes
            if box.confidence is not None
        ]
        return max(confidences) if confidences else 0.0


__all__ = [
    "AnnotatedScreenshot",
    "BoundingBox",
    "VisionAnalysis",
    "VisionModel",
    "VisionService",
]
