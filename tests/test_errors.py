"""Tests for the error taxonomy and secret scrubbing."""
from core.errors import (
    AuthenticationError,
    EmptySelectionError,
    NetworkError,
    ProviderTimeoutError,
    QuotaError,
    RateLimitError,
    WindowGoneError,
    friendly_error,
    scrub_secrets,
)


def test_error_codes_are_stable():
    assert AuthenticationError().code == "auth"
    assert QuotaError().code == "quota"
    assert RateLimitError().code == "rate_limit"
    assert NetworkError().code == "network"
    assert ProviderTimeoutError().code == "timeout"
    assert EmptySelectionError().code == "empty_selection"
    assert WindowGoneError().code == "window_gone"


def test_retryable_flags():
    assert RateLimitError().retryable is True
    assert NetworkError().retryable is True
    assert ProviderTimeoutError().retryable is True
    assert AuthenticationError().retryable is False


def test_scrub_secrets_removes_key():
    msg = "Error 401 for key sk-abc123XYZ and again sk-abc123XYZ"
    assert "sk-abc123XYZ" not in scrub_secrets(msg, ["sk-abc123XYZ"])


def test_scrub_secrets_ignores_short_or_empty():
    assert scrub_secrets("hello", [None, "", "x"]) == "hello"


def test_friendly_error_generic_exception_hides_details():
    message = friendly_error(ValueError("api_key=sk-secret"))
    assert "sk-secret" not in message
    assert "unexpected" in message.lower()


def test_friendly_error_scrubs_known_secrets():
    err = AuthenticationError("key sk-live-42 was rejected")
    message = friendly_error(err, ["sk-live-42"])
    assert "sk-live-42" not in message
