"""In-process fake of the deAPI HTTP API for tests and repo tools (no network).

Install it with ``mock.patch("urllib.request.urlopen", FakeAPI())``. Every
request is recorded in ``fake.calls``. Defaults:

- GET  /api/v2/models          -> tests/fixtures/models.json (filter honoured)
- POST /api/v2/<...>/price     -> {"data": {"price": fake.price, "is_estimated": false}}
- POST /api/v2/<...>           -> {"data": {"request_id": <uuid>}}; the job is done at once
- GET  /api/v2/jobs/<id>       -> the job (result_url on the results host)
- GET  https://results.example.test/... -> a small file (Content-Type by extension)
- GET  /api/v2/key             -> tests/fixtures/key.json

Override any route with ``fake.route(method, regex, response_or_callable)``.
Responses are ``(status, body_dict_or_bytes, headers_dict)`` tuples or a callable
taking the Call and returning one. Raise an exception from a callable to
simulate a network error.
"""

import email.message
import io
import json
import os
import re
import urllib.error
import urllib.parse
import uuid

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
RESULTS_HOST = "results.example.test"
TEST_KEY = "sk-dp-0-TESTKEYNOTREAL"
PNG_1x1 = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06"
           b"\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00"
           b"\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82")


def load_fixture(name):
    with open(os.path.join(FIXTURES, name), "r", encoding="utf-8") as handle:
        return json.load(handle)


class Call(object):
    def __init__(self, req):
        self.method = req.get_method()
        self.url = req.full_url
        parsed = urllib.parse.urlparse(self.url)
        self.host = parsed.hostname
        self.path = parsed.path
        self.query = dict(urllib.parse.parse_qsl(parsed.query))
        self.headers = {k.lower(): v for k, v in req.header_items()}
        data = req.data
        if data is None:
            self.body = b""
        elif isinstance(data, (bytes, bytearray)):
            self.body = bytes(data)
        else:
            self.body = b"".join(data)
        self.json = None
        self.fields = {}
        self.files = {}
        ctype = self.headers.get("content-type", "")
        if ctype.startswith("application/json") and self.body:
            self.json = json.loads(self.body.decode("utf-8"))
        elif ctype.startswith("multipart/form-data"):
            self._parse_multipart(ctype.split("boundary=", 1)[1])

    def _parse_multipart(self, boundary):
        for chunk in self.body.split(b"--" + boundary.encode()):
            if not chunk.strip() or chunk.strip() == b"--":
                continue
            head, _, value = chunk.lstrip(b"\r\n").partition(b"\r\n\r\n")
            value = value[:-2] if value.endswith(b"\r\n") else value
            head = head.decode("utf-8")
            name = re.search(r'name="([^"]*)"', head).group(1)
            filename = re.search(r'filename="([^"]*)"', head)
            if filename:
                self.files.setdefault(name, []).append({"filename": filename.group(1),
                                                         "size": len(value)})
            else:
                self.fields.setdefault(name, []).append(value.decode("utf-8"))

    def field(self, name):
        if self.json is not None:
            return self.json.get(name)
        values = self.fields.get(name)
        return values[0] if values else None

    def params(self):
        """Names of all parameters sent (JSON keys or multipart field names)."""
        if self.json is not None:
            return set(self.json)
        return set(self.fields) | set(self.files)


class FakeResponse(io.BytesIO):
    def __init__(self, status, body, headers):
        super().__init__(body)
        self.status = status
        self.headers = _headers(headers)

    def getcode(self):
        return self.status


def _headers(values):
    msg = email.message.Message()
    for key, value in (values or {}).items():
        msg[key] = str(value)
    return msg


class FakeAPI(object):
    def __init__(self, models=None, price=0.01, result_ext="png", result_text=None):
        self.calls = []
        self.routes = []
        self.models = models if models is not None else load_fixture("models.json")["data"]
        self.key = load_fixture("key.json")
        self.price = price
        self.is_estimated = False
        self.result_ext = result_ext
        self.result_text = result_text
        self.jobs = {}

    # -- configuration ---------------------------------------------------

    def route(self, method, pattern, response, times=None):
        """Add a route that wins over the defaults (and over older routes)."""
        self.routes.insert(0, [method.upper(), re.compile(pattern), response, times])
        return self

    def calls_to(self, method=None, path_regex=None):
        return [c for c in self.calls
                if (method is None or c.method == method)
                and (path_regex is None or re.search(path_regex, c.path))]

    def submissions(self):
        """Paid POSTs (not price quotes)."""
        return [c for c in self.calls if c.method == "POST" and not c.path.endswith("/price")]

    # -- urlopen -----------------------------------------------------------

    def __call__(self, req, timeout=None, *args, **kwargs):
        call = Call(req)
        self.calls.append(call)
        for route in self.routes:
            method, pattern, response, times = route
            if method == call.method and pattern.search(call.url):
                if times is not None:
                    if times <= 0:
                        continue
                    route[3] = times - 1
                result = response(call) if callable(response) else response
                return self._respond(call, result)
        return self._respond(call, self.default(call))

    def _respond(self, call, result):
        status, body, headers = (tuple(result) + (None,) * 3)[:3]
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode("utf-8")
            headers = dict({"Content-Type": "application/json"}, **(headers or {}))
        body = body or b""
        if status >= 400:
            raise urllib.error.HTTPError(call.url, status, "error", _headers(headers),
                                         io.BytesIO(body))
        return FakeResponse(status, body, headers)

    def default(self, call):
        if call.host == RESULTS_HOST:
            ext = os.path.splitext(call.path)[1].lstrip(".")
            ctype = {"png": "image/png", "mp4": "video/mp4", "mp3": "audio/mpeg",
                     "txt": "text/plain", "json": "application/json"}.get(ext, "application/octet-stream")
            return 200, PNG_1x1 if ext == "png" else b"result-bytes", {"Content-Type": ctype}
        if call.method == "GET" and call.path == "/api/v2/models":
            wanted = call.query.get("filter[inference_types]")
            models = self.models
            if wanted:
                types = set(wanted.split(","))
                models = [m for m in models if types & set(m.get("inference_types") or [])]
            return 200, {"data": models, "meta": {"current_page": 1, "last_page": 1}}
        if call.method == "GET" and call.path == "/api/v2/key":
            return 200, {"data": self.key}
        if call.method == "GET" and call.path == "/api/v2/account/balance":
            return 200, {"data": {"balance": 12.5}}
        if call.method == "GET" and call.path.endswith("/usage"):
            return 200, {"data": {"from": "2026-01-01T00:00:00+00:00",
                                  "to": "2026-01-31T00:00:00+00:00",
                                  "totals": {"cost": 0.5, "requests": 10, "errors": 0},
                                  "groups": []}}
        if call.method == "GET" and call.path.startswith("/api/v2/jobs/"):
            request_id = call.path.rsplit("/", 1)[1]
            job = self.jobs.get(request_id)
            if job is None:
                return 404, {"message": "Not found."}
            if isinstance(job, list):
                job = job.pop(0) if len(job) > 1 else job[0]
            return 200, {"data": job}
        if call.method == "POST" and call.path.endswith("/price"):
            if call.path == "/api/v2/prompts/enhancements/price":
                return 200, {"price": self.price}
            return 200, {"data": {"price": self.price, "is_estimated": self.is_estimated}}
        if call.method == "POST" and call.path == "/api/v2/prompts/enhancements":
            return 200, {"prompt": "an improved prompt", "negative_prompt": None}
        if call.method == "POST" and call.path.startswith("/api/v2/"):
            request_id = str(uuid.uuid4())
            self.jobs[request_id] = self.done_job(request_id)
            return 200, {"data": {"request_id": request_id}}
        if call.method == "GET" and call.path == "/v1/models":
            return 200, {"object": "list", "data": [{"id": "Flux1schnell", "object": "model"}]}
        return 404, {"message": "No fake route for %s %s" % (call.method, call.path)}

    def done_job(self, request_id):
        job = {"status": "done", "progress": 100, "price": {"amount": self.price,
                                                            "is_estimated": False}}
        if self.result_text is not None:
            job["result"] = self.result_text
        else:
            job["result_url"] = "https://%s/%s.%s" % (RESULTS_HOST, request_id, self.result_ext)
        return job
