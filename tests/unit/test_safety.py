"""Safety checks: the key never follows a redirect, quotes cannot be lowered by flags,
text files and output paths respect the sensitive-location rule, downloads are bounded."""

import importlib.util
import json
import os
import sys
import threading
import unittest
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from helpers import ROOT, TEST_KEY, CliTest, core


# --------------------------------------------------------------- redirects ---

class _Recorder(object):
    """Two loopback servers: `first` redirects to `second`, which records headers."""

    def __init__(self):
        self.seen = {"first": [], "second": []}
        recorder = self

        class Second(BaseHTTPRequestHandler):
            def do_GET(self):
                recorder.seen["second"].append(self.headers.get("Authorization"))
                body = json.dumps({"data": {"ok": True}}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.second = ThreadingHTTPServer(("127.0.0.1", 0), Second)
        target = "http://localhost:%d/elsewhere" % self.second.server_address[1]

        class First(BaseHTTPRequestHandler):
            def do_GET(self):
                recorder.seen["first"].append(self.headers.get("Authorization"))
                self.send_response(302)
                self.send_header("Location", target)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args):
                pass

        self.first = ThreadingHTTPServer(("127.0.0.1", 0), First)
        self.base = "http://127.0.0.1:%d" % self.first.server_address[1]
        for server in (self.first, self.second):
            threading.Thread(target=server.serve_forever, daemon=True).start()

    def close(self):
        for server in (self.first, self.second):
            server.shutdown()
            server.server_close()


def _load_asset(name):
    path = os.path.join(ROOT, "deapi-api", "assets", name)
    spec = importlib.util.spec_from_file_location("asset_" + name.replace(".", "_"), path)
    module = importlib.util.module_from_spec(spec)
    saved, sys.dont_write_bytecode = sys.dont_write_bytecode, True   # keep assets/ clean
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = saved
    return module


class RedirectTest(unittest.TestCase):
    def setUp(self):
        self.servers = _Recorder()
        self.addCleanup(self.servers.close)
        patcher = mock.patch.dict(os.environ, {"NO_PROXY": "*", "no_proxy": "*"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def assert_key_stayed(self):
        self.assertEqual(self.servers.seen["first"], ["Bearer " + TEST_KEY])
        self.assertEqual(self.servers.seen["second"], [None])

    def test_script_key_not_sent_to_redirect_target(self):
        ctx = core.Context(base_url=self.servers.base, environ={"DEAPI_API_KEY": TEST_KEY})
        ctx.request("GET", ctx.url("/api/v2/key"))
        self.assert_key_stayed()

    def test_native_job_example_key_not_sent_to_redirect_target(self):
        client = _load_asset("native_job.py").Client(TEST_KEY, api_url=self.servers.base)
        client.request("GET", "/api/v2/key")
        self.assert_key_stayed()

    def test_usage_report_example_key_not_sent_to_redirect_target(self):
        _load_asset("usage_report.py").get(self.servers.base, TEST_KEY, "/api/v2/key")
        self.assert_key_stayed()


# ------------------------------------------------------- quotes and limits ---

class QuoteFloorTest(CliTest):
    def last_quote(self, path=r"/price$"):
        return self.fake.calls_to("POST", path)[-1]

    def test_measured_length_wins_over_a_lower_duration(self):
        path = self.wav("long.wav", seconds=120, rate=1000)
        res = self.run_cli("transcribe", path, "--duration", "1", "--quote")
        self.assertEqual(res.code, 0, res.err)
        self.assertAlmostEqual(float(self.last_quote().field("duration_seconds")), 120, places=0)
        self.assertEqual(res.json["duration_from"], "file header")
        self.assertIn("differs", res.err)

    def test_longer_duration_raises_the_quote(self):
        path = self.wav("short.wav", seconds=2, rate=1000)
        res = self.run_cli("transcribe", path, "--duration", "30", "--quote")
        self.assertEqual(res.code, 0, res.err)
        self.assertAlmostEqual(float(self.last_quote().field("duration_seconds")), 30, places=0)

    def test_duration_ignored_for_links_other_than_tiktok(self):
        res = self.run_cli("transcribe", "https://www.youtube.com/watch?v=abc", "--duration",
                           "1", "--quote")
        self.assertEqual(res.code, 0, res.err)
        quote = self.last_quote()
        self.assertIsNone(quote.field("duration_seconds"))
        self.assertEqual(quote.field("source_url"), "https://www.youtube.com/watch?v=abc")
        self.assertIn("ignored", res.err)

    def test_tiktok_still_uses_the_given_length(self):
        res = self.run_cli("transcribe", "https://www.tiktok.com/@u/video/1", "--duration",
                           "40", "--quote")
        self.assertEqual(res.code, 0, res.err)
        self.assertEqual(self.last_quote().field("platform"), "tiktok")

    def test_video_enhance_fee_counts_against_the_limit(self):
        self.fake.price = 0.2   # video 0.2 + booster 0.2 > 0.25
        res = self.run_cli("video", "--prompt", "waves at sunset", "--enhance")
        self.assertEqual(res.code, core.EXIT_OVER_BUDGET, res.err)
        self.assertEqual(self.fake.submissions(), [])
        self.assertTrue(self.fake.calls_to("POST", r"/prompts/enhancements/price$"))

    def test_video_enhance_quote_reports_the_total(self):
        self.fake.price = 0.2
        res = self.run_cli("video", "--prompt", "waves at sunset", "--enhance", "--quote")
        self.assertEqual(res.code, 0, res.err)
        self.assertAlmostEqual(res.json["quoted_total_with_enhance"], 0.4)
        self.assertFalse(res.json["within_max_cost"])

    def test_animate_enhance_fee_counts_against_the_limit(self):
        self.fake.price = 0.2
        res = self.run_cli("animate", self.png(), "--prompt", "slow pan", "--enhance")
        self.assertEqual(res.code, core.EXIT_OVER_BUDGET, res.err)
        self.assertEqual(self.fake.submissions(), [])

    def test_raised_limit_is_visible(self):
        res = self.run_cli("image", "--prompt", "a fox", "--max-cost", "1", "--quote")
        self.assertEqual(res.code, 0, res.err)
        self.assertTrue(res.json["max_cost_raised"])
        self.assertIn("above the default", res.err)
        res = self.run_cli("image", "--prompt", "a fox", "--quote")
        self.assertNotIn("max_cost_raised", res.json)


# ---------------------------------------------------------- local files ---

class SensitiveFilesTest(CliTest):
    def test_text_files_in_sensitive_folders_are_refused(self):
        secret = self.write(os.path.join(".ssh", "notes.txt"), b"hello world, ten chars")
        for argv in (["embed", "--input-file", secret],
                     ["tts", "--text-file", secret],
                     ["music", "--caption", "calm piano", "--lyrics-file", secret]):
            res = self.run_cli(*(argv + ["--quote"]))
            self.assertEqual(res.code, core.EXIT_USAGE, argv)
            self.assertIn("--allow-hidden-path", res.error["message"])
        self.assertEqual(self.fake.calls_to("POST"), [])

    def test_allow_hidden_path_lets_a_named_text_file_through(self):
        secret = self.write(".notes.txt", b"hello world")
        res = self.run_cli("embed", "--input-file", secret, "--allow-hidden-path", "--quote")
        self.assertEqual(res.code, 0, res.err)

    def test_large_text_file_is_refused(self):
        path = self.write("big.txt", b"a" * (core.MAX_TEXT_FILE_BYTES + 1))
        res = self.run_cli("embed", "--input-file", path, "--quote")
        self.assertEqual(res.code, core.EXIT_USAGE)

    def test_link_into_a_sensitive_folder_is_refused(self):
        target = self.png(os.path.join(".ssh", "id.png"))
        link = self.path("photo.png")
        try:
            os.symlink(target, link)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not available")
        with self.assertRaises(core.DeapiError) as ctx:
            core.check_file(link, "image", "image")
        self.assertEqual(ctx.exception.code, core.EXIT_USAGE)

    def test_output_into_a_sensitive_folder_is_refused_before_any_call(self):
        res = self.run_cli("image", "--prompt", "a fox",
                           "--output", self.path(os.path.join(".ssh", "fox.png")))
        self.assertEqual(res.code, core.EXIT_USAGE)
        self.assertEqual(self.fake.calls_to("POST"), [])   # not even a quote

    def test_output_with_tilde_is_expanded(self):
        env = dict(self.env, HOME=self.tmp, USERPROFILE=self.tmp)
        res = self.run_cli("image", "--prompt", "a fox", "--output", "~/fox.png", env=env)
        self.assertEqual(res.code, 0, res.err)
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "fox.png")))
        self.assertFalse(os.path.exists(os.path.join(os.getcwd(), "~")))

    def test_file_system_error_is_json(self):
        blocker = self.write("not-a-dir", b"x")
        self.fake.result_text = "some text"
        res = self.run_cli("ocr", self.png(), "--output",
                           os.path.join(blocker, "sub", "scan.txt"))
        self.assertEqual(res.code, core.EXIT_ERROR, res.err)
        self.assertIn("File system error", res.error["message"])
        self.assertNotIn("Traceback", res.err)


# -------------------------------------------------------------- results ---

class DownloadTest(CliTest):
    def out_files(self):
        out = os.path.join(self.tmp, "out")
        return sorted(os.listdir(out)) if os.path.isdir(out) else []

    def test_unknown_type_never_takes_an_executable_extension(self):
        self.fake.result_ext = "sh"
        res = self.run_cli("image", "--prompt", "a fox")
        self.assertEqual(res.code, 0, res.err)
        self.assertTrue(res.json["files"][0].endswith(".png"), res.json["files"])

    def test_oversized_download_stops_and_cleans_up(self):
        with mock.patch.object(core, "MAX_DOWNLOAD_BYTES", 10):
            res = self.run_cli("image", "--prompt", "a fox")
        self.assertEqual(res.code, core.EXIT_ERROR)
        self.assertIn("larger than", res.error["message"])
        self.assertEqual([f for f in self.out_files() if f.endswith(".part")], [])

    def test_count_failure_keeps_finished_results(self):
        ids = []

        def submit(call):
            rid = str(uuid.uuid4())
            ids.append(rid)
            self.fake.jobs[rid] = self.fake.done_job(rid) if len(ids) == 1 else {
                "status": "error", "error_code": "JOB_FAILED", "error_reason": "MODEL_ERROR"}
            return 200, {"data": {"request_id": rid}}

        self.fake.route("POST", r"/api/v2/images/generations$", submit)
        res = self.run_cli("image", "--prompt", "a fox", "--count", "2")
        self.assertEqual(res.code, core.EXIT_JOB_FAILED, res.err)
        done = res.error["completed_results"]
        self.assertEqual([r["request_id"] for r in done], ids[:1])
        self.assertTrue(os.path.isfile(done[0]["files"][0]))


# --------------------------------------------------------------- misc ---

class ErrorMappingTest(unittest.TestCase):
    def test_403_without_json_is_not_a_suspended_account(self):
        err = core.map_http_error(403, None, {}, "GET", "/api/v2/key")
        self.assertEqual(err.code, core.EXIT_ERROR)
        self.assertIn("blocked before", err.message)
        err = core.map_http_error(403, {"message": "Client account is suspended."}, {})
        self.assertEqual(err.code, core.EXIT_SUSPENDED)

    def test_experimental_tag_marks_a_partner_model(self):
        self.assertTrue(core.is_partner({"status": "standard_model", "tags": ["experimental"]}))
        self.assertFalse(core.is_partner({"status": "standard_model", "tags": []}))


if __name__ == "__main__":
    unittest.main()
