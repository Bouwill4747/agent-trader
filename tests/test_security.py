"""
Security-focused tests — validates defenses against malformed data,
prompt injection, kill switch behavior, and Pydantic validation.

Addresses: I-3 (security audit)
"""

import json
import os
import tempfile
import pytest
from unittest.mock import patch, MagicMock

from src.data.models import GammaMarket
from src.analysis.llm_researcher import LLMResearcher
from src.data.polymarket_client import _validate_id
from src.trading.risk_manager import RiskManager


# ──────────────────────────────────────────────
# Pydantic Validation Tests (HIGH-02)
# ──────────────────────────────────────────────

class TestGammaMarketValidation:
    """Test that malformed API responses are caught by Pydantic."""

    def test_valid_market_passes(self):
        """A well-formed market passes validation."""
        market = GammaMarket(
            id="abc123",
            question="Will BTC hit $100k?",
            volume=50000.0,
            liquidity=10000.0,
            outcomePrices='["0.55","0.45"]',
            clobTokenIds='["token_yes","token_no"]',
        )
        assert market.id == "abc123"
        assert market.volume == 50000.0

    def test_none_volume_coerced_to_zero(self):
        """None volume/liquidity should become 0.0, not crash."""
        market = GammaMarket(id="abc", volume=None, liquidity=None)
        assert market.volume == 0.0
        assert market.liquidity == 0.0

    def test_empty_string_volume_coerced_to_zero(self):
        """Empty string volume should become 0.0."""
        market = GammaMarket(id="abc", volume="", liquidity="")
        assert market.volume == 0.0
        assert market.liquidity == 0.0

    def test_nan_volume_coerced_to_zero(self):
        """NaN values should become 0.0, not propagate."""
        market = GammaMarket(id="abc", volume=float("nan"))
        assert market.volume == 0.0

    def test_string_numeric_volume_works(self):
        """String numbers from API should be coerced properly."""
        market = GammaMarket(id="abc", volume="50000")
        assert market.volume == 50000.0

    def test_invalid_price_out_of_range_rejected(self):
        """Prices outside [0, 1] should be rejected."""
        with pytest.raises(Exception):
            GammaMarket(id="abc", outcomePrices='["1.50","0.45"]')

    def test_negative_price_rejected(self):
        """Negative prices should be rejected."""
        with pytest.raises(Exception):
            GammaMarket(id="abc", outcomePrices='["-0.10","1.10"]')

    def test_invalid_json_prices_rejected(self):
        """Malformed JSON in outcomePrices should be rejected."""
        with pytest.raises(Exception):
            GammaMarket(id="abc", outcomePrices="not json at all")

    def test_missing_id_and_condition_id_rejected(self):
        """Market with neither id nor condition_id should be rejected."""
        with pytest.raises(Exception):
            GammaMarket(id="", condition_id="")

    def test_valid_prices_pass(self):
        """Valid prices within [0, 1] pass validation."""
        market = GammaMarket(id="abc", outcomePrices='["0.55","0.45"]')
        assert market.outcomePrices == '["0.55","0.45"]'

    def test_invalid_token_ids_rejected(self):
        """Non-JSON clobTokenIds should be rejected."""
        with pytest.raises(Exception):
            GammaMarket(id="abc", clobTokenIds="not_valid_json")

    def test_extra_fields_preserved(self):
        """Extra fields from the API should be preserved (extra='allow')."""
        market = GammaMarket(id="abc", bestAsk=0.56, lastTradePrice=0.55)
        dump = market.model_dump()
        assert dump.get("bestAsk") == 0.56


# ──────────────────────────────────────────────
# ID Validation Tests (MEDIUM-02)
# ──────────────────────────────────────────────

class TestIDValidation:
    """Test that malicious ID parameters are rejected."""

    def test_valid_id_passes(self):
        """A normal alphanumeric ID passes."""
        assert _validate_id("abc123-def_456", "test") == "abc123-def_456"

    def test_empty_id_rejected(self):
        """Empty strings should be rejected."""
        with pytest.raises(ValueError, match="Empty"):
            _validate_id("", "test")

    def test_path_traversal_rejected(self):
        """Path traversal attempts should be rejected."""
        with pytest.raises(ValueError, match="Invalid characters"):
            _validate_id("../events", "market_id")

    def test_url_encoded_rejected(self):
        """URL-encoded characters should be rejected."""
        with pytest.raises(ValueError, match="Invalid characters"):
            _validate_id("abc%2F..%2Fevents", "market_id")

    def test_spaces_rejected(self):
        """IDs with spaces should be rejected."""
        with pytest.raises(ValueError, match="Invalid characters"):
            _validate_id("abc def", "market_id")

    def test_too_long_rejected(self):
        """Extremely long IDs should be rejected."""
        with pytest.raises(ValueError, match="exceeds max length"):
            _validate_id("a" * 300, "market_id")


# ──────────────────────────────────────────────
# Prompt Injection Defense Tests (H-1 security)
# ──────────────────────────────────────────────

class TestPromptInjectionDefense:
    """Test that text sanitization prevents prompt injection."""

    def test_control_characters_removed(self):
        """Control characters should be stripped from text."""
        text = "Normal text\x00\x01\x02with\x7fcontrol chars"
        result = LLMResearcher._sanitize_text(text)
        assert "\x00" not in result
        assert "\x01" not in result
        assert "\x7f" not in result
        assert "Normal text" in result

    def test_text_truncated_at_max_length(self):
        """Text exceeding max_length should be truncated."""
        long_text = "A" * 1000
        result = LLMResearcher._sanitize_text(long_text, max_length=100)
        assert len(result) <= 104  # 100 + "..."

    def test_empty_text_returns_empty(self):
        """Empty/None text returns empty string."""
        assert LLMResearcher._sanitize_text("") == ""
        assert LLMResearcher._sanitize_text(None) == ""

    def test_normal_text_preserved(self):
        """Normal article text should pass through unchanged."""
        text = "Bitcoin surges 5% on ETF approval news. Analysts remain cautious."
        result = LLMResearcher._sanitize_text(text)
        assert result == text

    def test_newlines_and_tabs_preserved(self):
        """Legitimate whitespace (newlines, tabs) should be kept."""
        text = "Line one\nLine two\tTabbed"
        result = LLMResearcher._sanitize_text(text)
        assert "\n" in result
        assert "\t" in result


# ──────────────────────────────────────────────
# Kill Switch Tests (H-3 security)
# ──────────────────────────────────────────────

class TestKillSwitch:
    """Test that the kill switch correctly detects the STOP file."""

    def test_kill_switch_detects_stop_file(self):
        """Kill switch should return True when STOP file exists."""
        with tempfile.NamedTemporaryFile(delete=False, suffix="_STOP") as f:
            stop_path = f.name
            f.write(b"STOP")

        try:
            risk = RiskManager()
            with patch("config.settings.KILL_SWITCH_PATH", stop_path):
                assert risk.check_kill_switch() is True
        finally:
            os.unlink(stop_path)

    def test_kill_switch_false_without_stop_file(self):
        """Kill switch should return False when no STOP file exists."""
        risk = RiskManager()
        with patch("config.settings.KILL_SWITCH_PATH", "/tmp/nonexistent_stop_file"):
            assert risk.check_kill_switch() is False


# ──────────────────────────────────────────────
# Secret Redaction Tests (HIGH-04)
# ──────────────────────────────────────────────

class TestSecretRedaction:
    """Test that the log filter redacts sensitive data."""

    def test_ethereum_key_redacted(self):
        """Ethereum private keys (0x + 64 hex) should be redacted."""
        from src.utils.logger import SecretRedactionFilter
        import logging

        filt = SecretRedactionFilter()
        record = logging.LogRecord(
            name="test", level=logging.ERROR, pathname="", lineno=0,
            msg="Key is 0x" + "a" * 64 + " end",
            args=(), exc_info=None,
        )
        filt.filter(record)
        assert "0x[REDACTED_KEY]" in record.msg
        assert "a" * 64 not in record.msg

    def test_anthropic_key_redacted(self):
        """Anthropic API keys (sk-ant-*) should be redacted."""
        from src.utils.logger import SecretRedactionFilter
        import logging

        filt = SecretRedactionFilter()
        record = logging.LogRecord(
            name="test", level=logging.ERROR, pathname="", lineno=0,
            msg="Using key sk-ant-api03-abc123xyz",
            args=(), exc_info=None,
        )
        filt.filter(record)
        assert "[REDACTED_ANTHROPIC_KEY]" in record.msg
        assert "sk-ant-api03-abc123xyz" not in record.msg

    def test_generic_api_key_redacted(self):
        """Generic key=value patterns should be redacted."""
        from src.utils.logger import SecretRedactionFilter
        import logging

        filt = SecretRedactionFilter()
        record = logging.LogRecord(
            name="test", level=logging.ERROR, pathname="", lineno=0,
            msg="api_key=supersecretvalue123",
            args=(), exc_info=None,
        )
        filt.filter(record)
        assert "supersecretvalue123" not in record.msg

    def test_normal_messages_unchanged(self):
        """Normal log messages without secrets should pass through."""
        from src.utils.logger import SecretRedactionFilter
        import logging

        filt = SecretRedactionFilter()
        msg = "Fetched 10 markets from Gamma API"
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg=msg, args=(), exc_info=None,
        )
        filt.filter(record)
        assert record.msg == msg
