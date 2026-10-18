"""Error taxonomy for QuickPrompt AI.

Every error carries a stable ``code`` and a safe, user-friendly message.
Provider error bodies are never surfaced raw; they pass through
:func:`scrub_secrets` first so API keys can never leak into the UI or logs.
"""
from __future__ import annotations


class QuickPromptError(Exception):
    """Base class for all application errors."""

    code = "generic"
    default_message = "Something went wrong."

    def __init__(self, message: str | None = None, *, detail: str | None = None,
                 retryable: bool = False):
        super().__init__(message or self.default_message)
        self.message = message or self.default_message
        self.detail = detail
        self.retryable = retryable

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.message


class ConfigError(QuickPromptError):
    code = "config"
    default_message = "The configuration is invalid."


class MissingApiKeyError(ConfigError):
    code = "missing_api_key"
    default_message = (
        "No API key is configured for this provider. "
        "Open Settings → Provider and save your API key first."
    )


class AuthenticationError(QuickPromptError):
    code = "auth"
    default_message = (
        "The provider rejected the API key. It may be invalid or expired — "
        "check it in Settings → Provider."
    )


class QuotaError(QuickPromptError):
    code = "quota"
    default_message = (
        "The provider reports insufficient credits or quota. "
        "Check your billing with the provider."
    )


class RateLimitError(QuickPromptError):
    code = "rate_limit"
    default_message = "The provider rate limit was hit. Wait a moment and retry."

    def __init__(self, message: str | None = None, *, detail: str | None = None):
        super().__init__(message, detail=detail, retryable=True)


class NetworkError(QuickPromptError):
    code = "network"
    default_message = (
        "Could not reach the provider. Check your internet connection and retry."
    )

    def __init__(self, message: str | None = None, *, detail: str | None = None):
        super().__init__(message, detail=detail, retryable=True)


class ProviderTimeoutError(QuickPromptError):
    code = "timeout"
    default_message = (
        "The provider did not answer in time. Increase the timeout in "
        "Settings → Provider or retry."
    )

    def __init__(self, message: str | None = None, *, detail: str | None = None):
        super().__init__(message, detail=detail, retryable=True)


class ProviderRequestError(QuickPromptError):
    """HTTP 4xx that is not auth/quota/rate-limit (usually a bad request)."""

    code = "bad_request"
    default_message = (
        "The provider rejected the request. Check the model name and settings."
    )

    def __init__(self, message: str | None = None, *, detail: str | None = None,
                 status: int | None = None):
        super().__init__(message, detail=detail)
        self.status = status


class ModelError(QuickPromptError):
    code = "model"
    default_message = (
        "The model or endpoint was not found. Check the model ID and API base URL."
    )


class InvalidResponseError(QuickPromptError):
    code = "invalid_response"
    default_message = "The provider returned an invalid or empty response."


class EmptySelectionError(QuickPromptError):
    code = "empty_selection"
    default_message = (
        "No text selection was found. Select some text in the active window "
        "and press the shortcut again."
    )


class InputTooLongError(QuickPromptError):
    code = "input_too_long"
    default_message = (
        "The selected text is too long to process in one request. "
        "Shorten it or split it into parts."
    )


class ClipboardError(QuickPromptError):
    code = "clipboard"
    default_message = "The clipboard could not be accessed."


class ReplaceError(QuickPromptError):
    code = "replace"
    default_message = "The selected text could not be replaced."


class WindowGoneError(ReplaceError):
    code = "window_gone"
    default_message = (
        "The original window is no longer active, so the selection was not "
        "replaced. Copy the result and paste it manually instead."
    )


class ShortcutError(QuickPromptError):
    code = "shortcut"
    default_message = (
        "The keyboard shortcut could not be registered. It may be reserved "
        "by Windows or another application — choose a different combination."
    )


class SecurityError(QuickPromptError):
    code = "security"
    default_message = "Secure credential storage is unavailable on this system."


class MicrophoneError(QuickPromptError):
    """Raised when the microphone cannot be opened or captured (dictation)."""

    code = "microphone"
    default_message = (
        "The microphone could not be used. Check that a microphone is "
        "connected and allowed in Windows privacy settings, then try again."
    )


#: Friendly default messages shown when an error code reaches the UI without
#: a specific message (kept in sync with the classes above).
USER_MESSAGES: dict[str, str] = {
    cls.code: cls.default_message
    for cls in (
        QuickPromptError, ConfigError, MissingApiKeyError, AuthenticationError,
        QuotaError, RateLimitError, NetworkError, ProviderTimeoutError,
        ProviderRequestError, ModelError, InvalidResponseError,
        EmptySelectionError, InputTooLongError, ClipboardError, ReplaceError,
        WindowGoneError, ShortcutError, SecurityError, MicrophoneError,
    )
}


def scrub_secrets(text: str, secrets: list[str | None]) -> str:
    """Remove any occurrence of known secrets from ``text``."""
    if not text:
        return text
    for secret in secrets:
        if secret and len(secret) >= 4 and secret in text:
            text = text.replace(secret, "***")
    return text


def friendly_error(err: Exception, secrets: list[str | None] | None = None) -> str:
    """Return a safe, user-facing message for any exception."""
    if isinstance(err, QuickPromptError):
        msg = err.message
    else:
        msg = "An unexpected error occurred. Please try again."
    return scrub_secrets(msg, secrets or [])
