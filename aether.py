import os
import sys
import json
import subprocess
import re
import glob
import getpass
import argparse
import urllib.request
import fnmatch
import difflib
import random
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.filters import has_completions, completion_is_selected
from dotenv import load_dotenv
from openai import OpenAI
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text
from rich.align import Align
from rich.rule import Rule

VERSION = "1.1.0"
CONFIG_PATH = os.path.expanduser("~/.aether_config.json")
MODEL = "nvidia/nemotron-3-super-120b-a12b:free" # Default model

AETHER_ASCII = r"""
[bold magenta]
    ___       __  __               
   /   | ___ / /_/ /_  ___  _____  
  / /| |/ _ \ __/ __ \/ _ \/ ___/  
 / ___ /  __/ /_/ / / /  __/ /     
/_/  |_\___/\__/_/ /_/\___/_/      
[/bold magenta]
"""

console = Console()

class Struct:
    def __init__(self, **entries):
        self.__dict__.update(entries)

def load_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r') as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_config(config):
    try:
        with open(CONFIG_PATH, 'w') as f:
            json.dump(config, f, indent=2)
    except Exception as e:
        console.print(f"[yellow]⚠️ Could not save config file: {e}[/yellow]\n")

def get_api_key(config, reset=False):
    # 1. Clear key if reset is requested
    if reset:
        if "OPENROUTER_API_KEY" in config:
            del config["OPENROUTER_API_KEY"]
            save_config(config)
            
    # 2. Try config file
    key = config.get("OPENROUTER_API_KEY")
    if key:
        return key
        
    # 3. Fallback to env
    load_dotenv()
    env_key = os.getenv("OPENROUTER_API_KEY")
    if env_key and env_key != "your_api_key_here":
        return env_key
        
    # 4. Prompt user
    console.print(AETHER_ASCII)
    console.print("It looks like this is your first time running the agent.")
    console.print("To get started, please enter your OpenRouter API key.")
    console.print("(Get one at https://openrouter.ai/keys)\n")
    
    key = getpass.getpass("API Key: ").strip()
    if not key:
        console.print("[bold red]Error:[/bold red] API Key cannot be empty.")
        sys.exit(1)
        
    config["OPENROUTER_API_KEY"] = key
    save_config(config)
    console.print(f"[green]✓ API key securely saved to {CONFIG_PATH}[/green]\n")
    return key

def check_for_updates():
    try:
        url = "https://pypi.org/pypi/aether-cli/json"
        req = urllib.request.Request(url, headers={'User-Agent': 'aether-cli'})
        with urllib.request.urlopen(req, timeout=0.8) as response:
            data = json.loads(response.read().decode())
            latest_version = data['info']['version']
            if latest_version != VERSION:
                console.print(f"[bold magenta]🔔 Notice:[/bold magenta] A new version of Aether is available ({latest_version}).")
                console.print("Run [bold cyan]pip install -U aether-cli[/bold cyan] or [bold cyan]npm install -g aether-ai-cli[/bold cyan] to update.\n")
    except Exception:
        pass

# Autocomplete Gitignore parsing
def load_gitignore_patterns():
    patterns = [
        '.git/', 'node_modules/', '__pycache__/', 'venv/', '.env', 
        'dist/', 'build/', '*.egg-info/', '.DS_Store'
    ]
    if os.path.exists('.gitignore'):
        try:
            with open('.gitignore', 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#'):
                        patterns.append(line)
        except Exception:
            pass
    return patterns

def should_ignore(path, patterns):
    path = os.path.normpath(path)
    parts = path.split(os.sep)
    for pattern in patterns:
        is_dir_pattern = pattern.endswith('/')
        clean_pattern = pattern.rstrip('/')
        for part in parts:
            if fnmatch.fnmatch(part, clean_pattern):
                return True
        if fnmatch.fnmatch(path, clean_pattern):
            return True
    return False

class FileMentionCompleter(Completer):
    def __init__(self):
        super().__init__()
        self.ignore_patterns = load_gitignore_patterns()

    def get_completions(self, document, complete_event):
        text_before_cursor = document.text_before_cursor
        words = text_before_cursor.split()
        if not words:
            return
            
        current_word = words[-1]
        if current_word.startswith('@'):
            search_query = current_word[1:]
            files = glob.glob(search_query + '*')
            
            for file in files:
                if os.path.isfile(file):
                    if not should_ignore(file, self.ignore_patterns):
                        yield Completion(file, start_position=-len(search_query))

# Tools Definition
tools = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Reads the contents of a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {
                        "type": "string",
                        "description": "The path to the file to read."
                    }
                },
                "required": ["filepath"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Writes content to a file, presenting a visual diff to the user before editing.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {
                        "type": "string",
                        "description": "The path to the file to write."
                    },
                    "content": {
                        "type": "string",
                        "description": "The content to write into the file."
                    }
                },
                "required": ["filepath", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Executes a shell command on the user's terminal.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command to execute."
                    }
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "Lists files and folders inside the directory (respects gitignore).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "The path of the directory to list (defaults to '.')."
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "grep_search",
            "description": "Searches for a text pattern in files under a directory (respects gitignore).",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The text pattern to search for."
                    },
                    "path": {
                        "type": "string",
                        "description": "The directory path to search in (defaults to '.')."
                    }
                },
                "required": ["query"]
            }
        }
    }
]

# Tool Implementations
def read_file(filepath: str) -> str:
    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            return f.read()
    except Exception as e:
        return f"Error reading file: {e}"

def write_file_with_diff(filepath: str, content: str) -> str:
    try:
        old_content = ""
        exists = os.path.exists(filepath)
        if exists:
            try:
                with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                    old_content = f.read()
            except Exception:
                pass
                
        if exists:
            diff = list(difflib.unified_diff(
                old_content.splitlines(keepends=True),
                content.splitlines(keepends=True),
                fromfile=f"a/{filepath}",
                tofile=f"b/{filepath}",
                n=3
            ))
            if not diff:
                return f"No changes to write for {filepath}"
                
            console.print(Panel(f"[bold magenta]Aether wants to modify file:[/bold magenta] [cyan]{filepath}[/cyan]", border_style="magenta"))
            for line in diff:
                if line.startswith('+') and not line.startswith('+++'):
                    console.print(f"[green]{line.rstrip()}[/green]")
                elif line.startswith('-') and not line.startswith('---'):
                    console.print(f"[red]{line.rstrip()}[/red]")
                elif line.startswith('@@'):
                    console.print(f"[cyan]{line.rstrip()}[/cyan]")
                else:
                    console.print(line.rstrip())
        else:
            console.print(Panel(
                f"[bold magenta]Aether wants to create a new file:[/bold magenta] [cyan]{filepath}[/cyan]\n"
                f"[dim]Content length: {len(content)} characters[/dim]", 
                border_style="magenta"
            ))
            
        confirmation = input("Allow writing file? (y/n): ")
        if confirmation.lower() != 'y':
            return "User denied file write operation."
            
        os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        return f"Successfully wrote to {filepath}"
    except Exception as e:
        return f"Error writing file: {e}"

def run_command(command: str) -> str:
    console.print(Panel(f"[bold magenta]Aether wants to run command:[/bold magenta]\n{command}", border_style="magenta"))
    confirmation = input("Allow? (y/n): ")
    if confirmation.lower() != 'y':
        return "User denied command execution."
    
    try:
        result = subprocess.run(
            command, shell=True, text=True, capture_output=True, check=False
        )
        output = f"Exit Code: {result.returncode}\n"
        if result.stdout:
            output += f"STDOUT:\n{result.stdout}\n"
        if result.stderr:
            output += f"STDERR:\n{result.stderr}\n"
        return output
    except Exception as e:
        return f"Error executing command: {e}"

def list_directory(path: str = ".") -> str:
    try:
        if not path:
            path = "."
        if not os.path.exists(path):
            return f"Error: Path '{path}' does not exist."
        if not os.path.isdir(path):
            return f"Error: Path '{path}' is not a directory."
            
        items = os.listdir(path)
        ignore_patterns = load_gitignore_patterns()
        
        result_items = []
        for item in items:
            item_path = os.path.join(path, item)
            if should_ignore(item_path, ignore_patterns):
                continue
            is_dir = os.path.isdir(item_path)
            suffix = "/" if is_dir else ""
            result_items.append(f"{item}{suffix}")
            
        result_items.sort()
        if not result_items:
            return f"Directory '{path}' is empty or all contents are ignored."
        return "\n".join(result_items)
    except Exception as e:
        return f"Error listing directory: {e}"

def grep_search(query: str, path: str = ".") -> str:
    try:
        if not path:
            path = "."
        if not os.path.exists(path):
            return f"Error: Path '{path}' does not exist."
            
        ignore_patterns = load_gitignore_patterns()
        matches = []
        
        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if not should_ignore(os.path.join(root, d), ignore_patterns)]
            
            for file in files:
                file_path = os.path.join(root, file)
                if should_ignore(file_path, ignore_patterns):
                    continue
                try:
                    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                        for line_num, line in enumerate(f, 1):
                            if query.lower() in line.lower():
                                matches.append(f"{file_path}:{line_num}: {line.strip()}")
                                if len(matches) >= 50:
                                    return "\n".join(matches) + "\n\n(Showed first 50 results. Truncated.)"
                except Exception:
                    pass
                    
        if not matches:
            return f"No matches found for query '{query}'."
        return "\n".join(matches)
    except Exception as e:
        return f"Error searching: {e}"

def truncate_output(text: str, max_chars: int = 4000) -> str:
    if len(text) <= max_chars:
        return text
    half = max_chars // 2
    return f"{text[:half]}\n\n[... TRUNCATED {len(text) - max_chars} CHARACTERS ...]\n\n{text[-half:]}"

def execute_tool(tool_call) -> str:
    func_name = tool_call.function.name
    args = json.loads(tool_call.function.arguments)
    
    console.print(f"  [dim magenta]⚙️ Using tool: {func_name}[/dim magenta]")
    
    if func_name == "read_file":
        return truncate_output(read_file(args.get("filepath")))
    elif func_name == "write_file":
        return write_file_with_diff(args.get("filepath"), args.get("content"))
    elif func_name == "run_command":
        return truncate_output(run_command(args.get("command")))
    elif func_name == "list_directory":
        return truncate_output(list_directory(args.get("path", ".")))
    elif func_name == "grep_search":
        return truncate_output(grep_search(args.get("query"), args.get("path", ".")))
    else:
        return f"Unknown function: {func_name}"

def inject_file_context(user_input: str, console: Console) -> str:
    pattern = r"(?:^|\s)@([a-zA-Z0-9_\-\.\/]+)"
    matches = re.findall(pattern, user_input)
    if not matches:
        return user_input
        
    context_blocks = []
    for filepath in set(matches):
        if os.path.isfile(filepath):
            try:
                with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                context_blocks.append(f"<file_context path=\"{filepath}\">\n{content}\n</file_context>")
                console.print(f"[dim]📎 Attached context from: {filepath}[/dim]")
            except Exception as e:
                console.print(f"[dim yellow]⚠️ Failed to attach {filepath}: {e}[/dim yellow]")
        else:
            console.print(f"[dim yellow]⚠️ File not found: {filepath}[/dim yellow]")
            
    if context_blocks:
        user_input += "\n\n" + "\n".join(context_blocks)
    return user_input

def handle_slash_command(command_str, messages):
    parts = command_str.split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""
    
    global MODEL
    
    if cmd == "/clear":
        if len(messages) > 1:
            messages[:] = [messages[0]]
        console.print("[green]✓ Chat history cleared.[/green]")
    elif cmd == "/model":
        if arg:
            MODEL = arg
            console.print(f"[green]✓ Switched model to:[/green] [bold cyan]{MODEL}[/bold cyan]")
        else:
            console.print(f"Current model: [bold cyan]{MODEL}[/bold cyan]")
            console.print("To change, type: [bold magenta]/model <model_name>[/bold magenta]")
    elif cmd == "/help":
        console.print(Panel(
            "[bold magenta]Available Commands:[/bold magenta]\n"
            "  [bold cyan]/clear[/bold cyan]       - Clear conversation history\n"
            "  [bold cyan]/model[/bold cyan]       - View or switch active model (e.g. `/model anthropic/claude-3.5-sonnet`)\n"
            "  [bold cyan]/help[/bold cyan]        - Show this help message\n"
            "  [bold cyan]exit[/bold cyan] or [bold cyan]quit[/bold cyan] - Exit the agent\n\n"
            "[bold magenta]Features:[/bold magenta]\n"
            "  - Attach files using [bold magenta]@filename[/bold magenta] with autocomplete\n"
            "  - Runs commands with confirmation\n"
            "  - Inspects file modifications before applying them (Diff preview)",
            title="[bold magenta]Aether Help[/bold magenta]",
            border_style="magenta"
        ))
    else:
        console.print(f"[red]Unknown command: {cmd}. Type [bold]/help[/bold] for list of commands.[/red]")

def parse_args():
    parser = argparse.ArgumentParser(description="Aether AI Coding Agent - A sleek, intelligent CLI assistant.")
    parser.add_argument("--version", action="store_true", help="Show the current version of Aether.")
    parser.add_argument("--model", type=str, help="Override the default model for this session.")
    parser.add_argument("--reset-key", action="store_true", help="Clear the saved API key and prompt for a new one.")
    parser.add_argument("--set-base-url", type=str, help="Set custom API base URL in configuration.")
    return parser.parse_args()

def main():
    args = parse_args()
    
    if args.version:
        print(f"Aether CLI version {VERSION}")
        sys.exit(0)
        
    config = load_config()
    
    if args.set_base_url:
        config["API_BASE_URL"] = args.set_base_url
        save_config(config)
        print(f"API base URL updated to: {args.set_base_url}")
        sys.exit(0)
        
    api_key = get_api_key(config, reset=args.reset_key)
    
    # Setup global model
    global MODEL
    if args.model:
        MODEL = args.model
    elif config.get("DEFAULT_MODEL"):
        MODEL = config.get("DEFAULT_MODEL")
        
    api_base = config.get("API_BASE_URL", "https://openrouter.ai/api/v1")
    
    # Initialize client
    client = OpenAI(
        base_url=api_base,
        api_key=api_key,
    )
    
    # Non-blocking (or fast timeout) update check
    check_for_updates()
    
    console.print(AETHER_ASCII)
    console.print(f"[dim]Version {VERSION} | Active Model: {MODEL}[/dim]")
    console.print("[dim]Type '/help' for commands, '@' to attach files, 'exit' to stop[/dim]\n")
    
    kb = KeyBindings()
    @kb.add('enter', filter=has_completions & completion_is_selected)
    def _(event):
        event.current_buffer.complete_state = None
        
    session = PromptSession(completer=FileMentionCompleter(), key_bindings=kb)
    
    messages = [
        {"role": "system", "content": "You are a helpful AI coding assistant running in a user's terminal. You can read/write files and execute shell commands to help them code. Be concise."}
    ]
    
    while True:
        try:
            prompt_html = HTML(
                '<ansimagenta>╭─</ansimagenta> <ansimagenta><b>👤 You</b></ansimagenta>\n'
                '<ansimagenta>╰─❯</ansimagenta> '
            )
            user_input = session.prompt(prompt_html)
            if user_input.lower() in ['exit', 'quit']:
                break
            if not user_input.strip():
                continue
                
            # Check slash command
            if user_input.strip().startswith('/'):
                handle_slash_command(user_input.strip(), messages)
                continue
                
            user_input = inject_file_context(user_input, console)
            messages.append({"role": "user", "content": user_input})
            
            STATUSES = [
                "Thinking...",
                "Searching context...",
                "Investigating codebase...",
                "Analyzing dependencies...",
                "Formulating solution...",
                "Synthesizing response..."
            ]
            
            while True:
                status_msg = random.choice(STATUSES)
                console.print(f"[dim]⚙️ {status_msg}[/dim]", end="")
                sys.stdout.flush()
                
                try:
                    stream = client.chat.completions.create(
                        model=MODEL,
                        messages=messages,
                        tools=tools,
                        tool_choice="auto",
                        stream=True
                    )
                except Exception as e:
                    sys.stdout.write("\r\033[K")
                    sys.stdout.flush()
                    console.print(f"\n[bold red]API Error:[/bold red] {e}")
                    break
                
                full_content = ""
                accumulated_tool_calls = {}
                first_token = True
                has_content = False
                
                for chunk in stream:
                    delta = chunk.choices[0].delta if chunk.choices else None
                    if not delta:
                        continue
                        
                    if delta.content or delta.tool_calls:
                        if first_token:
                            sys.stdout.write("\r\033[K")
                            sys.stdout.flush()
                            first_token = False
                            
                    if delta.content:
                        if not has_content:
                            console.print("[bold magenta]✦ Aether:[/bold magenta] ", end="")
                            sys.stdout.flush()
                            has_content = True
                        sys.stdout.write(delta.content)
                        sys.stdout.flush()
                        full_content += delta.content
                        
                    if delta.tool_calls:
                        for tc in delta.tool_calls:
                            idx = tc.index
                            if idx not in accumulated_tool_calls:
                                accumulated_tool_calls[idx] = {
                                    "id": "",
                                    "name": "",
                                    "arguments": "",
                                    "type": "function"
                                }
                            if tc.id:
                                accumulated_tool_calls[idx]["id"] = tc.id
                            if tc.function:
                                if tc.function.name:
                                    accumulated_tool_calls[idx]["name"] += tc.function.name
                                if tc.function.arguments:
                                    accumulated_tool_calls[idx]["arguments"] += tc.function.arguments
                                    
                if first_token:
                    sys.stdout.write("\r\033[K")
                    sys.stdout.flush()
                else:
                    if has_content:
                        print()
                
                # Convert accumulated_tool_calls to correct structure
                tool_calls_list = []
                for idx in sorted(accumulated_tool_calls.keys()):
                    tc = accumulated_tool_calls[idx]
                    tool_calls_list.append({
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": tc["arguments"]
                        }
                    })
                
                # Add assistant response to history
                assistant_msg = {"role": "assistant", "content": full_content or None}
                if tool_calls_list:
                    assistant_msg["tool_calls"] = tool_calls_list
                messages.append(assistant_msg)
                
                # If there are no tool calls, we're done
                if not tool_calls_list:
                    break
                    
                # Otherwise execute tool calls sequentially
                for tc_dict in tool_calls_list:
                    func_struct = Struct(**tc_dict["function"])
                    tc_struct = Struct(id=tc_dict["id"], type=tc_dict["type"], function=func_struct)
                    
                    tool_result = execute_tool(tc_struct)
                    
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc_dict["id"],
                        "name": tc_dict["function"]["name"],
                        "content": tool_result
                    })
                
                # Let the model process the tool results in the next loop iteration
                
        except (KeyboardInterrupt, EOFError):
            console.print("\n[yellow]Exiting...[/yellow]")
            break
        except Exception as e:
            console.print(f"\n[bold red]Error:[/bold red] {e}")

if __name__ == "__main__":
    main()
