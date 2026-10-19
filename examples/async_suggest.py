"""Debounced suggestions in asyncio code (a chat bot, a terminal UI, a server-side proxy).

SEEKGIST_API_KEY=sg_pub_... python examples/async_suggest.py
"""

from __future__ import annotations

import asyncio

from seekgist import AsyncSeekGist


async def main() -> None:
    async with AsyncSeekGist() as client:
        suggester = client.suggester(debounce=0.12, lang="auto", products=3)
        typed = ["d", "dr", "dre", "dres", "dress"]
        tasks = [asyncio.ensure_future(suggester.input(text)) for text in typed]  # fast typing
        results = await asyncio.gather(*tasks)
        for text, res in zip(typed, results, strict=True):
            print(f"{text!r}: {'(skipped)' if res is None else [s['text'] for s in res['suggestions']]}")


if __name__ == "__main__":
    asyncio.run(main())
