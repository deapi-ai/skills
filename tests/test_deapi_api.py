"""Tests for the deapi-api skill: docs, code blocks and the examples in deapi-api/assets.

Everything runs offline against a local fake of the native API and the OpenAI-compatible API
(an HTTP server on 127.0.0.1). Parts that need Node.js, curl or the `openai` packages are
skipped when those are missing:

    python -m unittest tests/test_deapi_api.py -v

Full run (Node.js 22+, curl, `pip install openai`, and DEAPI_TEST_NODE_MODULES pointing at a
node_modules folder that contains `openai`) exercises every example.
"""

import base64
import contextlib
import glob
import hashlib
import hmac
import importlib.util
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
import urllib.request
import uuid
import warnings
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL = os.path.join(ROOT, "deapi-api")
ASSETS = os.path.join(SKILL, "assets")
REFS = os.path.join(SKILL, "references")
FIXTURES = os.path.join(ROOT, "tests", "fixtures")
TEST_KEY = "sk-dp-0-TESTKEYNOTREAL"
PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06"
       b"\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00"
       b"\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82")
NODE = shutil.which("node")
CURL = shutil.which("curl")
SH = shutil.which("sh")


def load_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


SPEC = load_json(os.path.join(FIXTURES, "openapi.json"))


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def node_strips_types():
    if not NODE:
        return False
    try:
        out = subprocess.run([NODE, "--experimental-strip-types", "-e", "1"], capture_output=True,
                             timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    return out.returncode == 0


NODE_TS = node_strips_types()


def have_openai():
    try:
        import openai  # noqa: F401
    except ImportError:
        return False
    return True


# --------------------------------------------------------------------------- spec helpers

def _norm(path):
    return re.sub(r"\{[^}]+\}", "{}", path.rstrip("/"))


SPEC_PATHS = {_norm(p): p for p in SPEC["paths"]}


def _schema(ref_or_schema):
    if "$ref" in ref_or_schema:
        name = ref_or_schema["$ref"].split("/")[-1]
        return SPEC["components"]["schemas"][name]
    return ref_or_schema


def request_schema(path, method="post"):
    op = SPEC["paths"][path][method]
    content = op["requestBody"]["content"]
    return _schema(next(iter(content.values()))["schema"])


def query_params(path, method="get"):
    names = set()
    for param in SPEC["paths"][path][method].get("parameters", []):
        if "$ref" in param:
            param = SPEC["components"]["parameters"][param["$ref"].split("/")[-1]]
        if param.get("in") == "query":
            names.add(param["name"])
    return names


# --------------------------------------------------------------------------- fake API server

class Call(object):
    def __init__(self, method, path, query, headers, body):
        self.method, self.path, self.query, self.headers, self.body = method, path, query, headers, body

    @property
    def json(self):
        try:
            return json.loads(self.body.decode("utf-8"))
        except ValueError:
            return None


class FakeServer(object):
    """Native API v2 and OpenAI-compatible API on one local port."""

    def __init__(self):
        self.calls = []
        self.price = 0.002
        self.is_estimated = False
        self.estimate_basis = None
        self.pending_polls = 0
        self.job_error = None
        self.overrides = {}          # (method, path regex) -> (status, body, headers)
        self.video_ready_on_create = False
        self.usage_forbidden = False
        self.models = load_json(os.path.join(FIXTURES, "models.json"))["data"]
        self.key = load_json(os.path.join(FIXTURES, "key.json"))
        self._polls = {}
        self._videos = {}
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                pass

            def _handle(self):
                parsed = urllib.parse.urlsplit(self.path)
                if "chunked" in (self.headers.get("Transfer-Encoding") or "").lower():
                    body = self._read_chunked()
                else:
                    length = int(self.headers.get("Content-Length") or 0)
                    body = self.rfile.read(length) if length else b""
                call = Call(self.command, parsed.path, urllib.parse.parse_qs(parsed.query),
                            {k.lower(): v for k, v in self.headers.items()}, body)
                server.calls.append(call)
                status, payload, headers = server.respond(call)
                if isinstance(payload, (dict, list)):
                    data = json.dumps(payload).encode("utf-8")
                    headers = dict({"Content-Type": "application/json"}, **headers)
                else:
                    data = payload
                self.send_response(status)
                for name, value in headers.items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _read_chunked(self):
                chunks = []
                while True:
                    size = int(self.rfile.readline().split(b";")[0].strip() or b"0", 16)
                    if size == 0:
                        while self.rfile.readline() not in (b"\r\n", b"\n", b""):
                            pass
                        return b"".join(chunks)
                    chunks.append(self.rfile.read(size))
                    self.rfile.readline()

            do_GET = do_POST = do_PATCH = do_DELETE = _handle

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = "http://127.0.0.1:%d" % self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()

    # -- helpers for assertions
    def api_calls(self):
        return [c for c in self.calls if c.path.startswith(("/api/", "/v1/"))]

    def submissions(self):
        return [c for c in self.calls if c.method == "POST" and c.path.startswith("/api/v2/")
                and not c.path.endswith("/price")]

    # -- routing
    def respond(self, call):
        for (method, regex), response in self.overrides.items():
            if call.method == method and re.fullmatch(regex, call.path):
                return response
        if call.path.startswith("/results/"):
            return 200, PNG, {"Content-Type": "image/png"}
        auth = call.headers.get("authorization", "")
        if auth != "Bearer " + TEST_KEY:
            return 401, {"message": "Unauthenticated.", "code": "missing_key"}, {}
        if call.path.startswith("/v1/"):
            return self.respond_oai(call)
        return self.respond_native(call)

    def respond_native(self, call):
        path, method = call.path, call.method
        if method == "GET" and path == "/api/v2/models":
            wanted = call.query.get("filter[inference_types]", [None])[0]
            data = [m for m in self.models if not wanted or wanted in m.get("inference_types", [])]
            return 200, {"data": data, "meta": {"current_page": 1, "last_page": 1}}, {}
        if method == "GET" and path == "/api/v2/key":
            return 200, self.key, {}
        if method == "GET" and path in ("/api/v2/key/usage", "/api/v2/account/usage"):
            if path == "/api/v2/account/usage" and self.usage_forbidden:
                return 403, {"message": "This API key does not have permission to access this "
                                        "endpoint.", "code": "insufficient_permissions"}, {}
            group_by = call.query.get("group_by", ["day"])[0]
            return 200, {"data": {
                "from": call.query.get("from", [""])[0], "to": call.query.get("to", [""])[0],
                "group_by": group_by, "currency": "USD",
                "totals": {"cost": 0.75, "requests": 12, "errors": 1},
                "groups": [{"key": "Flux1schnell", "cost": 0.25, "requests": 10, "errors": 0},
                           {"key": "Kokoro", "cost": 0.5, "requests": 2, "errors": 1}]}}, {}
        if method == "POST" and path.endswith("/price"):
            return 200, {"data": {"price": self.price, "is_estimated": self.is_estimated,
                                  "estimate_basis": self.estimate_basis}}, {}
        if method == "POST" and path.startswith("/api/v2/"):
            request_id = str(uuid.uuid4())
            self._polls[request_id] = 0
            return 200, {"data": {"request_id": request_id}}, {}
        match = re.fullmatch(r"/api/v2/jobs/([^/]+)", path)
        if method == "GET" and match:
            request_id = match.group(1)
            if request_id not in self._polls:
                return 404, {"message": "No query results for the requested resource."}, {}
            self._polls[request_id] += 1
            if self._polls[request_id] <= self.pending_polls:
                return 200, {"data": {"status": "processing", "progress": 50.0, "price": None}}, {}
            if self.job_error:
                return 200, {"data": dict({"status": "error", "progress": 0, "price":
                                           {"amount": 0, "is_estimated": False}},
                                          **self.job_error)}, {}
            return 200, {"data": {"status": "done", "progress": 100.0,
                                  "result_url": self.base + "/results/%s.png?X-Amz-Signature=x"
                                  % request_id, "results_alt_formats": None, "result": None,
                                  "price": {"amount": self.price, "is_estimated": False}}}, {}
        return 404, {"message": "The route %s could not be found." % path}, {}

    def respond_oai(self, call):
        path, method = call.path, call.method
        if method == "GET" and path == "/v1/models":
            return 200, {"object": "list", "data": [
                {"id": m["slug"], "object": "model", "created": 1700000000, "owned_by": "deapi"}
                for m in self.models]}, {}
        if method == "POST" and path == "/v1/embeddings":
            items = call.json["input"]
            items = items if isinstance(items, list) else [items]
            return 200, {"object": "list", "model": call.json["model"],
                         "data": [{"object": "embedding", "index": i, "embedding": [0.1, 0.2, 0.3]}
                                  for i in range(len(items))],
                         "usage": {"prompt_tokens": 0, "total_tokens": 0}}, {}
        if method == "POST" and path == "/v1/images/generations":
            return 200, {"created": 1700000000,
                         "data": [{"b64_json": base64.b64encode(PNG).decode("ascii")}]}, {}
        if method == "POST" and path == "/v1/audio/speech":
            return 200, b"ID3\x03\x00\x00\x00fake-mp3", {"Content-Type": "audio/mpeg"}
        if method == "POST" and path == "/v1/audio/transcriptions":
            return 200, {"task": "transcribe", "language": "english", "duration": 1.0,
                         "text": "Good morning.", "segments": [], "words": []}, {}
        if method == "POST" and path == "/v1/videos":
            video_id = str(uuid.uuid4())
            self._videos[video_id] = 0
            status = "completed" if self.video_ready_on_create else "queued"
            return 200, self._video(video_id, status, call.json), {}
        match = re.fullmatch(r"/v1/videos/([^/]+)(/content)?", path)
        if method == "GET" and match and match.group(1) in self._videos:
            if match.group(2):
                return 200, b"\x00\x00\x00\x18ftypmp42fake", {"Content-Type": "video/mp4"}
            self._videos[match.group(1)] += 1
            return 200, self._video(match.group(1), "completed", {}), {}
        return 404, {"error": {"message": "not here", "type": "not_found_error",
                               "code": "endpoint_not_implemented"}}, {}

    @staticmethod
    def _video(video_id, status, body):
        return {"id": video_id, "object": "video", "status": status, "created_at": 1700000000,
                "model": (body or {}).get("model", ""), "progress": 100 if status == "completed"
                else 0, "seconds": (body or {}).get("seconds", "5"),
                "size": (body or {}).get("size", "768x768")}


def free_port():
    with contextlib.closing(socket.socket()) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def run(cmd, env=None, cwd=None, timeout=120):
    full_env = dict(os.environ, PYTHONIOENCODING="utf-8", NO_COLOR="1")
    full_env.update(env or {})
    return subprocess.run(cmd, capture_output=True, text=True, env=full_env, cwd=cwd,
                          timeout=timeout)


# --------------------------------------------------------------------------- docs

def code_blocks(text):
    """(language, code) for every fenced block."""
    return [(m.group(1).strip().lower(), m.group(2))
            for m in re.finditer(r"^```([^\n]*)\n(.*?)^```", text, re.M | re.S)]


def skill_docs():
    return [os.path.join(SKILL, "SKILL.md")] + sorted(glob.glob(os.path.join(REFS, "*.md")))


def asset_files():
    """The example files (no __pycache__ or other folders left by test runs)."""
    return [p for p in sorted(glob.glob(os.path.join(ASSETS, "*"))) if os.path.isfile(p)]


def read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


class DocsTest(unittest.TestCase):
    def test_frontmatter_and_layout(self):
        tools = load_module("check_frontmatter", os.path.join(ROOT, "tools", "check_frontmatter.py"))
        self.assertEqual(tools.check_skill(SKILL), [])
        block, _ = tools.split_frontmatter(read(os.path.join(SKILL, "SKILL.md")))
        fields, _ = tools.parse_frontmatter(block)
        self.assertEqual(tools.first_sentence(fields["description"]),
                         "Builds apps on deAPI: native API, OpenAI SDKs, webhooks.")

    def test_skill_lists_every_reference_and_asset(self):
        text = read(os.path.join(SKILL, "SKILL.md"))
        for path in sorted(glob.glob(os.path.join(REFS, "*.md"))):
            self.assertIn("references/" + os.path.basename(path), text)
        assets = [os.path.basename(p) for p in asset_files()]
        self.assertTrue(assets)
        for name in assets:
            self.assertIn("assets/" + name, text)
        for name in re.findall(r"assets/([A-Za-z0-9_.-]+)", text):
            self.assertTrue(os.path.isfile(os.path.join(ASSETS, name)), name)
        for doc in skill_docs():
            for ref in re.findall(r"references/([A-Za-z0-9_.-]+\.md)", read(doc)):
                self.assertTrue(os.path.isfile(os.path.join(REFS, ref)), (doc, ref))

    def test_code_blocks_parse(self):
        for doc in skill_docs():
            for number, (lang, code) in enumerate(code_blocks(read(doc)), 1):
                where = "%s block %d" % (os.path.basename(doc), number)
                if lang == "python":
                    compile(code, where, "exec")
                elif lang == "json":
                    json.loads(code)
                elif lang in ("bash", "sh") and SH:
                    out = subprocess.run([SH, "-n"], input=code, text=True, capture_output=True)
                    self.assertEqual(out.returncode, 0, (where, out.stderr))
                elif lang in ("javascript", "typescript") and NODE_TS:
                    suffix = ".mts" if lang == "typescript" else ".mjs"
                    with tempfile.TemporaryDirectory() as tmp:
                        path = os.path.join(tmp, "block" + suffix)
                        with open(path, "w", encoding="utf-8") as handle:
                            handle.write(code)
                        out = run([NODE, "--experimental-strip-types", "--check", path])
                        self.assertEqual(out.returncode, 0, (where, out.stderr))

    def test_native_paths_exist_in_spec(self):
        files = skill_docs() + asset_files()
        seen = set()
        for path in files:
            for raw in re.findall(r"/api/v2/[A-Za-z0-9_{}./\-]*", read(path)):
                route = raw.rstrip(".")
                if "/utilities/" in route or ".md" in route:
                    continue   # documentation page URLs, not API routes
                if route.endswith("/"):
                    self.assertTrue(any(p.startswith(_norm(route) + "/") for p in SPEC_PATHS),
                                    (path, raw))
                    continue
                seen.add(route)
                self.assertTrue(_norm(route) in SPEC_PATHS, (os.path.basename(path), raw))
        self.assertIn("/api/v2/jobs/{request_id}", seen)

    def test_endpoint_table_covers_every_job_endpoint(self):
        text = read(os.path.join(REFS, "native-api.md"))
        # Video background removal has no model in the public catalog, so it is not offered.
        skip = {"/api/v2/keys", "/api/v2/videos/background-removals"}
        for path, ops in SPEC["paths"].items():
            if "post" in ops and not path.endswith("/price") and path not in skip:
                self.assertIn("`POST %s`" % path, text)

    def test_public_content_rules(self):
        scan = load_module("scan_leaks", os.path.join(ROOT, "tools", "scan_leaks.py"))
        files = skill_docs() + asset_files() + [__file__]
        for path in files:
            rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
            findings = scan.scan_file(rel, read(path), scan.PATTERNS)
            self.assertEqual([str(f) for f in findings], [], rel)
            if path == __file__:
                continue
            text = read(path)
            self.assertNotIn("usage.cost", text, rel)        # not on the public API yet
            self.assertNotIn("/api/v1/client/", text.replace("(`/api/v1/client/...`)", ""), rel)


# --------------------------------------------------------------------------- webhooks

# Built at run time so secret scanners do not flag a made-up test value.
SECRET = "-".join(("test", "secret", "0123456789abcdef" * 2))  # gitleaks:allow (synthetic test value)
TIMESTAMP = "1705315800"
BODY = json.dumps({
    "event": "job.completed", "delivery_id": "c4d543d8-b52b-4f62-a6c1-5605111dd830",
    "timestamp": "2026-08-07T11:43:26+00:00",
    "data": {"job_request_id": "868543c4-9429-4434-972a-db27769c8a34", "status": "done",
             "previous_status": "processing", "job_type": "txt2img",
             "result_url": "https://results.example.test/1.png", "processing_time_ms": 12000,
             "price": {"amount": 0.0234, "is_estimated": False}}},
    separators=(",", ":")).encode("utf-8")
# HMAC-SHA256 of TIMESTAMP + "." + BODY with SECRET, computed independently of the assets.
EXPECTED = "sha256=" + hmac.new(SECRET.encode(), TIMESTAMP.encode() + b"." + BODY,
                                hashlib.sha256).hexdigest()


def webhook_headers(signature=EXPECTED, timestamp=TIMESTAMP, delivery="d-1"):
    return {"X-DeAPI-Signature": signature, "X-DeAPI-Timestamp": timestamp,
            "X-DeAPI-Event": "job.completed", "X-DeAPI-Delivery-Id": delivery,
            "Content-Type": "application/json"}


# (name, headers, body, secret, now, expected)
CASES = [
    ("valid", webhook_headers(), BODY, SECRET, 1705315800, True),
    ("valid at 299 s", webhook_headers(), BODY, SECRET, 1705316099, True),
    ("stale", webhook_headers(), BODY, SECRET, 1705316101, False),
    ("from the future", webhook_headers(), BODY, SECRET, 1705315499, False),
    ("wrong secret", webhook_headers(), BODY, SECRET + "x", 1705315800, False),
    ("tampered body", webhook_headers(), BODY.replace(b"0.0234", b"0.0001"), SECRET, 1705315800,
     False),
    ("unsigned", webhook_headers(signature=""), BODY, SECRET, 1705315800, False),
    ("no secret configured", webhook_headers(), BODY, "", 1705315800, False),
    ("bad timestamp", webhook_headers(timestamp="soon"), BODY, SECRET, 1705315800, False),
    ("short signature", webhook_headers(signature="sha256=00"), BODY, SECRET, 1705315800, False),
    ("lower-case headers", {k.lower(): v for k, v in webhook_headers().items()}, BODY, SECRET,
     1705315800, True),
]


class WebhookPythonTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_module("verify_webhook", os.path.join(ASSETS, "verify_webhook.py"))

    def test_sign_matches_documented_formula(self):
        self.assertEqual(self.mod.sign(SECRET, TIMESTAMP, BODY), EXPECTED)

    def test_cases(self):
        for name, headers, body, secret, now, expected in CASES:
            with self.subTest(name):
                self.assertIs(self.mod.verify_signature(headers, body, secret, now=now), expected)

    def test_handle_delivery_dedupes_and_rejects(self):
        log = self.mod.DeliveryLog()
        status, event = self.mod.handle_delivery(webhook_headers(), BODY, SECRET, log, now=1705315800)
        self.assertEqual((status, event["event"]), (200, "job.completed"))
        self.assertEqual(self.mod.handle_delivery(webhook_headers(), BODY, SECRET, log,
                                                  now=1705315800), (200, None))
        self.assertEqual(self.mod.handle_delivery(webhook_headers(delivery="d-2"), BODY, "x" * 40,
                                                  log, now=1705315800), (401, None))
        self.assertEqual(self.mod.handle_delivery(webhook_headers(), b"not json", SECRET, log,
                                                  now=1705315800), (400, None))

    def test_per_job_secret_lookup(self):
        asked = []

        def lookup(job_id):
            asked.append(job_id)
            return SECRET

        status, event = self.mod.handle_delivery(webhook_headers(), BODY, lookup,
                                                 self.mod.DeliveryLog(), now=1705315800)
        self.assertEqual(status, 200)
        self.assertEqual(asked, ["868543c4-9429-4434-972a-db27769c8a34"])

    def test_receiver_end_to_end(self):
        port = free_port()
        proc = subprocess.Popen([sys.executable, os.path.join(ASSETS, "verify_webhook.py"),
                                 "--port", str(port)], stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True,
                                env=dict(os.environ, DEAPI_WEBHOOK_SECRET=SECRET))
        try:
            self.assertIn("listening", proc.stdout.readline())
            now = str(int(time.time()))
            good = self.mod.sign(SECRET, now, BODY)
            self.assertEqual(post(port, BODY, webhook_headers(good, now)), 200)
            self.assertEqual(post(port, BODY, webhook_headers(good, now)), 200)   # duplicate
            self.assertEqual(post(port, BODY, webhook_headers(EXPECTED, now)), 401)
            self.assertIn("job.completed 868543c4", proc.stdout.readline())
        finally:
            proc.terminate()
            proc.communicate(timeout=10)

    def test_receiver_refuses_short_secret(self):
        out = run([sys.executable, os.path.join(ASSETS, "verify_webhook.py")],
                  env={"DEAPI_WEBHOOK_SECRET": "short"})
        self.assertEqual(out.returncode, 2)


def post(port, body, headers):
    req = urllib.request.Request("http://127.0.0.1:%d/webhooks" % port, data=body,
                                 headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status
    except urllib.error.HTTPError as err:
        return err.code


@unittest.skipUnless(NODE, "Node.js not installed")
class WebhookNodeTest(unittest.TestCase):
    def run_node(self, script):
        out = run([NODE, "--input-type=module", "-e", script], cwd=ASSETS)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def test_cases_match_python(self):
        cases = [{"name": n, "headers": h, "body": base64.b64encode(b).decode(), "secret": s,
                  "now": t, "expected": e} for n, h, b, s, t, e in CASES]
        script = """
import { verifyWebhook, sign, handleDelivery, DeliveryLog } from "./verify_webhook.mjs";
const cases = %s;
const results = cases.map((c) => [c.name, verifyWebhook({ headers: c.headers,
  rawBody: Buffer.from(c.body, "base64"), secret: c.secret, now: c.now })]);
const body = Buffer.from(cases[0].body, "base64");
const log = new DeliveryLog();
const first = handleDelivery(cases[0].headers, body, () => %s, log, %d);
const again = handleDelivery(cases[0].headers, body, %s, log, %d);
const bad = handleDelivery(cases[0].headers, Buffer.from("nope"), %s, log, %d);
console.log(JSON.stringify({ results, sig: sign(%s, "%s", body),
  first: [first.status, first.event && first.event.event], again: [again.status, again.event],
  bad: bad.status }));
""" % (json.dumps(cases), json.dumps(SECRET), 1705315800, json.dumps(SECRET), 1705315800,
            json.dumps(SECRET), 1705315800, json.dumps(SECRET), TIMESTAMP)
        out = self.run_node(script)
        self.assertEqual(out["sig"], EXPECTED)
        self.assertEqual(out["results"], [[c[0], c[5]] for c in CASES])
        self.assertEqual(out["first"], [200, "job.completed"])
        self.assertEqual(out["again"], [200, None])
        self.assertEqual(out["bad"], 400)

    def test_receiver_end_to_end(self):
        port = free_port()
        proc = subprocess.Popen([NODE, os.path.join(ASSETS, "verify_webhook.mjs"), "--port",
                                 str(port)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, env=dict(os.environ, DEAPI_WEBHOOK_SECRET=SECRET))
        try:
            self.assertIn("listening", proc.stdout.readline())
            now = str(int(time.time()))
            good = "sha256=" + hmac.new(SECRET.encode(), now.encode() + b"." + BODY,
                                        hashlib.sha256).hexdigest()
            self.assertEqual(post(port, BODY, webhook_headers(good, now)), 200)
            self.assertEqual(post(port, BODY, webhook_headers(EXPECTED, now)), 401)
            self.assertIn("job.completed 868543c4", proc.stdout.readline())
        finally:
            proc.terminate()
            proc.communicate(timeout=10)


# --------------------------------------------------------------------------- native examples

class NativeChecks(object):
    """Assertions shared by the Python, TypeScript and curl versions."""

    def assert_api_calls_are_clean(self, fake):
        for call in fake.api_calls():
            self.assertEqual(call.headers.get("authorization"), "Bearer " + TEST_KEY, call.path)
            agent = call.headers.get("user-agent", "")
            self.assertTrue(agent.startswith("deapi-example/"), agent)
        for call in fake.calls:
            if call.path.startswith("/results/"):
                self.assertNotIn("authorization", call.headers)

    def assert_bodies_match_spec(self, fake):
        for call in fake.calls:
            if call.method != "POST" or not call.path.startswith("/api/v2/"):
                continue
            schema = request_schema(call.path)
            body = call.json
            self.assertIsInstance(body, dict, call.path)
            unknown = set(body) - set(schema.get("properties", {}))
            self.assertEqual(unknown, set(), call.path)
            missing = set(schema.get("required", [])) - set(body)
            self.assertEqual(missing, set(), call.path)
        for call in fake.calls:
            if call.method == "GET" and _norm(call.path) in SPEC_PATHS:
                allowed = query_params(SPEC_PATHS[_norm(call.path)])
                self.assertEqual(set(call.query) - allowed, set(), call.path)

    def assert_no_key(self, *texts):
        for text in texts:
            self.assertNotIn("TESTKEYNOTREAL", text)


class NativeJobPythonTest(unittest.TestCase, NativeChecks):
    script = os.path.join(ASSETS, "native_job.py")

    def command(self, *args):
        return [sys.executable, self.script] + list(args)

    def env(self, fake, **extra):
        return dict({"DEAPI_API_KEY": TEST_KEY, "DEAPI_API_URL": fake.base}, **extra)

    def test_quote_only_submits_nothing(self):
        with FakeServer() as fake, tempfile.TemporaryDirectory() as tmp:
            out = run(self.command("a red fox", "--quote"), self.env(fake), cwd=tmp)
            self.assertEqual(out.returncode, 0, out.stderr)
            result = json.loads(out.stdout)
            self.assertTrue(result["quote_only"])
            self.assertEqual(result["quoted_price"], fake.price)
            self.assertEqual([(c.method, c.path) for c in fake.calls],
                             [("GET", "/api/v2/models"),
                              ("POST", "/api/v2/images/generations/price")])
            self.assertEqual(os.listdir(tmp), [])
            self.assert_api_calls_are_clean(fake)
            self.assert_bodies_match_spec(fake)
            self.assert_no_key(out.stdout, out.stderr)

    def test_full_run_downloads_without_key(self):
        with FakeServer() as fake, tempfile.TemporaryDirectory() as tmp:
            fake.pending_polls = 1
            out = run(self.command("a red fox", "--max-cost", "0.01"), self.env(fake), cwd=tmp)
            self.assertEqual(out.returncode, 0, out.stderr)
            result = json.loads(out.stdout)
            self.assertEqual(len(fake.submissions()), 1)
            with open(os.path.join(tmp, result["file"]), "rb") as handle:
                self.assertEqual(handle.read(), PNG)
            self.assertEqual(result["price"], {"amount": fake.price, "is_estimated": False})
            self.assert_api_calls_are_clean(fake)
            self.assert_bodies_match_spec(fake)
            self.assert_no_key(out.stdout, out.stderr)

    def test_quote_above_ceiling_or_unknown_stops(self):
        for price, basis in ((0.5, None), (0.0, "unavailable")):
            with FakeServer() as fake, tempfile.TemporaryDirectory() as tmp:
                fake.price, fake.estimate_basis = price, basis
                out = run(self.command("a red fox"), self.env(fake), cwd=tmp)
                self.assertEqual(out.returncode, 5, out.stderr)
                self.assertEqual(fake.submissions(), [])

    def test_refuses_foreign_hosts(self):
        for url in ("https://evil.example", "https://api.deapi.ai.evil.example",
                    "http://api.deapi.ai", "https://evil.example/x.deapi.ai"):
            with FakeServer() as fake:
                out = run(self.command("a red fox", "--quote"),
                          {"DEAPI_API_KEY": TEST_KEY, "DEAPI_API_URL": url})
                self.assertEqual(out.returncode, 1, url)
                self.assertIn("refusing", out.stderr)
                self.assertEqual(fake.calls, [])

    def test_errors_by_code_and_no_retry_of_submit(self):
        with FakeServer() as fake, tempfile.TemporaryDirectory() as tmp:
            fake.overrides[("POST", "/api/v2/images/generations")] = (
                422, {"message": "Insufficient balance.", "code": "insufficient_balance",
                      "errors": {"balance": ["Insufficient balance."]}}, {})
            out = run(self.command("a red fox"), self.env(fake), cwd=tmp)
            self.assertEqual(out.returncode, 1)
            self.assertIn("insufficient_balance", out.stderr)
            self.assert_no_key(out.stdout, out.stderr)
        with FakeServer() as fake, tempfile.TemporaryDirectory() as tmp:
            fake.overrides[("POST", "/api/v2/images/generations")] = (
                500, {"message": "Server Error"}, {})
            out = run(self.command("a red fox"), self.env(fake), cwd=tmp)
            self.assertEqual(out.returncode, 1)
            self.assertEqual(len(fake.submissions()), 1)   # a paid POST is not repeated

    def test_rate_limit_minute_is_retried_daily_is_not(self):
        with FakeServer() as fake:
            fake.overrides[("POST", "/api/v2/images/generations/price")] = (
                429, {"message": "Too Many Attempts."},
                {"X-RateLimit-Type": "daily", "Retry-After": "86400"})
            out = run(self.command("a red fox", "--quote"), self.env(fake))
            self.assertEqual(out.returncode, 1)
            self.assertEqual(len([c for c in fake.calls if c.path.endswith("/price")]), 1)
        mod = load_module("native_job", self.script)
        with FakeServer() as fake:
            fake.overrides[("POST", "/api/v2/images/generations/price")] = (
                429, {"message": "Too Many Attempts."},
                {"X-RateLimit-Type": "minute", "Retry-After": "1"})
            client = mod.Client(TEST_KEY, fake.base)
            with mock.patch.object(mod.time, "sleep") as sleep:
                with self.assertRaises(mod.ApiError):
                    client.quote("/api/v2/images/generations", {"prompt": "x"})
            self.assertEqual(sleep.call_count, 3)

    def test_failed_job(self):
        with FakeServer() as fake, tempfile.TemporaryDirectory() as tmp:
            fake.job_error = {"error_code": "PROCESSING_ERROR", "error_reason": "INPUT_TOO_LARGE",
                              "retryable": False, "refunded": True}
            out = run(self.command("a red fox"), self.env(fake), cwd=tmp)
            self.assertEqual(out.returncode, 1)
            self.assertIn("INPUT_TOO_LARGE", out.stderr)
            self.assertIn("retryable=False", out.stderr)

    def test_body_follows_model_card(self):
        mod = load_module("native_job", self.script)
        card = {"slug": "M", "info": {"limits": {"min_width": 256, "max_width": 1024,
                                                 "min_height": 256, "max_height": 1024,
                                                 "resolution_step": 64},
                                      "features": {"supports_steps": True,
                                                   "supports_guidance": False},
                                      "defaults": {"width": 768, "height": 768, "steps": 6}}}
        body = mod.image_body(card, "p", width=1000, height=5000, seed=7)
        self.assertEqual(body, {"prompt": "p", "model": "M", "width": 960, "height": 1024,
                                "seed": 7, "steps": 6})


@unittest.skipUnless(NODE_TS, "Node.js with --experimental-strip-types not installed")
class NativeJobTypeScriptTest(unittest.TestCase, NativeChecks):
    script = os.path.join(ASSETS, "native_job.ts")

    def node(self, fake, *args, cwd=None, url=None):
        return run([NODE, "--experimental-strip-types", "--no-warnings", self.script] + list(args),
                   {"DEAPI_API_KEY": TEST_KEY, "DEAPI_API_URL": url or fake.base}, cwd=cwd)

    def test_quote_only(self):
        with FakeServer() as fake:
            out = self.node(fake, "a red fox", "--quote")
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertTrue(json.loads(out.stdout)["quote_only"])
            self.assertEqual(fake.submissions(), [])
            self.assert_api_calls_are_clean(fake)
            self.assert_bodies_match_spec(fake)

    def test_full_run(self):
        with FakeServer() as fake, tempfile.TemporaryDirectory() as tmp:
            out = self.node(fake, "a red fox", "--max-cost", "0.01", cwd=tmp)
            self.assertEqual(out.returncode, 0, out.stderr)
            result = json.loads(out.stdout)
            with open(os.path.join(tmp, result["file"]), "rb") as handle:
                self.assertEqual(handle.read(), PNG)
            self.assert_api_calls_are_clean(fake)
            self.assert_bodies_match_spec(fake)
            self.assert_no_key(out.stdout, out.stderr)

    def test_ceiling_and_foreign_host(self):
        with FakeServer() as fake:
            fake.price = 0.5
            out = self.node(fake, "a red fox")
            self.assertEqual(out.returncode, 5)
            self.assertEqual(fake.submissions(), [])
        with FakeServer() as fake:
            out = self.node(fake, "a red fox", "--quote", url="https://api.deapi.ai.evil.example")
            self.assertEqual(out.returncode, 1)
            self.assertEqual(fake.calls, [])


@unittest.skipUnless(CURL and SH, "curl or sh not installed")
class NativeJobCurlTest(unittest.TestCase, NativeChecks):
    script = os.path.join(ASSETS, "native_job.sh")

    def sh(self, fake, *args, url=None, cwd=None):
        return run([SH, self.script] + list(args),
                   {"DEAPI_API_KEY": TEST_KEY, "DEAPI_API_URL": url or fake.base}, cwd=cwd)

    def test_steps(self):
        with FakeServer() as fake, tempfile.TemporaryDirectory() as tmp:
            out = self.sh(fake, "models", "txt2img")
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertTrue(json.loads(out.stdout)["data"])
            out = self.sh(fake, "quote")
            self.assertEqual(json.loads(out.stdout)["data"]["price"], fake.price)
            request_id = json.loads(self.sh(fake, "submit").stdout)["data"]["request_id"]
            job = json.loads(self.sh(fake, "status", request_id).stdout)["data"]
            self.assertEqual(job["status"], "done")
            target = os.path.join(tmp, "out.png")
            out = self.sh(fake, "download", job["result_url"], target)
            self.assertEqual(out.returncode, 0, out.stderr)
            with open(target, "rb") as handle:
                self.assertEqual(handle.read(), PNG)
            self.assertEqual(self.sh(fake, "download", job["result_url"], target).returncode, 2)
            self.assert_api_calls_are_clean(fake)
            self.assert_bodies_match_spec(fake)

    def test_http_error_fails(self):
        with FakeServer() as fake:
            fake.overrides[("POST", "/api/v2/images/generations")] = (
                402, {"message": "spend limit", "code": "key_limit_exceeded"}, {})
            out = self.sh(fake, "submit")
            self.assertNotEqual(out.returncode, 0)
            self.assertIn("key_limit_exceeded", out.stdout)

    def test_refuses_foreign_hosts(self):
        for url in ("https://evil.example", "https://evil.example/x.deapi.ai",
                    "https://api.deapi.ai.evil.example", "https://x.deapi.ai@example.com",
                    "http://api.deapi.ai"):
            with FakeServer() as fake:
                out = self.sh(fake, "quote", url=url)
                self.assertEqual(out.returncode, 2, url)
                self.assertEqual(fake.calls, [])


class UsageReportTest(unittest.TestCase, NativeChecks):
    script = os.path.join(ASSETS, "usage_report.py")

    def py(self, fake, *args):
        return run([sys.executable, self.script] + list(args),
                   {"DEAPI_API_KEY": TEST_KEY, "DEAPI_API_URL": fake.base})

    def test_key_report(self):
        with FakeServer() as fake:
            out = self.py(fake, "--group-by", "model", "--days", "30")
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertIn("Kokoro", out.stdout)
            self.assertIn("total", out.stdout)
            usage = [c for c in fake.calls if c.path == "/api/v2/key/usage"][0]
            self.assertEqual(usage.query["group_by"], ["model"])
            self.assert_api_calls_are_clean(fake)
            self.assert_bodies_match_spec(fake)
            self.assert_no_key(out.stdout, out.stderr)

    def test_account_report_needs_admin(self):
        with FakeServer() as fake:
            fake.usage_forbidden = True
            out = self.py(fake, "--account", "--group-by", "key")
            self.assertEqual(out.returncode, 1)
            self.assertIn("ADMIN", out.stderr)
            self.assertEqual(self.py(fake, "--group-by", "key").returncode, 2)

    def test_json(self):
        with FakeServer() as fake:
            out = self.py(fake, "--account", "--json")
            self.assertEqual(json.loads(out.stdout)["currency"], "USD")


# --------------------------------------------------------------------------- OpenAI SDK examples

@unittest.skipUnless(have_openai(), "openai package not installed")
class OpenAIPythonTest(unittest.TestCase):
    def setUp(self):
        # Recent SDKs warn that OpenAI's own video service is closing; not relevant here.
        self._warnings = warnings.catch_warnings()
        self._warnings.__enter__()
        warnings.simplefilter("ignore", DeprecationWarning)

    def tearDown(self):
        self._warnings.__exit__(None, None, None)

    def test_examples(self):
        with FakeServer() as fake, tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(os.environ, {"DEAPI_API_KEY": TEST_KEY,
                                             "DEAPI_OAI_URL": fake.base + "/v1"}):
            mod = load_module("openai_examples", os.path.join(ASSETS, "openai_examples.py"))
            client = mod.make_client()
            self.assertIn("Flux1schnell", mod.list_models(client))
            out = mod.image(client, "fox", os.path.join(tmp, "fox.png"))
            with open(out, "rb") as handle:
                self.assertEqual(handle.read(), PNG)
            mod.speech(client, "hello", os.path.join(tmp, "hi.mp3"))
            audio = os.path.join(tmp, "a.mp3")
            with open(audio, "wb") as handle:
                handle.write(b"ID3fake")
            self.assertEqual(mod.transcribe(client, audio), "Good morning.")
            self.assertEqual(len(mod.embed(client, ["a", "b"])), 2)
            mod.video(client, "boat", os.path.join(tmp, "boat.mp4"), sleep=lambda s: None)
            create = [c for c in fake.calls if c.method == "POST" and c.path == "/v1/videos"][0]
            self.assertTrue(create.headers.get("idempotency-key"))
            image_call = [c for c in fake.calls if c.path == "/v1/images/generations"][0]
            self.assertEqual(image_call.json["response_format"], "b64_json")
            for call in fake.api_calls():
                self.assertEqual(call.headers["authorization"], "Bearer " + TEST_KEY)

    def test_refuses_foreign_base_url(self):
        with mock.patch.dict(os.environ, {"DEAPI_API_KEY": TEST_KEY,
                                          "DEAPI_OAI_URL": "https://api.openai.com/v1"}):
            mod = load_module("openai_examples", os.path.join(ASSETS, "openai_examples.py"))
            with self.assertRaises(SystemExit):
                mod.make_client()

    def run_blocks(self, doc, fake, replace):
        """Execute the Python blocks of a reference page in order, against the fake."""
        namespace = {"__name__": "__doc_blocks__"}
        with tempfile.TemporaryDirectory() as tmp, mock.patch("time.sleep"), \
                mock.patch.dict(os.environ, {"DEAPI_API_KEY": TEST_KEY}), \
                contextlib.redirect_stdout(io.StringIO()):
            with open(os.path.join(tmp, "meeting.mp3"), "wb") as handle:
                handle.write(b"ID3fake")
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                for number, (lang, code) in enumerate(code_blocks(read(doc)), 1):
                    if lang != "python":
                        continue
                    for old, new in replace:
                        code = code.replace(old, new)
                    exec(compile(code, "%s block %d" % (os.path.basename(doc), number), "exec"),
                         namespace)
            finally:
                os.chdir(cwd)
        return namespace

    def test_openai_md_blocks_run(self):
        with FakeServer() as fake:
            self.run_blocks(os.path.join(REFS, "openai.md"), fake,
                            [("https://oai.deapi.ai/v1", fake.base + "/v1")])
            paths = {c.path for c in fake.calls}
            for path in ("/v1/models", "/v1/embeddings", "/v1/images/generations",
                         "/v1/audio/speech", "/v1/audio/transcriptions", "/v1/videos"):
                self.assertIn(path, paths)


class NativeDocBlocksTest(unittest.TestCase, NativeChecks):
    def test_native_md_blocks_run(self):
        doc = os.path.join(REFS, "native-api.md")
        with FakeServer() as fake, tempfile.TemporaryDirectory() as tmp, \
                mock.patch("time.sleep"), mock.patch.dict(os.environ, {"DEAPI_API_KEY": TEST_KEY}), \
                contextlib.redirect_stdout(io.StringIO()):
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                for number, (lang, code) in enumerate(code_blocks(read(doc)), 1):
                    if lang == "python":
                        code = code.replace('"https://api.deapi.ai"', repr(fake.base))
                        code = code.replace('"my-app/1.0"', '"deapi-example/doc"')
                        exec(compile(code, "native-api.md block %d" % number, "exec"),
                             {"__name__": "__doc_blocks__"})
            finally:
                os.chdir(cwd)
            self.assertEqual(len(fake.submissions()), 1)
            self.assertTrue(any(name.endswith(".png") for name in os.listdir(tmp)))
            self.assert_api_calls_are_clean(fake)
            self.assert_bodies_match_spec(fake)


@unittest.skipUnless(NODE_TS and os.path.isdir(os.environ.get("DEAPI_TEST_NODE_MODULES", "")),
                     "Node.js with the openai package (DEAPI_TEST_NODE_MODULES) not available")
class OpenAINodeTest(unittest.TestCase):
    def test_examples(self):
        with FakeServer() as fake, tempfile.TemporaryDirectory() as tmp:
            fake.video_ready_on_create = True
            shutil.copy(os.path.join(ASSETS, "openai_examples.ts"), tmp)
            os.symlink(os.environ["DEAPI_TEST_NODE_MODULES"], os.path.join(tmp, "node_modules"))
            with open(os.path.join(tmp, "a.mp3"), "wb") as handle:
                handle.write(b"ID3fake")
            env = {"DEAPI_API_KEY": TEST_KEY, "DEAPI_OAI_URL": fake.base + "/v1"}

            def node(*args):
                out = run([NODE, "--experimental-strip-types", "--no-warnings",
                           "openai_examples.ts"] + list(args), env, cwd=tmp)
                self.assertEqual(out.returncode, 0, out.stderr)
                return out.stdout

            self.assertIn("Flux1schnell", node("models"))
            node("image", "fox", "fox.png")
            node("speech", "hello", "hi.mp3")
            self.assertIn("Good morning.", node("transcribe", "a.mp3"))
            self.assertIn("2 vectors", node("embed", "a", "b"))
            node("video", "boat", "boat.mp4")
            with open(os.path.join(tmp, "fox.png"), "rb") as handle:
                self.assertEqual(handle.read(), PNG)
            create = [c for c in fake.calls if c.method == "POST" and c.path == "/v1/videos"][0]
            self.assertTrue(create.headers.get("idempotency-key"))
            for call in fake.api_calls():
                self.assertEqual(call.headers["authorization"], "Bearer " + TEST_KEY)


if __name__ == "__main__":
    unittest.main()
