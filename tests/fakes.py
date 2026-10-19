"""httpx MockTransport helpers for the SDK unit tests (no network, no API)."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx

from seekgist import AsyncSeekGist, SeekGist

PUB = "sg_pub_" + "a" * 44
SEC = "sg_sec_" + "b" * 44
Reply = httpx.Response | Exception | Callable[[httpx.Request], httpx.Response]


def ok(body: Any, status: int = 200, **headers: str) -> httpx.Response:
    h = {"x-request-id": "req_test", **{k.replace("_", "-"): v for k, v in headers.items()}}
    return httpx.Response(status, json=body, headers=h)


def problem(status: int, code: str, **headers: str) -> httpx.Response:
    body = {"type": "x", "title": code, "status": status, "code": code, "request_id": "req_test"}
    h = {"Content-Type": "application/problem+json", **{k.replace("_", "-"): v for k, v in headers.items()}}
    return httpx.Response(status, content=json.dumps(body).encode(), headers=h)


class Fake:
    def __init__(self, *replies: Reply) -> None:
        self.replies = list(replies)
        self.sent: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        request.read()
        self.sent.append(request)
        if not self.replies:
            raise AssertionError(f"no reply queued for {request.method} {request.url}")
        nxt = self.replies.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt(request) if callable(nxt) else nxt

    def body(self, i: int) -> Any:
        return json.loads(self.sent[i].content)

    def client(self, **kw: Any) -> SeekGist:
        c = SeekGist(
            kw.pop("api_key", SEC),
            base_url="http://api.test",
            http_client=httpx.Client(transport=httpx.MockTransport(self)),
            **kw,
        )
        c._cfg.sleep = lambda s: None
        c._cfg.random = lambda: 0.5
        return c

    def aclient(self, **kw: Any) -> AsyncSeekGist:
        async def handler(request: httpx.Request) -> httpx.Response:
            return self(request)

        c = AsyncSeekGist(
            kw.pop("api_key", SEC),
            base_url="http://api.test",
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
            **kw,
        )

        async def no_sleep(_: float) -> None:
            return None

        c._async_sleep = no_sleep
        c._cfg.random = lambda: 0.5
        return c
