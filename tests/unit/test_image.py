"""Image commands: image, edit, ocr, rmbg, upscale (offline, fake API)."""

import os
import unittest

from helpers import CliTest, fakeapi


class ImageTest(CliTest):
    def setUp(self):
        super().setUp()
        self.fake.models.extend(fakeapi.load_fixture("image_models.json")["data"])
        # Live cards give both sides; the shared fixture gives only the width.
        for model in self.fake.models:
            limits = (model.get("info") or {}).get("limits") or {}
            if model["slug"] in ("Ben2", "RealESRGAN_x4", "Nanonets_Ocr_S_F16"):
                limits.setdefault("min_height", limits.get("min_width"))
                limits.setdefault("max_height", limits.get("max_width"))

    def quote_call(self, path):
        calls = self.fake.calls_to("POST", r"%s/price$" % path)
        self.assertEqual(len(calls), 1, [c.path for c in self.fake.calls])
        return calls[0]

    def no_paid_calls(self):
        self.assertEqual(self.fake.submissions(), [])


class TestImage(ImageTest):
    def test_quote_only_quotes(self):
        res = self.run_cli("image", "--prompt", "fox", "--quote")
        self.assertEqual(res.code, 0, res.err)
        self.no_paid_calls()
        body = self.quote_call("/images/generations").json
        self.assertEqual(body["model"], "Flux_2_Klein_4B_BF16")
        self.assertEqual((body["width"], body["height"]), (1024, 1024))
        self.assertEqual(body["steps"], 4)          # pinned by the model
        self.assertIn("seed", body)
        self.assertNotIn("negative_prompt", body)
        self.assertTrue(res.json["quote_only"])
        self.assertEqual(res.json["size"], "1024x1024")

    def test_size_is_fitted_to_the_model(self):
        res = self.run_cli("image", "--prompt", "fox", "--size", "4000x1000", "--quote")
        self.assertEqual(res.code, 0, res.err)
        body = self.quote_call("/images/generations").json
        self.assertEqual((body["width"], body["height"]), (1536, 384))   # 4:1 kept
        self.assertIn("aspect kept", res.err)
        res = self.run_cli("image", "--prompt", "fox", "--size", "1000x1000", "--quote")
        body = self.fake.calls_to("POST", r"/price$")[-1].json
        self.assertEqual((body["width"], body["height"]), (992, 992))     # grid of 16

    def test_bad_size(self):
        for size in ("big", "0x512"):
            res = self.run_cli("image", "--prompt", "fox", "--size", size, "--quote")
            self.assertEqual(res.code, 2, size)
        res = self.run_cli("image", "--prompt", "fox", "--width", "-5", "--quote")
        self.assertEqual(res.code, 2)

    def test_aspect_ratio_limit_is_an_error(self):
        res = self.run_cli("image", "--prompt", "fox", "--model", "example-quality-image",
                           "--size", "3000x700", "--quote")
        self.assertEqual(res.code, 2)
        self.assertIn("3:1", res.error["message"])
        self.assertEqual(self.fake.calls_to("POST"), [])

    def test_count_sums_the_quote_and_submits_n_jobs(self):
        res = self.run_cli("image", "--prompt", "fox", "--count", "3", "--seed", "10",
                           "--quote")
        self.assertEqual(res.code, 0, res.err)
        self.assertEqual(res.json["count"], 3)
        self.assertAlmostEqual(res.json["quoted_total"], 0.03)
        self.no_paid_calls()
        res = self.run_cli("image", "--prompt", "fox", "--count", "3", "--seed", "10")
        self.assertEqual(res.code, 0, res.err)
        seeds = [c.json["seed"] for c in self.fake.submissions()]
        self.assertEqual(seeds, [10, 11, 12])
        self.assertEqual(len(res.json["results"]), 3)

    def test_count_over_threshold_submits_nothing(self):
        self.fake.price = 0.1
        res = self.run_cli("image", "--prompt", "fox", "--count", "3")
        self.assertEqual(res.code, 5)
        self.assertAlmostEqual(res.error["price"], 0.3)
        self.no_paid_calls()

    def test_count_range(self):
        for count in ("0", "5"):
            res = self.run_cli("image", "--prompt", "fox", "--count", count, "--quote")
            self.assertEqual(res.code, 2, count)
        self.assertEqual(self.fake.calls, [])

    def test_quality_valid_is_sent(self):
        res = self.run_cli("image", "--prompt", "fox", "--model", "example-quality-image",
                           "--quality", "HIGH", "--quote")
        self.assertEqual(res.code, 0, res.err)
        body = self.quote_call("/images/generations").json
        self.assertEqual(body["quality"], "high")
        self.assertNotIn("steps", body)              # supports_steps: false

    def test_quality_not_in_options_is_an_error(self):
        res = self.run_cli("image", "--prompt", "fox", "--model", "example-quality-image",
                           "--quality", "ultra", "--quote")
        self.assertEqual(res.code, 2)
        self.assertEqual(res.error["quality_options"], ["low", "medium", "high"])
        self.assertEqual(self.fake.calls_to("POST"), [])

    def test_quality_ignored_with_warning_when_model_has_none(self):
        res = self.run_cli("image", "--prompt", "fox", "--quality", "high", "--quote")
        self.assertEqual(res.code, 0, res.err)
        self.assertNotIn("quality", self.quote_call("/images/generations").json)
        self.assertIn("--quality ignored", res.err)

    def test_quality_prefers_a_model_with_that_preset_when_partners_allowed(self):
        res = self.run_cli("image", "--prompt", "fox", "--quality", "low",
                           "--allow-partner-models", "--quote")
        self.assertEqual(res.code, 0, res.err)
        self.assertEqual(res.json["model"], "example-quality-image")
        self.assertEqual(self.quote_call("/images/generations").json["quality"], "low")

    def test_lora_selects_a_model_that_lists_it(self):
        res = self.run_cli("image", "--prompt", "fox", "--lora", "example style:0.5",
                           "--lora", "ExampleInkLora", "--quote")
        self.assertEqual(res.code, 0, res.err)
        body = self.quote_call("/images/generations").json
        self.assertEqual(body["model"], "ExampleLoraModel")
        self.assertEqual(body["loras"], [{"name": "ExampleStyleLora", "weight": 0.5},
                                         {"name": "ExampleInkLora", "weight": 1.0}])

    def test_lora_errors(self):
        res = self.run_cli("image", "--prompt", "fox", "--model", "ExampleLoraModel",
                           "--lora", "Missing", "--quote")
        self.assertEqual(res.code, 2)
        self.assertIn("ExampleStyleLora", res.error["message"])
        res = self.run_cli("image", "--prompt", "fox", "--model", "Flux1schnell",
                           "--lora", "ExampleStyleLora", "--quote")
        self.assertEqual(res.code, 2)
        self.assertIn("does not take LoRAs", res.error["message"])
        res = self.run_cli("image", "--prompt", "fox", "--lora", "Missing", "--quote")
        self.assertEqual(res.code, 2)
        self.assertIn("lists the LoRA", res.error["message"])
        res = self.run_cli("image", "--prompt", "fox", "--lora", "ExampleStyleLora:-1",
                           "--quote")
        self.assertEqual(res.code, 2)
        self.assertEqual(self.fake.calls_to("POST"), [])

    def test_enhance_adds_the_booster_fee_to_the_quote(self):
        self.fake.route("POST", r"/prompts/enhancements/price$", (200, {"price": 0.002}))
        res = self.run_cli("image", "--prompt", "fox", "--enhance", "--quote")
        self.assertEqual(res.code, 0, res.err)
        boost = self.quote_call("/prompts/enhancements")
        self.assertEqual(boost.field("type"), "images.generations")
        self.assertEqual(boost.field("model_slug"), "Flux_2_Klein_4B_BF16")
        self.assertNotIn("enhance_prompt", self.quote_call("/images/generations").json)
        self.assertAlmostEqual(res.json["enhance_quoted_price"], 0.002)
        self.assertAlmostEqual(res.json["quoted_total_with_enhance"], 0.012)
        self.assertTrue(res.json["within_max_cost"])
        self.no_paid_calls()

    def test_enhance_fee_counts_against_the_threshold(self):
        self.fake.price = 0.2
        self.fake.route("POST", r"/prompts/enhancements/price$", (200, {"price": 0.1}))
        res = self.run_cli("image", "--prompt", "fox", "--enhance")
        self.assertEqual(res.code, 5)
        self.assertAlmostEqual(res.error["price"], 0.3)
        self.no_paid_calls()
        res = self.run_cli("image", "--prompt", "fox", "--enhance", "--quote")
        self.assertEqual(res.code, 0)
        self.assertFalse(res.json["within_max_cost"])

    def test_enhance_submits_enhance_prompt(self):
        res = self.run_cli("image", "--prompt", "fox", "--enhance")
        self.assertEqual(res.code, 0, res.err)
        (job,) = self.fake.submissions()
        self.assertIs(job.json["enhance_prompt"], True)

    def test_alt_format_download(self):
        def done(rid):
            return {"status": "done", "price": {"amount": 0.01, "is_estimated": False},
                    "result_url": "https://%s/%s.png" % (fakeapi.RESULTS_HOST, rid),
                    "results_alt_formats": {
                        "webp": "https://%s/%s.webp" % (fakeapi.RESULTS_HOST, rid)}}
        self.fake.done_job = done
        self.fake.route("GET", r"\.webp$", (200, b"RIFF0000WEBP", {"Content-Type": "image/webp"}))
        res = self.run_cli("image", "--prompt", "fox", "--format", "webp")
        self.assertEqual(res.code, 0, res.err)
        self.assertTrue(res.json["files"][0].endswith(".webp"))
        download = self.fake.calls_to("GET", r"\.webp$")[0]
        self.assertNotIn("authorization", download.headers)


class TestEdit(ImageTest):
    def test_single_image_quote_is_json_without_files(self):
        res = self.run_cli("edit", self.png(), "--prompt", "snow", "--quote")
        self.assertEqual(res.code, 0, res.err)
        call = self.quote_call("/images/edits")
        self.assertIsNotNone(call.json)
        self.assertEqual(call.json["model"], "Flux_2_Klein_4B_BF16")
        self.assertNotIn("width", call.json)        # 640x480 input fits the model
        self.no_paid_calls()

    def test_single_image_submit_uses_image_field(self):
        res = self.run_cli("edit", self.png(), "--prompt", "snow")
        self.assertEqual(res.code, 0, res.err)
        (job,) = self.fake.submissions()
        self.assertIn("image", job.files)
        self.assertNotIn("images[]", job.files)

    def test_several_images_pick_a_model_that_takes_them(self):
        images = [self.png("a.png"), self.png("b.png")]
        res = self.run_cli("edit", *images, "--prompt", "one scene")
        self.assertEqual(res.code, 0, res.err)
        (job,) = self.fake.submissions()
        self.assertEqual(job.field("model"), "Flux_2_Klein_4B_BF16")
        self.assertEqual(len(job.files["images[]"]), 2)
        self.assertEqual(res.json["input_images"], 2)
        self.assertEqual(res.json["images"], ["a.png", "b.png"])

    def test_too_many_images_for_explicit_model_is_an_error(self):
        images = [self.png("a.png"), self.png("b.png")]
        res = self.run_cli("edit", *images, "--prompt", "x", "--model",
                           "QwenImageEdit_Plus_NF4")
        self.assertEqual(res.code, 2)
        self.assertEqual(res.error["max_input_images"], 1)
        self.assertIn("at most 1", res.error["message"])
        self.assertIn("Flux_2_Klein_4B_BF16", res.error["hint"])
        self.assertEqual(self.fake.calls_to("POST"), [])

    def test_too_many_images_for_any_standard_model(self):
        images = [self.png("%d.png" % i) for i in range(4)]
        res = self.run_cli("edit", *images, "--prompt", "x", "--quote")
        self.assertEqual(res.code, 2)
        self.assertIn("example-quality-image (5, partner)", res.error["hint"])
        self.assertEqual(self.fake.calls_to("POST"), [])
        res = self.run_cli("edit", *images, "--prompt", "x", "--quote",
                           "--allow-partner-models")
        self.assertEqual(res.code, 0, res.err)
        self.assertEqual(res.json["model"], "example-quality-image")

    def test_api_cap_of_16_images(self):
        images = [self.png("%d.png" % i) for i in range(17)]
        res = self.run_cli("edit", *images, "--prompt", "x", "--quote")
        self.assertEqual(res.code, 2)
        self.assertEqual(self.fake.calls, [])

    def test_output_size_for_custom_size_models(self):
        res = self.run_cli("edit", self.png(), "--prompt", "x", "--size", "512x512",
                           "--quote")
        self.assertEqual(res.code, 0, res.err)
        body = self.quote_call("/images/edits").json
        self.assertEqual((body["width"], body["height"]), (512, 512))
        self.assertEqual(res.json["size"], "512x512")

    def test_one_side_follows_the_first_image(self):
        res = self.run_cli("edit", self.png(width=800, height=400), "--prompt", "x",
                           "--width", "1024", "--quote")
        self.assertEqual(res.code, 0, res.err)
        body = self.quote_call("/images/edits").json
        self.assertEqual((body["width"], body["height"]), (1024, 512))

    def test_large_first_image_is_fitted(self):
        res = self.run_cli("edit", self.png(width=3000, height=1500), "--prompt", "x",
                           "--quote")
        self.assertEqual(res.code, 0, res.err)
        body = self.quote_call("/images/edits").json
        self.assertEqual((body["width"], body["height"]), (1536, 768))

    def test_size_ignored_with_warning_for_fixed_size_models(self):
        res = self.run_cli("edit", self.png(), "--prompt", "x", "--size", "512x512",
                           "--model", "QwenImageEdit_Plus_NF4", "--quote")
        self.assertEqual(res.code, 0, res.err)
        self.assertNotIn("width", self.quote_call("/images/edits").json)
        self.assertIn("keeps the input size", res.err)

    def test_size_prefers_a_custom_size_model(self):
        # With --size the automatic choice skips models that keep the input size.
        self.fake.models = [m for m in self.fake.models if m["slug"] != "Flux_2_Klein_4B_BF16"]
        res = self.run_cli("edit", self.png(), "--prompt", "x", "--size", "800x800",
                           "--allow-partner-models", "--quote")
        self.assertEqual(res.code, 0, res.err)
        self.assertEqual(res.json["model"], "example-quality-image")

    def test_default_steps_sent_when_card_is_silent(self):
        res = self.run_cli("edit", self.png(), "--prompt", "x", "--model",
                           "ExampleEditStepsUnknown")
        self.assertEqual(res.code, 0, res.err)
        self.assertEqual(self.quote_call("/images/edits").json["steps"], 40)
        (job,) = self.fake.submissions()
        self.assertEqual(job.field("steps"), "40")

    def test_explicit_steps_clamped(self):
        res = self.run_cli("edit", self.png(), "--prompt", "x", "--model",
                           "QwenImageEdit_Plus_NF4", "--steps", "500", "--quote")
        self.assertEqual(res.code, 0, res.err)
        self.assertEqual(self.quote_call("/images/edits").json["steps"], 50)

    def test_enhance_uses_the_edit_booster_type(self):
        res = self.run_cli("edit", self.png(), "--prompt", "x", "--enhance", "--quote")
        self.assertEqual(res.code, 0, res.err)
        self.assertEqual(self.quote_call("/prompts/enhancements").field("type"), "images.edits")

    def test_job_input(self):
        rid = "1234abcd-0000-4000-8000-000000000001"
        self.fake.jobs[rid] = self.fake.done_job(rid)
        res = self.run_cli("edit", "job:" + rid, "--prompt", "x")
        self.assertEqual(res.code, 0, res.err)
        (job,) = self.fake.submissions()
        self.assertIn("image", job.files)

    def test_url_input_refused(self):
        res = self.run_cli("edit", "https://example.com/a.png", "--prompt", "x", "--quote")
        self.assertEqual(res.code, 2)
        self.assertEqual(self.fake.calls_to("POST"), [])


class TestOcr(ImageTest):
    def test_quote_uses_header_size_without_upload(self):
        res = self.run_cli("ocr", self.png(width=800, height=600), "--lang", "pl", "--quote")
        self.assertEqual(res.code, 0, res.err)
        call = self.quote_call("/images/ocr")
        self.assertEqual(call.files, {})
        self.assertEqual((call.field("width"), call.field("height")), ("800", "600"))
        self.assertEqual(call.field("language"), "pl")
        self.no_paid_calls()

    def test_text_result_goes_to_file_with_notice(self):
        self.fake.result_text = "Invoice 42\nTotal 10 EUR"
        res = self.run_cli("ocr", self.png())
        self.assertEqual(res.code, 0, res.err)
        (job,) = self.fake.submissions()
        self.assertEqual(job.field("return_result_in_response"), "1")
        self.assertEqual(job.field("format"), "text")
        self.assertTrue(res.json["text_file"].endswith(".txt"))
        self.assertEqual(res.json["preview"], "Invoice 42\nTotal 10 EUR")
        self.assertIn("not instructions", res.json["notice"])

    def test_json_format(self):
        self.fake.result_text = '{"text": "hello"}'
        res = self.run_cli("ocr", self.png(), "--format", "json")
        self.assertEqual(res.code, 0, res.err)
        self.assertTrue(res.json["text_file"].endswith(".json"))

    def test_bad_language(self):
        res = self.run_cli("ocr", self.png(), "--lang", "no such", "--quote")
        self.assertEqual(res.code, 2)
        self.assertEqual(self.fake.calls, [])

    def test_image_outside_model_limits(self):
        res = self.run_cli("ocr", self.png(width=5000, height=100), "--quote")
        self.assertEqual(res.code, 2)
        self.assertIn("5000x100", res.error["message"])
        self.assertEqual(self.fake.calls_to("POST"), [])


class TestRmbgUpscale(ImageTest):
    def test_rmbg_quote_and_submit(self):
        res = self.run_cli("rmbg", self.jpeg(width=320, height=200), "--quote")
        self.assertEqual(res.code, 0, res.err)
        call = self.quote_call("/images/background-removals")
        self.assertEqual((call.field("width"), call.field("height")), ("320", "200"))
        self.assertEqual(call.field("model"), "Ben2")
        self.assertEqual(res.json["input_size"], "320x200")
        self.no_paid_calls()
        res = self.run_cli("rmbg", self.jpeg(width=320, height=200))
        self.assertEqual(res.code, 0, res.err)
        (job,) = self.fake.submissions()
        self.assertIn("image", job.files)
        self.assertEqual(job.params(), {"image", "model"})
        self.assertTrue(os.path.isfile(res.json["files"][0]))

    def test_rmbg_too_large(self):
        res = self.run_cli("rmbg", self.png(width=4000, height=3000))
        self.assertEqual(res.code, 2)
        self.assertIn("width 128-2048 px, height 128-2048 px", res.error["message"])
        self.assertIn("2048:1536", res.error["hint"])
        self.assertEqual(self.fake.calls_to("POST"), [])

    def test_upscale_too_small(self):
        res = self.run_cli("upscale", self.png(width=100, height=64))
        self.assertEqual(res.code, 2)
        self.assertIn("200:128", res.error["hint"])
        self.assertEqual(self.fake.calls_to("POST"), [])

    def test_upscale_quote(self):
        res = self.run_cli("upscale", self.webp(width=300, height=150), "--quote")
        self.assertEqual(res.code, 0, res.err)
        call = self.quote_call("/images/upscales")
        self.assertEqual(call.field("model"), "RealESRGAN_x4")
        self.assertEqual((call.field("width"), call.field("height")), ("300", "150"))

    def test_input_given_twice(self):
        path = self.png()
        res = self.run_cli("upscale", path, "--image", path, "--quote")
        self.assertEqual(res.code, 2)

    def test_missing_input(self):
        res = self.run_cli("rmbg", "--quote")
        self.assertEqual(res.code, 2)


if __name__ == "__main__":
    unittest.main()
