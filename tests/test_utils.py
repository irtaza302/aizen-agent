"""Tests for aether.utils module."""

import os
import time
import pytest
import shutil
import tempfile

from aether.utils import (
    TokenTracker,
    BackupManager,
    truncate_output,
    load_gitignore_patterns,
    should_ignore,
    Struct,
)


class TestStruct:
    """Tests for the Struct namespace helper."""

    def test_basic_struct(self):
        s = Struct(name="test", value=42)
        assert s.name == "test"
        assert s.value == 42

    def test_empty_struct(self):
        s = Struct()
        assert not hasattr(s, "name")

    def test_nested_struct(self):
        inner = Struct(key="inner")
        outer = Struct(child=inner)
        assert outer.child.key == "inner"


class TestTokenTracker:
    """Tests for token tracking."""

    def test_initial_state(self):
        tracker = TokenTracker()
        assert tracker.total_tokens == 0
        assert tracker.message_count == 0

    def test_add_usage(self):
        tracker = TokenTracker()
        tracker.add_usage(100, 50)
        assert tracker.total_input_tokens == 100
        assert tracker.total_output_tokens == 50
        assert tracker.total_tokens == 150
        assert tracker.message_count == 1

    def test_add_multiple_usage(self):
        tracker = TokenTracker()
        tracker.add_usage(100, 50)
        tracker.add_usage(200, 100)
        assert tracker.total_tokens == 450
        assert tracker.message_count == 2

    def test_api_usage_overrides_estimate(self):
        tracker = TokenTracker()
        tracker.add_usage(100, 50)  # Estimated
        tracker.add_api_usage(500, 200)  # API-reported
        assert tracker.total_tokens == 700  # Uses API numbers
        assert tracker.input_tokens == 500
        assert tracker.output_tokens == 200

    def test_estimate_tokens_empty(self):
        tracker = TokenTracker()
        assert tracker.estimate_tokens("") == 0
        assert tracker.estimate_tokens(None) == 0

    def test_estimate_tokens_text(self):
        tracker = TokenTracker()
        result = tracker.estimate_tokens("hello world this is a test")
        assert result > 0
        assert isinstance(result, int)

    def test_session_duration(self):
        tracker = TokenTracker()
        duration = tracker.session_duration
        assert "s" in duration

    def test_summary_table(self):
        tracker = TokenTracker()
        tracker.add_usage(100, 50)
        table = tracker.get_summary_table()
        assert table is not None
        assert table.title == "📊 Session Usage"


class TestBackupManager:
    """Tests for the backup system."""

    def test_backup_creates_file(self, tmp_dir):
        backup_dir = os.path.join(tmp_dir, "backups")
        os.makedirs(backup_dir)

        # Monkey-patch BACKUPS_DIR
        import aether.utils
        original_dir = aether.utils.BACKUPS_DIR
        aether.utils.BACKUPS_DIR = backup_dir

        try:
            manager = BackupManager()
            manager.__init__()  # Re-init with new dir

            # Create test file
            filepath = os.path.join(tmp_dir, "test.py")
            with open(filepath, "w") as f:
                f.write("original content")

            backup_path = manager.backup(filepath)
            assert backup_path is not None
            assert os.path.exists(backup_path)
        finally:
            aether.utils.BACKUPS_DIR = original_dir

    def test_backup_nonexistent_file(self):
        manager = BackupManager()
        result = manager.backup("/nonexistent/file.py")
        assert result is None

    def test_undo_empty_stack(self):
        manager = BackupManager()
        manager.undo_stack = []
        result = manager.undo()
        assert "Nothing to undo" in result

    def test_undo_restores_file(self, tmp_dir):
        backup_dir = os.path.join(tmp_dir, "backups")
        os.makedirs(backup_dir)

        import aether.utils
        original_dir = aether.utils.BACKUPS_DIR
        aether.utils.BACKUPS_DIR = backup_dir

        try:
            manager = BackupManager()
            manager.__init__()

            filepath = os.path.join(tmp_dir, "test.py")
            with open(filepath, "w") as f:
                f.write("original")

            manager.backup(filepath)

            # Modify the file
            with open(filepath, "w") as f:
                f.write("modified")

            # Undo
            result = manager.undo()
            assert "✓" in result

            with open(filepath) as f:
                assert f.read() == "original"
        finally:
            aether.utils.BACKUPS_DIR = original_dir


class TestTruncateOutput:
    """Tests for output truncation."""

    def test_short_text_not_truncated(self):
        text = "short text"
        assert truncate_output(text) == text

    def test_long_text_truncated(self):
        text = "x" * 10000
        result = truncate_output(text, max_chars=100)
        assert "TRUNCATED" in result
        assert len(result) < len(text)

    def test_truncation_preserves_start_and_end(self):
        text = "START" + "x" * 10000 + "END"
        result = truncate_output(text, max_chars=200)
        assert "START" in result
        assert "END" in result


class TestGitignorePatterns:
    """Tests for gitignore pattern matching."""

    def test_should_ignore_node_modules(self):
        patterns = ["node_modules/"]
        assert should_ignore("node_modules/pkg/index.js", patterns) is True

    def test_should_ignore_pycache(self):
        patterns = ["__pycache__/"]
        assert should_ignore("src/__pycache__/module.pyc", patterns) is True

    def test_should_not_ignore_regular_file(self):
        patterns = ["node_modules/", "__pycache__/"]
        assert should_ignore("src/main.py", patterns) is False

    def test_should_ignore_env_file(self):
        patterns = [".env"]
        assert should_ignore(".env", patterns) is True

    def test_should_ignore_glob_pattern(self):
        patterns = ["*.pyc"]
        assert should_ignore("module.pyc", patterns) is True

    def test_should_not_ignore_py_files(self):
        patterns = ["*.pyc"]
        assert should_ignore("module.py", patterns) is False
