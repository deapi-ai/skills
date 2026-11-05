"""Price quotes, the cost limit, polling, job failures and the status command."""

import os
import unittest

from helpers import CliTest, core


class QuoteTests(CliTest):
    def test_quote_only_submits_nothing(self):
        res = self.run_cli("image", "--prompt", "fox", "--quote")
        self.assertEqual(res.code, 0, res.err)
        self.assertTrue(res.json["quote_only"])
        self.assertEqual(res.json["quoted_price"], 0.01)
        self.assertEqual(self.fake.submissions(), [])
        self.assertEqual(len(self.fake.calls_to("POST", r"/images/generations/price$")), 1)

    def test_price_only_alias(self):
        res = self.run_cli("image", "--prompt", "fox", "--price-only")
        self.assertTrue(res.json["quote_only"])

    def test_quote_precedes_submission(self):
        self.run_cli("image", "--prompt", "fox")
        posts = [c.path for c in self.fake.calls_to("POST")]
        self.assertEqual(posts, ["/api/v2/images/generations/price", "/api/v2/images/generations"])

    def test_over_budget_exits_5(self):
        self.fake.price = 0.30
        res = self.run_cli("image", "--prompt", "fox")
        self.assertEqual(res.code, core.EXIT_OVER_BUDGET)
        self.assertEqual(res.error["price"], 0.30)
        self.assertEqual(res.error["max_cost"], 0.25)
        self.assertEqual(self.fake.submissions(), [])

    def test_user_limit_from_flag_and_env(self):
        self.fake.price = 0.30
        res = self.run_cli("image", "--prompt", "fox", "--max-cost", "0.5")
        self.assertEqual(res.code, 0, res.err)
        res = self.run_cli("image", "--prompt", "fox",
                           env={"DEAPI_API_KEY": "sk-dp-0-TESTKEYNOTREAL", "DEAPI_MAX_COST": "1"})
        self.assertEqual(res.code, 0, res.err)
        res = self.run_cli("image", "--prompt", "fox", "--max-cost", "0.1",
                           env={"DEAPI_API_KEY": "sk-dp-0-TESTKEYNOTREAL", "DEAPI_MAX_COST": "1"})
        self.assertEqual(res.code, core.EXIT_OVER_BUDGET)

    def test_every_paid_result_shows_the_limit_in_force(self):
        env = {"DEAPI_API_KEY": "sk-dp-0-TESTKEYNOTREAL", "DEAPI_MAX_COST": "0.15"}
        res = self.run_cli("image", "--prompt", "fox", env=env)
        self.assertEqual(res.code, 0, res.err)
        self.assertEqual((res.json["max_cost"], res.json["max_cost_source"]),
                         (0.15, "DEAPI_MAX_COST"))
        res = self.run_cli("image", "--prompt", "fox", "--quote", env=env)
        self.assertEqual((res.json["max_cost"], res.json["max_cost_source"]),
                         (0.15, "DEAPI_MAX_COST"))
        res = self.run_cli("image", "--prompt", "fox", "--max-cost", "0.1925")
        self.assertEqual((res.json["max_cost"], res.json["max_cost_source"]),
                         (0.1925, "--max-cost"))
        res = self.run_cli("embed", "hello world")
        self.assertEqual(res.code, 0, res.err)
        self.assertEqual((res.json["max_cost"], res.json["max_cost_source"]), (0.25, "default"))
        self.fake.price = 0.30
        res = self.run_cli("image", "--prompt", "fox", env=env)
        self.assertEqual(res.code, core.EXIT_OVER_BUDGET)
        self.assertEqual((res.error["max_cost"], res.error["max_cost_source"]),
                         (0.15, "DEAPI_MAX_COST"))

    def test_bad_max_cost(self):
        res = self.run_cli("image", "--prompt", "fox", "--max-cost", "-1")
        self.assertEqual(res.code, core.EXIT_USAGE)

    def test_unavailable_estimate_is_unknown(self):
        self.fake.route("POST", r"/price$", (200, {"data": {"price": 0.0, "is_estimated": True,
                                                            "estimate_basis": "unavailable"}}))
        res = self.run_cli("image", "--prompt", "fox")
        self.assertEqual(res.code, core.EXIT_OVER_BUDGET)
        self.assertEqual(self.fake.submissions(), [])

    def test_is_estimated_reported(self):
        self.fake.is_estimated = True
        res = self.run_cli("image", "--prompt", "fox", "--quote")
        self.assertTrue(res.json["is_estimated"])

    def test_booster_price_shape(self):
        res = self.run_cli("boost", "--prompt", "a fox in snow", "--quote")
        self.assertEqual(res.code, 0, res.err)
        self.assertEqual(res.json["quoted_price"], 0.01)
        res = self.run_cli("boost", "--prompt", "a fox in snow")
        self.assertEqual(res.json["result"]["prompt"], "an improved prompt")

    def test_count_sums_quotes_once(self):
        self.fake.price = 0.08
        res = self.run_cli("image", "--prompt", "fox", "--count", "4")
        self.assertEqual(res.code, core.EXIT_OVER_BUDGET)
        self.assertAlmostEqual(res.error["price"], 0.32)
        self.assertEqual(self.fake.submissions(), [])
        self.fake.price = 0.05
        res = self.run_cli("image", "--prompt", "fox", "--count", "3", "--seed", "7")
        self.assertEqual(res.code, 0, res.err)
        seeds = [c.json["seed"] for c in self.fake.submissions()]
        self.assertEqual(seeds, [7, 8, 9])
        self.assertEqual(len(res.json["results"]), 3)
        self.assertEqual(len(self.fake.calls_to("POST", r"/price$")), 2)

    def test_tiktok_needs_duration(self):
        res = self.run_cli("transcribe", "https://www.tiktok.com/@a/video/1")
        self.assertEqual(res.code, core.EXIT_USAGE)
        self.assertEqual(self.fake.calls_to("POST"), [])
        res = self.run_cli("transcribe", "https://www.tiktok.com/@a/video/1", "--duration", "45",
                           "--quote")
        quote = self.fake.calls_to("POST", r"/price$")[-1]
        self.assertEqual(quote.field("platform"), "tiktok")
        self.assertEqual(float(quote.field("duration_seconds")), 45.0)
        self.assertIsNone(quote.field("source_url"))

    def test_local_wav_quoted_by_duration(self):
        clip = self.wav(seconds=3)
        res = self.run_cli("transcribe", clip, "--quote")
        self.assertEqual(res.code, 0, res.err)
        quote = self.fake.calls_to("POST", r"/price$")[-1]
        self.assertEqual(float(quote.field("duration_seconds")), 3.0)
        self.assertEqual(quote.files, {})

    def test_key_limit_warning_for_bigger_jobs(self):
        self.fake.price = 0.2
        self.fake.key = dict(self.fake.key, limit_remaining=0.05)
        res = self.run_cli("image", "--prompt", "fox")
        self.assertIn("remaining spend limit", res.err)


class JobTests(CliTest):
    def test_polling_timeout_exits_9(self):
        self.fake.done_job = lambda rid: {"status": "processing", "progress": 10}
        res = self.run_cli("image", "--prompt", "fox", "--timeout", "30")
        self.assertEqual(res.code, core.EXIT_UNKNOWN_STATE)
        self.assertTrue(res.error["request_id"])
        self.assertIn("status %s --wait" % res.error["request_id"], res.err)
        self.assertLessEqual(max(self.clock.sleeps), 10)

    def test_job_error_fields(self):
        self.fake.done_job = lambda rid: {"status": "error", "error_code": "PROCESSING_ERROR",
                                          "error_reason": "SOURCE_PRIVATE", "retryable": False,
                                          "refunded": True}
        res = self.run_cli("transcribe", "https://youtu.be/abc")
        self.assertEqual(res.code, core.EXIT_JOB_FAILED)
        self.assertEqual(res.error["error_reason"], "SOURCE_PRIVATE")
        self.assertEqual(len(self.fake.submissions()), 1)

    def test_retryable_refunded_retried_once(self):
        self.fake.done_job = lambda rid: {"status": "error", "error_code": "WORKER_ERROR",
                                          "error_reason": "CAPACITY_UNAVAILABLE",
                                          "retryable": True, "refunded": True}
        res = self.run_cli("image", "--prompt", "fox")
        self.assertEqual(res.code, core.EXIT_JOB_FAILED)
        self.assertEqual(len(self.fake.submissions()), 2)
        self.assertEqual(len(self.fake.calls_to("POST", r"/price$")), 2)

    def test_unknown_reason_has_hint(self):
        self.fake.done_job = lambda rid: {"status": "error", "error_code": "WORKER_ERROR"}
        res = self.run_cli("image", "--prompt", "fox")
        self.assertEqual(res.error["error_reason"], "PROCESSING_FAILED")

    def test_final_price_drift_warning(self):
        def job(rid):
            return {"status": "done", "result_url": "https://results.example.test/%s.png" % rid,
                    "price": {"amount": 0.02, "is_estimated": True}}
        self.fake.done_job = job
        res = self.run_cli("image", "--prompt", "fox")
        self.assertEqual(res.code, 0, res.err)
        self.assertIn("above the quote", res.err)
        self.assertTrue(res.json["price_is_estimated"])

    def test_status_command(self):
        rid = "c08a339c-73e5-4d67-a4d5-231302fbff9a"
        self.fake.jobs[rid] = {"status": "done", "result_url": "https://results.example.test/x.png",
                               "price": {"amount": 0.003}}
        res = self.run_cli("status", rid)
        self.assertEqual(res.code, 0, res.err)
        self.assertEqual(res.json["price"], 0.003)
        self.assertNotIn("files", res.json)
        res = self.run_cli("status", "job:" + rid, "--download")
        self.assertTrue(os.path.isfile(res.json["files"][0]))
        self.assertTrue(res.json["files"][0].endswith(rid + ".png"))

    def test_status_rejects_bad_id(self):
        res = self.run_cli("status", "../../etc/passwd")
        self.assertEqual(res.code, core.EXIT_USAGE)
        self.assertEqual(self.fake.calls, [])


if __name__ == "__main__":
    unittest.main()
