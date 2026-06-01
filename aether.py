import os
import json
import subprocess
import re
import glob
import getpass
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

AETHER_ASCII = r"""
[bold magenta]
    ___       __  __               
   /   | ___ / /_/ /_  ___  _____  
  / /| |/ _ \ __/ __ \/ _ \/ ___/  
 / ___ /  __/ /_/ / / /  __/ /     
/_/  |_\___/\__/_/ /_/\___/_/      
[/bold magenta]
"""

def get_api_key():
    config_path = os.path.expanduser("~/.aether_config.json")
    
    # 1. Try to load from config file
    if os.path.exists(config_path):
        try:
            with open(config_path, 'r') as f:
                config = json.load(f)
                key = config.get("OPENROUTER_API_KEY")
                if key:
                    return key
        except Exception:
            pass
            
    # 2. Fallback to .env for backward compatibility
    load_dotenv()
    env_key = os.getenv("OPENROUTER_API_KEY")
    if env_key and env_key != "your_api_key_here":
        return env_key
        
    # 3. Prompt user if no key found
    console = Console()
    console.print(AETHER_ASCII)
    console.print("It looks like this is your first time running the agent.")
    console.print("To get started, please enter your OpenRouter API key.")
    console.print("(Get one at https://openrouter.ai/keys)\n")
    
    key = getpass.getpass("API Key: ").strip()
    
    if not key:
        console.print("[bold red]Error:[/bold red] API Key cannot be empty.")
        exit(1)
        
    # Save key
    try:
        with open(config_path, 'w') as f:
            json.dump({"OPENROUTER_API_KEY": key}, f)
        console.print(f"[green]✓ API key securely saved to {config_path}[/green]\n")
    except Exception as e:
        console.print(f"[yellow]⚠️ Could not save config file: {e}[/yellow]\n")
        
    return key

OPENROUTER_API_KEY = get_api_key()

# Initialize OpenAI client pointing to OpenRouter
client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY,
)

# OpenRouter model (user requested)
MODEL = "nvidia/nemotron-3-super-120b-a12b:free"

console = Console()

# Define the tools available to the agent
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
            "description": "Writes content to a file, overwriting it if it exists.",
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
    }
]

# Tool implementation functions
def read_file(filepath: str) -> str:
    try:
        with open(filepath, 'r') as f:
            return f.read()
    except Exception as e:
        return f"Error reading file: {e}"

def write_file(filepath: str, content: str) -> str:
    try:
        # Create directories if they don't exist
        os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
        with open(filepath, 'w') as f:
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

# Tool dispatcher
def execute_tool(tool_call) -> str:
    func_name = tool_call.function.name
    args = json.loads(tool_call.function.arguments)
    
    console.print(f"  [dim magenta]⚙️ Using tool: {func_name}[/dim magenta]")
    
    if func_name == "read_file":
        return read_file(args.get("filepath"))
    elif func_name == "write_file":
        return write_file(args.get("filepath"), args.get("content"))
    elif func_name == "run_command":
        return run_command(args.get("command"))
    else:
        return f"Unknown function: {func_name}"

def inject_file_context(user_input: str, console: Console) -> str:
    """Parses @filename mentions and injects file content into the prompt."""
    pattern = r"(?:^|\s)@([a-zA-Z0-9_\-\.\/]+)"
    matches = re.findall(pattern, user_input)
    
    if not matches:
        return user_input
        
    context_blocks = []
    for filepath in set(matches):
        if os.path.isfile(filepath):
            try:
                with open(filepath, 'r') as f:
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

class FileMentionCompleter(Completer):
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
                    yield Completion(file, start_position=-len(search_query))

def main():
    console.print(AETHER_ASCII)
    console.print("[dim]Type 'exit' to stop[/dim]\n")
    
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
                
            user_input = inject_file_context(user_input, console)
            messages.append({"role": "user", "content": user_input})
            
            while True:
                with console.status("[bold magenta]✦ Thinking...[/bold magenta]", spinner="dots"):
                    response = client.chat.completions.create(
                        model=MODEL,
                        messages=messages,
                        tools=tools,
                        tool_choice="auto",
                    )
                
                response_message = response.choices[0].message
                
                # If there's content to display, show it
                if response_message.content:
                    console.print()
                    console.print(Panel(
                        Markdown(response_message.content),
                        title="[bold magenta]✦ Aether[/bold magenta]",
                        title_align="left",
                        border_style="magenta",
                        padding=(1, 2)
                    ))
                    console.print()
                
                # If there are no tool calls, we are done with this turn
                if not response_message.tool_calls:
                    messages.append({"role": "assistant", "content": response_message.content})
                    break
                
                # Handle tool calls
                messages.append(response_message) # Append assistant's tool calls
                
                for tool_call in response_message.tool_calls:
                    tool_result = execute_tool(tool_call)
                    
                    # Append tool result
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": tool_call.function.name,
                        "content": tool_result
                    })
                
                # Loop back to let the model see the tool results and respond
                
        except (KeyboardInterrupt, EOFError):
            console.print("\n[yellow]Exiting...[/yellow]")
            break
        except Exception as e:
            console.print(f"[bold red]Error:[/bold red] {e}")

if __name__ == "__main__":
    main()
