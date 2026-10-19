"""Request building, timeouts and safe retries (sync and async share every rule here)."""

from __future__ import annotations

import json
import random
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote

import httpx

from ._spec import ApiResponse, OperationSpec, RateLimit, RequestOptions, Usage
from .errors import ApiError, ConnectionError, SeekGistError, TimeoutError

RETRY_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})
READ_ONLY_POSTS = frozenset({"search_post"})  # POSTs that only read: a retry cannot do anything twice
MAX_BACKOFF_S = 8.0
MAX_RETRY_AFTER_S = 60.0


def new_idempotency_key(prefix: str = "sg") -> str:
    """A fresh Idempotency-Key (8-255 characters, per docs/api.md)."""
    return f"{prefix}-{uuid.uuid4()}"


@dataclass
class Config:
    base_url: str
    api_key: str | None
    timeout: float
    max_retries: int
    headers: dict[str, str]
    sleep: Callable[[float], Any] = time.sleep
    random: Callable[[], float] = random.random
    waits: list[float] = field(default_factory=list)  # last call's waits (tests read it)


@dataclass
class Built:
    method: str
    url: str
    headers: dict[str, str]
    params: list[tuple[str, str]]
    content: bytes | None
    files: list[tuple[str, Any]] | None
    data: dict[str, str] | None
    retryable: bool
    timeout: float
    max_retries: int


def _q(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list | tuple):
        return ",".join(_q(v) or "" for v in value) if value else None
    return str(value)


def _multipart(body: Mapping[str, Any]) -> tuple[list[tuple[str, Any]], dict[str, str]]:
    files: list[tuple[str, Any]] = []
    data: dict[str, str] = {}
    for name, value in body.items():
        if value is None:
            continue
        if isinstance(value, bytes):
            files.append((name, ("upload", value)))
        elif isinstance(value, tuple) or hasattr(value, "read"):
            files.append((name, value))
        elif isinstance(value, dict | list):
            data[name] = json.dumps(value, ensure_ascii=False)
        else:
            data[name] = _q(value) or ""
    return files, data


def build(
    cfg: Config, spec: OperationSpec, params: Mapping[str, Any], body: Any, options: RequestOptions | None
) -> Built:
    opts: RequestOptions = options or {}
    path = spec.path
    for name in spec.path_params:
        value = params.get(name)
        if value is None or value == "":
            raise SeekGistError(f'{spec.id}: missing path parameter "{name}"')
        path = path.replace("{" + name + "}", quote(str(value), safe=""))
    query: list[tuple[str, str]] = []
    for name in spec.query:
        v = _q(params.get(name))
        if v is not None:
            query.append((name, v))
    for name, raw in (opts.get("query") or {}).items():
        v = _q(raw)
        if v is not None:
            query.append((name, v))
    headers = {"Accept": "application/json" if spec.result == "json" else "*/*", **cfg.headers}
    if cfg.api_key and spec.auth:
        headers["Authorization"] = f"Bearer {cfg.api_key}"
    for name in spec.headers:
        if params.get(name) is not None:
            headers[name] = str(params[name])
    content: bytes | None = None
    files = data = None
    if spec.body == "json" and body is not None:
        headers["Content-Type"] = "application/json"
        content = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
    elif spec.body == "multipart" and body is not None:
        files, data = _multipart(body)
    retryable = spec.method == "GET" or spec.id in READ_ONLY_POSTS
    if spec.idempotent and spec.method != "GET":
        headers["Idempotency-Key"] = opts.get("idempotency_key") or new_idempotency_key()
        retryable = True
    headers.update(opts.get("headers") or {})
    return Built(
        method=spec.method,
        url=cfg.base_url.rstrip("/") + path,
        headers=headers,
        params=query,
        content=content,
        files=files,
        data=data,
        retryable=retryable,
        timeout=float(opts.get("timeout", cfg.timeout)),
        max_retries=int(opts.get("max_retries", cfg.max_retries)),
    )


def request_kwargs(b: Built) -> dict[str, Any]:
    kw: dict[str, Any] = {"headers": b.headers, "params": b.params, "timeout": b.timeout}
    if b.content is not None:
        kw["content"] = b.content
    if b.files is not None:
        kw["files"] = b.files or None
        kw["data"] = b.data
    return kw


def backoff_s(attempt: int, retry_after_s: float | None, rnd: Callable[[], float]) -> float:
    """0.5 s, 1 s, 2 s ... up to 8 s with jitter; Retry-After wins (capped at 60 s)."""
    if retry_after_s is not None:
        return min(retry_after_s, MAX_RETRY_AFTER_S)
    base = min(MAX_BACKOFF_S, 0.5 * 2.0 ** (attempt - 1))
    return round(base * (0.75 + rnd() * 0.5), 3)


def parse_usage(header: str | None) -> Usage | None:
    """Parse `X-SeekGist-Usage: searches=1234, limit=150000, billed=1, period="2026-10"`."""
    if not header:
        return None
    values: dict[str, str] = {}
    for part in header.split(","):
        key, sep, value = part.partition("=")
        if not sep:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] == '"':
            value = value[1:-1]
        values[key.strip().lower()] = value
    searches = _int(values.get("searches"))
    if searches is None:
        return None
    limit, billed = _int(values.get("limit")), _int(values.get("billed"))
    return Usage(searches, -1 if limit is None else limit, billed or 0, values.get("period", ""))


def parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value.strip()))
    except ValueError:
        pass
    try:
        return max(0.0, parsedate_to_datetime(value).timestamp() - time.time())
    except (TypeError, ValueError):
        return None


def parse_rate_limit(headers: httpx.Headers) -> RateLimit:
    return RateLimit(
        limit=_int(headers.get("ratelimit-limit")),
        remaining=_int(headers.get("ratelimit-remaining")),
        reset=_int(headers.get("ratelimit-reset")),
        retry_after_s=parse_retry_after(headers.get("retry-after")),
    )


def _int(value: str | None) -> int | None:
    try:
        return int(value.strip()) if value is not None and value.strip() else None
    except ValueError:
        return None


def _problem(res: httpx.Response) -> dict[str, Any] | None:
    try:
        parsed = res.json()
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) and isinstance(parsed.get("code"), str) else None


def outcome(
    spec: OperationSpec, b: Built, res: httpx.Response, attempt: int
) -> tuple[ApiResponse[Any] | None, ApiError | None, bool]:
    """(response, error, retry?) for one answer."""
    rid = res.headers.get("x-request-id")
    if res.is_success:
        data: Any
        if res.status_code == 204 or not res.content:
            data = None
        elif spec.result == "text":
            data = res.text
        elif spec.result == "binary":
            data = res.content
        else:
            try:
                data = res.json()
            except ValueError as e:
                raise SeekGistError(
                    f"{spec.id}: the API sent a body that is not JSON (status {res.status_code})"
                ) from e
        resp = ApiResponse(
            data=data,
            status=res.status_code,
            headers=res.headers,
            request_id=rid,
            usage=parse_usage(res.headers.get("x-seekgist-usage")),
            rate_limit=parse_rate_limit(res.headers),
            replayed=res.headers.get("idempotent-replayed") == "true",
            attempts=attempt,
        )
        return resp, None, False
    err = ApiError(res.status_code, _problem(res), rid, parse_retry_after(res.headers.get("retry-after")))
    retry = res.status_code == 429 or (
        b.retryable
        and res.status_code in RETRY_STATUS
        and (res.status_code != 409 or err.code == "idempotency_in_progress")
    )
    return None, err, retry and attempt <= b.max_retries


def network_error(err: httpx.HTTPError, b: Built) -> SeekGistError:
    if isinstance(err, httpx.TimeoutException):
        return TimeoutError(f"no answer within {b.timeout} s")
    return ConnectionError(f"could not reach the SeekGist API: {err}")
