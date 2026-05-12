
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
