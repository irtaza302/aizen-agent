"""Tests for aizen.commands module."""

import os
import pytest
from unittest.mock import patch, MagicMock

from aizen.commands import handle_slash_command, AizenCompleter
from aizen.utils import TokenTracker


@pytest.mark.asyncio
class TestSlashCommands:
    """Tests for slash command handling."""

    async def test_clear_command(self):
        messages = [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        result = await handle_slash_command("/clear", messages, TokenTracker())
        assert result is False
        assert len(messages) == 1
        assert messages[0]["role"] == "system"

    async def test_model_view(self):
        """Test /model without arguments shows current model."""
        result = await handle_slash_command("/model", [], TokenTracker())
        assert result is False

    async def test_model_switch(self):
        """Test /model with argument switches model."""
        from aizen.config import get_active_model, set_active_model
        original = get_active_model()
        try:
            await handle_slash_command("/model test/new-model", [], TokenTracker())
            assert get_active_model() == "test/new-model"
        finally:
            set_active_model(original)

    async def test_help_command(self):
        """Test /help runs without error."""
        result = await handle_slash_command("/help", [], TokenTracker())
        assert result is False

    async def test_drop_command(self):
        """Test /drop removes context blocks from messages."""
        messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "Here is a file: <file_context path=\"test.py\">print('hello')</file_context>"},
            {"role": "user", "content": "Here is a url: <url_context url=\"http://example.com\">example text</url_context> and more text."},
            {"role": "user", "content": "Here is a command: <command_context cmd=\"ls -l\">file1.txt</command_context>"},
        ]
        result = await handle_slash_command("/drop", messages, TokenTracker())
        assert result is False
        assert "print('hello')" not in messages[1]["content"]
        assert "[File context dropped to save tokens]" in messages[1]["content"]
        assert "example text" not in messages[2]["content"]
        assert "[URL context dropped to save tokens]" in messages[2]["content"]
        assert "and more text." in messages[2]["content"]
        assert "file1.txt" not in messages[3]["content"]
        assert "[Command context dropped to save tokens]" in messages[3]["content"]

    async def test_usage_command(self):
        """Test /usage runs without error."""
        tracker = TokenTracker()
        tracker.add_usage(100, 50)
        result = await handle_slash_command("/usage", [], tracker)
        assert result is False

    async def test_retry_with_user_message(self):
        messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "test"},
            {"role": "assistant", "content": "response"},
        ]
        result = await handle_slash_command("/retry", messages, TokenTracker())
        assert result is True
        assert messages[-1]["role"] == "user"

    async def test_retry_no_messages(self):
        messages = [{"role": "system", "content": "system"}]
        result = await handle_slash_command("/retry", messages, TokenTracker())
        assert result is False

    async def test_compact_short_conversation(self):
        messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "hello"},
        ]
        result = await handle_slash_command("/compact", messages, TokenTracker())
        assert result is False

    async def test_compact_long_conversation(self):
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
        await handle_slash_command("/compact", messages, TokenTracker())
        assert len(messages) < original_len

    async def test_unknown_command(self):
        result = await handle_slash_command("/nonexistent", [], TokenTracker())
        assert result is False

    async def test_config_command(self):
        """Test /config runs without error."""
        result = await handle_slash_command("/config", [], TokenTracker())
        assert result is False

    async def test_undo_command(self):
        """Test /undo runs without error."""
        result = await handle_slash_command("/undo", [], TokenTracker())
        assert result is False

    async def test_export_command(self, tmp_dir):
        messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        export_path = os.path.join(tmp_dir, "export.md")
        result = await handle_slash_command(
            f"/export {export_path}", messages, TokenTracker()
        )
        assert result is False
        assert os.path.exists(export_path)
        with open(export_path) as f:
            content = f.read()
        assert "Aizen" in content

    @patch("aizen.commands.subprocess.run")
    @patch("aizen.commands.prompt")
    async def test_commit_command(self, mock_prompt, mock_run):
        """Test /commit command calls git and API."""
        mock_prompt.return_value = "y"
        
        # Mock git diff --cached returning a diff
        mock_diff = MagicMock()
        mock_diff.stdout = "diff --git a/test b/test"
        mock_run.return_value = mock_diff
        
        # Mock client
        class MockMessage:
            content = "feat: add test"
        class MockChoice:
            message = MockMessage()
        class MockResponse:
            choices = [MockChoice()]
        class MockCompletions:
            async def create(self, **kwargs):
                return MockResponse()
        class MockChat:
            completions = MockCompletions()
        class MockClient:
            chat = MockChat()
            
        result = await handle_slash_command("/commit", [], TokenTracker(), client=MockClient())
        
        assert result is False
        mock_run.assert_any_call(["git", "commit", "-m", "feat: add test"], check=True)


class TestAizenCompleter:
    """Tests for the autocomplete system."""

    def test_slash_command_completion(self):
        completer = AizenCompleter()
        # Create a mock document
        doc = MagicMock()
        doc.text_before_cursor = "/he"

        completions = list(completer.get_completions(doc, None))
        texts = [c.text for c in completions]
        assert "/help" in texts

    def test_slash_command_no_args_completion(self):
        """Should not complete after space for commands that don't take args."""
        completer = AizenCompleter()
        doc = MagicMock()
        doc.text_before_cursor = "/help "

        completions = list(completer.get_completions(doc, None))
        assert len(completions) == 0

    @patch("aizen.commands.get_cached_models")
    def test_slash_command_model_completion(self, mock_get_models):
        """Should complete models after /model """
        mock_get_models.return_value = [{"id": "test/model", "name": "Test Model", "context_length": 8000}]
        completer = AizenCompleter()
        doc = MagicMock()
        doc.text_before_cursor = "/model "

        completions = list(completer.get_completions(doc, None))
        assert len(completions) > 0

    def test_no_completion_for_regular_text(self):
        completer = AizenCompleter()
        doc = MagicMock()
        doc.text_before_cursor = "hello world"

        completions = list(completer.get_completions(doc, None))
        assert len(completions) == 0
