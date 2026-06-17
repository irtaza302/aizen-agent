"""Tests for aizen.tools package."""

import os
from unittest.mock import patch

from aizen.tools import backup_manager
from aizen.tools.file_ops import read_file, replace_file_content, write_file_with_diff


class TestReadFile:
    """Tests for the read_file tool."""

    def test_read_existing_file(self, sample_file):
        result = read_file(sample_file)
        assert "def hello():" in result
        assert "Hello, world!" in result
        assert "[File:" in result

    def test_read_nonexistent_file(self, tmp_dir):
        result = read_file(os.path.join(tmp_dir, "nonexistent.py"))
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


class TestReplaceFileContent:
    """Tests for the replace_file_content tool."""

    def test_replace_existing_content(self, sample_file):
        result = replace_file_content(
            sample_file,
            'print("Hello, world!")',
            'print("Hello, Aizen!")',
            1, 999,
            auto_approve=True,
        )
        assert "✓" in result
        with open(sample_file) as f:
            assert 'print("Hello, Aizen!")' in f.read()

    def test_replace_nonexistent_file(self, tmp_dir):
        result = replace_file_content(
            os.path.join(tmp_dir, "nonexistent.py"),
            "old",
            "new",
            1, 999,
            auto_approve=True,
        )
        assert "Error" in result
        assert "does not exist" in result

    def test_replace_content_not_found(self, sample_file):
        result = replace_file_content(
            sample_file,
            "this text does not exist in the file",
            "replacement",
            1, 999,
            auto_approve=True,
        )
        assert "Error" in result
        assert "Could not find" in result

    def test_replace_multiple_occurrences(self, tmp_dir):
        filepath = os.path.join(tmp_dir, "dupes.py")
        with open(filepath, "w") as f:
            f.write("hello\nhello\nhello\n")

        result = replace_file_content(filepath, "hello", "world", 1, 999, auto_approve=True)
        assert "Error" in result
        assert "occurrences" in result.lower()

    def test_replace_auto_heal_whitespace(self, sample_file):
        # Provide target_content with incorrect indentation
        bad_whitespace_old = 'def hello():\n"""Say hello."""\nprint("Hello, world!")'
        new_content = 'def hello():\n    """Say hello."""\n    print("Healed!")'

        result = replace_file_content(
            sample_file,
            bad_whitespace_old,
            new_content,
            1, 999,
            auto_approve=True,
        )
        assert "✓" in result
        with open(sample_file) as f:
            assert 'print("Healed!")' in f.read()

    def test_replace_user_deny(self, sample_file):
        with patch("builtins.input", return_value="n"):
            result = replace_file_content(
                sample_file,
                'print("Hello, world!")',
                'print("changed")',
                1, 999,
                auto_approve=False,
            )
        assert "denied" in result.lower()

    def test_replace_creates_backup(self, sample_file):
        initial_stack = len(backup_manager.undo_stack)
        replace_file_content(
            sample_file,
            'print("Hello, world!")',
            'print("Hello, Aizen!")',
            1, 999,
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
        result = write_file_with_diff(filepath, "print('content')\n", auto_approve=True)
        assert "✓" in result
        assert os.path.exists(filepath)

    def test_write_overwrite_existing(self, sample_file):
        result = write_file_with_diff(sample_file, "print('new content')\n", auto_approve=True)
        assert "✓" in result
        with open(sample_file) as f:
            assert f.read() == "print('new content')\n"

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


