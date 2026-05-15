from __future__ import annotations

import asyncio
from pathlib import Path

from PIL import Image

from browser_use import AnnotationConfig as ExportedAnnotationConfig
from browser_use import DomAnnotator as ExportedDomAnnotator
from browser_use.dom import AnnotationConfig, DOMElement, DomAnnotator


class FakePage:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object | None]] = []

    async def evaluate(self, script: str, arg: object | None = None) -> object:
        self.calls.append((script, arg))
        if "getBoundingClientRect" in script and isinstance(arg, dict) and arg.get("mode") == "extract":
            return [
                {"index": 4, "x": 11, "y": 22, "width": 101, "height": 31, "label": "Open"},
                {"index": 5, "x": 50, "y": 70, "width": 88, "height": 26, "label": "Close"},
            ]
        return {"ok": True}


def test_dom_annotator_api_is_public_and_serializable() -> None:
    config = AnnotationConfig(
        border_color="#58a6ff",
        fill_color="#58a6ff",
        fill_opacity=0.18,
        label_background="#161b22",
        label_color="#e6edf3",
        font_size=13,
        stroke_width=2,
        overlay_id="__sprint15_overlay__",
    )
    element = DOMElement(index=7, tag_name="button", text="Checkout", x=10, y=20, width=90, height=30)

    assert ExportedDomAnnotator is DomAnnotator
    assert ExportedAnnotationConfig is AnnotationConfig
    assert config.model_dump()["overlay_id"] == "__sprint15_overlay__"
    assert element.index == 7


def test_svg_overlay_injection_and_selection_helpers() -> None:
    async def run() -> FakePage:
        page = FakePage()
        annotator = DomAnnotator(
            page=page,
            config=AnnotationConfig(
                border_color="#3fb950",
                fill_color="#3fb950",
                fill_opacity=0.25,
                label_background="#0d1117",
                label_color="#ffffff",
                font_size=14,
                stroke_width=3,
                overlay_id="__sprint15_svg__",
            ),
        )
        elements = [
            {"index": 0, "tag_name": "button", "text": "One", "x": 5, "y": 10, "width": 80, "height": 24},
            {"index": 1, "tag_name": "a", "text": "Two", "x": 20, "y": 50, "width": 90, "height": 28},
            {"index": 2, "tag_name": "input", "text": "Three", "x": 30, "y": 90, "width": 120, "height": 32},
        ]
        await annotator.highlight_range(elements, count=2)
        script, payload = page.calls[-1]
        assert "svg" in script.lower()
        assert "createElementNS" in script
        assert "MutationObserver" in script
        assert "getBoundingClientRect" in script
        assert "resize" in script and "scroll" in script
        assert isinstance(payload, dict)
        assert payload["config"]["border_color"] == "#3fb950"
        assert [element["index"] for element in payload["elements"]] == [0, 1]
        assert annotator.visible is True

        await annotator.highlight_element(elements, index=2)
        assert [element["index"] for element in page.calls[-1][1]["elements"]] == [2]  # type: ignore[index]
        await annotator.highlight_all(elements)
        assert [element["index"] for element in page.calls[-1][1]["elements"]] == [0, 1, 2]  # type: ignore[index]
        return page

    asyncio.run(run())


def test_extract_bounding_boxes_and_show_hide() -> None:
    async def run() -> None:
        page = FakePage()
        annotator = DomAnnotator(page=page, config=AnnotationConfig(overlay_id="__sprint15_toggle__"))
        elements = [
            {"index": 4, "tag_name": "button", "text": "Open", "x": 1, "y": 2, "width": 3, "height": 4},
            {"index": 5, "tag_name": "button", "text": "Close", "x": 5, "y": 6, "width": 7, "height": 8},
        ]
        await annotator.highlight_all(elements[:1])
        await annotator.hide()
        assert annotator.visible is False
        hide_script, hide_payload = page.calls[-1]
        assert "remove" in hide_script
        assert isinstance(hide_payload, dict)
        assert hide_payload["overlay_id"] == "__sprint15_toggle__"

        await annotator.show()
        assert annotator.visible is True
        assert [element["index"] for element in page.calls[-1][1]["elements"]] == [4]  # type: ignore[index]

        boxes = await annotator.extract_bounding_boxes(elements)
        assert [box.index for box in boxes] == [4, 5]
        assert boxes[0].x == 11
        assert boxes[0].label == "Open"

    asyncio.run(run())


def test_annotate_image_path_draws_pillow_fallback(tmp_path: Path) -> None:
    async def run() -> Path:
        source = tmp_path / "source.png"
        output = tmp_path / "annotated.png"
        Image.new("RGB", (220, 140), "white").save(source)
        annotator = DomAnnotator(
            page=None,
            config=AnnotationConfig(
                border_color="#f85149",
                fill_color="#f85149",
                fill_opacity=0.20,
                label_background="#161b22",
                label_color="#ffffff",
                font_size=12,
                stroke_width=3,
            ),
        )
        result = await annotator.annotate_image_path(
            source,
            elements=[
                {"index": 0, "tag_name": "button", "text": "First", "x": 10, "y": 15, "width": 80, "height": 36},
                {"index": 1, "tag_name": "a", "text": "Second", "x": 120, "y": 70, "width": 70, "height": 30},
            ],
            output_path=output,
        )
        assert result.image_path == str(output)
        assert [box.index for box in result.bounding_boxes] == [0, 1]
        return output

    image_path = asyncio.run(run())
    image = Image.open(image_path).convert("RGB")
    changed_pixels = sum(
        1
        for x in range(8, 194)
        for y in range(13, 104)
        if image.getpixel((x, y)) != (255, 255, 255)
    )
    assert changed_pixels > 180
