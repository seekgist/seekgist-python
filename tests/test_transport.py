from __future__ import annotations

import httpx
import pytest
from fakes import PUB, SEC, Fake, ok, problem

from seekgist import ApiError, ConnectionError, SeekGist, SeekGistError, TimeoutError, parse_usage


def test_path_query_and_bearer_key() -> None:
    fake = Fake(ok({"data": [], "next_cursor": None}))
    fake.client().synonyms.list(cursor="c/1", limit=5)
    req = fake.sent[0]
    assert req.url.path == "/v1/synonyms"
    assert req.url.params["cursor"] == "c/1"
    assert req.url.params["limit"] == "5"
    assert req.headers["authorization"] == f"Bearer {SEC}"
    assert req.headers["user-agent"].startswith("seekgist-python/")


def test_path_params_are_encoded_and_extra_query_is_sent() -> None:
    fake = Fake(ok({"id": "x"}), ok({"search_id": "s"}))
    c = fake.client()
    c.catalog.products_get(product_id="a b/c")
    assert fake.sent[0].url.raw_path == b"/v1/products/a%20b%2Fc"
    c.search.search_get(q="لابتوب", facets="brand", options={"query": {"filter[brand]": ["Lenovo", "HP"]}})
    assert fake.sent[1].url.params["q"] == "لابتوب"
    assert fake.sent[1].url.params["filter[brand]"] == "Lenovo,HP"


def test_python_keyword_params_use_the_wire_name() -> None:
    fake = Fake(ok({"report": "overview"}))
    fake.client().analytics.stats(report="overview", from_="2026-10-01", to="2026-10-13")
    assert fake.sent[0].url.params["from"] == "2026-10-01"


def test_missing_path_param_fails_before_the_network() -> None:
    fake = Fake()
    with pytest.raises(SeekGistError, match="missing path parameter"):
        fake.client().jobs.get(job_id="")
    assert fake.sent == []


def test_no_key_on_public_operations() -> None:
    fake = Fake(ok([]))
    fake.client().billing.plans()
    assert "authorization" not in fake.sent[0].headers


def test_key_shape_is_checked() -> None:
    with pytest.raises(SeekGistError, match="sg_pub_"):
        SeekGist("not-a-key")


def test_multipart_upload() -> None:
    fake = Fake(ok({"id": "imp_1"}, 202))
    fake.client().imports.upload(
        body={"file": ("p.csv", b"id,name\n1,Lamp\n", "text/csv"), "source": "csv", "dry_run": True}
    )
    req = fake.sent[0]
    assert req.headers["content-type"].startswith("multipart/form-data")
    assert b'filename="p.csv"' in req.content
    assert b'name="dry_run"\r\n\r\ntrue' in req.content
    assert req.headers["idempotency-key"].startswith("sg-")


def test_idempotent_write_keeps_one_key_across_retries() -> None:
    fake = Fake(
        problem(503, "service_unavailable"),
        httpx.ConnectError("reset"),
        ok({"upserted": 1, "unchanged": 0, "deleted": 0}),
    )
    c = fake.client()
    res = c.request("products_batch", body={"upsert": []})
    assert res.attempts == 3
    keys = {r.headers["idempotency-key"] for r in fake.sent}
    assert len(keys) == 1
    assert c._cfg.waits == [0.5, 1.0]


def test_callers_key_and_replayed_flag() -> None:
    fake = Fake(ok({"upserted": 0, "unchanged": 0, "deleted": 0}, idempotent_replayed="true"))
    res = fake.client().request("products_batch", body={}, options={"idempotency_key": "my-key-0001"})
    assert fake.sent[0].headers["idempotency-key"] == "my-key-0001"
    assert res.replayed is True


def test_non_idempotent_write_is_not_retried() -> None:
    fake = Fake(problem(502, "bad_gateway"))
    with pytest.raises(ApiError) as e:
        fake.client().events.event(body={"type": "view"})  # type: ignore[typeddict-item]
    assert e.value.status == 502
    assert len(fake.sent) == 1
    fake2 = Fake(httpx.ConnectError("down"))
    with pytest.raises(ConnectionError):
        fake2.client().events.click(body={"search_id": "s", "product_id": "1", "position": 1})  # type: ignore[typeddict-item]
    assert len(fake2.sent) == 1


def test_429_is_retried_for_any_call_after_retry_after() -> None:
    fake = Fake(problem(429, "rate_limited", retry_after="2"), ok({"accepted": 1}, 202))
    c = fake.client()
    c.events.event(body={"type": "view"})  # type: ignore[typeddict-item]
    assert len(fake.sent) == 2
    assert c._cfg.waits == [2.0]


def test_409_only_retried_while_in_progress() -> None:
    fake = Fake(problem(409, "conflict"))
    with pytest.raises(ApiError, match="conflict"):
        fake.client().request("products_batch", body={})
    fake2 = Fake(problem(409, "idempotency_in_progress"), ok({"upserted": 0, "unchanged": 0, "deleted": 0}))
    fake2.client().request("products_batch", body={})
    assert len(fake2.sent) == 2


def test_409_in_progress_waits_retry_after_and_resends_the_same_key() -> None:
    """The API answers 409 idempotency_in_progress with Retry-After = seconds left on the first
    call's lease (at most 10 s; after a server crash the key is free when it runs out)."""
    done = ok({"upserted": 1, "unchanged": 0, "deleted": 0})
    fake = Fake(
        problem(409, "idempotency_in_progress", retry_after="4"),
        problem(409, "idempotency_in_progress", retry_after="3"),
        done,
    )
    c = fake.client()  # default max_retries=2 covers a full 10 s lease
    c.request("products_batch", body={"upsert": []})
    assert c._cfg.waits == [4.0, 3.0]
    keys = {r.headers["idempotency-key"] for r in fake.sent}
    assert len(fake.sent) == 3 and len(keys) == 1


def test_search_post_is_retried_and_stops_at_max_retries() -> None:
    fake = Fake(problem(500, "internal_error"), problem(500, "internal_error"))
    with pytest.raises(ApiError):
        fake.client(max_retries=1).find("lamp")
    assert len(fake.sent) == 2


def test_usage_rate_limit_and_request_id() -> None:
    fake = Fake(
        ok(
            {"search_id": "s1", "ids": [], "hits": [], "total": 0},
            x_seekgist_usage='searches=1234, limit=150000, billed=1, period="2026-10"',
            ratelimit_limit="10",
            ratelimit_remaining="9",
            x_request_id="req_abc",
        )
    )
    res = fake.client().find("laptop", lang="en", per_page=5, sort=None)
    assert fake.body(0) == {"q": "laptop", "lang": "en", "per_page": 5}
    assert res.usage is not None and (res.usage.searches, res.usage.limit, res.usage.billed, res.usage.period) == (
        1234,
        150000,
        1,
        "2026-10",
    )
    assert (res.rate_limit.limit, res.rate_limit.remaining) == (10, 9)
    assert res.request_id == "req_abc"


def test_problem_becomes_api_error_with_field_errors() -> None:
    body = {
        "type": "x",
        "title": "Invalid request",
        "status": 422,
        "code": "invalid_request",
        "detail": "Some fields are not valid.",
        "request_id": "req_1",
        "errors": [{"field": "upsert.0.price", "code": "greater_than_equal", "message": "Input should be >= 0"}],
    }
    fake = Fake(httpx.Response(422, json=body))
    with pytest.raises(ApiError) as e:
        fake.client().catalog.products_batch(body={})
    assert e.value.code == "invalid_request"
    assert e.value.request_id == "req_1"
    assert e.value.field_errors[0]["field"] == "upsert.0.price"
    assert "422 invalid_request" in str(e.value)


def test_non_problem_error_body() -> None:
    fake = Fake(httpx.Response(502, text="<html>bad gateway</html>"))
    with pytest.raises(ApiError) as e:
        fake.client(max_retries=0).health.check()
    assert e.value.code == "http_502"


def test_text_and_binary_results() -> None:
    fake = Fake(httpx.Response(200, text="a,b\n"), httpx.Response(200, content=b"\x01\x02"))
    c = fake.client()
    assert c.synonyms.export(format="csv") == "a,b\n"
    assert c.files.download(k="x", e=1, n="a.csv", s="y") == b"\x01\x02"


def test_timeout_error() -> None:
    fake = Fake(httpx.ReadTimeout("slow"))
    with pytest.raises(TimeoutError):
        fake.client(max_retries=0).health.check(options={"timeout": 0.05})


def test_public_key_works_for_search() -> None:
    fake = Fake(ok({"query": "lap", "suggestions": []}))
    fake.client(api_key=PUB).search.suggest(q="lap", lang="ar")
    assert fake.sent[0].headers["authorization"] == f"Bearer {PUB}"


def test_parse_usage() -> None:
    u = parse_usage('period="2026-11",billed=0,searches=5,limit=-1')
    assert u is not None and (u.searches, u.limit, u.billed, u.period) == (5, -1, 0, "2026-11")
    assert parse_usage("") is None
    assert parse_usage("limit=5") is None


def test_unknown_operation() -> None:
    with pytest.raises(SeekGistError, match="unknown operation"):
        Fake().client().request("nope")
