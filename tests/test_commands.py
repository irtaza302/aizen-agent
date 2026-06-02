"""Tests for aether.commands module."""

import os
import pytest
from unittest.mock import patch, MagicMock

from aether.commands import handle_slash_command, AetherCompleter
from aether.utils import TokenTracker


class TestSlashCommands:
    """Tests for slash command handling."""

    def test_clear_command(self):
        messages = [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        result = handle_slash_command("/clear", messages, TokenTracker())
        assert result is False
        assert len(messages) == 1
        assert messages[0]["role"] == "system"

    def test_model_view(self):
        """Test /model without arguments shows current model."""
        result = handle_slash_command("/model", [], TokenTracker())
        assert result is False

    def test_model_switch(self):
        """Test /model with argument switches model."""
        from aether.config import get_active_model, set_active_model
        original = get_active_model()
        try:
            handle_slash_command("/model test/new-model", [], TokenTracker())
            assert get_active_model() == "test/new-model"
        finally:
            set_active_model(original)

    def test_help_command(self):
        """Test /help runs without error."""
        result = handle_slash_command("/help", [], TokenTracker())
        assert result is False

    def test_usage_command(self):
        """Test /usage runs without error."""
        tracker = TokenTracker()
        tracker.add_usage(100, 50)
        result = handle_slash_command("/usage", [], tracker)
        assert result is False

    def test_retry_with_user_message(self):
        messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "test"},
            {"role": "assistant", "content": "response"},
        ]
        result = handle_slash_command("/retry", messages, TokenTracker())
        assert result is True
        assert messages[-1]["role"] == "user"

    def test_retry_no_messages(self):
        messages = [{"role": "system", "content": "system"}]
        result = handle_slash_command("/retry", messages, TokenTracker())
        assert result is False

    def test_compact_short_conversation(self):
        messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "hello"},
        ]
        result = handle_slash_command("/compact", messages, TokenTracker())
        assert result is False

    def test_compact_long_conversation(self):
        messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "question 1"},
            {"role": "assistant", "content": "answer 1"},
            {"role": "user", "content": "question 2"},
            {"role": "assistant", "content": "answer 2"},
            {"role": "user", "content": "question 3"},
            {"role": "assistant", "content": "answer 3"},
            {"role": "user", "content": "question 4"},
            {"role": "assistant", "content": "answer 4"},
        ]
        original_len = len(messages)
        handle_slash_command("/compact", messages, TokenTracker())
        assert len(messages) < original_len

    def test_unknown_command(self):
        result = handle_slash_command("/nonexistent", [], TokenTracker())
        assert result is False

    def test_config_command(self):
        """Test /config runs without error."""
        result = handle_slash_command("/config", [], TokenTracker())
        assert result is False

    def test_undo_command(self):
        """Test /undo runs without error."""
        result = handle_slash_command("/undo", [], TokenTracker())
        assert result is False

    def test_export_command(self, tmp_dir):
        messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        export_path = os.path.join(tmp_dir, "export.md")
        result = handle_slash_command(
            f"/export {export_path}", messages, TokenTracker()
        )
        assert result is False
        assert os.path.exists(export_path)
        with open(export_path) as f:
            content = f.read()
        assert "Aether" in content


class TestAetherCompleter:
    """Tests for the autocomplete system."""

    def test_slash_command_completion(self):
        completer = AetherCompleter()
        # Create a mock document
        doc = MagicMock()
        doc.text_before_cursor = "/he"

        completions = list(completer.get_completions(doc, None))
        texts = [c.text for c in completions]
        assert "/help" in texts

    def test_slash_command_no_args_completion(self):
        """Should not complete after space (user is typing args)."""
        completer = AetherCompleter()
        doc = MagicMock()
        doc.text_before_cursor = "/model "

        completions = list(completer.get_completions(doc, None))
        assert len(completions) == 0

    def test_no_completion_for_regular_text(self):
        completer = AetherCompleter()
        doc = MagicMock()
        doc.text_before_cursor = "hello world"

        completions = list(completer.get_completions(doc, None))
        assert len(completions) == 0
