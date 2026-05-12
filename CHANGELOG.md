
## v0.1.0 — Sprint 1 [MINOR bump]
- All public API classes import cleanly with no network calls. Package `__init__.py` files correctly re-export the public API surface.
- All three provider adapters (OpenAI, Anthropic, Google) expose `ainvoke`, `astream`, `with_structured_output`, and `bind_tools`. The `ainvoke` method is a coroutine function. Adapters instantiate with dummy credentials without contacting any provider.
- `StructuredOutputChatModel` correctly wraps a `BaseChatModel`, and `ainvoke` returns a `Pydantic v2` model instance populated from the JSON string. Parse failures would raise `ValidationError` via `schema.model_validate_json` (present in `_parse_structured_response`), but the contract's happy-path test passes cleanly.
- `BrowserStateSummary`, `AgentOutput`, and `AgentHistory` all accept input data and serialize to `dict` via `model_dump()` without validation errors. The `AgentHistory` model correctly holds nested `AgentOutput` and `BrowserStateSummary` instances.
- Config loading correctly reads a JSON config file (headless=true in file, viewport 1200x800). The environment variable `BROWSER_USE_BROWSER_HEADLESS=false` overrides the file value to `False`, confirming three-tier precedence (env > JSON file > defaults). Width and height are read from the JSON file as expected.

## v0.2.0 — Sprint 2 [MINOR bump]

## v0.3.0 — Sprint 3 [MINOR bump]
