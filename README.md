# Aizen AI Agent 🚀

[![CI](https://github.com/irtaza302/aizen-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/irtaza302/aizen-agent/actions/workflows/ci.yml)

A helpful AI coding assistant you can use right in your terminal. Aizen reads your code, edits files safely, runs commands, and helps you get things done faster — all with a friendly command‑line interface.

## ✨ Features

### Core
- **Asynchronous Architecture** — Fully asynchronous operations leveraging `asyncio` and `AsyncOpenAI` for concurrent processing, parallel tool runs, and streaming. Native retry logic ensures resilience against API blips.
- **Stateful Terminal Session** — Commands execute in a persistent Bash shell, meaning environment variables (`export`) and directory changes (`cd`) persist naturally across tool calls.
- **Persistent Memory** — AI that learns your preferences across sessions using local SQLite memory (`~/.aizen_memory.db`) and autonomously remembers architectural decisions using the `remember_fact` tool.
- **Semantic Codebase Search** — Fast local RAG (Retrieval-Augmented Generation) using the `/search` command.
- **One-Shot & Scripting** — Use the `-p` flag for fast CLI pipelines or install shell hooks with `--install-shell`.
- **Custom Plugin System** — Easily extend Aizen by dropping Python scripts into `~/.aizen/plugins/` to register custom AI tools.
- **Rich Markdown Rendering** — AI responses are rendered with full Markdown formatting (headers, code blocks, lists, bold/italic) via Rich's live display.
- **Streaming with Live Preview** — Watch responses render in real-time inside a styled panel with an animated thinking spinner.
- **Surgical File Editing** — The `edit_file` tool makes precise search-and-replace edits with color-coded diff previews, instead of rewriting entire files.
- **SQLite Session Persistence** — Session storage is powered by a SQLite database (`~/.aizen_sessions/aizen.db`), auto-migrating older JSON sessions.
- **Project-Specific Rules** — Customizes agent behavior per repository by auto-loading `.aizen_rules` or `.cursorrules` from the current working directory.
- **Smart Context & Autocomplete** — Auto-detects project languages/frameworks on startup. `@`-mention files with Tab completion that respects `.gitignore` and supports directory traversal.
- **Vision Support** — Attach images natively (e.g., `@mockup.png`) and Aizen will automatically encode them for Vision APIs (GPT-4o, Claude 3.5 Sonnet).
- **Real-time Command Streaming** — Long-running shell commands stream their output live to the terminal instead of freezing with a spinner.
- **Cost Guardrails** — Real‑time cost tracking, cross-session analytics (`/stats`), and strict budget caps (`--budget`).

### Tools
Aizen has built-in tools the AI can use:

| Tool | Description |
|------|-------------|
| `read_file` | Read file contents before making changes |
| `write_file` | Create new files or overwrite entirely (with preview) |
| `replace_file_content` | Surgical search-and-replace on existing files (with line-bounds and diff preview) |
| `multi_replace_file_content` | Perform multiple, non-adjacent surgical edits sequentially in a single pass |
| `run_terminal_command` | Execute shell commands in a persistent stateful bash session (supports background isolated execution) |
| `check_background_task` | Check the status and read recent output of a command running in the background |
| `kill_background_task` | Kill a running background task |
| `list_directory` | List files/folders with sizes, respecting `.gitignore` |
| `grep_search` | Search for text or regex patterns across the codebase |
| `find_files` | Find files by glob pattern (e.g., `*.py`, `Dockerfile`) |
| `get_file_outline` | Extract AST outline of a Python file (classes, methods, docstrings) without blowing up the context window |
| `web_search` | Search the web for current information, docs, or API references |
| `remember_fact` | Store a fact in persistent memory (used autonomously by AI) |

### Commands

| Command | Description |
|---------|-------------|
| `/help` | Show all available commands |
| `/auto [task]` | Enter a fully autonomous agentic loop to execute a complex task step-by-step |
| `/model [name]` | View or switch the active AI model (saves as default) |
| `/clear` | Clear conversation history |
| `/drop` | Drop attached files/URLs/commands from history to save tokens |
| `/save [name]` | Save current conversation to SQLite database |
| `/load [name]` | Load a previously saved conversation |
| `/checkpoint [name]` | Save a conversation snapshot to memory |
| `/restore [name]` | Restore a saved conversation checkpoint |
| `/search [query]` | Perform semantic search across your codebase |
| `/reindex [dir]` | Manually trigger indexing for local semantic search |
| `/usage` | Show token usage, estimated session cost (USD), and statistics |
| `/stats` | Cross-session usage tracked in SQLite. Run for a beautiful summary and sparkline chart |
| `/budget [amt]` | Enforce session limits |
| `/commit` | Auto-generate a commit message for staged/unstaged changes and commit them |
| `/pr [title]` | Create a GitHub PR with an AI-generated description |
| `/branch`, `/stash`, `/amend`, `/log` | Full AI-assisted git workflow |
| `/diff` | Show all uncommitted changes (staged, unstaged, untracked) |
| `/compact` | Summarize older messages using AI to save tokens |
| `/remember <fact>` | Store a fact in persistent memory |
| `/memory` | View all stored memories |
| `/forget <id>` | Remove a specific memory |
| `/undo` | Undo the last file modification |
| `/retry` | Retry the last message |
| `/copy` | Copy last AI response to clipboard |
| `/export [file]` | Export conversation to a Markdown file |
| `/config` | View current configuration |
| `/mcp` | View configured MCP servers and their connection status |

### Safety & UX
- **Command Safety** — Read-only commands (`ls`, `cat`, `git status`, etc.) auto-execute. Destructive commands (`rm`, `sudo`, etc.) always require confirmation.
- **Autonomous Limits** — The `/auto` mode enforces a strict 25-step execution limit to prevent infinite loops and runaway costs.
- **`--yolo` Mode** — Auto-approve all operations for power users.
- **Background Tasks** — Run builds, tests, or other long-running tasks asynchronously while continuing to interact with Aizen.
- **File Backups** — Every file modification creates a backup. Use `/undo` to restore.
- **Multi-line Input** — End a line with `\` to continue on the next line.
- **Cost Tracking & Token Usage** — Live tracking of input/output tokens, session duration, and estimated session cost in USD. Pulls live pricing from OpenRouter cache.
- **Multi-Model Routing** — Override the global model inline by typing `@claude-3.5-sonnet <prompt>`. Supports Anthropic, Google, OpenAI, DeepSeek, and Meta models.
- **Structured Logging** — Rotated file logging at `~/.aizen_logs/aizen.log` plus verbose console debugging logs via `--verbose`.
- **Graceful Error Recovery** — Helpful hints for common API errors (invalid key, rate limits, timeouts).

## Dependencies

- `openai` — OpenAI-compatible API client
- `python-dotenv` — Environment variable management
- `rich` — Rich text, Markdown rendering, panels, tables, and live display
- `prompt_toolkit` — Interactive command line with autocomplete

## Installation

### 1. Python (pip / pipx) — Recommended
```bash
pipx install aizen-ai-cli
# Or:
pip install aizen-ai-cli
```

### 2. NPM (Node.js)
```bash
npm install -g aizen-ai-cli
```

### 3. Homebrew (macOS)
```bash
brew tap irtaza302/aizen
brew install aizen
```

### 4. Local Development
```bash
git clone https://github.com/irtaza302/aizen-agent.git
cd aizen-agent
pip install -r requirements.txt
python aizen.py
```

## Usage

```bash
aizen
```
Or for a single non-interactive response:
```bash
aizen -p "fix this"
cat log.txt | aizen -p "summarize"
```

On first launch, you'll be prompted for your [OpenRouter API key](https://openrouter.ai/keys). It's saved securely to `~/.aizen_config.json`.

### Command Line Arguments

| Flag | Description |
|------|-------------|
| `--version` | Show version |
| `-p`, `--prompt` | Run a single prompt (non-interactive one-shot mode) |
| `--install-shell` | Install `ai` shell alias |
| `--budget <amt>` | Enforce strict session spending limit in USD |
| `--model <name>` | Override the default model for this session |
| `--reset-key` | Clear and re-enter your API key |
| `--set-base-url <url>` | Set custom API base URL (e.g., `http://localhost:11434/v1` for Ollama) |
| `--yolo` | Auto-approve all file writes and command executions |
| `--verbose` | Enable verbose logging output to the console |

### Attaching Context (`@`)

Type `@` followed by a filename, directory, web URL, or command to give Aizen context. Autocomplete filters out `.gitignore`d files:

- **Files:** `@aizen.py` attaches the file contents.
- **Directories:** `@tests/` generates and attaches a visual directory tree respecting `.gitignore`.
- **URLs:** `@https://docs.python.org/...` fetches the webpage, converts it to markdown, and attaches it.
- **Commands:** `@cmd:"pytest"` or `@cmd:ls` securely runs the command in the background and injects its `stdout` and `stderr` directly into the prompt.

```
👤 You
❯ Can you refactor @aizen.py to use async?

👤 You
❯ Explain this output: @cmd:"npm run build"
```

### Multi-line Input

End a line with `\` to continue typing on the next line:

```
👤 You
❯ Write a function that \
⋮  takes a list of numbers \
⋮  and returns the sorted unique values
```

## Configuration

Aizen stores its config in `~/.aizen_config.json`:

```json
{
  "OPENROUTER_API_KEY": "sk-or-...",
  "API_BASE_URL": "https://openrouter.ai/api/v1",
  "DEFAULT_MODEL": "anthropic/claude-sonnet-4"
}
```

### Custom Plugin System

Aizen supports a dead-simple custom Python plugin system! If you want to give the AI access to internal company APIs, smart home controls, or custom scripts, you can easily create custom tools.

Just drop a `.py` file into `~/.aizen/plugins/` that exposes a tool schema and an execution function, and Aizen will automatically load it on startup. 

See the full guide in [PLUGINS.md](PLUGINS.md) for a quick example!

### Model Context Protocol (MCP) Support

Aizen supports integrating with external [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) servers to extend its capabilities (e.g. connecting to local databases, searching the web, or accessing custom APIs).

To configure MCP servers, add an `"mcp_servers"` block to your `~/.aizen_config.json`:

```json
{
  "mcp_servers": {
    "sqlite": {
      "command": "uvx",
      "args": ["mcp-server-sqlite", "--db-path", "~/test.db"]
    },
    "everything": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-everything"]
    }
  }
}
```

When you start Aizen, it will automatically connect to these servers and make their tools available to the AI.

Sessions are saved in a SQLite database at `~/.aizen_sessions/aizen.db`, and file backups are placed in `~/.aizen_backups/`.

### 📂 Project-Specific Rules

Aizen supports loading custom, project-specific rules files (such as `.aizen_rules` or `.cursorrules`) from the root of your project directory. 
When Aizen starts, it checks for these files in the current working directory in the following order:
1. `.aizen_rules`
2. `.cursorrules`

If one is found, Aizen automatically appends its contents to the system prompt. This allows you to enforce codebase-specific styling guidelines, coding standards, or project rules without editing Aizen's global configuration.

### 🔄 Background Task Management

For long-running processes (e.g., running test suites, starting local dev servers, or building bundles), you can run commands in the background asynchronously:
- Aizen's `run_command` tool supports a boolean `background` parameter. If set to `true`, the tool immediately returns a unique `task_id` (e.g., `bg_a1b2c3d4`).
- You can inspect the status and read the recent stdout/stderr output of a background task using the `check_background_task` tool.
- You can terminate any active background task using the `kill_background_task` tool.

This allows you to continue discussing other topics or refactoring files with Aizen while your tests or builds run in parallel.

### 💰 Cost Tracking

Aizen dynamically estimates session costs in USD for known models based on token usage:
- Input and output tokens are tracked in real-time.
- The estimated session cost is displayed in the CLI status bar and summary tables (via the `/usage` command).
- The cost calculations support popular models from Anthropic (Claude 3.5/3.7 Sonnet, Opus, Haiku), Google (Gemini 2.5 Pro/Flash), and OpenAI (GPT-4o, o1, o3-mini).

### 📌 Session Checkpoints & Restoring

You can save and restore conversation snapshots at any point during your session:
- `/checkpoint [name]`: Save the current conversation messages history as a named snapshot in memory.
- `/restore [name]`: Revert the conversation history to the specified checkpoint. If run without a name, it lists all currently active checkpoints.

This is extremely useful when experimenting with different implementation approaches or when recovering from an unintended direction.

### 📝 Structured Logging

All internal activities, tool calls, and API events are written to a rotating file logger:
- Logs are located at `~/.aizen_logs/aizen.log`.
- Up to three rotated log files are kept (5 MB per file limit).
- You can run Aizen with the `--verbose` flag to mirror log output directly to the console stderr stream.

## Publishing & Development

Use the included `publish.sh` script to build and publish across all platforms (PyPI, NPM, and PyInstaller binaries).