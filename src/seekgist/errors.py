"""Errors the SDK raises. Catch `SeekGistError` for all of them."""

from __future__ import annotations

from typing import Any


class SeekGistError(Exception):
    """Base class of every SDK error."""


class ApiError(SeekGistError):
    """The API answered with an error status (RFC 9457 problem). Use `code` in your program."""

    def __init__(
        self, status: int, problem: dict[str, Any] | None, request_id: str | None, retry_after_s: float | None
    ) -> None:
        self.status = status
        self.problem = problem
        self.code: str = str((problem or {}).get("code") or f"http_{status}")
        self.request_id = request_id or (problem or {}).get("request_id")
        self.retry_after_s = retry_after_s
        detail = (problem or {}).get("detail") or (problem or {}).get("title") or "request failed"
        suffix = f" (request {self.request_id})" if self.request_id else ""
        super().__init__(f"{status} {self.code}: {detail}{suffix}")

    @property
    def field_errors(self) -> list[dict[str, Any]]:
        """Field errors of a 422 answer (empty when there are none)."""
        return list((self.problem or {}).get("errors") or [])


class ConnectionError(SeekGistError):  # same name as the JS and PHP SDKs (shadows the builtin)
    """The request never got an answer (DNS, refused connection, reset)."""


class TimeoutError(SeekGistError):  # same name as the JS and PHP SDKs (shadows the builtin)
    """One attempt took longer than the timeout."""


class WebhookVerificationError(SeekGistError):
    """A webhook failed the signature or time check. Answer it with HTTP 400."""
