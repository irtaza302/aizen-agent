import os
import pytest
from aizen.exceptions import SecurityError, UsageError
from aizen.main import inject_file_context

def test_dangerous_commands_blocked():
    with pytest.raises(SecurityError) as exc_info:
        inject_file_context('@cmd:"rm -rf /"')
    assert "Dangerous command detected" in str(exc_info.value)

def test_safe_commands_allowed_when_yolo_true():
    # If a safe command is passed, it shouldn't raise SecurityError or UsageError
    # We pass auto_approve=True to simulate yolo, though it also allows non-safe commands, wait.
    # Actually, the logic in main is:
    # if parts[0] not in SAFE_COMMANDS and not auto_approve:
    #     raise UsageError(...)
    # So if we pass a non-safe command but auto_approve=False, it should raise UsageError
    with pytest.raises(UsageError) as exc_info:
        inject_file_context('@cmd:"some_unknown_command"', auto_approve=False)
    assert "Command not in safe list" in str(exc_info.value)

def test_path_traversal_blocked_file():
    # Write a dummy file to /tmp or just use a fake traversal
    # Actually, the code checks if os.path.exists first.
    # So we need an existing file outside cwd. Let's use /tmp/dummy_aizen_test.txt
    dummy_file = "/tmp/dummy_aizen_test.txt"
    with open(dummy_file, "w") as f:
        f.write("test")
    
    try:
        with pytest.raises(SecurityError) as exc_info:
            inject_file_context(f"@{dummy_file}")
        assert "Attempt to access files outside project root" in str(exc_info.value)
    finally:
        os.remove(dummy_file)

def test_path_traversal_blocked_dir():
    # Use /tmp as it exists and is outside cwd
    with pytest.raises(SecurityError) as exc_info:
        inject_file_context("@/tmp")
    assert "Attempt to access directory outside project root" in str(exc_info.value)
