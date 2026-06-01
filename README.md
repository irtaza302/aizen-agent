# Aether AI Agent 🚀

Aether is a sleek, intelligent AI coding assistant that runs directly in your terminal. It helps you code, reads your files, and can run commands for you—all from a beautifully designed CLI interface.

## Features
- **Sleek CLI & Streaming:** Beautifully styled terminal UI with custom ASCII art, magenta accents, and real-time streaming responses.
- **Slash Commands:** Switch models or clear context on-the-fly using commands like `/model`, `/clear`, and `/help`.
- **Smart Autocomplete:** Respects `.gitignore` and filters out boilerplate folders (like `node_modules`, `venv`, `.git`) when attaching files with `@`.
- **Workspace Exploration Tools:** Dynamic workspace listing (`list_directory`) and semantic searches (`grep_search`) let the agent study the workspace.
- **Interactive Diff Previews:** Color-coded diff verification (green additions, red deletions) before any file write operations.
- **Flexible Configuration:** Set custom models, keys, and base URLs (supporting local Ollama, Gemini, Anthropic, or OpenAI).

## Dependencies
Aether requires the following Python packages:
- `openai` - OpenAI API client
- `python-dotenv` - Environment variable management
- `rich` - Rich text and beautiful formatting in the terminal
- `prompt_toolkit` - Building powerful interactive command lines

These dependencies are automatically installed when you install Aether via any of the methods below.

## Installation

You can install Aether via multiple package managers:

### 1. Python (pip / pipx) Recommended
```bash
pipx install aether-cli
# Or using pip:
pip install aether-cli
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

### 4. Local Installation
Clone the repository and run the install script:
```bash
git clone https://github.com/irtaza302/aether-agent.git
cd aether-agent
./install.sh
```

## Usage

Simply run:
```bash
aether
```

When you first launch Aether, you'll be prompted to provide your [OpenRouter API key](https://openrouter.ai/keys). It will be saved securely in `~/.aether_config.json`.

### Command Line Arguments
- `--version` - View the current version of Aether.
- `--model <model_name>` - Override the active model for this session.
- `--reset-key` - Reset/clear the saved API key.
- `--set-base-url <url>` - Set a custom API base URL (e.g. `http://localhost:11434/v1` for local Ollama).

### Slash Commands
Inside the interactive session, you can run:
*   `/model <name>` - View or switch the active model on the fly.
*   `/clear` - Clear the current conversation history.
*   `/help` - Show the interactive help screen.

### Attaching Files
Type `@` followed by a filename to give Aether context about your code (autocomplete will automatically filter files ignored by git):
```
👤 You
❯ Can you refactor @aether.py to use async?
```

## Publishing & Development
If you are developing Aether, you can use the included `publish.sh` script to build and publish across all platforms (PyPI, NPM, and PyInstaller binaries).