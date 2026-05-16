# Browser Use Bridge

AI browser automation bridge for Chinese LLMs, custom model providers, and OpenAI-compatible endpoints.

Browser Use Bridge lets an agent inspect pages, reason over DOM state, and execute browser actions through a Python API, CLI, and MCP server. It is designed for Playwright-based browser automation with first-class support for providers such as Kimi, Qwen, GLM, MiniMax, DeepSeek, Ollama, and custom OpenAI-compatible endpoints.

## Install

```bash
pip install browser-use-bridge
```

Optional provider groups:

```bash
pip install "browser-use-bridge[cn]"
pip install "browser-use-bridge[ollama]"
```

## Python API

```python
from browser_use_bridge import Agent, BrowserSession, Tools
```

## CLI

```bash
browser-use-bridge --help
browser-use-bridge list-tools --json
browser-use-bridge mcp --claude-config --json
```

## License

MIT
