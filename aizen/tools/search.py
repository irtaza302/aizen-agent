"""
Search and directory tools: list_directory, grep_search, find_files, web_search.
"""

import fnmatch
import os
import re
import shutil
import subprocess
import urllib.parse
import urllib.request

from ..logging_config import logger
from ..utils import load_gitignore_patterns, should_ignore
from .helpers import is_binary_file


def list_directory(path: str = ".") -> str:
    try:
        if not path:
            path = "."
        if not os.path.exists(path):
            return f"Error: Path '{path}' does not exist."
        if not os.path.isdir(path):
            return f"Error: '{path}' is not a directory."

        items = os.listdir(path)
        ignore_patterns = load_gitignore_patterns()

        dirs = []
        files = []
        for item in sorted(items):
            item_path = os.path.join(path, item)
            if should_ignore(item_path, ignore_patterns):
                continue
            if os.path.isdir(item_path):
                dirs.append(f"📁 {item}/")
            else:
                try:
                    size = os.path.getsize(item_path)
                    if size < 1024:
                        size_str = f"{size}B"
                    elif size < 1024 * 1024:
                        size_str = f"{size / 1024:.1f}KB"
                    else:
                        size_str = f"{size / 1024 / 1024:.1f}MB"
                    files.append(f"📄 {item} ({size_str})")
                except OSError:
                    files.append(f"📄 {item}")

        if not dirs and not files:
            return f"Directory '{path}' is empty or all contents are ignored."

        result = ""
        if dirs:
            result += "\n".join(dirs)
        if files:
            if result:
                result += "\n"
            result += "\n".join(files)
        return result
    except Exception as e:
        return f"Error listing directory: {e}"


def grep_search(query: str, path: str = ".", is_regex: bool = False) -> str:
    try:
        if not path:
            path = "."
        if not os.path.exists(path):
            return f"Error: Path '{path}' does not exist."

        if shutil.which("rg"):
            args = ["rg", "-n", "-m", "50"]
            if not is_regex:
                args.append("-F")
            args.extend(["-i", query, path])
            try:
                result = subprocess.run(args, capture_output=True, text=True, timeout=10)
                if result.stdout:
                    lines = result.stdout.splitlines()
                    if len(lines) >= 50:
                        lines.append("\n(Showing first 50 results)")
                    return "\n".join(lines)
                if result.returncode == 1:
                    return "No matches found."
            except Exception as e:
                logger.debug("ripgrep failed, falling back to python search: %s", e)

        if is_regex:
            try:
                pattern = re.compile(query, re.IGNORECASE)
            except re.error as e:
                return f"Invalid regex pattern: {e}"

        ignore_patterns = load_gitignore_patterns()
        matches = []

        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if not should_ignore(os.path.join(root, d), ignore_patterns)]

            for file in files:
                file_path = os.path.join(root, file)
                if should_ignore(file_path, ignore_patterns):
                    continue
                if is_binary_file(file_path):
                    continue
                try:
                    with open(file_path, encoding="utf-8", errors="ignore") as f:
                        for line_num, line in enumerate(f, 1):
                            matched = False
                            if is_regex:
                                matched = bool(pattern.search(line))
                            else:
                                matched = query.lower() in line.lower()

                            if matched:
                                matches.append(f"{file_path}:{line_num}: {line.strip()}")
                                if len(matches) >= 50:
                                    return "\n".join(matches) + "\n\n(Showing first 50 results)"
                except (UnicodeDecodeError, PermissionError, OSError) as e:
                    logger.debug("grep_search skipped %s: %s", file_path, e)

        if not matches:
            return f"No matches found for '{query}'."
        return "\n".join(matches)
    except Exception as e:
        return f"Error searching: {e}"


def find_files(pattern: str, path: str = ".") -> str:
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
                if fnmatch.fnmatch(file, pattern) or fnmatch.fnmatch(file.lower(), pattern.lower()):
                    file_path = os.path.join(root, file)
                    if not should_ignore(file_path, ignore_patterns):
                        matches.append(file_path)
                        if len(matches) >= 100:
                            return "\n".join(matches) + "\n\n(Showing first 100 results)"

        if not matches:
            return f"No files matching '{pattern}' found."
        return "\n".join(matches)
    except Exception as e:
        return f"Error finding files: {e}"


def web_search_impl(query: str) -> str:
    url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query)
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            },
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            html = response.read().decode("utf-8", errors="ignore")

        results = []
        snippets = re.findall(
            r'<a class="result__snippet[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
            html,
            re.IGNORECASE | re.DOTALL,
        )

        for href, text in snippets[:5]:
            clean_href = urllib.parse.unquote(
                href.replace("//duckduckgo.com/l/?uddg=", "").split("&")[0]
            )
            clean_text = re.sub(r"<[^>]+>", "", text).strip()
            results.append(f"URL: {clean_href}\nSnippet: {clean_text}\n")

        if not results:
            return "No results found or unable to parse search page."

        return "\n".join(results)
    except Exception as e:
        return f"Error performing web search: {e}"
