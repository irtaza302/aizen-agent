"""Tests for aether.tools module."""

import os
import json
import pytest
from unittest.mock import patch, MagicMock

from aether.tools import (
    read_file,
    write_file_with_diff,
    edit_file,
    is_command_safe,
    run_command_impl,
    list_directory,
    grep_search,
    find_files,
    execute_tool,
    _is_binary_file,
    _detect_language,
    _try_repair_json,
    backup_manager,
)
from aether.utils import Struct


class TestReadFile:
    """Tests for the read_file tool."""

    def test_read_existing_file(self, sample_file):
        result = read_file(sample_file)
        assert "def hello():" in result
        assert "Hello, world!" in result
        assert "[File:" in result

    def test_read_nonexistent_file(self):
        result = read_file("/nonexistent/path/file.py")
        assert "Error" in result
        assert "does not exist" in result

    def test_read_binary_file(self, binary_file):
        result = read_file(binary_file)
        assert "Error" in result
        assert "binary" in result.lower()

    def test_read_large_file(self, large_file):
        result = read_file(large_file)
        assert "Error" in result
        assert "too large" in result.lower()

    def test_read_file_shows_line_count(self, sample_file):
        result = read_file(sample_file)
        assert "lines" in result

    def test_read_file_shows_byte_count(self, sample_file):
        result = read_file(sample_file)
        assert "bytes" in result


class TestEditFile:
    """Tests for the edit_file tool."""

    def test_edit_existing_content(self, sample_file):
        result = edit_file(
            sample_file,
            'print("Hello, world!")',
            'print("Hello, Aether!")',
            auto_approve=True,
        )
        assert "✓" in result
        with open(sample_file) as f:
            assert 'print("Hello, Aether!")' in f.read()

    def test_edit_nonexistent_file(self, tmp_dir):
        result = edit_file(
            os.path.join(tmp_dir, "nonexistent.py"),
            "old",
            "new",
            auto_approve=True,
        )
        assert "Error" in result
        assert "does not exist" in result

    def test_edit_content_not_found(self, sample_file):
        result = edit_file(
            sample_file,
            "this text does not exist in the file",
            "replacement",
            auto_approve=True,
        )
        assert "Error" in result
        assert "Could not find" in result

    def test_edit_multiple_occurrences(self, tmp_dir):
        filepath = os.path.join(tmp_dir, "dupes.py")
        with open(filepath, "w") as f:
            f.write("hello\nhello\nhello\n")

        result = edit_file(filepath, "hello", "world", auto_approve=True)
        assert "Error" in result
        assert "occurrences" in result.lower()

    def test_edit_whitespace_hint(self, sample_file):
        # Try editing with normalized whitespace
        result = edit_file(
            sample_file,
            'print( "Hello, world!" )',  # Extra spaces
            'print("changed")',
            auto_approve=True,
        )
        assert "Error" in result
        # Should give a whitespace hint if it's close but not exact

    def test_edit_user_deny(self, sample_file):
        with patch("builtins.input", return_value="n"):
            result = edit_file(
                sample_file,
                'print("Hello, world!")',
                'print("changed")',
                auto_approve=False,
            )
        assert "denied" in result.lower()

    def test_edit_creates_backup(self, sample_file):
        initial_stack = len(backup_manager.undo_stack)
        edit_file(
            sample_file,
            'print("Hello, world!")',
            'print("Hello, Aether!")',
            auto_approve=True,
        )
        assert len(backup_manager.undo_stack) > initial_stack


class TestWriteFile:
    """Tests for the write_file_with_diff tool."""

    def test_write_new_file(self, tmp_dir):
        filepath = os.path.join(tmp_dir, "new_file.py")
        result = write_file_with_diff(filepath, "print('new')\n", auto_approve=True)
        assert "✓" in result
        assert os.path.exists(filepath)
        with open(filepath) as f:
            assert "print('new')" in f.read()

    def test_write_creates_parent_dirs(self, tmp_dir):
        filepath = os.path.join(tmp_dir, "deep", "nested", "file.py")
        result = write_file_with_diff(filepath, "content\n", auto_approve=True)
        assert "✓" in result
        assert os.path.exists(filepath)

    def test_write_overwrite_existing(self, sample_file):
        result = write_file_with_diff(sample_file, "new content\n", auto_approve=True)
        assert "✓" in result
        with open(sample_file) as f:
            assert f.read() == "new content\n"

    def test_write_no_changes(self, sample_file):
        with open(sample_file) as f:
            original = f.read()
        result = write_file_with_diff(sample_file, original, auto_approve=True)
        assert "No changes" in result

    def test_write_user_deny(self, tmp_dir):
        filepath = os.path.join(tmp_dir, "denied.py")
        with patch("builtins.input", return_value="n"):
            result = write_file_with_diff(filepath, "content\n", auto_approve=False)
        assert "denied" in result.lower()


class TestCommandSafety:
    """Tests for command safety checks."""

    def test_safe_commands(self):
        assert is_command_safe("ls") is True
        assert is_command_safe("ls -la") is True
        assert is_command_safe("git status") is True
        assert is_command_safe("git log --oneline") is True
        assert is_command_safe("cat file.txt") is True
        assert is_command_safe("python --version") is True

    def test_dangerous_commands(self):
        assert is_command_safe("rm -rf /") is False
        assert is_command_safe("sudo apt install") is False
        assert is_command_safe("chmod 777 file") is False
        assert is_command_safe("kill -9 1234") is False

    def test_unknown_commands_unsafe(self):
        assert is_command_safe("npm install") is False
        assert is_command_safe("pip install requests") is False
        assert is_command_safe("make build") is False


class TestRunCommand:
    """Tests for command execution."""

    def test_run_simple_command(self):
        result = run_command_impl("echo 'hello'", auto_approve=True)
        assert "hello" in result

    def test_run_failing_command(self):
        result = run_command_impl("false", auto_approve=True)
        assert "Exit code:" in result or "exit code" in result.lower()

    def test_run_command_timeout(self):
        result = run_command_impl("sleep 10", auto_approve=True, timeout=1)
        assert "timed out" in result.lower()

    def test_run_command_user_deny(self):
        with patch("builtins.input", return_value="n"):
            result = run_command_impl("npm install", auto_approve=False)
        assert "denied" in result.lower()


class TestListDirectory:
    """Tests for directory listing."""

    def test_list_current_dir(self, sample_dir):
        old_cwd = os.getcwd()
        try:
            os.chdir(sample_dir)
            result = list_directory(".")
            assert "src" in result
            assert "README.md" in result
        finally:
            os.chdir(old_cwd)

    def test_list_nonexistent_dir(self):
        result = list_directory("/nonexistent/path")
        assert "Error" in result

    def test_list_respects_gitignore(self, sample_dir):
        old_cwd = os.getcwd()
        try:
            os.chdir(sample_dir)
            result = list_directory(".")
            assert "node_modules" not in result
        finally:
            os.chdir(old_cwd)


class TestGrepSearch:
    """Tests for grep search."""

    def test_basic_search(self, sample_dir):
        result = grep_search("hello", sample_dir)
        assert "hello" in result.lower()

    def test_search_no_results(self, sample_dir):
        result = grep_search("zzz_nonexistent_pattern_zzz", sample_dir)
        assert "No matches" in result

    def test_regex_search(self, sample_dir):
        result = grep_search(r"def \w+", sample_dir, is_regex=True)
        assert "def" in result

    def test_invalid_regex(self, sample_dir):
        result = grep_search("[invalid", sample_dir, is_regex=True)
        assert "Invalid regex" in result


class TestFindFiles:
    """Tests for file finding."""

    def test_find_by_extension(self, sample_dir):
        result = find_files("*.py", sample_dir)
        assert "main.py" in result
        assert "utils.py" in result

    def test_find_by_name(self, sample_dir):
        result = find_files("README.md", sample_dir)
        assert "README.md" in result

    def test_find_no_results(self, sample_dir):
        result = find_files("*.xyz", sample_dir)
        assert "No files" in result


class TestHelpers:
    """Tests for helper functions."""

    def test_binary_detection(self):
        assert _is_binary_file("image.png") is True
        assert _is_binary_file("photo.jpg") is True
        assert _is_binary_file("doc.pdf") is True
        assert _is_binary_file("code.py") is False
        assert _is_binary_file("readme.md") is False
        assert _is_binary_file("data.json") is False

    def test_language_detection(self):
        assert _detect_language("main.py") == "python"
        assert _detect_language("app.js") == "javascript"
        assert _detect_language("style.css") == "css"
        assert _detect_language("config.yaml") == "yaml"
        assert _detect_language("Dockerfile") == "dockerfile"
        assert _detect_language("unknown.xyz") == "text"

    def test_json_repair_valid(self):
        result = _try_repair_json('{"key": "value"}')
        assert result == {"key": "value"}

    def test_json_repair_trailing_comma(self):
        result = _try_repair_json('{"key": "value",}')
        assert result == {"key": "value"}

    def test_json_repair_irreparable(self):
        result = _try_repair_json("not json at all")
        assert result is None


class TestExecuteTool:
    """Tests for the tool dispatcher."""

    def test_execute_read_file(self, sample_file):
        tool_call = Struct(
            id="call_1",
            type="function",
            function=Struct(
                name="read_file",
                arguments=json.dumps({"filepath": sample_file}),
            ),
        )
        result = execute_tool(tool_call)
        assert "def hello():" in result

    def test_execute_unknown_tool(self):
        tool_call = Struct(
            id="call_1",
            type="function",
            function=Struct(
                name="nonexistent_tool",
                arguments="{}",
            ),
        )
        result = execute_tool(tool_call)
        assert "Unknown tool" in result

    def test_execute_invalid_json(self):
        tool_call = Struct(
            id="call_1",
            type="function",
            function=Struct(
                name="read_file",
                arguments="not valid json {{{",
            ),
        )
        result = execute_tool(tool_call)
        assert "Error" in result or "Invalid JSON" in result

    def test_execute_repaired_json(self, sample_file):
        # Trailing comma — should be repaired
        tool_call = Struct(
            id="call_1",
            type="function",
            function=Struct(
                name="read_file",
                arguments=f'{{"filepath": "{sample_file}",}}',
            ),
        )
        result = execute_tool(tool_call)
        assert "def hello():" in result
