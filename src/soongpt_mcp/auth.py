"""Session storage backed by OS keyring with env var fallback.

Stores the rusaint session JSON (the only thing needed to reuse an
authenticated uSaint session). Never stores student_id or password.
"""
from __future__ import annotations

import keyring

from .config import get_config


class AuthError(RuntimeError):
    """Raised when keyring operations fail in a way the caller should surface."""


def save_session(json_str: str) -> None:
    if not isinstance(json_str, str) or not json_str:
        raise AuthError("save_session received empty or non-string payload")
    cfg = get_config()
    try:
        keyring.set_password(
            cfg.keyring_service_name,
            cfg.keyring_session_key,
            json_str,
        )
    except AuthError:
        raise
    except Exception as exc:
        raise AuthError(
            "Failed to save session to OS keyring "
            f"(service={cfg.keyring_service_name!r}, "
            f"key={cfg.keyring_session_key!r}): {exc}. "
            "Check your OS keyring backend (macOS Keychain / Secret Service / "
            "Windows Credential Manager)."
        ) from exc


def load_session() -> str | None:
    cfg = get_config()
    try:
        value = keyring.get_password(
            cfg.keyring_service_name,
            cfg.keyring_session_key,
        )
    except Exception as exc:
        raise AuthError(
            "Failed to read session from OS keyring "
            f"(service={cfg.keyring_service_name!r}, "
            f"key={cfg.keyring_session_key!r}): {exc}."
        ) from exc
    if value:
        return value
    return cfg.env_session_json


def clear_session() -> None:
    cfg = get_config()
    try:
        keyring.delete_password(
            cfg.keyring_service_name,
            cfg.keyring_session_key,
        )
    except keyring.errors.PasswordDeleteError:
        return
    except Exception as exc:
        raise AuthError(
            "Failed to clear session from OS keyring "
            f"(service={cfg.keyring_service_name!r}, "
            f"key={cfg.keyring_session_key!r}): {exc}."
        ) from exc
