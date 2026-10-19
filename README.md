# seekgist: SeekGist for Python

The official Python client for the [SeekGist](https://seekgist.com) API: Arabic-first e-commerce
search, suggest, catalog sync and webhooks. Python 3.12+, sync (`SeekGist`) and asyncio
(`AsyncSeekGist`) clients on httpx, fully typed (`py.typed`, TypedDicts for every request and
response).

```bash
pip install seekgist
```

Documentation: https://seekgist.com/docs. Support: support@seekgist.com.

## Quick start

```python
from seekgist import SeekGist

with SeekGist(api_key="sg_sec_...") as client:  # or set SEEKGIST_API_KEY
    products = [{"id": "42", "name": {"en": "Desk lamp", "ar": "مصباح مكتب"}, "price": "12.500", "currency": "JOD"}]
    summary = client.upsert_products(products)  # batches, retries, one Idempotency-Key per batch
    print(summary.upserted, summary.errors)

    res = client.find("مصباح", lang="ar", per_page=12)
    print(res.data["ids"])  # product ids in rank order; res.data["hits"] has the product cards
    print(res.usage, res.request_id)  # searches used this period; quote the request id to support
```

```python
from seekgist import AsyncSeekGist

async with AsyncSeekGist() as client:
    res = await client.find("white sneakers", lang="en")
```

Keys: a **public** key (`sg_pub_...`) can search, suggest, recommend and send events.
A **secret** key (`sg_sec_...`) can do everything an API key may; keep it on your server.
Options: `base_url` (default `https://api.seekgist.com`, or `SEEKGIST_BASE_URL`), `timeout`
(10 s per attempt), `max_retries` (2), `headers`, `http_client`.

## All API methods

One namespace per API area, one method per operation, keyword arguments only:
`client.search`, `events`, `catalog`, `imports`, `connectors`, `jobs`, `synonyms`, `rules`,
`experiments`, `translation`, `analytics`, `webhooks`, `account`, `billing`, `health`, `files`.

Catalog translation works: `translation.backfill`, `status`, `review_*`, `glossary_*`, `export`,
`approved` and `ack`. Not available on this platform: the chat methods. They answer
`404 feature_unavailable`.

```python
client.synonyms.create(body={"kind": "equivalent", "terms": ["sneakers", "trainers", "سنيكرز"]})
client.catalog.products_get(product_id="42")
client.analytics.stats(report="top_queries", from_="2026-10-01")  # `from` is a keyword: from_
client.search.search_get(q="sneakers", options={"query": {"filter[brand]": ["Nike", "Adidas"]}})
r = client.request("search_post", body={"q": "dress"})  # any operation, with headers
```

Every method takes `options={"timeout", "max_retries", "idempotency_key", "headers", "query"}`.
Dashboard sign-in, admin and store-app callback endpoints are not in the SDK on purpose.

## Helpers

| Helper | What it does |
|---|---|
| `client.find(q, **fields)` | POST /v1/search; returns `ApiResponse` with `.usage` |
| `client.upsert_products(products, batch_size=500, delete_ids=..., replace_all=False, key_prefix=None, on_batch=None)` | Batches of up to 1,000 with one `Idempotency-Key` per batch (`<key_prefix>-<n>`) and 5 retries; the same `key_prefix` again = safe resume; `replace_all=True` = full sync. Returns `UpsertSummary` |
| `client.wait_for_job(job_id)` | Polls `GET /v1/jobs/{id}` until done |
| `client.pages(lambda c: client.synonyms.list(cursor=c))` | Every item of a cursor list |
| `AsyncSeekGist.suggester(debounce=0.12, lang=..., products=...)` | `await s.input(text)`: debounced, drops out-of-date answers, caches; `None` for short or stale text. Suggest is never billed |
| `verify_webhook(secret, headers, raw_body)` | Standard Webhooks signature + 5-minute time check; returns the event |
| `parse_usage(header)` | Reads `X-SeekGist-Usage` |

Debounce matters where people type: in a browser use the JS SDK (`@seekgist/sdk`) or the
widget. In Python the suggester is for asyncio code such as a chat bot or a server-side proxy.

## Errors, retries and idempotency

- `ApiError` (`status`, `code`, `request_id`, `field_errors`, `retry_after_s`): use `code` in
  your program. `TimeoutError`, `ConnectionError`: no answer. `WebhookVerificationError`: bad webhook.
  All are `SeekGistError`.
- Retries (default 2, backoff 0.5 s, 1 s, 2 s ... with jitter; `Retry-After` wins): GET calls,
  search, and writes that take an `Idempotency-Key` (the SDK adds one and keeps it across
  retries). Other writes (click, event) are never retried after a network error
  or a 5xx; a 429 is retried for every call.
- Store search pages: `SeekGist(timeout=0.3, max_retries=0)` and fall back to the store's own
  search on any `SeekGistError`.

## Examples

`examples/search.py`, `examples/sync_catalog.py`, `examples/webhook_receiver.py` (standard
library server), `examples/async_suggest.py`.

## Development

From a clone of https://github.com/seekgist/seekgist-python:

```bash
pip install -e . pytest pytest-asyncio
pytest
```

Bugs and questions: https://github.com/seekgist/seekgist-python/issues or support@seekgist.com.

## License

MIT. See `LICENSE`.
