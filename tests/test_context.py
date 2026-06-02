"""Tests for aether.context module."""

import pytest

from aether.context import ContextManager, MODEL_CONTEXT_WINDOWS, DEFAULT_CONTEXT_WINDOW


class TestContextManager:
    """Tests for context window management."""

    def test_known_model_limit(self):
        cm = ContextManager("openai/gpt-4o")
        assert cm.context_limit == 128_000

    def test_unknown_model_default(self):
        cm = ContextManager("unknown/model-xyz")
        assert cm.context_limit == DEFAULT_CONTEXT_WINDOW

    def test_custom_limit_overrides(self):
        cm = ContextManager("openai/gpt-4o", custom_limit=50_000)
        assert cm.context_limit == 50_000

    def test_usage_fraction(self):
        cm = ContextManager("openai/gpt-4o")  # 128k
        cm.update(64_000)
        assert cm.usage_fraction == pytest.approx(0.5, rel=0.01)

    def test_usage_percent(self):
        cm = ContextManager("openai/gpt-4o")
        cm.update(96_000)  # 75%
        assert cm.usage_percent == 75

    def test_warning_at_threshold(self):
        cm = ContextManager("openai/gpt-4o")  # 128k
        cm.update(96_000)  # 75%
        warning = cm.check_and_warn()
        assert warning is not None
        assert "75%" in warning

    def test_no_warning_below_threshold(self):
        cm = ContextManager("openai/gpt-4o")
        cm.update(10_000)  # ~8%
        warning = cm.check_and_warn()
        assert warning is None

    def test_auto_compact_at_threshold(self):
        cm = ContextManager("openai/gpt-4o")
        cm.update(110_000)  # ~86%
        assert cm.needs_auto_compact() is True

    def test_no_auto_compact_below_threshold(self):
        cm = ContextManager("openai/gpt-4o")
        cm.update(50_000)
        assert cm.needs_auto_compact() is False

    def test_warning_only_once(self):
        cm = ContextManager("openai/gpt-4o")
        cm.update(96_000)  # 75%
        first = cm.check_and_warn()
        assert first is not None
        second = cm.check_and_warn()
        # Second call should return None (already warned at 75%)
        # unless it's above auto-compact threshold
        assert second is None

    def test_set_model_resets_warning(self):
        cm = ContextManager("openai/gpt-4o")
        cm.update(96_000)
        cm.check_and_warn()  # First warning
        cm.set_model("openai/gpt-4o-mini")
        cm.update(96_000)
        warning = cm.check_and_warn()  # Should warn again after model change
        assert warning is not None

    def test_usage_bar(self):
        cm = ContextManager("openai/gpt-4o")
        cm.update(64_000)  # 50%
        bar = cm.get_usage_bar(20)
        assert "50%" in bar

    def test_footer_text(self):
        cm = ContextManager("openai/gpt-4o")
        cm.update(10_000)
        footer = cm.get_footer_text()
        assert "ctx:" in footer

    def test_estimate_messages_tokens(self):
        cm = ContextManager("openai/gpt-4o")
        messages = [
            {"role": "system", "content": "You are an AI."},
            {"role": "user", "content": "Hello world"},
        ]
        estimator = lambda text: max(1, int(len(text.split()) * 1.3))
        total = cm.estimate_messages_tokens(messages, estimator)
        assert total > 0
