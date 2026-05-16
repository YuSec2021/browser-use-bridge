from __future__ import annotations

import asyncio
from pathlib import Path

from PIL import Image

from browser_use import VisionService as ExportedVisionService
from browser_use.agent import MessageManager
from browser_use.browser.views import BrowserStateSummary
from browser_use.vision import AnnotatedScreenshot, BoundingBox, VisionAnalysis, VisionService


def test_vision_models_are_public_and_serializable() -> None:
    box = BoundingBox(index=3, x=10, y=20, width=100, height=40, label="Submit", confidence=0.91)
    screenshot = AnnotatedScreenshot(
        image_path="/tmp/sprint14-api.jpg",
        content_type="image/jpeg",
        base64_data="abc123",
        mode="viewport",
        width=320,
        height=200,
        bounding_boxes=[box],
    )
    analysis = VisionAnalysis(raw_response={"target": "Submit"}, bounding_boxes=[box], confidence_scores=[0.91])

    assert ExportedVisionService is VisionService
    assert screenshot.model_dump()["bounding_boxes"][0]["index"] == 3
    assert analysis.raw_response["target"] == "Submit"


def test_capture_modes_pass_jpeg_options_to_page() -> None:
    class FakePage:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []
            self.viewport_size = {"width": 800, "height": 600}

        async def screenshot(self, **kwargs: object) -> bytes:
            self.calls.append(kwargs)
            Path(str(kwargs["path"])).write_bytes(b"fake-jpeg-bytes")
            return b"fake-jpeg-bytes"

    class FakeTab:
        def __init__(self, page: FakePage) -> None:
            self.page = page

    class FakeManager:
        def __init__(self, page: FakePage) -> None:
            self.page = page

        def get_active_tab(self) -> FakeTab:
            return FakeTab(self.page)

    class FakeSession:
        def __init__(self, page: FakePage) -> None:
            self.session_manager = FakeManager(page)

    async def run() -> FakePage:
        page = FakePage()
        service = VisionService(browser_session=FakeSession(page), output_dir="/tmp")
        await service.capture(mode="full_page", path="/tmp/sprint14-full.jpg")
        await service.capture(mode="viewport", path="/tmp/sprint14-viewport.jpg")
        await service.capture(
            mode="element",
            bounding_box=BoundingBox(index=7, x=11, y=13, width=101, height=41),
            path="/tmp/sprint14-element.jpg",
        )
        return page

    page = asyncio.run(run())

    assert page.calls[0]["full_page"] is True
    assert page.calls[0]["type"] == "jpeg"
    assert page.calls[0]["quality"] == 85
    assert page.calls[1]["full_page"] is False
    assert page.calls[2]["clip"] == {"x": 11, "y": 13, "width": 101, "height": 41}


def test_annotate_analyze_refine_and_message_manager() -> None:
    class RefiningVisionModel:
        def __init__(self) -> None:
            self.calls = 0
            self.messages: object | None = None

        async def ainvoke(self, messages: object, **kwargs: object) -> dict[str, object]:
            self.calls += 1
            self.messages = messages
            if self.calls == 1:
                return {
                    "raw_response": {"answer": "maybe [4]"},
                    "bounding_boxes": [
                        {"index": 4, "x": 8, "y": 8, "width": 20, "height": 20, "label": "Maybe", "confidence": 0.42}
                    ],
                    "confidence_scores": [0.42],
                }
            return {
                "raw_response": {"answer": "confirmed [5]"},
                "bounding_boxes": [
                    {"index": 5, "x": 70, "y": 30, "width": 55, "height": 24, "label": "Confirm", "confidence": 0.93}
                ],
                "confidence_scores": [0.93],
            }

    async def run() -> tuple[AnnotatedScreenshot, VisionAnalysis, RefiningVisionModel]:
        image_path = Path("/tmp/sprint14-test-source.jpg")
        Image.new("RGB", (180, 110), "white").save(image_path, format="JPEG")
        model = RefiningVisionModel()
        service = VisionService(model=model, output_dir="/tmp")
        screenshot = await service.annotate_image_path(
            image_path,
            bounding_boxes=[
                BoundingBox(index=4, x=8, y=8, width=20, height=20, label="Maybe"),
                BoundingBox(index=5, x=70, y=30, width=55, height=24, label="Confirm"),
            ],
            path="/tmp/sprint14-test-annotated.jpg",
        )
        analysis = await service.refine(screenshot, "Select the confirm control", confidence_threshold=0.8, max_refinements=2)
        return screenshot, analysis, model

    screenshot, analysis, model = asyncio.run(run())

    assert Path(screenshot.image_path).exists()
    assert analysis.bounding_boxes[0].index == 5
    assert analysis.confidence_scores == [0.93]
    assert analysis.refinement_count == 1
    assert "data:image/jpeg;base64," in str(model.messages)

    state = BrowserStateSummary(
        url="file:///tmp/sprint14-message.html",
        title="Vision Message Page",
        elements=[{"index": 5, "tag": "button", "text": "Confirm"}],
    )
    messages = MessageManager(task="Use vision").build_messages(state, screenshots=[screenshot])
    content = messages[-1]["content"]
    assert isinstance(content, list)
    assert any(part.get("type") == "image_url" for part in content)
    assert "[5]" in "\n".join(part.get("text", "") for part in content if part.get("type") == "text")
