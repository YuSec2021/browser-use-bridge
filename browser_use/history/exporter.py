from __future__ import annotations

import base64
import copy
import html
import json
import mimetypes
from pathlib import Path
from typing import Any

from browser_use.checkpoint import Checkpoint, CheckpointManager


class HistoryExporter:
    """Export checkpoint-backed agent history as replay artifacts."""

    def __init__(
        self,
        checkpoint_manager: CheckpointManager | None = None,
        output_dir: str | Path | None = None,
    ) -> None:
        self.checkpoint_manager = checkpoint_manager or CheckpointManager()
        self.output_dir = Path(output_dir or "history-exports").expanduser()

    def to_json(self, checkpoint_id: str, *, task_id: str | None = None) -> Path:
        checkpoint = self.checkpoint_manager.load(checkpoint_id, task_id=task_id)
        payload = self._build_payload(checkpoint)
        path = self._artifact_path(checkpoint, "json")
        path.write_text(self._stable_json(payload), encoding="utf-8")
        return path

    def to_html(self, checkpoint_id: str, *, task_id: str | None = None) -> Path:
        checkpoint = self.checkpoint_manager.load(checkpoint_id, task_id=task_id)
        payload = self._build_payload(checkpoint)
        html_payload = self._payload_with_embedded_screenshots(payload)
        path = self._artifact_path(checkpoint, "html")
        path.write_text(self._render_html(html_payload), encoding="utf-8")
        return path

    def to_gif(
        self,
        checkpoint_id: str,
        *,
        task_id: str | None = None,
        fps: int = 2,
        resolution: tuple[int, int] | None = None,
        loop: int = 0,
    ) -> Path:
        checkpoint = self.checkpoint_manager.load(checkpoint_id, task_id=task_id)
        payload = self._build_payload(checkpoint)
        path = self._artifact_path(checkpoint, "gif")
        self._write_gif(path, payload["steps"], fps=fps, resolution=resolution, loop=loop)
        metadata = {
            "checkpoint_id": checkpoint.checkpoint_id,
            "task_id": checkpoint.task_id,
            "fps": fps,
            "loop": loop,
            "frame_labels": [self._frame_label(step) for step in payload["steps"]],
            "actions_per_frame": [step["actions_executed"] for step in payload["steps"]],
        }
        path.with_suffix(".json").write_text(self._stable_json(metadata), encoding="utf-8")
        return path

    def export(
        self,
        checkpoint_id: str,
        *,
        task_id: str | None = None,
        format: str = "html",
    ) -> dict[str, Path]:
        if format == "all":
            return {
                "json": self.to_json(checkpoint_id, task_id=task_id),
                "html": self.to_html(checkpoint_id, task_id=task_id),
                "gif": self.to_gif(checkpoint_id, task_id=task_id),
            }
        if format == "json":
            return {"json": self.to_json(checkpoint_id, task_id=task_id)}
        if format == "html":
            return {"html": self.to_html(checkpoint_id, task_id=task_id)}
        if format == "gif":
            return {"gif": self.to_gif(checkpoint_id, task_id=task_id)}
        raise ValueError(f"Unsupported replay format: {format}")

    def _build_payload(self, checkpoint: Checkpoint) -> dict[str, Any]:
        checkpoint_payload = checkpoint.model_dump(mode="json")
        history_payload = self._normalize_value(checkpoint.agent_history or {"histories": []})
        histories = history_payload.get("histories", [])
        if not isinstance(histories, list):
            histories = []

        steps: list[dict[str, Any]] = []
        previous_state: dict[str, Any] | None = None
        for index, history in enumerate(histories):
            entry = history if isinstance(history, dict) else self._normalize_value(history)
            if not isinstance(entry, dict):
                entry = {}
            state = self._normalize_state(entry.get("state"))
            model_output = self._normalize_model_output(entry.get("model_output"))
            step = {
                "step_index": index,
                "timestamp": entry.get("timestamp", ""),
                "duration_ms": entry.get("duration_ms", 0),
                "actions_executed": self._normalize_actions(model_output.get("actions", [])),
                "outcome": entry.get("outcome", self._outcome_from_entry(entry)),
                "token_count": entry.get("token_count", self._token_count_from_entry(entry)),
                "llm_model": entry.get("llm_model", self._llm_model_from_entry(entry)),
                "model_metadata": self._model_metadata_from_entry(entry),
                "error_message": entry.get("error_message", self._error_message_from_entry(entry)),
                "dom_state": state,
                "dom_diff": self._dom_diff(previous_state, state) if previous_state is not None else self._empty_diff(),
                "screenshots": self._normalize_screenshots(entry.get("screenshots", entry.get("screenshot", []))),
            }
            steps.append(step)
            previous_state = state

        return {
            "checkpoint": checkpoint_payload,
            "history": history_payload,
            "steps": steps,
        }

    def _artifact_path(self, checkpoint: Checkpoint, suffix: str) -> Path:
        checkpoint_id = self._safe_part(checkpoint.checkpoint_id)
        filename = f"{checkpoint_id}.replay.gif" if suffix == "gif" else f"{checkpoint_id}.{suffix}"
        path = self.output_dir / self._safe_part(checkpoint.task_id) / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def _safe_part(value: str) -> str:
        safe = "".join(character if character.isalnum() or character in {"-", "_", "."} else "_" for character in value)
        return safe or "unknown"

    @classmethod
    def _normalize_value(cls, value: Any) -> Any:
        if hasattr(value, "model_dump"):
            return cls._normalize_value(value.model_dump(mode="json", exclude_none=True))
        if isinstance(value, dict):
            return {str(key): cls._normalize_value(item) for key, item in value.items()}
        if isinstance(value, list):
            return [cls._normalize_value(item) for item in value]
        if isinstance(value, tuple):
            return [cls._normalize_value(item) for item in value]
        return value

    @classmethod
    def _normalize_state(cls, state: Any) -> dict[str, Any]:
        normalized = cls._normalize_value(state or {})
        return normalized if isinstance(normalized, dict) else {}

    @classmethod
    def _normalize_model_output(cls, model_output: Any) -> dict[str, Any]:
        normalized = cls._normalize_value(model_output or {})
        return normalized if isinstance(normalized, dict) else {}

    @classmethod
    def _normalize_actions(cls, actions: Any) -> list[Any]:
        normalized = cls._normalize_value(actions)
        if normalized is None:
            return []
        return normalized if isinstance(normalized, list) else [normalized]

    @classmethod
    def _normalize_screenshots(cls, screenshots: Any) -> list[str]:
        normalized = cls._normalize_value(screenshots)
        if normalized in (None, ""):
            return []
        if isinstance(normalized, list):
            return [str(item) for item in normalized]
        return [str(normalized)]

    @staticmethod
    def _outcome_from_entry(entry: dict[str, Any]) -> str:
        if entry.get("error_message") or entry.get("error_summary"):
            return "error"
        return "success"

    @staticmethod
    def _token_count_from_entry(entry: dict[str, Any]) -> int:
        usage = entry.get("token_usage") or entry.get("usage") or {}
        if isinstance(usage, dict):
            return int(usage.get("total_tokens") or usage.get("token_count") or 0)
        return 0

    @staticmethod
    def _llm_model_from_entry(entry: dict[str, Any]) -> str:
        metadata = entry.get("model_metadata") or {}
        if isinstance(metadata, dict):
            return str(metadata.get("model") or metadata.get("model_name") or "")
        return ""

    @staticmethod
    def _model_metadata_from_entry(entry: dict[str, Any]) -> dict[str, Any]:
        metadata = entry.get("model_metadata") or {}
        if isinstance(metadata, dict):
            return metadata
        return {}

    @staticmethod
    def _error_message_from_entry(entry: dict[str, Any]) -> str:
        error_summary = entry.get("error_summary") or {}
        if isinstance(error_summary, dict):
            return str(error_summary.get("message") or error_summary.get("error") or "")
        if error_summary:
            return str(error_summary)
        return ""

    @classmethod
    def _dom_diff(cls, before_state: dict[str, Any], after_state: dict[str, Any]) -> dict[str, list[Any]]:
        before = cls._elements_by_key(before_state.get("elements", []))
        after = cls._elements_by_key(after_state.get("elements", []))
        before_keys = set(before)
        after_keys = set(after)
        added = [after[key] for key in cls._sort_keys(after_keys - before_keys)]
        removed = [before[key] for key in cls._sort_keys(before_keys - after_keys)]
        modified = [
            {"index": cls._display_key(key), "before": before[key], "after": after[key]}
            for key in cls._sort_keys(before_keys & after_keys)
            if before[key] != after[key]
        ]
        return {"added": added, "removed": removed, "modified": modified}

    @staticmethod
    def _empty_diff() -> dict[str, list[Any]]:
        return {"added": [], "removed": [], "modified": []}

    @classmethod
    def _elements_by_key(cls, elements: Any) -> dict[Any, dict[str, Any]]:
        normalized = cls._normalize_value(elements)
        if not isinstance(normalized, list):
            return {}

        by_key: dict[Any, dict[str, Any]] = {}
        for position, element in enumerate(normalized):
            if not isinstance(element, dict):
                element = {"text": str(element)}
            key = element.get("index", position)
            by_key[key] = copy.deepcopy(element)
        return by_key

    @classmethod
    def _sort_keys(cls, keys: set[Any]) -> list[Any]:
        return sorted(keys, key=lambda key: (0, key) if isinstance(key, int) else (1, str(key)))

    @staticmethod
    def _display_key(key: Any) -> Any:
        return key

    @staticmethod
    def _stable_json(payload: Any) -> str:
        return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"

    def _payload_with_embedded_screenshots(self, payload: dict[str, Any]) -> dict[str, Any]:
        html_payload = copy.deepcopy(payload)
        for step in html_payload["steps"]:
            step["screenshots"] = [self._screenshot_data_uri(path) for path in step.get("screenshots", [])]
        return html_payload

    @staticmethod
    def _screenshot_data_uri(path: str) -> str:
        screenshot = Path(path)
        if not screenshot.exists():
            return ""
        mime_type = mimetypes.guess_type(screenshot.name)[0] or "image/png"
        encoded = base64.b64encode(screenshot.read_bytes()).decode("ascii")
        return f"data:{mime_type};base64,{encoded}"

    def _render_html(self, payload: dict[str, Any]) -> str:
        replay_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        escaped_json = html.escape(replay_json, quote=False)
        title = html.escape(str(payload["checkpoint"].get("checkpoint_id", "replay")))
        return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AgentHistory Replay - {title}</title>
<style>
:root {{
  --surface: #0d1117;
  --surface-raised: #161b22;
  --border: #30363d;
  --text-primary: #e6edf3;
  --text-muted: #8b949e;
  --accent-blue: #58a6ff;
  --accent-green: #3fb950;
  --accent-amber: #d29922;
  --accent-red: #f85149;
  --accent-purple: #bc8cff;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  background: var(--surface);
  color: var(--text-primary);
  font-family: Inter, system-ui, sans-serif;
  font-size: 14px;
}}
header, main {{ width: min(1180px, calc(100vw - 32px)); margin: 0 auto; }}
header {{ padding: 24px 0 12px; border-bottom: 1px solid var(--border); }}
h1 {{ margin: 0 0 8px; font: 700 24px JetBrains Mono, Fira Code, monospace; }}
h2 {{ margin: 0 0 12px; font: 600 18px JetBrains Mono, Fira Code, monospace; }}
.meta, .muted {{ color: var(--text-muted); }}
.toolbar {{ display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-top: 16px; }}
button, input {{
  min-height: 34px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--surface-raised);
  color: var(--text-primary);
  font: 13px JetBrains Mono, Consolas, monospace;
}}
button {{ padding: 0 12px; cursor: pointer; }}
button:hover, button.active {{ border-color: var(--accent-blue); color: var(--accent-blue); }}
input {{ width: 110px; padding: 0 8px; }}
main {{ display: grid; grid-template-columns: 280px 1fr; gap: 16px; padding: 16px 0 28px; }}
section {{ border: 1px solid var(--border); border-radius: 6px; background: var(--surface-raised); padding: 14px; min-width: 0; }}
.step-timeline {{ display: grid; gap: 8px; }}
.step-button {{ width: 100%; text-align: left; display: grid; gap: 4px; padding: 10px; }}
.panel-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
.action-timeline, .dom-diff-viewer, .screenshot-gallery {{ min-height: 140px; }}
pre {{
  overflow: auto;
  margin: 0;
  padding: 12px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: #0d1117;
  color: var(--text-primary);
  font: 13px JetBrains Mono, Consolas, monospace;
}}
.screenshot-gallery {{ display: grid; gap: 10px; }}
.screenshot-gallery img {{ max-width: 100%; border: 1px solid var(--border); border-radius: 6px; background: var(--surface); }}
.status-success {{ color: var(--accent-green); }}
.status-error {{ color: var(--accent-red); }}
.url {{ color: var(--accent-blue); word-break: break-all; }}
.diff-added {{ color: var(--accent-green); }}
.diff-removed {{ color: var(--accent-red); }}
.diff-modified {{ color: var(--accent-amber); }}
@media (max-width: 820px) {{
  main {{ grid-template-columns: 1fr; }}
  .panel-grid {{ grid-template-columns: 1fr; }}
}}
</style>
</head>
<body>
<header>
  <h1>AgentHistory Replay</h1>
  <div class="meta">checkpoint <strong>{title}</strong> · task <strong>{html.escape(str(payload["checkpoint"].get("task_id", "")))}</strong></div>
  <div class="toolbar">
    <button id="prev" type="button">Previous</button>
    <button id="next" type="button">Next</button>
    <label class="muted" for="jump">Jump to step</label>
    <input id="jump" type="number" min="0" value="0">
  </div>
</header>
<script id="data-replay-payload" type="application/json">{escaped_json}</script>
<main>
  <section>
    <h2>Steps</h2>
    <div id="step-timeline" class="step-timeline"></div>
  </section>
  <section>
    <h2 id="step-title">Step</h2>
    <p id="step-url" class="url"></p>
    <div class="panel-grid">
      <div>
        <h2>Action Timeline</h2>
        <pre id="action-timeline" class="action-timeline"></pre>
      </div>
      <div>
        <h2>DOM Diff Viewer</h2>
        <pre id="dom-diff-viewer" class="dom-diff-viewer"></pre>
      </div>
    </div>
    <h2>Screenshot Gallery</h2>
    <div id="screenshot-gallery" class="screenshot-gallery"></div>
  </section>
</main>
<script>
const payload = JSON.parse(document.getElementById('data-replay-payload').textContent);
const steps = payload.steps || [];
let current = 0;
const timeline = document.getElementById('step-timeline');
const actionTimeline = document.getElementById('action-timeline');
const diffViewer = document.getElementById('dom-diff-viewer');
const gallery = document.getElementById('screenshot-gallery');
const titleNode = document.getElementById('step-title');
const urlNode = document.getElementById('step-url');
const jump = document.getElementById('jump');

function renderTimeline() {{
  timeline.innerHTML = '';
  steps.forEach((step, index) => {{
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'step-button' + (index === current ? ' active' : '');
    const statusClass = step.outcome === 'error' ? 'status-error' : 'status-success';
    button.innerHTML = `<strong>Step ${{index}}</strong><span class="${{statusClass}}">${{step.outcome || 'unknown'}}</span><span class="muted">${{step.timestamp || ''}}</span>`;
    button.addEventListener('click', () => renderStep(index));
    timeline.appendChild(button);
  }});
}}

function renderStep(index) {{
  if (!steps.length) return;
  current = Math.max(0, Math.min(index, steps.length - 1));
  const step = steps[current];
  titleNode.textContent = `Step ${{step.step_index}}`;
  urlNode.textContent = (step.dom_state && step.dom_state.url) || '';
  actionTimeline.textContent = JSON.stringify(step.actions_executed || [], null, 2);
  diffViewer.textContent = JSON.stringify(step.dom_diff || {{}}, null, 2);
  gallery.innerHTML = '';
  (step.screenshots || []).forEach((src, shotIndex) => {{
    if (!src) return;
    const image = document.createElement('img');
    image.src = src;
    image.alt = `Step ${{current}} screenshot ${{shotIndex + 1}}`;
    gallery.appendChild(image);
  }});
  if (!gallery.children.length) {{
    const empty = document.createElement('p');
    empty.className = 'muted';
    empty.textContent = 'No screenshots for this step.';
    gallery.appendChild(empty);
  }}
  jump.value = String(current);
  renderTimeline();
}}

document.getElementById('prev').addEventListener('click', () => renderStep(current - 1));
document.getElementById('next').addEventListener('click', () => renderStep(current + 1));
jump.addEventListener('change', () => renderStep(Number(jump.value || 0)));
renderStep(0);
</script>
</body>
</html>
"""

    def _write_gif(
        self,
        path: Path,
        steps: list[dict[str, Any]],
        *,
        fps: int,
        resolution: tuple[int, int] | None,
        loop: int,
    ) -> None:
        from PIL import Image, ImageDraw

        frames = []
        for index, step in enumerate(steps):
            source = self._first_existing_screenshot(step.get("screenshots", []))
            if source is None:
                frame = Image.new("RGB", resolution or (640, 360), "#0d1117")
            else:
                frame = Image.open(source).convert("RGB")
                if resolution is not None:
                    frame = self._resize_frame(frame, resolution)
            draw = ImageDraw.Draw(frame)
            draw.rectangle((0, 0, min(frame.width, 48), min(frame.height, 18)), fill=(13, 17, 23))
            draw.text((4, 3), str(index), fill=(88, 166, 255))
            frames.append(frame)

        if not frames:
            frames = [Image.new("RGB", resolution or (640, 360), "#0d1117")]

        duration = max(1, round(1000 / max(1, fps)))
        try:
            frames[0].save(
                path,
                save_all=True,
                append_images=frames[1:],
                duration=duration,
                loop=loop,
                optimize=False,
            )
        except TypeError:
            frames[0].save(path)

    @staticmethod
    def _resize_frame(frame: Any, resolution: tuple[int, int]) -> Any:
        if hasattr(frame, "resize"):
            from PIL import Image

            resampling = getattr(getattr(Image, "Resampling", None), "LANCZOS", 1)
            return frame.resize(resolution, resampling)

        from PIL import Image

        width, height = resolution
        resized = Image.new("RGB", resolution, "#0d1117")
        source_width, source_height = frame.size
        for y in range(height):
            source_y = min(source_height - 1, int(y * source_height / max(1, height)))
            for x in range(width):
                source_x = min(source_width - 1, int(x * source_width / max(1, width)))
                resized.putpixel((x, y), frame.getpixel((source_x, source_y)))
        return resized

    @staticmethod
    def _first_existing_screenshot(screenshots: list[str]) -> Path | None:
        for screenshot in screenshots:
            path = Path(screenshot)
            if path.exists():
                return path
        return None

    @staticmethod
    def _frame_label(step: dict[str, Any]) -> str:
        actions = step.get("actions_executed") or []
        if not actions:
            return f"step {step.get('step_index', 0)}"
        return f"step {step.get('step_index', 0)}: {json.dumps(actions, ensure_ascii=False, sort_keys=True)}"


__all__ = ["HistoryExporter"]
