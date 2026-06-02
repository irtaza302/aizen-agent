"""Tests for aether.session module."""

import os
import json
import pytest

from aether.session import save_session, load_session, list_sessions
from aether.utils import TokenTracker


class TestSaveSession:
    """Tests for session saving."""

    def test_save_session_auto_name(self, tmp_dir, monkeypatch):
        import aether.session
        monkeypatch.setattr(aether.session, "SESSIONS_DIR", tmp_dir)

        messages = [
            {"role": "system", "content": "You are Aether."},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        path = save_session(messages)
        assert os.path.exists(path)
        assert path.endswith(".json")

    def test_save_session_custom_name(self, tmp_dir, monkeypatch):
        import aether.session
        monkeypatch.setattr(aether.session, "SESSIONS_DIR", tmp_dir)

        messages = [{"role": "user", "content": "test"}]
        path = save_session(messages, "my_session")
        assert "my_session" in path

    def test_save_session_with_tracker(self, tmp_dir, monkeypatch):
        import aether.session
        monkeypatch.setattr(aether.session, "SESSIONS_DIR", tmp_dir)

        tracker = TokenTracker()
        tracker.add_usage(100, 50)

        messages = [{"role": "user", "content": "test"}]
        path = save_session(messages, "tracked", tracker)

        with open(path) as f:
            data = json.load(f)
        assert data["tokens"]["input"] == 100
        assert data["tokens"]["output"] == 50

    def test_save_session_sanitizes_name(self, tmp_dir, monkeypatch):
        import aether.session
        monkeypatch.setattr(aether.session, "SESSIONS_DIR", tmp_dir)

        messages = [{"role": "user", "content": "test"}]
        path = save_session(messages, "my session/with:special*chars")
        basename = os.path.basename(path)
        assert "/" not in basename
        assert ":" not in basename
        assert "*" not in basename


class TestLoadSession:
    """Tests for session loading."""

    def test_load_existing_session(self, tmp_dir, monkeypatch):
        import aether.session
        monkeypatch.setattr(aether.session, "SESSIONS_DIR", tmp_dir)

        messages = [
            {"role": "system", "content": "You are Aether."},
            {"role": "user", "content": "Hello"},
        ]
        save_session(messages, "test_load")

        loaded = load_session("test_load")
        assert loaded is not None
        assert len(loaded) == 2
        assert loaded[0]["role"] == "system"

    def test_load_nonexistent_session(self, tmp_dir, monkeypatch):
        import aether.session
        monkeypatch.setattr(aether.session, "SESSIONS_DIR", tmp_dir)

        result = load_session("nonexistent")
        assert result is None


class TestListSessions:
    """Tests for session listing."""

    def test_list_empty(self, tmp_dir, monkeypatch):
        import aether.session
        monkeypatch.setattr(aether.session, "SESSIONS_DIR", tmp_dir)

        sessions = list_sessions()
        assert sessions == []

    def test_list_sessions(self, tmp_dir, monkeypatch):
        import aether.session
        monkeypatch.setattr(aether.session, "SESSIONS_DIR", tmp_dir)

        save_session([{"role": "user", "content": "test 1"}], "session_a")
        save_session([{"role": "user", "content": "test 2"}], "session_b")

        sessions = list_sessions()
        assert len(sessions) == 2
        names = {s["name"] for s in sessions}
        assert "session_a" in names
        assert "session_b" in names

    def test_list_sessions_nonexistent_dir(self, monkeypatch):
        import aether.session
        monkeypatch.setattr(aether.session, "SESSIONS_DIR", "/nonexistent/path")

        sessions = list_sessions()
        assert sessions == []
