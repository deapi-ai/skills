"""Tests for the shared commands: models, status, account, doctor, embed, boost."""

import json
import os
import unittest

from helpers import TEST_KEY, CliTest, core, fakeapi

RATE_HEADERS = {"X-RateLimit-Limit": "120", "X-RateLimit-Remaining": "119",
                "X-RateLimit-Reset": "1800000000", "X-RateLimit-Source": "static",
                "X-RateLimit-Policy": "120;w=60"}


class ModelsTest(CliTest):
    def test_summary_lists_commands_and_marks_partner_models(self):
        res = self.run_cli("models", "--type", "image")
        self.assertEqual(res.code, 0, res.err)
        self.assertEqual(self.fake.calls[0].query.get("filter[inference_types]"), "txt2img")
        by_slug = {m["slug"]: m for m in res.json}
        self.assertIn("image", by_slug["Flux_2_Klein_4B_BF16"]["commands"])
        self.assertIn("edit", by_slug["Flux_2_Klein_4B_BF16"]["commands"])
        self.assertTrue(by_slug["partner-image-example"]["partner"])
        self.assertIn("estimate", by_slug["partner-image-example"]["note"])
        self.assertNotIn("info", by_slug["Flux1schnell"])

    def test_task_alias_and_raw_inference_type(self):
        self.assertEqual(self.run_cli("models", "--type", "stt").code, 0)
        self.assertIn("audio_file2text", self.fake.calls[-1].query["filter[inference_types]"])
        res = self.run_cli("models", "--type", "txt2embedding")
        self.assertEqual([m["slug"] for m in res.json], ["Bge_M3_FP16"])

    def test_bad_type_is_a_usage_error_without_requests(self):
        res = self.run_cli("models", "--type", "image; rm -rf /")
        self.assertEqual(res.code, core.EXIT_USAGE)
        self.assertEqual(self.fake.calls, [])

    def test_details_and_single_card(self):
        res = self.run_cli("models", "--type", "embed", "--details")
        self.assertEqual(res.json[0]["info"]["limits"]["max_input_tokens"], 8192)
        res = self.run_cli("models", "Kokoro")
        self.assertEqual(res.code, 0)
        self.assertEqual(res.json["slug"], "Kokoro")
        self.assertIn("info", res.json)

    def test_unknown_slug_exits_7(self):
        res = self.run_cli("models", "NoSuchModel")
        self.assertEqual(res.code, core.EXIT_VALIDATION)
        self.assertIn("never guess", res.error["hint"])

    def test_oai_lists_ids(self):
        res = self.run_cli("models", "--oai")
        self.assertEqual(res.code, 0, res.err)
        self.assertEqual(res.json, ["Flux1schnell"])
        call = self.fake.calls[0]
        self.assertEqual((call.host, call.path), ("oai.deapi.ai", "/v1/models"))
        res = self.run_cli("models", "--oai", "--details")
        self.assertEqual(res.json[0]["object"], "model")

    def test_oai_with_type_is_refused(self):
        res = self.run_cli("models", "--oai", "--type", "image")
        self.assertEqual(res.code, core.EXIT_USAGE)
        self.assertEqual(self.fake.calls, [])


class StatusTest(CliTest):
    RID = "11111111-2222-3333-4444-555555555555"

    def test_done_without_download_only_reports(self):
        self.fake.jobs[self.RID] = self.fake.done_job(self.RID)
        res = self.run_cli("status", self.RID)
        self.assertEqual(res.code, 0, res.err)
        self.assertTrue(res.json["has_file"])
        self.assertIn("--download", res.json["hint"])
        self.assertEqual([c for c in self.fake.calls if c.host == fakeapi.RESULTS_HOST], [])

    def test_download_never_sends_the_key(self):
        self.fake.jobs[self.RID] = self.fake.done_job(self.RID)
        res = self.run_cli("status", "job:" + self.RID, "--download")
        self.assertEqual(res.code, 0, res.err)
        self.assertTrue(os.path.isfile(res.json["files"][0]))
        download = [c for c in self.fake.calls if c.host == fakeapi.RESULTS_HOST][0]
        self.assertNotIn("authorization", download.headers)

    def test_format_implies_download_of_alt_version(self):
        job = self.fake.done_job(self.RID)
        job["results_alt_formats"] = {"webp": "https://%s/%s.webp" % (fakeapi.RESULTS_HOST,
                                                                      self.RID), "jpg": None}
        self.fake.jobs[self.RID] = job
        res = self.run_cli("status", self.RID, "--format", "webp")
        self.assertEqual(res.code, 0, res.err)
        self.assertEqual(res.json["alt_formats"], ["webp"])
        self.assertTrue(res.json["files"][0].endswith(".webp"))

    def test_running_job_gives_a_hint_and_downloads_nothing(self):
        self.fake.jobs[self.RID] = {"status": "processing", "progress": 40, "price": None,
                                    "preview": "opaque-preview"}
        res = self.run_cli("status", self.RID, "--download")
        self.assertEqual(res.code, 0, res.err)
        self.assertEqual(res.json["status"], "processing")
        self.assertEqual(res.json["progress_preview"], "opaque-preview")
        self.assertIn("do not submit", res.json["hint"])
        self.assertIn("nothing to download", res.err)

    def test_wait_until_done_with_text_result(self):
        self.fake.jobs[self.RID] = [{"status": "pending"}, {"status": "processing"},
                                    {"status": "done", "result": "hello",
                                     "price": {"amount": 0.002, "is_estimated": True}}]
        res = self.run_cli("status", self.RID, "--wait")
        self.assertEqual(res.code, 0, res.err)
        self.assertEqual(res.json["preview"], "hello")
        self.assertTrue(res.json["price_is_estimated"])
        self.assertTrue(res.json["text_file"].endswith(".txt"))

    def test_failed_job_exits_8(self):
        self.fake.jobs[self.RID] = {"status": "error", "error_code": "WORKER_ERROR",
                                    "error_reason": "SOURCE_PRIVATE", "retryable": False,
                                    "refunded": True}
        res = self.run_cli("status", self.RID)
        self.assertEqual(res.code, core.EXIT_JOB_FAILED)
        self.assertEqual(res.error["error_reason"], "SOURCE_PRIVATE")

    def test_unknown_job_hides_the_raw_server_message(self):
        self.fake.route("GET", r"/api/v2/jobs/", (404, {"message": "No query results for "
                                                       "model [ExampleRecord]."}))
        res = self.run_cli("status", self.RID)
        self.assertEqual(res.code, core.EXIT_ERROR)
        self.assertIn("No job", res.error["message"])
        self.assertNotIn("ExampleRecord", res.out + res.err)

    def test_bad_id(self):
        self.assertEqual(self.run_cli("status", "../etc").code, core.EXIT_USAGE)
        self.assertEqual(self.fake.calls, [])


class AccountTest(CliTest):
    def test_api_role_shows_limit_without_private_fields(self):
        res = self.run_cli("account")
        self.assertEqual(res.code, 0, res.err)
        self.assertIsNone(res.json["balance"])
        self.assertIn("ADMIN", res.json["note"])
        self.assertEqual(res.json["key"]["limit_remaining"], 3.75)
        for field in ("preview", "name", "id", "ip_whitelist", "created_at", "last_used_at"):
            self.assertNotIn(field, res.json["key"])
        self.assertNotIn("sk-dp-1", res.out)
        self.assertNotIn("example key", res.out)
        self.assertEqual(self.fake.calls_to("GET", r"/account/"), [])
        usage = self.fake.calls_to("GET", r"/api/v2/key/usage$")[0]
        self.assertEqual(usage.query["group_by"], "type")
        self.assertRegex(usage.query["from"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")

    def test_balance_alias_and_admin_balance(self):
        self.fake.key = dict(self.fake.key, role="admin")
        self.fake.route("GET", r"/api/v2/account/usage", (200, {"data": {
            "from": "a", "to": "b", "currency": "USD",
            "totals": {"cost": 2.0, "requests": 3, "errors": 0},
            "groups": [{"key": "images.generations", "cost": 2.0, "requests": 3,
                        "errors": 0}]}}))
        res = self.run_cli("balance")
        self.assertEqual(res.code, 0, res.err)
        self.assertEqual(res.json["balance"], 12.5)
        self.assertEqual(res.json["account_usage"]["by_type"][0]["type"], "images.generations")

    def test_period_limits_are_errors_not_clamps(self):
        for argv in (["--days", "91"], ["--days", "0"], ["--from", "2000-01-01"],
                     ["--from", "yesterday"], ["--days", "3", "--from", "2026-01-01"]):
            res = self.run_cli("account", *argv)
            self.assertEqual(res.code, core.EXIT_USAGE, argv)
        self.assertEqual(self.fake.calls, [])

    def test_from_to_are_sent_as_utc(self):
        args = type("A", (), {"days": None, "date_from": "2026-03-01",
                              "date_to": "2026-03-10T12:00:00+02:00"})()
        now = core_datetime(2026, 4, 1)
        start, end = misc_module()._usage_period(args, now=now)
        self.assertEqual((start, end), ("2026-03-01T00:00:00Z", "2026-03-10T10:00:00Z"))

    def test_usage_failure_is_reported_not_fatal(self):
        self.fake.route("GET", r"/api/v2/key/usage", (500, {"message": "boom"}))
        res = self.run_cli("account")
        self.assertEqual(res.code, 0, res.err)
        self.assertIn("key_usage_error", res.json)


def misc_module():
    from deapi_lib.commands import misc
    return misc


def core_datetime(*parts):
    import datetime
    return datetime.datetime(*parts)


class DoctorTest(CliTest):
    def test_missing_key_is_reported_without_requests(self):
        res = self.run_cli("doctor", env={})
        self.assertEqual(res.code, 0)
        self.assertFalse(res.json["key_set"])
        self.assertFalse(res.json["ok"])
        self.assertIn("DEAPI_API_KEY", " ".join(res.json["warnings"]))
        self.assertIn("python", res.json)
        self.assertEqual(self.fake.calls, [])

    def test_report_with_limits_and_no_key_material(self):
        self.fake.route("GET", r"/api/v2/key$", (200, {"data": self.fake.key}, RATE_HEADERS))
        self.fake.route("GET", r"/api/v2/models", (200, {"data": [], "meta": {"total": 17}},
                                                   {"X-RateLimit-Limit": "600",
                                                    "X-RateLimit-Source": "tier"}))
        res = self.run_cli("doctor")
        self.assertEqual(res.code, 0, res.err)
        out = res.json
        self.assertTrue(out["key_set"])
        self.assertEqual(out["key_source"], "DEAPI_API_KEY")
        self.assertEqual(out["api"], "ok")
        self.assertEqual(out["models_available"], 17)
        self.assertEqual(out["rate_limits"]["account_endpoints"]["limit"], 120)
        self.assertEqual(out["rate_limits"]["account_endpoints"]["policy"], "120;w=60")
        self.assertEqual(out["rate_limits"]["account_endpoints"]["reset_at"],
                         "2027-01-15T08:00:00Z")
        self.assertEqual(out["rate_limits"]["models_and_prices"]["source"], "tier")
        self.assertEqual(out["oai"]["status"], "ok")
        self.assertEqual(out["warnings"], [])
        self.assertTrue(out["ok"])
        self.assertNotIn("preview", out["key"])
        self.assertNotIn(TEST_KEY, res.out + res.err)
        self.assertNotIn("sk-dp-1", res.out)
        self.assertEqual(self.fake.calls_to("GET", r"/account/balance"), [])

    def test_admin_without_limit_warns_and_shows_balance(self):
        self.fake.key = dict(self.fake.key, role="admin", limit=None, limit_remaining=None,
                             limit_used=None, reset_interval="none", limit_resets_at=None)
        res = self.run_cli("doctor")
        self.assertEqual(res.code, 0, res.err)
        text = " ".join(res.json["warnings"])
        self.assertIn("ADMIN key", text)
        self.assertIn("no spend limit", text)
        self.assertEqual(res.json["balance"], 12.5)
        self.assertFalse(res.json["ok"])
        self.assertIsNone(res.json["rate_limits"])

    def test_low_remaining_limit_and_expiry_warn(self):
        self.fake.key = dict(self.fake.key, limit_remaining=0.1,
                             expires_at=(core_now_plus_days(2)))
        res = self.run_cli("doctor")
        text = " ".join(res.json["warnings"])
        self.assertIn("0.1 USD", text)
        self.assertIn("expires", text)

    def test_rejected_key_exits_3_without_echoing_it(self):
        self.fake.route("GET", r"/api/v2/key$", (401, {"message": "Unauthenticated.",
                                                       "code": "invalid_key"}))
        res = self.run_cli("doctor")
        self.assertEqual(res.code, core.EXIT_AUTH)
        self.assertEqual(res.error["api_code"], "invalid_key")
        self.assertEqual(res.error["report"]["api"], "failed")
        self.assertNotIn(TEST_KEY, res.out + res.err)

    def test_oai_failure_is_a_warning(self):
        self.fake.route("GET", r"/v1/models", (404, {"error": {"message": "nope"}}))
        res = self.run_cli("doctor")
        self.assertEqual(res.code, 0, res.err)
        self.assertEqual(res.json["oai"]["status"], "failed")


def core_now_plus_days(days):
    import datetime
    when = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=days)
    return when.replace(microsecond=0).isoformat()


class EmbedTest(CliTest):
    def test_quote_sends_only_input_and_model(self):
        res = self.run_cli("embed", "hello world", "--quote")
        self.assertEqual(res.code, 0, res.err)
        quote = self.fake.calls_to("POST", r"/embeddings/price$")[0]
        self.assertEqual(quote.json, {"input": "hello world", "model": "Bge_M3_FP16"})
        self.assertEqual(self.fake.submissions(), [])
        self.assertTrue(res.json["quote_only"])
        self.assertEqual(res.json["params"]["input"], "1 text(s), 11 characters")

    def test_run_saves_vectors_and_summarises_them(self):
        self.fake.result_text = json.dumps([[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]])
        res = self.run_cli("embed", "--input", "one", "--input", "two")
        self.assertEqual(res.code, 0, res.err)
        sub = self.fake.submissions()[0]
        self.assertEqual(sub.json["input"], ["one", "two"])
        self.assertIs(sub.json["return_result_in_response"], True)
        self.assertEqual((res.json["vectors"], res.json["dimensions"]), (2, 3))
        self.assertTrue(res.json["text_file"].endswith(".json"))
        self.assertIn("2 vector(s) of 3 numbers", res.json["preview"])

    def test_openai_shaped_result(self):
        self.fake.result_text = json.dumps({"data": [{"embedding": [1, 2, 3, 4]}]})
        res = self.run_cli("embed", "x y z")
        self.assertEqual((res.json["vectors"], res.json["dimensions"]), (1, 4))

    def test_unknown_result_shape_keeps_preview(self):
        self.fake.result_text = json.dumps({"something": "else"})
        res = self.run_cli("embed", "x y z")
        self.assertEqual(res.code, 0, res.err)
        self.assertNotIn("vectors", res.json)
        self.assertIn("something", res.json["preview"])

    def test_input_file_lines_and_whole(self):
        path = self.write("texts.txt", "first line\n\n second line \n".encode("utf-8"))
        self.assertEqual(self.run_cli("embed", "--input-file", path, "--quote").code, 0)
        self.assertEqual(self.fake.calls[-1].json["input"], ["first line", "second line"])
        self.assertEqual(self.run_cli("embed", "--input-file", path, "--whole",
                                      "--quote").code, 0)
        self.assertEqual(self.fake.calls[-1].json["input"], "first line\n\n second line")
        self.assertEqual(self.run_cli("embed", "--whole", "x").code, core.EXIT_USAGE)

    def test_too_many_texts_is_an_error_with_the_limit(self):
        path = self.write("many.txt", ("t\n" * 2049).encode("utf-8"))
        res = self.run_cli("embed", "--input-file", path)
        self.assertEqual(res.code, core.EXIT_USAGE)
        self.assertIn("2048", res.error["message"])
        self.assertEqual(self.fake.calls, [])

    def test_long_text_warns_about_the_token_limit(self):
        res = self.run_cli("embed", "x" * 40000, "--quote")
        self.assertEqual(res.code, 0, res.err)
        self.assertIn("8192 tokens", res.err)

    def test_no_text_and_over_budget(self):
        self.assertEqual(self.run_cli("embed").code, core.EXIT_USAGE)
        self.fake.price = 1.0
        res = self.run_cli("embed", "hello")
        self.assertEqual(res.code, core.EXIT_OVER_BUDGET)
        self.assertEqual(self.fake.submissions(), [])


class BoostTest(CliTest):
    def test_quote_image_type(self):
        res = self.run_cli("boost", "--prompt", "a red fox", "--quote")
        self.assertEqual(res.code, 0, res.err)
        quote = self.fake.calls_to("POST", r"/enhancements/price$")[0]
        self.assertEqual(quote.field("type"), "images.generations")
        self.assertEqual(quote.field("model_slug"), "Flux_2_Klein_4B_BF16")
        self.assertEqual(self.fake.submissions(), [])
        self.assertEqual(res.json["quoted_price"], 0.01)
        self.assertTrue(res.json["within_max_cost"])

    def test_run_returns_the_new_prompt(self):
        res = self.run_cli("boost", "--prompt", "a red fox", "--type", "video",
                           "--negative", "blurry")
        self.assertEqual(res.code, 0, res.err)
        sub = self.fake.submissions()[0]
        self.assertEqual(sub.field("type"), "videos.generations")
        self.assertEqual(sub.field("negative_prompt"), "blurry")
        self.assertEqual(res.json["result"]["prompt"], "an improved prompt")
        self.assertEqual(res.json["original_prompt"], "a red fox")
        self.assertIn("negative_prompt", res.json["result"])

    def test_edit_quotes_without_upload_and_submits_the_image(self):
        png = self.png()
        res = self.run_cli("boost", "--prompt", "make it night", "--type", "edit",
                           "--image", png)
        self.assertEqual(res.code, 0, res.err)
        quote = self.fake.calls_to("POST", r"/enhancements/price$")[0]
        self.assertEqual(quote.files, {})
        self.assertEqual(quote.field("type"), "images.edits")
        sub = self.fake.submissions()[0]
        self.assertIn("image", sub.files)

    def test_image_rules(self):
        self.assertEqual(self.run_cli("boost", "--prompt", "abc", "--type", "animate").code,
                         core.EXIT_USAGE)
        png = self.png()
        self.assertEqual(self.run_cli("boost", "--prompt", "abc", "--image", png).code,
                         core.EXIT_USAGE)
        self.assertEqual(self.fake.calls, [])

    def test_types_and_aliases(self):
        res = self.run_cli("boost", "--prompt", "hello there", "--type", "speech", "--quote")
        self.assertEqual(res.json["type"], "audio.speech")
        res = self.run_cli("boost", "--prompt", "lofi beat", "--type", "audio.music", "--quote")
        self.assertEqual(res.json["type"], "audio.music")
        self.assertEqual(self.run_cli("boost", "--prompt", "abc", "--type", "ocr").code,
                         core.EXIT_USAGE)

    def test_short_prompt_is_refused_locally(self):
        self.assertEqual(self.run_cli("boost", "--prompt", "ab").code, core.EXIT_USAGE)
        self.assertEqual(self.run_cli("boost", "--prompt", "abc", "--negative", "x").code,
                         core.EXIT_USAGE)
        self.assertEqual(self.fake.calls, [])

    def test_auto_selection_falls_back_when_a_model_has_no_guide(self):
        def price(call):
            if call.field("model_slug") == "Flux_2_Klein_4B_BF16":
                return 422, {"message": "The given data was invalid.",
                             "errors": {"model_slug": ["No prompt guide for this model."]}}
            return 200, {"price": 0.0004}
        self.fake.route("POST", r"/enhancements/price$", price)
        res = self.run_cli("boost", "--prompt", "a red fox", "--quote")
        self.assertEqual(res.code, 0, res.err)
        self.assertNotEqual(res.json["model"], "Flux_2_Klein_4B_BF16")
        self.assertNotEqual(res.json["model"], "partner-image-example")

    def test_explicit_model_is_not_replaced(self):
        self.fake.route("POST", r"/enhancements/price$", (422, {
            "message": "The given data was invalid.",
            "errors": {"model_slug": ["No prompt guide for this model."]}}))
        res = self.run_cli("boost", "--prompt", "a red fox", "--model", "Flux1schnell")
        self.assertEqual(res.code, core.EXIT_VALIDATION)
        self.assertEqual(len(self.fake.calls_to("POST", r"/price$")), 1)
        self.assertEqual(self.fake.submissions(), [])

    def test_over_budget_submits_nothing(self):
        self.fake.price = 0.5
        res = self.run_cli("boost", "--prompt", "a red fox")
        self.assertEqual(res.code, core.EXIT_OVER_BUDGET)
        self.assertEqual(self.fake.submissions(), [])
        res = self.run_cli("boost", "--prompt", "a red fox", "--max-cost", "1")
        self.assertEqual(res.code, 0, res.err)


if __name__ == "__main__":
    unittest.main()
