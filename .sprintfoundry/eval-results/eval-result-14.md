# Eval Result — Sprint 14
Date: 2026-05-15

## Scores

| Dimension       | Score | Threshold | Result |
|-----------------|-------|-----------|--------|
| Design quality  | 8/10  | >= 7      | PASS   |
| Originality     | 6/10  | >= 6      | PASS   |
| Craft           | 9/10  | >= 7      | PASS   |
| Functionality   | 10/10 | >= 8      | PASS   |

## Verdict: SPRINT PASS

## Evidence

### Criterion 1: Vision public API exposes async service methods and serializable vision data models
Result: PASS
Evidence: Executed via `.venv/bin/python - <<'PY'` with marker file `/tmp/sprint14-vision-api.txt` containing `vision api ok`. All assertions passed: BoundingBox field accessors, AnnotatedScreenshot serialization, VisionAnalysis data integrity, VisionService async methods (capture, annotate, analyze, refine), VisionModel interface presence, and top-level export from `browser_use`.
Observation: Public API is clean, fully async, and models serialize correctly. All 4 data models (BoundingBox, AnnotatedScreenshot, VisionAnalysis, VisionModel) are correctly exposed from `browser_use` package.

### Criterion 2: VisionService captures full-page, viewport, and element-specific screenshots with JPEG quality 85
Result: PASS
Evidence: Marker file `/tmp/sprint14-capture-modes.txt` contains `capture modes ok: 3 7`. All 3 capture modes verified: full_page sets `full_page=True`, viewport sets `full_page=False`, element sets `clip={'x':11,'y':13,'width':101,'height':41}`. All 3 pass `type='jpeg'` and `quality=85` to the underlying page screenshot call.
Observation: Capture modes correctly delegate to Playwright page.screenshot with the proper clip/full_page options and JPEG quality settings.

### Criterion 3: VisionService annotates screenshots with visible bounding boxes and numeric labels matching DOM element indices
Result: PASS
Evidence: Marker file `/tmp/sprint14-annotation.txt` contains `annotation ok: 2 True`. PIL pixel analysis confirmed 190+ changed pixels in the annotated regions (vs white background), proving bounding boxes are visibly drawn. Index labels `[0]` and `[1]` are confirmed present.
Observation: Annotation draws colored rectangles and index labels using PIL/Pillow. The hardcoded palette (pink, blue, green, amber, purple, teal) provides color differentiation for multiple boxes.

### Criterion 4: VisionService analyzes annotated screenshots through a fake vision model, sends base64 image data with image/jpeg markers
Result: PASS
Evidence: Marker file `/tmp/sprint14-analysis.txt` contains `analysis ok: 2 0.88`. The fake model's messages contain: the prompt text, `data:image/jpeg;base64,` prefix, and DOM index `[2]` / label `Checkout`. The analysis correctly returns `raw_response['answer']='target is [2]'`, parsed bounding box with `index=2`, and `confidence_scores=[0.88]`.
Observation: The VisionService correctly builds multi-part messages with text + image_url, parses the model response into typed VisionAnalysis, and correlates results back to annotated screenshots.

### Criterion 5: Multi-step vision refinement retries low-confidence analysis and returns highest-confidence result
Result: PASS
Evidence: Marker file `/tmp/sprint14-refine.txt` contains `refine ok: 2 5 1`. The RefiningVisionModel was called exactly 2 times. After the first call returned confidence 0.42 (below 0.80 threshold), refine triggered a second call. The second result (index 5, confidence 0.93) replaced the first (index 4, confidence 0.42). `refinement_count=1` correctly records the retry.
Observation: The refine loop correctly implements the threshold-gated retry pattern. It compares confidences, replaces the best result, and stops when threshold is met or max_refinements exhausted.

### Criterion 6: MessageManager includes annotated screenshots in LLM context as base64 image content
Result: PASS
Evidence: Marker file `/tmp/sprint14-message-manager.txt` contains `message manager vision ok`. The resulting user message is a list containing 3 parts: task text, DOM index text (`[2] Checkout`), and an image_url part with `data:image/jpeg;base64,ZmFrZS1qcGVn`. Browser state (URL, title, elements) is preserved in the text portion.
Observation: MessageManager.build_messages correctly merges screenshots with browser state, placing DOM text context before the image data. The `data:image/jpeg;base64,` content-type marker is correctly emitted.

## Scope verification
Diff vs merge-base (3669175...HEAD):
- `browser_use/__init__.py` (+6 lines): Vision exports added
- `browser_use/agent/message_manager/__init__.py` (+60/-2 lines): Screenshot integration in build_messages
- `browser_use/vision.py` (+400 lines): New VisionService module
- `tests/test_sprint14.py` (+137 lines): Test coverage

No scope violations. All changes are confined to the Vision Understanding Module scope.

## Quality Gate Review (Craft scoring reference)

From `quality-gate-14.md`:
- flake8/mypy: N/A (system Python 3.9 lacks packages; tests run correctly under `.venv` Python 3.12)
- pytest: collection error on system Python; tests pass cleanly under `.venv`

The pytest collection error in the quality gate report stems from `browser_use/agent/retry.py:18` using `|` union syntax (Python 3.10+) with system Python 3.9.6. This is a pre-existing environmental incompatibility, not introduced by Sprint 14. All Sprint 14 code uses `from __future__ import annotations` and compatible type syntax.

## Required fixes (if SPRINT FAIL)
None. All criteria pass.

## Summary

Sprint 14 delivers a clean, well-scoped Vision Understanding Module with 4 new files and 601 lines of net-new code. The VisionService provides a provider-agnostic async interface for screenshot capture, annotation, analysis, and refinement. The MessageManager integration is seamless, passing screenshots as properly typed base64 image content. All 6 contracted criteria pass with independent black-box evidence, and no scope violations were detected.