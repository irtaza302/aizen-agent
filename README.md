# Aizen AI Agent 🚀

[![CI](https://github.com/irtaza302/aizen-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/irtaza302/aizen-agent/actions/workflows/ci.yml)

Aizen is a powerful, asynchronous AI assistant that integrates seamlessly into your terminal workflow. It reads your code, edits files safely, runs commands, and provides real‑time, richly formatted assistance—all while keeping costs transparent and sessions persistent.

## 🌟 Key Benefits

- **Effortless Integration** — Operates directly in your terminal, preserving shell state across commands.
- **One-Shot & Scripting** — Use the `-p` flag for fast CLI pipelines or install shell hooks with `--install-shell`.
- **Intelligent Context** — Auto-detects project languages, frameworks, and Git state on startup.
- **Persistent Memory** — AI that learns your preferences across sessions using local SQLite memory.
- **Git & PR Workflow** — Built-in commands to branch, stash, amend, and create PRs with AI.
- **Cost Guardrails** — Real‑time cost tracking, cross-session analytics (`/stats`), and strict budget caps (`--budget`).
- **Rich Visual Feedback** — Stream responses with live previews, with graceful Ctrl+C cancellation.
- **Semantic Codebase Search** — Fast local RAG (Retrieval-Augmented Generation) using the `/search` command.
- **Extensible Architecture** — Custom plugins, MCP integration, and project‑specific rules tailor Aizen to your workflow.

## 🚀 Core Features

### Asynchronous Architecture
- Fully asynchronous operations using `asyncio` and `AsyncOpenAI` for concurrent processing, parallel tool runs, and streaming. Native retry logic ensures resilience against API blips.

### One-Shot & Shell Integration
- Run `aizen -p "fix this"` for a single non-interactive response, or pipe input via `cat log.txt | aizen -p "summarize"`. Run `aizen --install-shell` to get the handy `ai` command wrapper.

### Smart Auto-Context & Memory
- Aizen automatically parses `package.json`, `pyproject.toml`, etc. to understand your stack.
- Uses `~/.aizen_memory.db` to remember architectural decisions and user preferences autonomously using the `remember_fact` tool.

### Stateful Terminal Session
- Environment variables and directory changes persist across interactions.

### Rich Markdown Rendering
- Full Markdown support with headers, code blocks, lists, and styling via Rich.

### Surgical File Editing
- Precise search‑and‑replace with color‑coded diff previews (`edit_file`).

### Vision Support
- Native image handling and encoding for Vision APIs (e.g., GPT‑4o, Claude 3.5 Sonnet).

## 🎛️ Workflow Commands

- `/auto <task>` — Enter autonomous agentic mode for a complex task.
- `/commit` — Auto-generate and commit changes.
- `/pr [title]` — Create a GitHub PR with an AI-generated description.
- `/branch`, `/stash`, `/amend`, `/log` — Full AI-assisted git workflow.
- `/remember <fact>` — Store a fact in persistent memory.
- `/memory` — View all stored memories.
- `/forget <id>` — Remove a specific memory.

## 💰 Cost Tracking & Analytics

- **Real-time Pricing**: Pulls live pricing from OpenRouter cache.
- **Budgeting**: Enforce session limits with `--budget 0.50` or `/budget`.
- **Analytics**: Cross-session usage tracked in SQLite. Run `/stats` for a beautiful summary and sparkline chart.
- **Multi-Model Routing**: Override the global model inline by typing `@claude-3.5-sonnet <prompt>`. Supports Anthropic, Google, OpenAI, DeepSeek, and Meta models.

## 📌 Session Management & Search

- `/search [query]` — Perform semantic search across your codebase.
- `/reindex [dir]` — Manually trigger indexing for local semantic search.
- `/checkpoint [name]` — Save conversation snapshots.
- `/restore [name]` — Restore a previous checkpoint.
- `/export` — Export conversation to Markdown.

## 📁 Structured Logging

- Logs stored at `~/.aizen_logs/aizen.log` (rotated, 5 MB caps, 3 files).
- Verbose flag mirrors output to console.

## 📦 Publishing & Development

- Use `publish.sh` to build and publish to PyPI, NPM, and PyInstaller binaries.