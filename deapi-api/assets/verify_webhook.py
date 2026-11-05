#!/usr/bin/env python3
"""Verify and deduplicate deAPI webhook deliveries. Standard library only (Python 3.9+).

Use the functions in any framework; give them the RAW request body (bytes):

    from verify_webhook import DeliveryLog, handle_delivery
    log = DeliveryLog()
    status, event = handle_delivery(request.headers, raw_body, secret, log)
    # respond with `status` at once; process `event` (not None) in the background

Or run a minimal receiver for local tests:

    DEAPI_WEBHOOK_SECRET=... python verify_webhook.py --port 8080

Signature: X-DeAPI-Signature = "sha256=" + hex(HMAC_SHA256(secret, timestamp + "." + raw_body)),
timestamp from X-DeAPI-Timestamp; deliveries older than 5 minutes are rejected.
"""

import argparse
import collections
import hashlib
import hmac
import json
import os
import queue
import sys
import threading
import time

TOLERANCE_SECONDS = 300


def _header(headers, name):
    """Case-insensitive header lookup for dicts, email.message objects and framework maps."""
    value = headers.get(name)
    if value is None:
        lowered = name.lower()
        for key in headers.keys():
            if key.lower() == lowered:
                value = headers[key]
                break
    return value or ""


def sign(secret, timestamp, raw_body):
    """The value deAPI puts in X-DeAPI-Signature."""
    message = str(timestamp).encode("ascii") + b"." + raw_body
    return "sha256=" + hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def verify_signature(headers, raw_body, secret, now=None, tolerance=TOLERANCE_SECONDS):
    """True when the delivery is signed with `secret` and its timestamp is fresh."""
    if isinstance(raw_body, str):
        raw_body = raw_body.encode("utf-8")
    signature = _header(headers, "X-DeAPI-Signature").strip()
    timestamp = _header(headers, "X-DeAPI-Timestamp").strip()
    if not secret or not signature or not timestamp.isdigit():
        return False   # unsigned deliveries (no secret configured) are rejected too
    now = time.time() if now is None else now
    if abs(now - int(timestamp)) > tolerance:
        return False
    return hmac.compare_digest(signature.encode("ascii", "replace"),
                               sign(secret, timestamp, raw_body).encode("ascii"))


class DeliveryLog(object):
    """Remembers processed delivery IDs (in memory, bounded).

    In production use a table with a unique index on the delivery ID instead, so that
    several workers and restarts share it.
    """

    def __init__(self, max_items=10000):
        self._seen = collections.OrderedDict()
        self._max = max_items
        self._lock = threading.Lock()

    def first_time(self, delivery_id):
        """Record the ID; False when it was seen before."""
        with self._lock:
            if delivery_id in self._seen:
                return False
            self._seen[delivery_id] = True
            while len(self._seen) > self._max:
                self._seen.popitem(last=False)
            return True


def handle_delivery(headers, raw_body, secret, log, now=None):
    """Check one delivery. Returns (http_status, event_or_None).

    `secret` is the signing secret, or a function job_request_id -> secret when you send a
    per-job webhook_secret. 401: bad signature or stale timestamp. 400: not JSON.
    200 with None: a duplicate. 200 with the event dict: new, process it.
    """
    if isinstance(raw_body, str):
        raw_body = raw_body.encode("utf-8")
    try:
        event = json.loads(raw_body.decode("utf-8"))
    except ValueError:
        return 400, None
    if not isinstance(event, dict):
        return 400, None
    if callable(secret):
        # The body is parsed only to find which secret applies; nothing in it is trusted yet.
        secret = secret(((event.get("data") or {}).get("job_request_id")) or "")
    if not verify_signature(headers, raw_body, secret, now=now):
        return 401, None
    delivery_id = _header(headers, "X-DeAPI-Delivery-Id") or event.get("delivery_id") or ""
    if delivery_id and not log.first_time(delivery_id):
        return 200, None
    return 200, event


def process(event):
    """Replace with your logic. Keep it idempotent per job and event."""
    data = event.get("data") or {}
    price = data.get("price") or {}
    print("%s %s %s price=%s%s" % (event.get("event"), data.get("job_request_id"),
                                   data.get("status"), price.get("amount"),
                                   " (estimated)" if price.get("is_estimated") else ""))
    sys.stdout.flush()


def serve(port, secret, host="127.0.0.1"):
    """Minimal receiver: answers at once, processes in a background thread."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    log = DeliveryLog()
    work = queue.Queue()

    def worker():
        while True:
            process(work.get())

    threading.Thread(target=worker, daemon=True).start()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(min(length, 1 << 20))
            status, event = handle_delivery(self.headers, raw, secret, log)
            self.send_response(status)
            self.send_header("Content-Length", "0")
            self.end_headers()
            if event is not None:
                work.put(event)

        def log_message(self, fmt, *args):   # keep request lines out of the output
            pass

    server = ThreadingHTTPServer((host, port), Handler)
    print("listening on http://%s:%d/ (POST)" % (host, port))
    sys.stdout.flush()
    server.serve_forever()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Minimal deAPI webhook receiver")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args(argv)
    secret = os.environ.get("DEAPI_WEBHOOK_SECRET", "")
    if len(secret) < 32:
        print("set DEAPI_WEBHOOK_SECRET (32-255 characters)", file=sys.stderr)
        return 2
    serve(args.port, secret, args.host)
    return 0


if __name__ == "__main__":
    sys.exit(main())
