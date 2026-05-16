# Browser Use Bridge

**English** | [中文](#中文说明)

---

AI browser automation bridge with first-class support for Chinese LLMs, custom model providers, and any OpenAI-compatible endpoint.

Built on top of [browser-use](https://github.com/browser-use/browser-use) — extending it with Chinese LLM adapters, vision understanding, memory, checkpointing, and more.

[![PyPI](https://img.shields.io/badge/PyPI-1.0.0-blue)](https://pypi.org/project/browser-use-bridge/1.0.0/)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://pypi.org/project/browser-use-bridge/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## What's Different from browser-use

`browser-use-bridge` is a fork of [browser-use](https://github.com/browser-use/browser-use) with the following additions and changes:

### Added

| Feature | Details |
|---|---|
| **Chinese LLM adapters** | Native support for Kimi (Moonshot), Qwen (DashScope), GLM (Zhipu), MiniMax, DeepSeek — no LangChain required |
| **Custom model provider** | `ChatCustom`: point at any OpenAI-compatible endpoint with `base_url` + `api_key` |
| **Ollama local models** | `ChatOllama` with health checking, model discovery, streaming, and vision model support |
| **Vision understanding** | `VisionService`: screenshot → annotated image → Vision LLM analysis; automatic fallback when DOM is sparse |
| **Planner / Controller separation** | Two-agent architecture: Planner decomposes tasks into sub-goals; Controller executes and verifies each step |
| **Memory store** | BM25 keyword retrieval (zero deps) or ChromaDB vector backend; injected into Agent context automatically |
| **Checkpoint / Resume** | `CheckpointManager`: save task state at any step, resume after interruption |
| **History export** | `HistoryExporter`: export completed runs as JSON, self-contained HTML timeline, or animated GIF |
| **Structured retry** | `RetryController`: exponential backoff, error classification, loop detection with page fingerprinting |
| **Updated default models** | Kimi `kimi-2.6`, Qwen `qwen3.6-plus`, GLM `glm-5.1`, MiniMax `MiniMax-M2.7`, DeepSeek `deepseek-v4-pro` |
| **Independent packaging** | Published as `browser-use-bridge` on PyPI with optional dependency groups per provider |

### Changed

| Aspect | browser-use | browser-use-bridge |
|---|---|---|
| Package name | `browser_use` | `browser_use_bridge` |
| CLI command | `browser-use` | `browser-use-bridge` |
| LLM base class | LangChain `BaseChatModel` | Lightweight custom `BaseChatModel` (no LangChain dependency) |
| Provider auto-detection | — | Detects Chinese gateways from `base_url` pattern |

---

## Installation

```bash
pip install browser-use-bridge
```

Install with Chinese LLM SDKs:

```bash
pip install "browser-use-bridge[cn]"        # Qwen (DashScope) + GLM (Zhipu) + Anthropic
pip install "browser-use-bridge[kimi]"       # Moonshot Kimi
pip install "browser-use-bridge[deepseek]"   # DeepSeek
pip install "browser-use-bridge[minimax]"    # MiniMax
pip install "browser-use-bridge[ollama]"     # Ollama local models
pip install "browser-use-bridge[all]"        # Everything
```

---

## Quick Start

### Python API

```python
import asyncio

from browser_use_bridge import Agent, BrowserSession
from browser_use_bridge.llm import ChatKimi

async def main():
    session = BrowserSession()
    try:
        await session.start()
        agent = Agent(
            task="Search for the latest AI news and summarize the top 3 results",
            llm=ChatKimi(model="kimi-2.6", api_key="your-key"),
            browser_session=session,
        )
        history = await agent.run()
        return history
    finally:
        await session.close()

history = asyncio.run(main())
```

### With Memory and Checkpoint

```python
import asyncio

from browser_use_bridge import Agent
from browser_use_bridge.browser import BrowserSession
from browser_use_bridge.llm import ChatQwen
from browser_use_bridge.memory import MemoryStore
from browser_use_bridge.checkpoint import CheckpointManager

async def main():
    session = BrowserSession()
    checkpoint_manager = CheckpointManager(autosave_every_steps=5)
    try:
        await session.start()
        agent = Agent(
            task="Fill in the registration form at example.com",
            llm=ChatQwen(model="qwen3.6-plus"),
            browser_session=session,
            memory_store=MemoryStore(),
        )
        history = await agent.run()
        checkpoint_manager.save(
            task_id="registration-form",
            step_counter=len(history.histories),
            current_url=await session.get_current_url(),
            agent_history=history.model_dump(mode="json"),
            label="completed",
        )
        return history
    finally:
        await session.close()

history = asyncio.run(main())
```

### CLI

```bash
# Run a task
browser-use-bridge run --task "Open baidu.com and search for Python" --provider kimi

# List all registered tools
browser-use-bridge list-tools

# Start MCP server for Claude Desktop
browser-use-bridge mcp --stdio

# Resume an interrupted task
browser-use-bridge resume <checkpoint_id>

# List saved checkpoints
browser-use-bridge checkpoint list
```

### Export History

```python
from browser_use_bridge.history import HistoryExporter

exporter = HistoryExporter(output_dir="history-exports")
artifacts = exporter.export("<checkpoint_id>", format="html")
print(artifacts["html"])
```

### Custom / Local Model

```python
from browser_use_bridge.llm import ChatCustom

# Any OpenAI-compatible endpoint
llm = ChatCustom(
    model="my-model",
    base_url="http://localhost:8080/v1",
    api_key="optional",
)
```

---

## Supported Providers

| Provider | Class | Default Model | Install |
|---|---|---|---|
| OpenAI | `ChatOpenAI` | `gpt-4o` | built-in |
| Anthropic | `ChatAnthropic` | `claude-sonnet-4-20250514` | `[cn]` |
| Google Gemini | `ChatGoogle` | `gemini-2.0-flash` | built-in |
| Kimi (Moonshot) | `ChatKimi` | `kimi-2.6` | built-in |
| Qwen (DashScope) | `ChatQwen` | `qwen3.6-plus` | `[cn]` |
| GLM (Zhipu) | `ChatGLM` | `glm-5.1` | `[cn]` |
| MiniMax | `ChatMiniMax` | `MiniMax-M2.7` | built-in |
| DeepSeek | `ChatDeepSeek` | `deepseek-v4-pro` | built-in |
| Ollama (local) | `ChatOllama` | `llama3` | `[ollama]` |
| Custom endpoint | `ChatCustom` | configurable | built-in |

---

## Environment Variables

Create a `.env` file in your project root:

```env
MOONSHOT_API_KEY=your-kimi-key
DASHSCOPE_API_KEY=your-qwen-key
ZHIPU_API_KEY=your-glm-key
MINIMAX_API_KEY=your-minimax-key
DEEPSEEK_API_KEY=your-deepseek-key
OPENAI_API_KEY=your-openai-key
```

---

## License

MIT — see [LICENSE](LICENSE).

Original [browser-use](https://github.com/browser-use/browser-use) is also MIT licensed.

---

# 中文说明

**[English](#browser-use-bridge)** | 中文

---

基于 [browser-use](https://github.com/browser-use/browser-use) 构建的 AI 浏览器自动化框架，新增国产大模型支持、视觉理解、记忆存储、断点续传等能力。

---

## 相比 browser-use 的改动说明

`browser-use-bridge` 是 [browser-use](https://github.com/browser-use/browser-use) 的 Fork 版本，主要改动如下：

### 新增功能

| 功能 | 说明 |
|---|---|
| **国产大模型适配器** | 原生支持 Kimi（月之暗面）、通义千问（DashScope）、智谱 GLM、MiniMax、DeepSeek，无需 LangChain |
| **自定义模型提供商** | `ChatCustom`：通过 `base_url` + `api_key` 接入任意 OpenAI 兼容接口 |
| **Ollama 本地模型** | `ChatOllama`：含健康检查、模型发现、流式输出、视觉模型支持 |
| **视觉理解模块** | `VisionService`：截图 → 标注图像 → Vision LLM 分析；DOM 稀少时自动降级到视觉模式 |
| **Planner / Controller 分离** | 双 Agent 架构：Planner 将任务分解为子目标，Controller 逐步执行并验证 |
| **记忆存储** | BM25 关键词检索（零依赖）或 ChromaDB 向量后端；自动注入 Agent 上下文 |
| **断点续传** | `CheckpointManager`：任意步骤保存任务状态，中断后可恢复 |
| **历史回放导出** | `HistoryExporter`：导出为 JSON、自包含 HTML 时间线、或 GIF 动画 |
| **结构化重试** | `RetryController`：指数退避、错误分级、基于页面指纹的循环检测 |
| **最新默认模型** | Kimi `kimi-2.6`、千问 `qwen3.6-plus`、GLM `glm-5.1`、MiniMax `MiniMax-M2.7`、DeepSeek `deepseek-v4-pro` |
| **独立 PyPI 发布** | 以 `browser-use-bridge` 发布，各模型 SDK 按需安装 |

### 变更对比

| 方面 | browser-use | browser-use-bridge |
|---|---|---|
| 包名 | `browser_use` | `browser_use_bridge` |
| CLI 命令 | `browser-use` | `browser-use-bridge` |
| LLM 基类 | LangChain `BaseChatModel` | 轻量自研 `BaseChatModel`（无 LangChain 依赖） |
| 国产模型接入 | 不支持 | 原生支持，含 API Key 自动读取 |

---

## 安装

```bash
pip install browser-use-bridge
```

安装国产模型 SDK：

```bash
pip install "browser-use-bridge[cn]"        # 千问 + GLM + Anthropic
pip install "browser-use-bridge[kimi]"       # Kimi（月之暗面）
pip install "browser-use-bridge[deepseek]"   # DeepSeek
pip install "browser-use-bridge[minimax]"    # MiniMax
pip install "browser-use-bridge[ollama]"     # Ollama 本地模型
pip install "browser-use-bridge[all]"        # 全部安装
```

---

## 快速开始

### Python API

```python
import asyncio

from browser_use_bridge import Agent
from browser_use_bridge.browser import BrowserSession
from browser_use_bridge.llm import ChatKimi

async def main():
    session = BrowserSession()
    try:
        await session.start()
        agent = Agent(
            task="搜索最新的 AI 新闻，总结前 3 条结果",
            llm=ChatKimi(model="kimi-2.6", api_key="your-key"),
            browser_session=session,
        )
        history = await agent.run()
        return history
    finally:
        await session.close()

history = asyncio.run(main())
```

### 带记忆和断点续传

```python
import asyncio

from browser_use_bridge import Agent
from browser_use_bridge.browser import BrowserSession
from browser_use_bridge.llm import ChatQwen
from browser_use_bridge.memory import MemoryStore
from browser_use_bridge.checkpoint import CheckpointManager

async def main():
    session = BrowserSession()
    checkpoint_manager = CheckpointManager(autosave_every_steps=5)
    try:
        await session.start()
        agent = Agent(
            task="填写 example.com 的注册表单",
            llm=ChatQwen(model="qwen3.6-plus"),
            browser_session=session,
            memory_store=MemoryStore(),
        )
        history = await agent.run()
        checkpoint_manager.save(
            task_id="registration-form",
            step_counter=len(history.histories),
            current_url=await session.get_current_url(),
            agent_history=history.model_dump(mode="json"),
            label="completed",
        )
        return history
    finally:
        await session.close()

history = asyncio.run(main())
```

### CLI

```bash
# 执行任务
browser-use-bridge run --task "打开百度搜索 Python" --provider kimi

# 列出所有工具
browser-use-bridge list-tools

# 启动 MCP 服务（供 Claude Desktop 使用）
browser-use-bridge mcp --stdio

# 恢复中断的任务
browser-use-bridge resume <checkpoint_id>

# 列出已保存的断点
browser-use-bridge checkpoint list
```

### 导出历史

```python
from browser_use_bridge.history import HistoryExporter

exporter = HistoryExporter(output_dir="history-exports")
artifacts = exporter.export("<checkpoint_id>", format="html")
print(artifacts["html"])
```

### 自定义 / 本地模型

```python
from browser_use_bridge.llm import ChatCustom

# 任意 OpenAI 兼容接口
llm = ChatCustom(
    model="my-model",
    base_url="http://localhost:8080/v1",
    api_key="optional",
)
```

---

## 支持的模型提供商

| 提供商 | 类名 | 默认模型 | 安装方式 |
|---|---|---|---|
| OpenAI | `ChatOpenAI` | `gpt-4o` | 内置 |
| Anthropic | `ChatAnthropic` | `claude-sonnet-4-20250514` | `[cn]` |
| Google Gemini | `ChatGoogle` | `gemini-2.0-flash` | 内置 |
| Kimi（月之暗面） | `ChatKimi` | `kimi-2.6` | 内置 |
| 通义千问（DashScope） | `ChatQwen` | `qwen3.6-plus` | `[cn]` |
| 智谱 GLM | `ChatGLM` | `glm-5.1` | `[cn]` |
| MiniMax | `ChatMiniMax` | `MiniMax-M2.7` | 内置 |
| DeepSeek | `ChatDeepSeek` | `deepseek-v4-pro` | 内置 |
| Ollama（本地） | `ChatOllama` | `llama3` | `[ollama]` |
| 自定义接口 | `ChatCustom` | 可配置 | 内置 |

---

## 环境变量

在项目根目录创建 `.env` 文件：

```env
MOONSHOT_API_KEY=your-kimi-key
DASHSCOPE_API_KEY=your-qwen-key
ZHIPU_API_KEY=your-glm-key
MINIMAX_API_KEY=your-minimax-key
DEEPSEEK_API_KEY=your-deepseek-key
OPENAI_API_KEY=your-openai-key
```

---

## 开源协议

MIT — 详见 [LICENSE](LICENSE)。

原项目 [browser-use](https://github.com/browser-use/browser-use) 同样采用 MIT 协议。
