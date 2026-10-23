"""Shared state-aware logging helpers for recurring background jobs."""

from __future__ import annotations

import logging
import socket
import threading
from dataclasses import dataclass


@dataclass
class _FailureState:
    count: int = 1


_failures: dict[tuple[str, str], _FailureState] = {}
_lock = threading.Lock()


def error_summary(error: BaseException | str | None) -> str:
    """Return a short, stable error description suitable for the console."""
    if error is None:
        return "未知錯誤"
    if isinstance(error, socket.timeout):
        return "連線逾時"
    if isinstance(error, ConnectionRefusedError):
        return "服務未啟動或拒絕連線"
    if isinstance(error, ConnectionResetError):
        return "來源已中斷連線"
    if isinstance(error, BaseException):
        detail = str(error).strip()
        name = type(error).__name__
        return f"{name}: {detail[:160]}" if detail else name
    return str(error).strip()[:180] or "未知錯誤"


def log_failure(
    logger: logging.Logger,
    key: str,
    component: str,
    message: str,
    error: BaseException | str | None = None,
    *,
    level: int = logging.WARNING,
) -> bool:
    """Log only the first failure in a continuous failure period.

    Returns True when a console line was emitted and False when a duplicate was
    suppressed. Calls are thread-safe so this can also be used by RTL-TCP.
    """
    state_key = (logger.name, key)
    with _lock:
        state = _failures.get(state_key)
        if state is not None:
            state.count += 1
            return False
        _failures[state_key] = _FailureState()

    suffix = f"：{error_summary(error)}" if error is not None else ""
    logger.log(level, "⚠️ [%s] %s%s", component, message, suffix)
    return True


def log_recovery(
    logger: logging.Logger,
    key: str,
    component: str,
    message: str,
) -> bool:
    """Log recovery once and include the number of suppressed duplicate errors."""
    state_key = (logger.name, key)
    with _lock:
        state = _failures.pop(state_key, None)
    if state is None:
        return False

    suppressed = state.count - 1
    suffix = f" | 期間略過 {suppressed} 次重複錯誤" if suppressed else ""
    logger.info("✅ [%s] %s%s", component, message, suffix)
    return True


def is_failure_active(logger: logging.Logger, key: str) -> bool:
    with _lock:
        return (logger.name, key) in _failures
