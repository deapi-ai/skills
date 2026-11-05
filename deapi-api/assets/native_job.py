#!/usr/bin/env python3
"""One text-to-image job on the native deAPI API v2, end to end.

model card -> free price quote -> cost ceiling -> submit -> poll -> download.
Standard library only (Python 3.9+). Copy the pieces you need into your app.

    export DEAPI_API_KEY=sk-dp-...
    python native_job.py "a red fox in the snow" --quote            # price only, nothing paid
    python native_job.py "a red fox in the snow" --max-cost 0.01    # run if the quote is <= 0.01 USD

The key is read from DEAPI_API_KEY and sent only to the API host. Result links are
pre-signed, so they are downloaded without it. DEAPI_API_URL (default
https://api.deapi.ai) may point at another *.deapi.ai host or at a local mock.
"""

import argparse
import json
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = "deapi-example/2.0 (+https://docs.deapi.ai)"
DEFAULT_MAX_COST = 0.25  # USD per job


class ApiError(Exception):
    """An HTTP error from deAPI. Branch on .status and .code, not on the message."""

    def __init__(self, status, body, headers):
        self.status = status
        self.body = body if isinstance(body, dict) else {}
        self.headers = headers
        self.code = self.body.get("code")
        message = self.body.get("message") or ""
        super(ApiError, self).__init__("HTTP %s%s %s" % (status, " " + self.code if self.code else "",
                                                         message))


class JobFailed(Exception):
    def __init__(self, request_id, job):
        self.request_id = request_id
        self.job = job
        super(JobFailed, self).__init__("job %s failed: %s (retryable=%s, refunded=%s)" % (
            request_id, job.get("error_reason") or job.get("error_code"), job.get("retryable"),
            job.get("refunded")))


def checked_api_url(url):
    """Allow only https://*.deapi.ai, or plain http on this machine for tests and mocks."""
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme == "https" and (host == "deapi.ai" or host.endswith(".deapi.ai")):
        return url.rstrip("/")
    if parts.scheme == "http" and host in ("127.0.0.1", "localhost"):
        return url.rstrip("/")
    raise ValueError("refusing to send the API key to %r; use an https://*.deapi.ai URL" % host)


class Client(object):
    def __init__(self, api_key, api_url="https://api.deapi.ai", timeout=60):
        if not api_key:
            raise ValueError("DEAPI_API_KEY is not set")
        self.api_key = api_key
        self.api_url = checked_api_url(api_url)
        self.timeout = timeout

    def request(self, method, path, body=None, query=None, retries=3):
        url = self.api_url + path
        if query:
            url += "?" + urllib.parse.urlencode(query, doseq=True)
        data = None if body is None else json.dumps(body).encode("utf-8")
        headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
        if data is not None:
            headers["Content-Type"] = "application/json"
        for attempt in range(retries + 1):
            req = urllib.request.Request(url, data=data, headers=headers, method=method)
            # Unredirected: urllib would copy a normal header to a redirect target.
            req.add_unredirected_header("Authorization", "Bearer " + self.api_key)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    raw = resp.read()
                    return json.loads(raw.decode("utf-8")) if raw else {}
            except urllib.error.HTTPError as err:
                raw = err.read()
                try:
                    parsed = json.loads(raw.decode("utf-8"))
                except ValueError:
                    parsed = {"message": raw[:200].decode("utf-8", "replace")}
                error = ApiError(err.code, parsed, err.headers)
            # Retry only what is safe to repeat: 429 on the minute window (any method),
            # 5xx on GET. A job POST that hit a 5xx may already exist and be billed.
            if attempt < retries and self._retryable(method, error):
                time.sleep(self._delay(error, attempt))
                continue
            raise error

    @staticmethod
    def _retryable(method, error):
        if error.status == 429:
            return (error.headers.get("X-RateLimit-Type") or "minute") == "minute"
        return method == "GET" and error.status >= 500

    @staticmethod
    def _delay(error, attempt):
        try:
            return min(float(error.headers.get("Retry-After")), 60.0)
        except (TypeError, ValueError):
            return min(2.0 ** attempt, 30.0)

    def model_card(self, inference_type, slug=None):
        """The model card for `slug`, or the first native-fleet model for the task."""
        data = self.request("GET", "/api/v2/models",
                            query={"filter[inference_types]": inference_type, "limit": 100})
        models = data.get("data") or []
        for model in models:
            if slug and model.get("slug") == slug:
                return model
            if not slug and model.get("status") == "standard_model":
                return model
        raise ValueError("no %s model %s is available to this key" % (inference_type, slug or ""))

    def quote(self, path, body):
        """Free price quote. Returns (price or None when unknown, is_estimated)."""
        data = self.request("POST", path + "/price", body)
        info = data.get("data", data)  # the prompt booster answers {"price": ...} without "data"
        if info.get("estimate_basis") == "unavailable":
            return None, True
        return info.get("price"), bool(info.get("is_estimated"))

    def submit(self, path, body):
        return self.request("POST", path, body)["data"]["request_id"]

    def wait(self, request_id, timeout=900, first_delay=2.0, max_delay=10.0, sleep=time.sleep):
        delay, waited = first_delay, 0.0
        while True:
            job = self.request("GET", "/api/v2/jobs/" + urllib.parse.quote(request_id))["data"]
            if job.get("status") == "done":
                return job
            if job.get("status") == "error":
                raise JobFailed(request_id, job)
            if waited >= timeout:
                raise TimeoutError("job %s still %s after %ss; check it later with "
                                   "GET /api/v2/jobs/%s" % (request_id, job.get("status"),
                                                            int(waited), request_id))
            sleep(delay)
            waited += delay
            delay = min(delay * 1.5, max_delay)


def download(url, path):
    """Save a result link. No Authorization header: the link is pre-signed."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=300) as resp, open(path, "xb") as out:
        while True:
            chunk = resp.read(1 << 16)
            if not chunk:
                break
            out.write(chunk)
    return path


def image_body(card, prompt, width=None, height=None, seed=None):
    """Request body built from the model card: defaults, limits and feature flags."""
    info = card.get("info") or {}
    limits, features, defaults = (info.get("limits") or {}, info.get("features") or {},
                                  info.get("defaults") or {})
    step = limits.get("resolution_step") or limits.get("dimension_multiple") or 1

    def size(value, name):
        value = int(value or defaults.get(name) or 512)
        low, high = limits.get("min_" + name, value), limits.get("max_" + name, value)
        value = max(low, min(high, value))
        return max(low, value - value % step)

    body = {"prompt": prompt, "model": card["slug"],
            "width": size(width, "width"), "height": size(height, "height"),
            "seed": random.randint(0, 2 ** 31 - 1) if seed is None else seed}
    if features.get("supports_steps"):
        body["steps"] = defaults.get("steps") or limits.get("min_steps") or 4
    if features.get("supports_guidance"):
        body["guidance"] = defaults.get("guidance") or limits.get("min_guidance") or 1
    return body


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("prompt")
    parser.add_argument("--model", help="model slug (default: first native-fleet txt2img model)")
    parser.add_argument("--width", type=int)
    parser.add_argument("--height", type=int)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--quote", action="store_true", help="only quote the price (free)")
    parser.add_argument("--max-cost", type=float, default=DEFAULT_MAX_COST,
                        help="refuse jobs quoted above this many USD (default 0.25)")
    parser.add_argument("--out", help="output file (default: <request_id>.png)")
    args = parser.parse_args(argv)

    try:
        client = Client(os.environ.get("DEAPI_API_KEY", ""),
                        os.environ.get("DEAPI_API_URL", "https://api.deapi.ai"))
        card = client.model_card("txt2img", args.model)
        body = image_body(card, args.prompt, args.width, args.height, args.seed)
        path = "/api/v2/images/generations"
        price, estimated = client.quote(path, body)
        summary = {"model": body["model"], "quoted_price": price, "is_estimated": estimated,
                   "max_cost": args.max_cost}
        if args.quote:
            print(json.dumps(dict(summary, quote_only=True)))
            return 0
        if price is None or price > args.max_cost:
            print(json.dumps(dict(summary, error="quote above the cost ceiling or unknown")))
            return 5
        request_id = client.submit(path, body)
        job = client.wait(request_id)
        out = args.out or request_id + ".png"
        download(job["result_url"], out)
        print(json.dumps(dict(summary, request_id=request_id, price=job.get("price"), file=out)))
        return 0
    except ApiError as err:
        print("error: %s" % err, file=sys.stderr)
        return 1
    except (JobFailed, TimeoutError, ValueError, urllib.error.URLError, OSError) as err:
        print("error: %s" % err, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
