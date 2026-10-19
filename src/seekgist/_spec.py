"""Shapes shared by the generated code (seekgist._generated) and the hand-written client.

The generator imports these names; keep them stable.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import IO, Any, NamedTuple, Protocol, TypedDict

import httpx

# A file for multipart uploads: bytes, an open binary file, or (filename, content[, content type]).
FileInput = bytes | IO[bytes] | tuple[str, bytes | IO[bytes]] | tuple[str, bytes | IO[bytes], str]


class OperationSpec(NamedTuple):
    """One API operation, as written by the generator from docs/api/openapi.yaml."""

    id: str
    method: str
    path: str
    path_params: tuple[str, ...]
    query: tuple[str, ...]
    headers: tuple[str, ...]
    body: str  # json | multipart | none
    result: str  # json | text | binary
    idempotent: bool  # accepts an Idempotency-Key header
    auth: bool  # needs an API key


class RequestOptions(TypedDict, total=False):
    """Per-call options. Every generated method takes them as `options=`."""

    timeout: float
    """Time limit for one attempt, in seconds (default: the client's timeout)."""
    max_retries: int
    """Retries after the first attempt (default: the client's max_retries)."""
    idempotency_key: str
    """Idempotency-Key for writes. The SDK makes one when the operation accepts it and you give none."""
    headers: Mapping[str, str]
    """Extra headers for this call."""
    query: Mapping[str, Any]
    """Extra query parameters, e.g. {"filter[brand]": "Nike"} on GET /v1/search."""


@dataclass(frozen=True)
class Usage:
    """Parsed `X-SeekGist-Usage` header (search responses). `limit = -1` means no fixed limit."""

    searches: int
    limit: int
    billed: int
    period: str


@dataclass(frozen=True)
class RateLimit:
    """Parsed `RateLimit-*` and `Retry-After` headers. None when the header was not sent."""

    limit: int | None = None
    remaining: int | None = None
    reset: int | None = None
    retry_after_s: float | None = None


@dataclass
class ApiResponse[T]:
    """The full answer of one call: body plus the headers that matter."""

    data: T
    status: int
    headers: httpx.Headers
    request_id: str | None = None
    usage: Usage | None = None
    rate_limit: RateLimit = field(default_factory=RateLimit)
    replayed: bool = False
    """True when the server replayed a stored answer for the same Idempotency-Key."""
    attempts: int = 1


class SyncCaller(Protocol):
    def call(
        self, spec: OperationSpec, params: dict[str, Any], body: Any, options: RequestOptions | None = None
    ) -> ApiResponse[Any]: ...


class AsyncCaller(Protocol):
    async def acall(
        self, spec: OperationSpec, params: dict[str, Any], body: Any, options: RequestOptions | None = None
    ) -> ApiResponse[Any]: ...
