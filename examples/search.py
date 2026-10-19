"""Search from Python with a PUBLIC or SECRET key.

SEEKGIST_API_KEY=sg_pub_... SEEKGIST_BASE_URL=http://localhost:58000 python examples/search.py "فستان"
"""

from __future__ import annotations

import sys

from seekgist import ApiError, SeekGist


def main() -> int:
    query = sys.argv[1] if len(sys.argv) > 1 else "dress"
    with SeekGist() as client:  # reads SEEKGIST_API_KEY and SEEKGIST_BASE_URL
        try:
            res = client.find(query, per_page=5)
        except ApiError as e:
            print(f"API error {e.status} {e.code} (request {e.request_id})", file=sys.stderr)
            return 1
        print(f"{res.data['total']} results for {query!r} ({res.data['took_ms']} ms)")
        for hit in res.data["hits"]:
            p = hit["product"]
            print(f"- {p['name'].get('ar') or p['name'].get('en')} | {p['price']} {p['currency']}")
        if res.usage:
            print(f"usage: {res.usage.searches} searches in {res.usage.period} (limit {res.usage.limit})")
        sug = client.search.suggest(q=query[:3])
        print("suggestions:", ", ".join(s["text"] for s in sug["suggestions"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
