"""Debounced suggestions for asyncio code: only the last text in a burst is sent, answers
that are out of date are dropped, and recent answers are cached. Suggest is never billed."""

from __future__ import annotations

import asyncio
import contextlib
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ._generated.types import SuggestResponse

SuggestFn = Callable[..., Awaitable["SuggestResponse"]]


class AsyncSuggester:
    """Call `await s.input(text)` on every key press: you get the answer for that text, or
    None when the text is too short or a newer `input()` came first."""

    def __init__(
        self,
        suggest: SuggestFn,
        *,
        debounce: float = 0.12,
        min_chars: int = 1,
        lang: str | None = None,
        limit: int | None = None,
        products: int | None = None,
        session: str | None = None,
        timeout: float = 1.5,
        cache_size: int = 50,
    ) -> None:
        self._suggest = suggest
        self._debounce = debounce
        self._min_chars = min_chars
        self._params: dict[str, Any] = {"lang": lang, "limit": limit, "products": products, "session": session}
        self._timeout = timeout
        self._cache_size = cache_size
        self._cache: OrderedDict[str, SuggestResponse] = OrderedDict()
        self._task: asyncio.Task[SuggestResponse | None] | None = None
        self._seq = 0

    async def input(self, text: str) -> SuggestResponse | None:
        self._seq += 1
        seq = self._seq
        self.cancel(keep_seq=True)
        q = text.strip()
        if len(q) < self._min_chars:
            return None
        key = f"{self._params['lang'] or ''}\0{q.lower()}"
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        task = asyncio.ensure_future(self._run(q))
        self._task = task
        try:
            res = await task
        except asyncio.CancelledError:
            if seq != self._seq:
                return None
            raise
        if res is None or seq != self._seq:
            return None
        self._remember(key, res)
        return res

    def cancel(self, *, keep_seq: bool = False) -> None:
        """Stop the waiting or running call (its `input()` returns None)."""
        if not keep_seq:
            self._seq += 1
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None

    async def _run(self, q: str) -> SuggestResponse | None:
        await asyncio.sleep(self._debounce)
        params = {k: v for k, v in self._params.items() if v is not None}
        return await self._suggest(q=q, **params, options={"timeout": self._timeout, "max_retries": 0})

    def _remember(self, key: str, res: SuggestResponse) -> None:
        if self._cache_size <= 0:
            return
        self._cache[key] = res
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)

    async def aclose(self) -> None:
        task = self._task
        self.cancel()
        if task:
            # The caller of input() already got this task's result or error.
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
