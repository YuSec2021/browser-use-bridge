
## v0.1.0 — Sprint 1 [MINOR bump]
- All public API classes import cleanly with no network calls. Package `__init__.py` files correctly re-export the public API surface.
- All three provider adapters (OpenAI, Anthropic, Google) expose `ainvoke`, `astream`, `with_structured_output`, and `bind_tools`. The `ainvoke` method is a coroutine function. Adapters instantiate with dummy credentials without contacting any provider.
- `StructuredOutputChatModel` correctly wraps a `BaseChatModel`, and `ainvoke` returns a `Pydantic v2` model instance populated from the JSON string. Parse failures would raise `ValidationError` via `schema.model_validate_json` (present in `_parse_structured_response`), but the contract's happy-path test passes cleanly.
- `BrowserStateSummary`, `AgentOutput`, and `AgentHistory` all accept input data and serialize to `dict` via `model_dump()` without validation errors. The `AgentHistory` model correctly holds nested `AgentOutput` and `BrowserStateSummary` instances.
- Config loading correctly reads a JSON config file (headless=true in file, viewport 1200x800). The environment variable `BROWSER_USE_BROWSER_HEADLESS=false` overrides the file value to `False`, confirming three-tier precedence (env > JSON file > defaults). Width and height are read from the JSON file as expected.

## v0.2.0 — Sprint 2 [MINOR bump]

## v0.3.0 — Sprint 3 [MINOR bump]

## v0.4.0 — Sprint 4 [MINOR bump]
- All 6 stdout lines match the expected contract exactly. `Agent`, `MessageManager`, `ActionLoopDetector`, `AgentHistory`, `AgentHistoryList`, and `AgentOutput` are all importable from `browser_use.agent`. Save-to-file and load-from-file round-trip preserved history length (1), next_goal ("finish"), and URL ("https://example.test").
- FakeLLM was called twice (navigate on step 1, done on step 2). The Agent correctly routed the "navigate" action through the injected FakeTools, which called `browser_session.navigate`. The browser title was confirmed as "Sprint 4 Agent". `history.histories[-1].model_output.actions[0]["done"]["success"]` returned `True`. The loop terminated cleanly at step 2.
- With max_tokens=220 and keep_recent_steps=2 across 6 history entries:
- FakeTools was called three times: click, click, done. The ActionLoopDetector with `max_repetitions=2` detected the repeated "click" action after the second call and injected a loop-detection nudge into the third LLM prompt. The LLM received the nudge and returned a "done" action. The final action's `done.success` is `True`.
- The agent ran one step (done action). After save-to-file and load-from-file, the restored `AgentHistoryList` has 1 history entry. The restored entry has `state.title = "Persisted Run"`, `model_output.memory = "loaded"`, and `model_output.actions[0]["done"]["text"] = "finished"`. No data was lost in the round-trip.

## v0.5.0 — Sprint 5 [MINOR bump]

## v0.6.0 — Sprint 6 [MINOR bump]

## v0.7.0 — Sprint 7 [MINOR bump]

## v0.8.0 — Sprint 8 [MINOR bump]
- `mcp` subcommand present in top-level help with exit code 0. All required flags (`--stdio`, `--json`) present in `mcp --help`. The `--claude-config` flag is also present as an extra, bonus feature.
- Response contains `jsonrpc: "2.0"`, `id: 1`, and a `result` object. `result.serverInfo.name` = `browser-use`. `result.capabilities.tools` confirms MCP tools support. Both tools and resources capabilities are declared.
- 7 tools listed including all required `navigate`, `click`, `extract_content`, and `done`. Every tool has a human-readable `description` and a valid `inputSchema` object. Tools/list returned as response 2 after initialize (notification produces no response).
- Navigate call to file:// URL succeeded with `result.content` array containing JSON. Extract_content call returned `result.content` with inner JSON containing all expected keywords from the contract page (`Sprint 8 MCP`, `Contract Page`, `Continue`). Both responses are valid JSON-RPC with no errors.
- `resources/list` returned `browser-use://state/current` in the URIs array. `resources/read` for that URI returned a JSON object containing the page URL, title (`Sprint 8 Resource`), content, and interactive elements including the `aria-label: Search` input field.
- Output is valid JSON with `mcpServers` object. Server entry is named `browser-use`. `args` array contains both `mcp` and `--stdio` as required.

## v0.9.0 — Sprint 9 [MINOR bump]
- All four adapter classes are exported from `browser_use.llm`, importable as both top-level names and module-level names, and each instantiates without making any HTTP requests.
- `endpoint='international'` routes to `https://api.moonshot.ai/v1`, `endpoint='cn'` routes to `https://api.moonshot.cn/v1`. Request parameters (temperature, model, messages) are correctly forwarded. API keys are preserved per-client.
- `region='cn'` routes to `https://dashscope.aliyuncs.com/compatible-mode/v1`, `region='international'` routes to `https://dashscope-intl.aliyuncs.com/compatible-mode/v1`. When `thinking=True` and `thinking_budget=1024`, `extra_body` contains `enable_thinking: true` and `thinking_budget: 1024`.
- Base URL correctly set to `https://open.bigmodel.cn/api/paas/v4`. Request parameters `temperature=0.1` and `max_tokens=32` are correctly forwarded to the completions call.
- Both `MiniMax-M1` and `MiniMax-M2` models are accepted. When `reasoning=True` is set, `extra_body` contains `reasoning_split: true`. Endpoint correctly routes to `https://api.minimax.io/v1`.
- All four providers support `.with_structured_output(Probe)` and correctly parse Pydantic model responses from raw JSON content. Each provider returned the correct model name in the `answer` field and the integer `9` in the `score` field.

## v0.10.0 — Sprint 10 [MINOR bump]
- `python -m browser_use list-tools --json` returns valid JSON containing all three Sprint 10 actions. `search_google` schema has required `query` field. `open_tab` schema has required `url` field. `switch_tab` schema has required `tab_id` field.
- `open_tab` action created two distinct tabs with different tab IDs. `switch_tab` action correctly switched to each tab and `session.get_title()` returned the expected page title. `TabCreatedEvent` was emitted for each new tab. No errors.
- `Tools().execute_action({'search_google': {'query': 'browser use sprint 10 polish'}})` produced the exact expected URL `https://www.google.com/search?q=browser+use+sprint+10+polish`. Return value was `{'ok': True, 'url': expected, 'query': 'browser use sprint 10 polish'}`.
- `--log-json --log-file` produces JSON lines on disk. `--json` flag produces structured JSON to stdout. `BROWSER_USE_TRACE_ID` env var sets trace ID across both output streams. All trace IDs are consistent at `sprint10-trace-001`.
- `ObservabilityHub` dispatches events to both `langsmith` and `langfuse` hooks. Payload includes `trace_id`, `name`, and `payload` fields exactly as specified. No network access or third-party SDK imports required.
- `BrowserSession(profile=BrowserProfile(allowed_domains=['allowed.example'], proxy={'server': 'http://127.0.0.1:7777'}))` launched a Chromium process with `--proxy-server=http://127.0.0.1:7777` flag. Navigation to `https://blocked.example/...` raised `BrowserSecurityError` with `blocked.example` in the message. Both `session.navigate` and `session.open_tab` were blocked.

## v0.11.0 — Sprint 12 [MINOR bump]
- Deterministic exponential backoff confirmed. No jitter variance observed with jitter=0.0.
- classify_error() correctly maps BrowserSecurityError to NON_RECOVERABLE and TimeoutError to RECOVERABLE. No retry for non-recoverable errors.
- Browser state (url) preserved across retries. RetryController used injectably. Final history entry shows successful completion.
- Page fingerprint uses stable JSON of URL + normalized element keys (index, tag, tag_name, text, href, type). Changed element text produces different fingerprint, preventing false positive loop detection.
- Nudge injected via `manager.build_messages(state, nudge=nudge)`. Loop detector state reset after nudge consumed (consume_nudge clears recent_actions). Agent successfully broke the loop.
- RetryExhaustedError.summary is a structured dict with full attempt history including error_type, error message, category, and delay per attempt. Abort strategy selected from RecoveryStrategy enum.

## v0.12.0 — Sprint 13 [MINOR bump]

## v0.13.0 — Sprint 14 [MINOR bump]
- Public API is clean, fully async, and models serialize correctly. All 4 data models (BoundingBox, AnnotatedScreenshot, VisionAnalysis, VisionModel) are correctly exposed from `browser_use` package.
- Capture modes correctly delegate to Playwright page.screenshot with the proper clip/full_page options and JPEG quality settings.
- Annotation draws colored rectangles and index labels using PIL/Pillow. The hardcoded palette (pink, blue, green, amber, purple, teal) provides color differentiation for multiple boxes.
- The VisionService correctly builds multi-part messages with text + image_url, parses the model response into typed VisionAnalysis, and correlates results back to annotated screenshots.
- The refine loop correctly implements the threshold-gated retry pattern. It compares confidences, replaces the best result, and stops when threshold is met or max_refinements exhausted.
- MessageManager.build_messages correctly merges screenshots with browser state, placing DOM text context before the image data. The `data:image/jpeg;base64,` content-type marker is correctly emitted.

## v0.14.0 — Sprint 15 [MINOR bump]

## v0.15.0 — Sprint 16 [MINOR bump]

## v0.16.0 — Sprint 17 [MINOR bump]

## v0.17.0 — Sprint 18 [MINOR bump]

## v0.18.0 — Sprint 19 [MINOR bump]
- The public API surface is clean. `from browser_use import HistoryExporter` resolves to the correct module class. JSON schema is complete with every contracted field present.
- DOM diff algorithm correctly handles the three-element state transition chain. Added, removed, and modified elements are all properly computed from consecutive step comparisons.
- HTML is fully self-contained. All required UI components are present. Terminal Precision dark theme colors are embedded inline.
- GIF export produces a valid animated image with configurable parameters. JSON sidecar correctly encodes fps, loop, and per-frame action labels.
- The `browser-use-bridge replay` CLI command supports all contracted formats and correctly reports structured JSON output with generated file paths.
- The exporter correctly isolates exports by task_id even when checkpoint_id collides. Repeat calls with same parameters produce identical output (deterministic).

## v0.19.0 — Sprint 20 [MINOR bump]

## v0.11.0 — Sprint 11 [MINOR bump]

## v1.1.0 — Sprint 12 [MINOR bump]
- Deterministic exponential backoff confirmed. No jitter variance observed with jitter=0.0.
- classify_error() correctly maps BrowserSecurityError to NON_RECOVERABLE and TimeoutError to RECOVERABLE. No retry for non-recoverable errors.
- Browser state (url) preserved across retries. RetryController used injectably. Final history entry shows successful completion.
- Page fingerprint uses stable JSON of URL + normalized element keys (index, tag, tag_name, text, href, type). Changed element text produces different fingerprint, preventing false positive loop detection.
- Nudge injected via `manager.build_messages(state, nudge=nudge)`. Loop detector state reset after nudge consumed (consume_nudge clears recent_actions). Agent successfully broke the loop.
- RetryExhaustedError.summary is a structured dict with full attempt history including error_type, error message, category, and delay per attempt. Abort strategy selected from RecoveryStrategy enum.
- Fix: ChatQwen markdown-fenced JSON parsing regression
