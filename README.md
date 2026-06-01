# Aether AI Agent 🚀

Aether is a sleek, intelligent AI coding assistant that runs directly in your terminal. It helps you code, reads your files, and can run commands for you—all from a beautifully designed CLI interface.

## Features
- **Sleek CLI:** A beautifully styled terminal UI with custom ASCII art and magenta accents.
- **Context Awareness:** Automatically read and inject your project's files into the prompt using `@filename`.
- **Tool Integration:** Can read/write files and execute shell commands right from the terminal.
- **Powered by OpenRouter:** By default, it connects to top-tier models (like Nemotron-120B) for free, via OpenRouter.

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

When you first launch Aether, you'll be prompted to provide your [OpenRouter API key](https://openrouter.ai/keys).

### Attaching Files
Type `@` followed by a filename to give Aether context about your code:
```
👤 You
❯ Can you refactor @aether.py to use async?
```

## Publishing & Development
If you are developing Aether, you can use the included `publish.sh` script to build and publish across all platforms (PyPI, NPM, and PyInstaller binaries).
