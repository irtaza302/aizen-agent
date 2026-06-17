"""Tests for aizen.main module."""

import os
from unittest.mock import MagicMock, patch

from aizen.main import inject_file_context


class TestMain:
    """Tests for main.py."""

    @patch("aizen.main.subprocess.run")
    def test_inject_file_context_command(self, mock_run):
        # Setup mock for subprocess
        mock_result = MagicMock()
        mock_result.stdout = "hello world\n"
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        user_input = "What does this mean? @cmd:\"echo 'hello world'\""

        result = inject_file_context(user_input)

        mock_run.assert_called_once_with("echo 'hello world'", shell=True, capture_output=True, text=True, timeout=30, cwd=os.getcwd())
        assert "<command_context cmd=\"echo 'hello world'\">" in result
        assert "hello world" in result
        assert "</command_context>" in result

    @patch("aizen.main.subprocess.run")
    def test_inject_file_context_command_with_stderr(self, mock_run):
        # Setup mock for subprocess
        mock_result = MagicMock()
        mock_result.stdout = ""
        mock_result.stderr = "error message\n"
        mock_run.return_value = mock_result

        user_input = "Error here: @cmd:ls-fake"

        result = inject_file_context(user_input)

        mock_run.assert_called_once_with("ls-fake", shell=True, capture_output=True, text=True, timeout=30, cwd=os.getcwd())
        assert "<command_context cmd=\"ls-fake\">" in result
        assert "error message" in result
        assert "--- STDERR ---" in result
