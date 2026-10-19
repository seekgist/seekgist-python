from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from fakes import Fake, ok, problem

from seekgist import AsyncSuggester, SeekGistError, WebhookVerificationError, sign_webhook, verify_webhook

# Test data: tests/testdata/ in the public SDK repository, packages/sdk-codegen/testdata/ here.
HERE = Path(__file__).parent
TESTDATA = HERE / "testdata" if (HERE / "testdata").is_dir() else HERE.parents[1] / "sdk-codegen" / "testdata"
VECTORS = json.loads((TESTDATA / "webhooks.json").read_text(encoding="utf-8"))


def product(i: int) -> dict[str, Any]:
    return {"id": str(i), "name": {"en": f"Item {i}"}, "price": "1.000", "currency": "JOD"}


def counts(up: int, deleted: int = 0, errors: list[dict[str, Any]] | None = None) -> Any:
    return ok({"upserted": up, "unchanged": 0, "deleted": deleted, **({"errors": errors} if errors else {})})


# --- upsert_products -------------------------------------------------------------------------


def test_upsert_batches_with_one_stable_key_per_batch() -> None:
    fake = Fake(
        counts(2),
        problem(503, "service_unavailable"),
        counts(2),
        counts(1, 1, [{"index": 0, "id": "5", "code": "invalid", "message": "bad price"}]),
        counts(0, 1),
    )
    seen: list[int] = []
    s = fake.client().upsert_products(
        [product(i) for i in range(1, 6)],
        batch_size=2,
        delete_ids=["x", "y"],
        key_prefix="run-42",
        on_batch=lambda b: seen.append(b.count),
    )
    assert [r.headers["idempotency-key"] for r in fake.sent] == [
        "run-42-0",
        "run-42-1",
        "run-42-1",
        "run-42-2",
        "run-42-3",
    ]
    assert fake.body(3) == {"upsert": [product(5)], "delete": ["x"]}
    assert fake.body(4) == {"delete": ["y"]}
    assert seen == [2, 2, 2, 1]
    assert (s.batches, s.upserted, s.deleted, s.key_prefix, s.sync_id) == (4, 5, 2, "run-42", None)
    assert s.errors == [{"index": 0, "id": "5", "code": "invalid", "message": "bad price", "batch": 2}]


def test_full_sync_marks_only_the_last_batch() -> None:
    fake = Fake(counts(2), counts(1))
    s = fake.client().upsert_products([product(i) for i in range(3)], batch_size=2, replace_all=True, sync_id="sync-1")
    assert fake.body(0) == {"upsert": [product(0), product(1)], "sync_id": "sync-1"}
    assert fake.body(1) == {"upsert": [product(2)], "sync_id": "sync-1", "sync_complete": True, "replace_all": True}
    assert s.sync_id == "sync-1"


def test_empty_runs() -> None:
    fake = Fake(counts(0, 9))
    assert fake.client().upsert_products([], replace_all=True).deleted == 9
    assert fake.body(0)["sync_complete"] is True
    assert Fake().client().upsert_products([]).batches == 0


def test_batch_size_limit() -> None:
    with pytest.raises(SeekGistError, match="batch_size"):
        Fake().client().upsert_products([product(1)], batch_size=1001)


async def test_async_upsert_and_find() -> None:
    fake = Fake(
        counts(1),
        ok(
            {"search_id": "s", "ids": ["1"], "hits": [], "total": 1},
            x_seekgist_usage='searches=3, limit=-1, billed=1, period="2026-10"',
        ),
    )
    async with fake.aclient() as c:
        s = await c.upsert_products([product(1)], key_prefix="k-1")
        assert s.upserted == 1
        res = await c.find({"q": "lamp", "lang": "en"}, per_page=3)
        assert res.usage is not None and res.usage.limit == -1
    assert fake.body(1) == {"q": "lamp", "lang": "en", "per_page": 3}


# --- jobs and pages --------------------------------------------------------------------------


def job(status: str) -> Any:
    return ok({"id": "j1", "kind": "reindex", "status": status, "created_at": "2026-10-13T00:00:00Z"})


def test_wait_for_job_and_pages() -> None:
    fake = Fake(job("queued"), job("running"), job("succeeded"))
    c = fake.client()
    assert c.wait_for_job("j1", interval=0.01)["status"] == "succeeded"
    assert len(fake.sent) == 3
    fake2 = Fake(
        ok({"data": [{"id": "1"}, {"id": "2"}], "next_cursor": "n2"}), ok({"data": [{"id": "3"}], "next_cursor": None})
    )
    c2 = fake2.client()
    assert [s["id"] for s in c2.pages(lambda cur: c2.synonyms.list(cursor=cur))] == ["1", "2", "3"]
    assert fake2.sent[1].url.params["cursor"] == "n2"


async def test_async_wait_for_job_and_pages() -> None:
    fake = Fake(job("running"), job("failed"), ok({"data": [{"id": "a"}], "next_cursor": None}))
    async with fake.aclient() as c:
        assert (await c.wait_for_job("j1", interval=0.01))["status"] == "failed"
        assert [x["id"] async for x in c.pages(lambda cur: c.synonyms.list(cursor=cur))] == ["a"]


# --- suggester -------------------------------------------------------------------------------


async def test_suggester_debounces_and_drops_stale_answers() -> None:
    calls: list[str] = []

    async def suggest(**kw: Any) -> Any:
        calls.append(kw["q"])
        await asyncio.sleep(0.05)
        return {"query": kw["q"], "suggestions": []}

    s = AsyncSuggester(suggest, debounce=0.02)
    a = asyncio.ensure_future(s.input("l"))
    b = asyncio.ensure_future(s.input("la"))
    await asyncio.sleep(0)
    c = asyncio.ensure_future(s.input("lap "))
    assert await a is None
    assert await b is None
    assert (await c) == {"query": "lap", "suggestions": []}
    assert calls == ["lap"]
    first = asyncio.ensure_future(s.input("sam"))
    await asyncio.sleep(0.03)  # the call for "sam" is running now
    second = asyncio.ensure_future(s.input("samsung"))
    assert await first is None
    assert (await second)["query"] == "samsung"
    assert await s.input("lap") == {"query": "lap", "suggestions": []}  # from the cache
    assert calls == ["lap", "sam", "samsung"]
    assert await s.input(" ") is None


async def test_suggester_from_client_uses_short_timeout_and_no_retries() -> None:
    fake = Fake(problem(503, "service_unavailable"))
    async with fake.aclient() as c:
        s = c.suggester(debounce=0, lang="ar", products=3)
        with pytest.raises(Exception, match="503"):
            await s.input("آيفون")
        assert fake.sent[0].url.params["lang"] == "ar"
        assert fake.sent[0].url.params["products"] == "3"
        assert len(fake.sent) == 1
        await s.aclose()


# --- webhooks --------------------------------------------------------------------------------


def test_signature_matches_the_server_signer() -> None:
    v = VECTORS
    assert sign_webhook(v["secret"], v["headers"]["webhook-id"], v["timestamp"], v["body"]) == v["signature"]


def test_verify_accepts_what_the_server_signer_sends() -> None:
    # The server signer is not part of the public SDK repository: there this test is skipped.
    reference = pytest.importorskip("seekgist_providers.webhooks")
    secret = reference.new_secret()
    body = b'{"id":"evt_1","type":"search.zero_results_spike","data":{}}'
    hdrs = reference.headers([secret], "evt_1", body)
    assert verify_webhook(secret, hdrs, body)["type"] == "search.zero_results_spike"


def test_verify_good_rotation_and_bad_messages() -> None:
    v = VECTORS
    ev = verify_webhook(v["secret"], v["headers"], v["body"].encode(), now=v["timestamp"] + 10)
    assert ev["data"]["note"] == "تم الاستيراد"
    verify_webhook(v["other_secret"], v["rotation_headers"], v["body"], now=v["timestamp"])
    upper = {k.title(): val for k, val in v["headers"].items()}
    verify_webhook(v["secret"], upper, v["body"], now=v["timestamp"])
    with pytest.raises(WebhookVerificationError, match="no matching"):
        verify_webhook(v["secret"], v["headers"], v["body"].replace("succeeded", "failed"), now=v["timestamp"])
    with pytest.raises(WebhookVerificationError, match="no matching"):
        verify_webhook(v["other_secret"], v["headers"], v["body"], now=v["timestamp"])
    with pytest.raises(WebhookVerificationError, match="too old"):
        verify_webhook(v["secret"], v["headers"], v["body"], now=v["timestamp"] + 301)
    with pytest.raises(WebhookVerificationError, match="missing"):
        verify_webhook(v["secret"], {}, v["body"])
    with pytest.raises(WebhookVerificationError, match="base64"):
        verify_webhook("whsec_***", v["headers"], v["body"], now=v["timestamp"])
