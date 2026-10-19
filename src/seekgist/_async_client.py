"""The async client: same API as SeekGist, with `await` (httpx.AsyncClient)."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

import httpx

from . import _transport as tr
from ._client import DONE, find_body, make_config
from ._generated.api import OPS, AsyncGeneratedNamespaces
from ._spec import ApiResponse, OperationSpec, RequestOptions
from ._upsert import BatchInfo, UpsertSummary, plan
from .errors import SeekGistError
from .suggest import AsyncSuggester

if TYPE_CHECKING:
    from ._generated.types import Job, ProductIn, SearchRequest, SearchResponse


class AsyncSeekGist(AsyncGeneratedNamespaces):
    """SeekGist API client for asyncio.

    >>> async with AsyncSeekGist(api_key="sg_sec_...") as client:
    ...     res = await client.find("white sneakers", lang="en")
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str | None = None,
        timeout: float = 10.0,
        max_retries: int = 2,
        headers: Mapping[str, str] | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._cfg = make_config(api_key, base_url, timeout, max_retries, headers)
        self._own_http = http_client is None
        self._http = http_client or httpx.AsyncClient()
        self._async_sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
        self._init_namespaces(self)

    async def acall(
        self, spec: OperationSpec, params: dict[str, Any], body: Any, options: RequestOptions | None = None
    ) -> ApiResponse[Any]:
        """Send one operation with timeouts and safe retries (used by the generated namespaces)."""
        b = tr.build(self._cfg, spec, params, body, options)
        self._cfg.waits = []
        attempt = 0
        while True:
            attempt += 1
            try:
                res = await self._http.request(b.method, b.url, **tr.request_kwargs(b))
            except httpx.HTTPError as e:
                err = tr.network_error(e, b)
                if not b.retryable or attempt > b.max_retries:
                    raise err from e
                await self._wait(tr.backoff_s(attempt, None, self._cfg.random))
                continue
            resp, api_err, retry = tr.outcome(spec, b, res, attempt)
            if resp is not None:
                return resp
            assert api_err is not None
            if not retry:
                raise api_err
            await self._wait(tr.backoff_s(attempt, api_err.retry_after_s, self._cfg.random))

    async def _wait(self, seconds: float) -> None:
        self._cfg.waits.append(seconds)
        await self._async_sleep(seconds)

    async def request(
        self,
        operation_id: str,
        params: Mapping[str, Any] | None = None,
        body: Any = None,
        options: RequestOptions | None = None,
    ) -> ApiResponse[Any]:
        """Any operation by its OpenAPI operationId, with the full response (headers included)."""
        if operation_id not in OPS:
            raise SeekGistError(f"unknown operation {operation_id!r}")
        return await self.acall(OPS[operation_id], dict(params or {}), body, options)

    async def aclose(self) -> None:
        if self._own_http:
            await self._http.aclose()

    async def __aenter__(self) -> AsyncSeekGist:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def find(
        self, query: str | SearchRequest, *, options: RequestOptions | None = None, **fields: Any
    ) -> ApiResponse[SearchResponse]:
        """Search products (POST /v1/search). See SeekGist.find."""
        return await self.request("search_post", None, find_body(query, fields), options)

    def suggester(
        self,
        *,
        debounce: float = 0.12,
        min_chars: int = 1,
        lang: str | None = None,
        limit: int | None = None,
        products: int | None = None,
        session: str | None = None,
        timeout: float = 1.5,
        cache_size: int = 50,
    ) -> AsyncSuggester:
        """A debounced suggest helper (for a chat bot, a terminal UI or a server-side proxy)."""
        return AsyncSuggester(
            self.search.suggest,
            debounce=debounce,
            min_chars=min_chars,
            lang=lang,
            limit=limit,
            products=products,
            session=session,
            timeout=timeout,
            cache_size=cache_size,
        )

    async def upsert_products(
        self,
        products: Sequence[ProductIn | Mapping[str, Any]],
        *,
        batch_size: int = 500,
        delete_ids: Sequence[str] = (),
        replace_all: bool = False,
        sync_id: str | None = None,
        key_prefix: str | None = None,
        max_retries: int = 5,
        request_timeout: float | None = None,
        on_batch: Callable[[BatchInfo], None] | None = None,
    ) -> UpsertSummary:
        """Upsert (and delete) products in batches. See SeekGist.upsert_products."""
        p = plan(
            products,
            batch_size=batch_size,
            delete_ids=delete_ids,
            replace_all=replace_all,
            sync_id=sync_id,
            key_prefix=key_prefix,
        )
        for index, count, body, key in p.batches:
            opts: RequestOptions = {"idempotency_key": key, "max_retries": max_retries}
            if request_timeout is not None:
                opts["timeout"] = request_timeout
            res = await self.request("products_batch", None, body, opts)
            info = BatchInfo(index, count, res.data or {}, res.replayed, key)
            p.summary.add(info)
            if on_batch:
                on_batch(info)
        return p.summary

    async def wait_for_job(self, job_id: str, *, interval: float = 1.0, max_wait: float = 600.0) -> Job:
        """Poll GET /v1/jobs/{id} until the job is succeeded, failed or canceled."""
        deadline = time.monotonic() + max_wait
        while True:
            job = await self.jobs.get(job_id=job_id)
            if job["status"] in DONE:
                return job
            if time.monotonic() + interval > deadline:
                raise SeekGistError(f"job {job_id} is still {job['status']} after the time limit")
            await self._wait(interval)

    async def pages(self, fetch_page: Callable[[str | None], Awaitable[Mapping[str, Any]]]) -> AsyncIterator[Any]:
        """Walk every page of a cursor list:
        `async for s in client.pages(lambda c: client.synonyms.list(cursor=c)): ...`"""
        cursor: str | None = None
        while True:
            page = await fetch_page(cursor)
            for item in page.get("data") or []:
                yield item
            cursor = page.get("next_cursor")
            if not cursor:
                return
