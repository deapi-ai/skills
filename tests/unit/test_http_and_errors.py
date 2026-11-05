"""HTTP client: retries, error mapping by `code`, key handling and redaction."""

import socket
import unittest
import urllib.error

from helpers import TEST_KEY, CliTest, core


def boom(call):
    raise urllib.error.URLError(ConnectionResetError("connection reset"))


class RetryTests(CliTest):
    def test_get_retried_on_5xx(self):
        self.fake.route("GET", r"/api/v2/models", (503, {"message": "busy"}), times=2)
        res = self.run_cli("models")
        self.assertEqual(res.code, 0, res.err)
        self.assertEqual(len(self.fake.calls_to("GET", r"/api/v2/models$")), 3)

    def test_get_retried_on_network_error(self):
        self.fake.route("GET", r"/api/v2/models", boom, times=1)
        res = self.run_cli("models")
        self.assertEqual(res.code, 0, res.err)

    def test_get_gives_up_after_retries(self):
        self.fake.route("GET", r"/api/v2/models", (500, {"message": "down"}))
        res = self.run_cli("models")
        self.assertEqual(res.code, core.EXIT_ERROR)
        self.assertEqual(len(self.fake.calls_to("GET", r"/api/v2/models$")),
                         core.MAX_RETRIES + 1)

    def test_price_quote_retried_on_5xx(self):
        self.fake.route("POST", r"/generations/price$", (502, b"bad gateway"), times=1)
        res = self.run_cli("image", "--prompt", "fox", "--quote")
        self.assertEqual(res.code, 0, res.err)

    def test_submit_not_retried_on_5xx(self):
        self.fake.route("POST", r"/images/generations$", (500, {"message": "oops"}))
        res = self.run_cli("image", "--prompt", "fox")
        self.assertEqual(res.code, core.EXIT_UNKNOWN_STATE)
        self.assertEqual(len(self.fake.submissions()), 1)
        self.assertIn("may have been accepted", res.err)

    def test_submit_not_retried_on_dropped_connection(self):
        self.fake.route("POST", r"/images/generations$", boom)
        res = self.run_cli("image", "--prompt", "fox")
        self.assertEqual(res.code, core.EXIT_UNKNOWN_STATE)
        self.assertEqual(len(self.fake.submissions()), 1)

    def test_submit_dns_failure_is_not_unknown_state(self):
        def dns(call):
            raise urllib.error.URLError(socket.gaierror("name not known"))
        self.fake.route("POST", r"/images/generations$", dns)
        res = self.run_cli("image", "--prompt", "fox")
        self.assertEqual(res.code, core.EXIT_ERROR)

    def test_submit_retried_on_429(self):
        self.fake.route("POST", r"/images/generations$",
                        (429, {"message": "Too Many Attempts."},
                         {"Retry-After": "3", "X-RateLimit-Type": "minute"}), times=1)
        res = self.run_cli("image", "--prompt", "fox")
        self.assertEqual(res.code, 0, res.err)
        self.assertEqual(len(self.fake.submissions()), 2)
        self.assertIn(3, self.clock.sleeps)

    def test_daily_limit_not_waited(self):
        self.fake.route("POST", r"/images/generations$",
                        (429, {"message": "Too Many Attempts."},
                         {"Retry-After": "86400", "X-RateLimit-Type": "daily",
                          "X-RateLimit-Daily-Reset": "2026-01-02T00:00:00Z"}))
        res = self.run_cli("image", "--prompt", "fox")
        self.assertEqual(res.code, core.EXIT_RATE_LIMIT)
        self.assertEqual(len(self.fake.submissions()), 1)
        self.assertEqual(self.clock.sleeps, [])
        self.assertEqual(res.error.get("rate_limit_type"), "daily")


class ErrorMappingTests(CliTest):
    def submit_error(self, status, body, headers=None):
        self.fake.route("POST", r"/images/generations$", (status, body, headers or {}))
        return self.run_cli("image", "--prompt", "fox")

    def test_insufficient_balance(self):
        res = self.submit_error(422, {"message": "Not enough funds.", "code": "insufficient_balance",
                                      "errors": {"balance": ["Insufficient balance."]}})
        self.assertEqual(res.code, core.EXIT_BALANCE)

    def test_key_limit_exceeded(self):
        res = self.submit_error(402, {"message": "Limit reached.", "code": "key_limit_exceeded",
                                      "resets_at": "2026-01-02T00:00:00+00:00"})
        self.assertEqual(res.code, core.EXIT_KEY_LIMIT)
        self.assertEqual(res.error["resets_at"], "2026-01-02T00:00:00+00:00")

    def test_ip_not_allowed(self):
        res = self.submit_error(403, {"message": "IP.", "code": "ip_not_allowed"})
        self.assertEqual(res.code, core.EXIT_FORBIDDEN)

    def test_insufficient_permissions(self):
        res = self.submit_error(403, {"message": "No.", "code": "insufficient_permissions"})
        self.assertEqual(res.code, core.EXIT_FORBIDDEN)

    def test_suspended_account_has_no_code(self):
        res = self.submit_error(403, {"message": "Client account is suspended."})
        self.assertEqual(res.code, core.EXIT_SUSPENDED)

    def test_endpoint_gone(self):
        res = self.submit_error(410, {"message": "Removed.", "successor": "https://api.deapi.ai/x"})
        self.assertEqual(res.code, core.EXIT_GONE)
        self.assertEqual(res.error["successor"], "https://api.deapi.ai/x")
        self.assertIn("outdated", res.err)

    def test_auth_codes(self):
        for code in ("missing_key", "invalid_key", "key_expired", "key_revoked"):
            self.fake = __import__("fakeapi").FakeAPI()
            res = self.submit_error(401, {"message": "Unauthenticated.", "code": code})
            self.assertEqual(res.code, core.EXIT_AUTH, code)
            self.assertNotIn("paste it", res.err.lower())

    def test_unknown_code_falls_back_to_status(self):
        res = self.submit_error(422, {"message": "Invalid.", "code": "something_new",
                                      "errors": {"width": ["Too big."]}})
        self.assertEqual(res.code, core.EXIT_VALIDATION)
        self.assertIn("width: Too big.", res.err)

    def test_model_not_on_plan(self):
        res = self.submit_error(422, {"message": "Invalid.", "errors": {
            "model": ["This model is not available on your plan. Please upgrade to access it."]}})
        self.assertEqual(res.code, core.EXIT_VALIDATION)
        self.assertIn("plan", res.error["hint"])

    def test_bare_validation_map(self):
        self.fake.route("POST", r"/prompts/enhancements$", (422, {"type": ["The type field is required."]}))
        res = self.run_cli("boost", "--prompt", "a fox in snow")
        self.assertEqual(res.code, core.EXIT_VALIDATION)
        self.assertIn("type: The type field is required.", res.err)

    def test_unit_mapping_table(self):
        cases = [(422, "insufficient_balance", 6), (402, "key_limit_exceeded", 12),
                 (403, "ip_not_allowed", 4), (403, None, 11), (410, None, 13),
                 (429, "rate_limited", 10), (401, "invalid_key", 3), (404, None, 1),
                 (500, None, 1)]
        for status, code, expected in cases:
            payload = {"message": "x"}
            if code:
                payload["code"] = code
            err = core.map_http_error(status, payload, {}, "POST", "/api/v2/x")
            self.assertEqual(err.code, expected, (status, code))


class KeyTests(CliTest):
    def test_missing_key(self):
        res = self.run_cli("models", env={})
        self.assertEqual(res.code, core.EXIT_AUTH)
        self.assertIn("DEAPI_API_KEY", res.err)
        self.assertEqual(self.fake.calls, [])

    def test_key_sent_as_is(self):
        self.run_cli("models")
        self.assertEqual(self.fake.calls[0].headers["authorization"], "Bearer " + TEST_KEY)

    def test_key_whitespace_trimmed(self):
        self.run_cli("models", env={"DEAPI_API_KEY": "  %s\n" % TEST_KEY})
        self.assertEqual(self.fake.calls[0].headers["authorization"], "Bearer " + TEST_KEY)

    def test_user_agent(self):
        self.run_cli("models")
        self.assertTrue(self.fake.calls[0].headers["user-agent"].startswith("deapi-skill/"))

    def test_key_never_printed(self):
        scenarios = [(401, {"message": "Bad key %s" % TEST_KEY, "code": "invalid_key"}),
                     (403, {"message": "No %s" % TEST_KEY, "code": "ip_not_allowed"}),
                     (422, {"message": "Invalid", "errors": {"x": [TEST_KEY]}}),
                     (500, {"message": "Server %s" % TEST_KEY})]
        for status, body in scenarios:
            self.fake = __import__("fakeapi").FakeAPI()
            self.fake.route("GET", r"/api/v2/models", (status, body))
            res = self.run_cli("models")
            self.assertNotEqual(res.code, 0)
            self.assertNotIn(TEST_KEY, res.out + res.err, status)

        def net(call):
            raise urllib.error.URLError(OSError("refused for " + TEST_KEY))
        self.fake = __import__("fakeapi").FakeAPI()
        self.fake.route("GET", r"/api/v2/models", net)
        res = self.run_cli("models")
        self.assertNotIn(TEST_KEY, res.out + res.err)

    def test_key_file(self):
        good = self.write("key.txt", (TEST_KEY + "\n").encode())
        res = self.run_cli("models", "--key-file", good, env={})
        self.assertEqual(res.code, 0, res.err)
        self.assertEqual(self.fake.calls[0].headers["authorization"], "Bearer " + TEST_KEY)

    def test_key_file_with_many_lines_is_refused_silently(self):
        bad = self.write("notes.txt", b"secret-line-one\nsecret-line-two\n")
        res = self.run_cli("models", "--key-file", bad, env={})
        self.assertEqual(res.code, core.EXIT_AUTH)
        self.assertNotIn("secret-line", res.out + res.err)
        self.assertEqual(self.fake.calls, [])

    def test_foreign_base_url_sends_nothing(self):
        for url in ("https://evil.example", "http://api.deapi.ai", "https://deapi.ai.evil.example",
                    "https://user:pw@api.deapi.ai"):  # leak-scan: allow
            res = self.run_cli("models", "--base-url", url)
            self.assertEqual(res.code, core.EXIT_USAGE, url)
        self.assertEqual(self.fake.calls, [])

    def test_base_url_allowed_hosts(self):
        for url in ("https://api.deapi.ai", "http://127.0.0.1:8080", "http://localhost:9"):
            self.assertTrue(core.validate_base_url(url))

    def test_no_authorization_on_result_download(self):
        res = self.run_cli("image", "--prompt", "fox")
        self.assertEqual(res.code, 0, res.err)
        downloads = [c for c in self.fake.calls if c.host == "results.example.test"]
        self.assertEqual(len(downloads), 1)
        self.assertNotIn("authorization", downloads[0].headers)
        api_calls = [c for c in self.fake.calls if c.host == "api.deapi.ai"]
        self.assertTrue(all("authorization" in c.headers for c in api_calls))


if __name__ == "__main__":
    unittest.main()
