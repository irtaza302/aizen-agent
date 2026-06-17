import subprocess

import questionary

from ..config import Theme, console, get_active_model
from .registry import CommandContext, register


@register("/commit", "Auto-generate and commit changes")
async def commit_cmd(arg: str, ctx: CommandContext) -> bool:
    if not ctx.client:
        console.print(
            f"  [{Theme.ERROR}]API client is not available for /commit.[/{Theme.ERROR}]\n"
        )
        return False

    try:
        # Check staged changes
        result = subprocess.run(
            ["git", "diff", "--cached"], capture_output=True, text=True, check=True
        )
        diff = result.stdout.strip()

        if not diff:
            # Check unstaged
            result_unstaged = subprocess.run(
                ["git", "diff"], capture_output=True, text=True, check=True
            )
            unstaged_diff = result_unstaged.stdout.strip()

            if not unstaged_diff:
                console.print(
                    f"  [{Theme.WARNING}]No changes found to commit.[/{Theme.WARNING}]\n"
                )
                return False

            answer = await questionary.confirm(
                "No staged changes. Stage all current changes?"
            ).ask_async()
            if not answer:
                console.print(f"  [{Theme.WARNING}]Commit aborted.[/{Theme.WARNING}]\n")
                return False

            subprocess.run(["git", "add", "-u"], check=True)
            result = subprocess.run(
                ["git", "diff", "--cached"], capture_output=True, text=True, check=True
            )
            diff = result.stdout.strip()

        if not diff:
            console.print(
                f"  [{Theme.WARNING}]No changes staged to commit.[/{Theme.WARNING}]\n"
            )
            return False

        console.print(f"  [{Theme.MUTED}]Generating commit message...[/{Theme.MUTED}]")

        commit_messages = [
            {
                "role": "system",
                "content": "You are a senior developer. Write a concise, conventional commit message for the following diff. Output ONLY the commit message, no explanation, no markdown blocks.",
            },
            {"role": "user", "content": f"Diff:\n{diff[:10000]}"},
        ]

        response = await ctx.client.chat.completions.create(
            model=get_active_model(),
            messages=commit_messages,
            max_tokens=200,
        )
        commit_content = response.choices[0].message.content
        commit_msg = commit_content.strip() if commit_content else ""
        commit_msg = commit_msg.replace("```text", "").replace("```", "").strip()

        if not commit_msg:
            console.print(
                f"\n  [{Theme.WARNING}]⚠️ The model failed to generate a commit message.[/{Theme.WARNING}]"
            )
            action = "Edit message"
        else:
            console.print(
                f"\n  [bold {Theme.TEXT}]Generated Commit Message:[/bold {Theme.TEXT}]"
            )
            console.print(f"  [{Theme.ACCENT}]{commit_msg}[/{Theme.ACCENT}]\n")

            action = await questionary.select(
                "Commit with this message?",
                choices=["Yes, commit this", "Edit message", "Cancel"],
            ).ask_async()

        if action == "Yes, commit this":
            final_msg = commit_msg
        elif action == "Edit message":
            final_msg = await questionary.text("Edit message:", default=commit_msg).ask_async()
        else:
            console.print("[yellow]Commit aborted.[/yellow]\n")
            return False

        if final_msg is None:
            console.print(f"  [{Theme.WARNING}]Commit aborted.[/{Theme.WARNING}]\n")
            return False

        final_msg = final_msg.strip()
        if not final_msg:
            console.print(
                f"  [{Theme.ERROR}]Error: Commit message cannot be empty. Aborted.[/{Theme.ERROR}]\n"
            )
            return False

        subprocess.run(["git", "commit", "-m", final_msg], check=True)
        console.print(f"  [{Theme.SUCCESS}]✓ Committed successfully.[/{Theme.SUCCESS}]\n")

    except subprocess.CalledProcessError:
        console.print(
            f"  [{Theme.ERROR}]Error: Not a git repository or git command failed.[/{Theme.ERROR}]\n"
        )
    except Exception as e:
        console.print(f"  [{Theme.ERROR}]Error during auto-commit: {e}[/{Theme.ERROR}]\n")
    return False

@register("/diff", "Show all uncommitted changes")
async def diff_cmd(arg: str, ctx: CommandContext) -> bool:
    try:
        result_staged = subprocess.run(
            ["git", "diff", "--cached", "--stat"], capture_output=True, text=True, check=True
        )
        result_unstaged = subprocess.run(
            ["git", "diff", "--stat"], capture_output=True, text=True, check=True
        )
        result_untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            capture_output=True,
            text=True,
            check=True,
        )

        has_output = False

        if result_staged.stdout.strip():
            console.print(f"  [bold {Theme.SUCCESS}]Staged changes:[/bold {Theme.SUCCESS}]")
            console.print(f"[dim]{result_staged.stdout.strip()}[/dim]")
            has_output = True

        if result_unstaged.stdout.strip():
            console.print(f"  [bold {Theme.WARNING}]Unstaged changes:[/bold {Theme.WARNING}]")
            console.print(f"[dim]{result_unstaged.stdout.strip()}[/dim]")
            has_output = True

        if result_untracked.stdout.strip():
            untracked = result_untracked.stdout.strip().split("\n")
            console.print(
                f"  [bold {Theme.ACCENT}]Untracked files ({len(untracked)}):[/bold {Theme.ACCENT}]"
            )
            for f in untracked[:20]:
                console.print(f"  [dim]+ {f}[/dim]")
            if len(untracked) > 20:
                console.print(f"  [dim]... and {len(untracked) - 20} more[/dim]")
            has_output = True

        if not has_output:
            console.print(f"  [{Theme.SUCCESS}]✓ Working tree is clean.[/{Theme.SUCCESS}]")

        if arg == "--full" or arg == "-f":
            result_full = subprocess.run(
                ["git", "diff"], capture_output=True, text=True, check=True
            )
            if result_full.stdout.strip():
                from rich.syntax import Syntax
                syntax = Syntax(result_full.stdout, "diff", theme="monokai")
                console.print(syntax)

        console.print()
    except subprocess.CalledProcessError:
        console.print(
            f"  [{Theme.ERROR}]Error: Not a git repository or git command failed.[/{Theme.ERROR}]\n"
        )
    except Exception as e:
        console.print(f"  [{Theme.ERROR}]Error showing diff: {e}[/{Theme.ERROR}]\n")
    return False

@register("/pr", "Create a GitHub PR with AI-generated description")
async def pr_cmd(arg: str, ctx: CommandContext) -> bool:
    try:
        gh_check = subprocess.run(["gh", "--version"], capture_output=True, text=True)
        if gh_check.returncode != 0:
            raise FileNotFoundError

        subprocess.run(
            ["git", "diff", "--stat", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        subprocess.run(
            ["git", "log", "--oneline", "origin/HEAD..HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        pr_title = arg if arg else None
        console.print(f"  [{Theme.MUTED}]Creating pull request...[/{Theme.MUTED}]")

        cmd_parts = ["gh", "pr", "create", "--fill"]
        if pr_title:
            cmd_parts = ["gh", "pr", "create", "--title", pr_title, "--fill"]

        result = subprocess.run(cmd_parts, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            console.print(
                f"  [{Theme.SUCCESS}]✓ PR created: {result.stdout.strip()}[/{Theme.SUCCESS}]\n"
            )
        else:
            console.print(
                f"  [{Theme.ERROR}]PR creation failed: {result.stderr.strip()}[/{Theme.ERROR}]\n"
            )

    except FileNotFoundError:
        console.print(
            f"  [{Theme.ERROR}]GitHub CLI (gh) not found. Install with: brew install gh[/{Theme.ERROR}]\n"
        )
    except Exception as e:
        console.print(f"  [{Theme.ERROR}]Error creating PR: {e}[/{Theme.ERROR}]\n")
    return False

@register("/branch", "Create and switch to a new git branch")
async def branch_cmd(arg: str, ctx: CommandContext) -> bool:
    if not arg:
        try:
            result = subprocess.run(
                ["git", "branch", "--show-current"], capture_output=True, text=True, timeout=5
            )
            branches = subprocess.run(
                ["git", "branch", "-a"], capture_output=True, text=True, timeout=5
            )
            console.print(
                f"  [{Theme.TEXT}]Current branch:[/{Theme.TEXT}] [{Theme.ACCENT}]{result.stdout.strip()}[/{Theme.ACCENT}]"
            )
            if branches.stdout.strip():
                console.print(f"  [{Theme.MUTED}]{branches.stdout.strip()}[/{Theme.MUTED}]\n")
        except Exception as e:
            console.print(f"  [{Theme.ERROR}]Error: {e}[/{Theme.ERROR}]\n")
    else:
        try:
            result = subprocess.run(
                ["git", "checkout", "-b", arg], capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                console.print(
                    f"  [{Theme.SUCCESS}]✓ Created and switched to branch: {arg}[/{Theme.SUCCESS}]\n"
                )
            else:
                console.print(f"  [{Theme.ERROR}]{result.stderr.strip()}[/{Theme.ERROR}]\n")
        except Exception as e:
            console.print(f"  [{Theme.ERROR}]Error: {e}[/{Theme.ERROR}]\n")
    return False

@register("/stash", "Stash changes with an AI-generated message")
async def stash_cmd(arg: str, ctx: CommandContext) -> bool:
    try:
        stash_msg = arg if arg else "Stashed by Aizen"
        result = subprocess.run(
            ["git", "stash", "push", "-m", stash_msg],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            console.print(
                f"  [{Theme.SUCCESS}]✓ Changes stashed: {stash_msg}[/{Theme.SUCCESS}]\n"
            )
        else:
            console.print(f"  [{Theme.ERROR}]{result.stderr.strip()}[/{Theme.ERROR}]\n")
    except Exception as e:
        console.print(f"  [{Theme.ERROR}]Error stashing: {e}[/{Theme.ERROR}]\n")
    return False

@register("/amend", "Amend the last commit with a regenerated message")
async def amend_cmd(arg: str, ctx: CommandContext) -> bool:
    try:
        subprocess.run(
            ["git", "diff", "--cached", "--stat"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        last_msg = subprocess.run(
            ["git", "log", "-1", "--pretty=%B"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        console.print(f"  [{Theme.MUTED}]Amending last commit...[/{Theme.MUTED}]")
        console.print(
            f"  [{Theme.MUTED}]Previous message: {last_msg.stdout.strip()}[/{Theme.MUTED}]"
        )

        new_msg = arg if arg else last_msg.stdout.strip()
        result = subprocess.run(
            ["git", "commit", "--amend", "-m", new_msg],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode == 0:
            console.print(f"  [{Theme.SUCCESS}]✓ Commit amended: {new_msg}[/{Theme.SUCCESS}]\n")
        else:
            console.print(f"  [{Theme.ERROR}]{result.stderr.strip()}[/{Theme.ERROR}]\n")
    except Exception as e:
        console.print(f"  [{Theme.ERROR}]Error amending: {e}[/{Theme.ERROR}]\n")
    return False

@register("/log", "Show recent git commits")
async def log_cmd(arg: str, ctx: CommandContext) -> bool:
    try:
        count = int(arg) if arg and arg.isdigit() else 10
        result = subprocess.run(
            ["git", "log", "--oneline", f"-{count}", "--decorate", "--graph"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            console.print(f"  [{Theme.ACCENT}]📜 Recent commits:[/{Theme.ACCENT}]")
            for line in result.stdout.strip().splitlines():
                console.print(f"  [{Theme.TEXT}]{line}[/{Theme.TEXT}]")
            console.print()
        else:
            console.print(f"  [{Theme.MUTED}]No commits found.[/{Theme.MUTED}]\n")
    except Exception as e:
        console.print(f"  [{Theme.ERROR}]Error: {e}[/{Theme.ERROR}]\n")
    return False
