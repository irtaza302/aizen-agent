"""Shared test fixtures for Aether test suite."""

import os
import json
import pytest
import tempfile
import shutil


@pytest.fixture
def tmp_dir():
    """Create a temporary directory for file operations."""
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def sample_file(tmp_dir):
    """Create a sample Python file for testing."""
    filepath = os.path.join(tmp_dir, "sample.py")
    content = '''def hello():
    """Say hello."""
    print("Hello, world!")


def add(a, b):
    return a + b


if __name__ == "__main__":
    hello()
'''
    with open(filepath, "w") as f:
        f.write(content)
    return filepath


@pytest.fixture
def sample_dir(tmp_dir):
    """Create a directory structure for testing."""
    # Create subdirectories
    os.makedirs(os.path.join(tmp_dir, "src"))
    os.makedirs(os.path.join(tmp_dir, "tests"))
    os.makedirs(os.path.join(tmp_dir, ".git"))
    os.makedirs(os.path.join(tmp_dir, "node_modules", "pkg"))

    # Create files
    files = {
        "README.md": "# Test Project",
        "src/main.py": "print('hello')",
        "src/utils.py": "def helper(): pass",
        "tests/test_main.py": "def test_1(): assert True",
        ".gitignore": "node_modules/\n__pycache__/\n",
    }
    for path, content in files.items():
        filepath = os.path.join(tmp_dir, path)
        with open(filepath, "w") as f:
            f.write(content)

    return tmp_dir


@pytest.fixture
def large_file(tmp_dir):
    """Create a large file for size limit testing."""
    filepath = os.path.join(tmp_dir, "large.txt")
    with open(filepath, "w") as f:
        f.write("x" * 2_000_000)  # 2MB
    return filepath


@pytest.fixture
def binary_file(tmp_dir):
    """Create a binary file for binary detection testing."""
    filepath = os.path.join(tmp_dir, "image.png")
    with open(filepath, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
    return filepath


@pytest.fixture
def sessions_dir(tmp_dir):
    """Create a temporary sessions directory."""
    d = os.path.join(tmp_dir, "sessions")
    os.makedirs(d)
    return d


@pytest.fixture
def mock_config(tmp_dir):
    """Create a temporary config file."""
    config_path = os.path.join(tmp_dir, "config.json")
    config = {
        "OPENROUTER_API_KEY": "sk-test-key-1234",
        "API_BASE_URL": "https://openrouter.ai/api/v1",
        "DEFAULT_MODEL": "test/model",
    }
    with open(config_path, "w") as f:
        json.dump(config, f)
    return config_path
