"""Tests for aether.config module."""

import os
import json
import pytest
from unittest.mock import patch

from aether.config import (
    VERSION,
    load_config,
    save_config,
    get_active_model,
    set_active_model,
    _should_check_updates,
)


class TestVersion:
    """Tests for version consistency."""

    def test_version_is_string(self):
        assert isinstance(VERSION, str)

    def test_version_format(self):
        parts = VERSION.split(".")
        assert len(parts) >= 2
        for part in parts:
            assert part.isdigit()

    def test_version_matches_init(self):
        from aether import __version__
        assert __version__ == VERSION


class TestConfig:
    """Tests for config loading and saving."""

    def test_load_config_nonexistent(self, tmp_dir, monkeypatch):
        import aether.config
        monkeypatch.setattr(aether.config, "CONFIG_PATH", os.path.join(tmp_dir, "nope.json"))
        config = load_config()
        assert config == {}

    def test_load_config_existing(self, mock_config, monkeypatch):
        import aether.config
        monkeypatch.setattr(aether.config, "CONFIG_PATH", mock_config)
        config = load_config()
        assert config["OPENROUTER_API_KEY"] == "sk-test-key-1234"
        assert config["DEFAULT_MODEL"] == "test/model"

    def test_save_and_load_config(self, tmp_dir, monkeypatch):
        import aether.config
        config_path = os.path.join(tmp_dir, "test_config.json")
        monkeypatch.setattr(aether.config, "CONFIG_PATH", config_path)

        config = {"key": "value", "number": 42}
        save_config(config)

        loaded = load_config()
        assert loaded["key"] == "value"
        assert loaded["number"] == 42

    def test_load_corrupted_config(self, tmp_dir, monkeypatch):
        import aether.config
        config_path = os.path.join(tmp_dir, "bad.json")
        monkeypatch.setattr(aether.config, "CONFIG_PATH", config_path)

        with open(config_path, "w") as f:
            f.write("not valid json {{{")

        config = load_config()
        assert config == {}


class TestActiveModel:
    """Tests for model management."""

    def test_get_default_model(self):
        model = get_active_model()
        assert isinstance(model, str)
        assert len(model) > 0

    def test_set_and_get_model(self):
        original = get_active_model()
        try:
            set_active_model("test/custom-model")
            assert get_active_model() == "test/custom-model"
        finally:
            set_active_model(original)


class TestUpdateChecker:
    """Tests for the update check logic."""

    def test_should_check_no_previous(self):
        assert _should_check_updates({}) is True

    def test_should_check_stale(self):
        import time
        config = {"_last_update_check": time.time() - 100_000}
        assert _should_check_updates(config) is True

    def test_should_not_check_recent(self):
        import time
        config = {"_last_update_check": time.time() - 100}
        assert _should_check_updates(config) is False
