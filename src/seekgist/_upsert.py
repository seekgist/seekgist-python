"""Batch planning for upsert_products (shared by the sync and async clients).

Every batch has its own Idempotency-Key `<key_prefix>-<n>`, so a retry after a network error
never applies a batch twice. Give the same `key_prefix` again to resume a run that stopped
half way: batches the server already did are answered from its 24 h store.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from ._spec import ApiResponse
from ._transport import new_idempotency_key
from .errors import SeekGistError

MAX_BATCH = 1000


@dataclass
class BatchInfo:
    index: int
    count: int
    result: dict[str, Any]
    replayed: bool
    idempotency_key: str


@dataclass
class UpsertSummary:
    batches: int = 0
    upserted: int = 0
    unchanged: int = 0
    deleted: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)
    """Products the API refused (each with `batch`, the 0-based batch number). The rest was saved."""
    key_prefix: str = ""
    sync_id: str | None = None

    def add(self, info: BatchInfo) -> None:
        r = info.result
        self.batches += 1
        self.upserted += int(r.get("upserted", 0))
        self.unchanged += int(r.get("unchanged", 0))
        self.deleted += int(r.get("deleted", 0))
        self.errors += [{**e, "batch": info.index} for e in r.get("errors") or []]


@dataclass
class Plan:
    summary: UpsertSummary
    batches: list[tuple[int, int, dict[str, Any], str]]  # (index, item count, body, idempotency key)


def plan(
    products: Sequence[Any],
    *,
    batch_size: int = 500,
    delete_ids: Sequence[str] = (),
    replace_all: bool = False,
    sync_id: str | None = None,
    key_prefix: str | None = None,
) -> Plan:
    if not 1 <= batch_size <= MAX_BATCH:
        raise SeekGistError(f"batch_size must be 1-{MAX_BATCH} (got {batch_size})")
    items: list[tuple[str, Any]] = [("upsert", p) for p in products] + [("delete", d) for d in delete_ids]
    prefix = key_prefix or new_idempotency_key("sg-upsert")
    sid = (sync_id or new_idempotency_key("sync")) if replace_all else None
    summary = UpsertSummary(key_prefix=prefix, sync_id=sid)
    if not items and not replace_all:
        return Plan(summary, [])
    total = max(1, -(-len(items) // batch_size))
    batches = []
    for index in range(total):
        part = items[index * batch_size : (index + 1) * batch_size]
        body: dict[str, Any] = {}
        ups = [v for k, v in part if k == "upsert"]
        dels = [v for k, v in part if k == "delete"]
        if ups:
            body["upsert"] = ups
        if dels:
            body["delete"] = dels
        if sid:
            body["sync_id"] = sid
            if index == total - 1:
                body["sync_complete"] = True
                body["replace_all"] = True
        batches.append((index, len(part), body, f"{prefix}-{index}"))
    return Plan(summary, batches)


def run_sync(
    p: Plan,
    send: Callable[[dict[str, Any], str], ApiResponse[Any]],
    on_batch: Callable[[BatchInfo], None] | None,
) -> UpsertSummary:
    for index, count, body, key in p.batches:
        res = send(body, key)
        info = BatchInfo(index, count, res.data or {}, res.replayed, key)
        p.summary.add(info)
        if on_batch:
            on_batch(info)
    return p.summary
