"""Receive SeekGist webhooks with the standard library and check the signature.

SEEKGIST_WEBHOOK_SECRET=whsec_... python examples/webhook_receiver.py   (listens on :3001)
"""

from __future__ import annotations

import os
from http.server import BaseHTTPRequestHandler, HTTPServer

from seekgist import WebhookVerificationError, verify_webhook

SECRET = os.environ.get("SEEKGIST_WEBHOOK_SECRET", "")
seen: set[str] = set()  # drop repeated deliveries (use your database in production)


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))  # the RAW body
        try:
            event = verify_webhook(SECRET, dict(self.headers), body)
        except WebhookVerificationError as e:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(str(e).encode())
            return
        if event["id"] not in seen:
            seen.add(event["id"])
            print(event["type"], event["data"])
        self.send_response(204)
        self.end_headers()


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", 3001), Handler).serve_forever()
