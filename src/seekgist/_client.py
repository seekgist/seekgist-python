"""The sync client: generated namespaces (client.search, client.catalog, ...) plus helpers."""

from __future__ import annotations

import os
import re
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from typing import TYPE_CHECKING, Any

import httpx

from . import _transport as tr
from ._generated.api import OPS, GeneratedNamespaces
from ._spec import ApiResponse, OperationSpec, RequestOptions
from ._upsert import BatchInfo, UpsertSummary, plan, run_sync
from .errors import SeekGistError

if TYPE_CHECKING:
    from ._generated.types import Job, ProductIn, SearchRequest, SearchResponse

__version__ = "1.0.0"
DEFAULT_BASE_URL = "https://api.seekgist.com"
KEY_RE = re.compile(r"^sg_(pub|sec)_[A-Za-z0-9]{20,}$")
DONE = frozenset({"succeeded", "failed", "canceled"})


def make_config(
    api_key: str | None, base_url: str | None, timeout: float, max_retries: int, headers: Mapping[str, str] | None
) -> tr.Config:
    key = api_key or os.environ.get("SEEKGIST_API_KEY") or None
    if key and not KEY_RE.match(key):
        raise SeekGistError("api_key must look like sg_pub_... or sg_sec_...")
    return tr.Config(
        base_url=base_url or os.environ.get("SEEKGIST_BASE_URL") or DEFAULT_BASE_URL,
        api_key=key,
        timeout=timeout,
        max_retries=max_retries,
        headers={"User-Agent": f"seekgist-python/{__version__}", **(headers or {})},
    )


def find_body(query: str | SearchRequest, fields: Mapping[str, Any]) -> dict[str, Any]:
    base: dict[str, Any] = {"q": query} if isinstance(query, str) else dict(query)
    return {**base, **{k: v for k, v in fields.items() if v is not None}}


class SeekGist(GeneratedNamespaces):
    """SeekGist API client (sync). Use `AsyncSeekGist` in async code.

    >>> client = SeekGist(api_key="sg_sec_...")   # or set SEEKGIST_API_KEY
    >>> client.find("فستان سهرة").data["hits"]
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str | None = None,
        timeout: float = 10.0,
        max_retries: int = 2,
        headers: Mapping[str, str] | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._cfg = make_config(api_key, base_url, timeout, max_retries, headers)
        self._own_http = http_client is None
        self._http = http_client or httpx.Client()
        self._init_namespaces(self)

    # --- plumbing -----------------------------------------------------------------------------

    def call(
        self, spec: OperationSpec, params: dict[str, Any], body: Any, options: RequestOptions | None = None
    ) -> ApiResponse[Any]:
        """Send one operation with timeouts and safe retries (used by the generated namespaces)."""
        b = tr.build(self._cfg, spec, params, body, options)
        self._cfg.waits = []
        attempt = 0
        while True:
            attempt += 1
            try:
                res = self._http.request(b.method, b.url, **tr.request_kwargs(b))
            except httpx.HTTPError as e:
                err = tr.network_error(e, b)
                if not b.retryable or attempt > b.max_retries:
                    raise err from e
                self._wait(tr.backoff_s(attempt, None, self._cfg.random))
                continue
            resp, api_err, retry = tr.outcome(spec, b, res, attempt)
            if resp is not None:
                return resp
            assert api_err is not None
            if not retry:
                raise api_err
            self._wait(tr.backoff_s(attempt, api_err.retry_after_s, self._cfg.random))

    def _wait(self, seconds: float) -> None:
        self._cfg.waits.append(seconds)
        self._cfg.sleep(seconds)

    def request(
        self,
        operation_id: str,
        params: Mapping[str, Any] | None = None,
        body: Any = None,
        options: RequestOptions | None = None,
    ) -> ApiResponse[Any]:
        """Any operation by its OpenAPI operationId, with the full response (headers included)."""
        if operation_id not in OPS:
            raise SeekGistError(f"unknown operation {operation_id!r}")
        return self.call(OPS[operation_id], dict(params or {}), body, options)

    def close(self) -> None:
        if self._own_http:
            self._http.close()

    def __enter__(self) -> SeekGist:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- helpers ------------------------------------------------------------------------------

    def find(
        self, query: str | SearchRequest, *, options: RequestOptions | None = None, **fields: Any
    ) -> ApiResponse[SearchResponse]:
        """Search products (POST /v1/search). `query` is the shopper's text or a full SearchRequest;
        `fields` are SearchRequest fields (lang, page, per_page, filters, sort, facets, session).
        The answer has `.usage` (X-SeekGist-Usage): searches used this period and your limit."""
        return self.request("search_post", None, find_body(query, fields), options)

    def upsert_products(
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
        """Upsert (and delete) products in batches (max 1,000 items per call) with one
        Idempotency-Key per batch and retries. `replace_all=True` = full sync: products you did
        not send are deleted after the last batch. Needs the catalog:write scope."""
        p = plan(
            products,
            batch_size=batch_size,
            delete_ids=delete_ids,
            replace_all=replace_all,
            sync_id=sync_id,
            key_prefix=key_prefix,
        )

        def send(body: dict[str, Any], key: str) -> ApiResponse[Any]:
            opts: RequestOptions = {"idempotency_key": key, "max_retries": max_retries}
            if request_timeout is not None:
                opts["timeout"] = request_timeout
            return self.request("products_batch", None, body, opts)

        return run_sync(p, send, on_batch)

    def wait_for_job(self, job_id: str, *, interval: float = 1.0, max_wait: float = 600.0) -> Job:
        """Poll GET /v1/jobs/{id} until the job is succeeded, failed or canceled."""
        deadline = time.monotonic() + max_wait
        while True:
            job = self.jobs.get(job_id=job_id)
            if job["status"] in DONE:
                return job
            if time.monotonic() + interval > deadline:
                raise SeekGistError(f"job {job_id} is still {job['status']} after the time limit")
            self._wait(interval)

    def pages(self, fetch_page: Callable[[str | None], Mapping[str, Any]]) -> Iterator[Any]:
        """Walk every page of a cursor list:
        `for s in client.pages(lambda c: client.synonyms.list(cursor=c)): ...`"""
        cursor: str | None = None
        while True:
            page = fetch_page(cursor)
            yield from page.get("data") or []
            cursor = page.get("next_cursor")
            if not cursor:
                return
