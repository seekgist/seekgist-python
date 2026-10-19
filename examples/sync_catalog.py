"""Push products with a SECRET key: batches of 500, one Idempotency-Key per batch, retries.
Run it again with the same KEY_PREFIX to resume a stopped run safely.

    SEEKGIST_API_KEY=sg_sec_... KEY_PREFIX=nightly-2026-10-13 python examples/sync_catalog.py
"""

from __future__ import annotations

import os

from seekgist import SeekGist

products = [
    {
        "id": f"demo-{i}",
        "name": {"en": f"Demo product {i}", "ar": f"منتج تجريبي {i}"},
        "price": f"{5 + i % 50:.3f}",
        "currency": "USD",
        "in_stock": i % 7 != 0,
    }
    for i in range(1200)
]

with SeekGist() as client:
    summary = client.upsert_products(
        products,
        batch_size=500,
        key_prefix=os.environ.get("KEY_PREFIX"),
        on_batch=lambda b: print(f"batch {b.index + 1}: {b.count} items{' (already done)' if b.replayed else ''}"),
    )
    print(summary)
