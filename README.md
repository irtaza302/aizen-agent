# Aether AI Agent 🚀

A professional-grade AI coding assistant that runs directly in your terminal. Aether reads your code, writes files with surgical precision, runs commands safely, and helps you build faster — all from a beautifully designed CLI.

## ✨ Features

### Core
- **Rich Markdown Rendering** — AI responses are rendered with full Markdown formatting (headers, code blocks, lists, bold/italic) via Rich's live display.
- **Streaming with Live Preview** — Watch responses render in real-time inside a styled panel with animated thinking spinner.
- **Surgical File Editing** — The `edit_file` tool makes precise search-and-replace edits with color-coded diff previews, instead of rewriting entire files.
- **Smart Autocomplete** — `@`-mention files with Tab completion that respects `.gitignore` and supports directory traversal.

### Tools
Aether has 7 built-in tools the AI can use:

| Tool | Description |
|------|-------------|
| `read_file` | Read file contents before making changes |
| `write_file` | Create new files (with preview) |
| `edit_file` | Surgical search-and-replace on existing files (with diff preview) |
| `run_command` | Execute shell commands (safe commands auto-run, dangerous ones require approval) |
| `list_directory` | List files/folders with sizes, respecting `.gitignore` |
| `grep_search` | Search for text or regex patterns across the codebase |
| `find_files` | Find files by glob pattern (e.g., `*.py`, `Dockerfile`) |

### Commands

| Command | Description |
|---------|-------------|
| `/help` | Show all available commands |
| `/model [name]` | View or switch the active AI model |
| `/clear` | Clear conversation history |
| `/save [name]` | Save current conversation to disk |
| `/load [name]` | Load a previously saved conversation |
| `/usage` | Show token usage and session statistics |
| `/compact` | Summarize older messages to save tokens |
| `/undo` | Undo the last file modification |
| `/retry` | Retry the last message |
| `/copy` | Copy last AI response to clipboard |
| `/export [file]` | Export conversation to a Markdown file |
| `/config` | View current configuration |

### Safety & UX
- **Command Safety** — Read-only commands (`ls`, `cat`, `git status`, etc.) auto-execute. Destructive commands (`rm`, `sudo`, etc.) always require confirmation.
- **`--yolo` Mode** — Auto-approve all operations for power users.
- **File Backups** — Every file modification creates a backup. Use `/undo` to restore.
- **Multi-line Input** — End a line with `\` to continue on the next line.
- **Session Persistence** — Conversations auto-save on exit. Use `/save` and `/load` to manage.
- **Token Tracking** — Track input/output tokens and session duration with `/usage`.
- **Graceful Error Recovery** — Helpful hints for common API errors (invalid key, rate limits, timeouts).

## Dependencies

- `openai` — OpenAI-compatible API client
- `python-dotenv` — Environment variable management
- `rich` — Rich text, Markdown rendering, panels, tables, and live display
- `prompt_toolkit` — Interactive command line with autocomplete

## Installation

### 1. Python (pip / pipx) — Recommended
```bash
pipx install aether-ai-cli
# Or:
pip install aether-ai-cli
```

### 2. NPM (Node.js)
```bash
npm install -g aether-ai-cli
```

### 3. Homebrew (macOS)
```bash
brew tap irtaza302/aether-agent
brew install aether
```

### 4. Local Development
```bash
git clone https://github.com/irtaza302/aether-agent.git
cd aether-agent
pip install -r requirements.txt
python aether.py
```

## Usage

```bash
aether
```

On first launch, you'll be prompted for your [OpenRouter API key](https://openrouter.ai/keys). It's saved securely to `~/.aether_config.json`.

### Command Line Arguments

| Flag | Description |
|------|-------------|
| `--version` | Show version |
| `--model <name>` | Override the default model for this session |
| `--reset-key` | Clear and re-enter your API key |
| `--set-base-url <url>` | Set custom API base URL (e.g., `http://localhost:11434/v1` for Ollama) |
| `--yolo` | Auto-approve all file writes and command executions |

### Attaching Files

Type `@` followed by a filename to give Aether context. Autocomplete filters out `.gitignore`d files:

```
👤 You
❯ Can you refactor @aether.py to use async?
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

Aether stores its config in `~/.aether_config.json`:

```json
{
  "OPENROUTER_API_KEY": "sk-or-...",
  "API_BASE_URL": "https://openrouter.ai/api/v1",
  "DEFAULT_MODEL": "anthropic/claude-sonnet-4"
}
```

Sessions are saved to `~/.aether_sessions/` and file backups to `~/.aether_backups/`.

## Publishing & Development

Use the included `publish.sh` script to build and publish across all platforms (PyPI, NPM, and PyInstaller binaries).