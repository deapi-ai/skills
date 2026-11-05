"""Model selection, languages and parameter fitting to model cards."""

import unittest

import fakeapi
from helpers import CliTest, core


class ModelSelectionTests(CliTest):
    def test_ct2_default_regardless_of_order(self):
        for models in (self.fake.models, list(reversed(self.fake.models))):
            self.fake = fakeapi.FakeAPI(models=models)
            res = self.run_cli("transcribe", "https://youtu.be/abc", "--quote")
            self.assertEqual(res.code, 0, res.err)
            self.assertEqual(res.json["model"], "WhisperLargeV3Ct2")

    def test_models_filter_sent_to_api(self):
        self.run_cli("models", "--type", "transcribe")
        query = self.fake.calls[0].query
        self.assertEqual(query["filter[inference_types]"],
                         "audio2text,video2text,audio_file2text,video_file2text")

    def test_partner_models_skipped_without_flag(self):
        models = [m for m in self.fake.models if m["slug"] == "partner-image-example"] + \
            [m for m in self.fake.models if m["slug"] == "Flux1schnell"]
        self.fake = fakeapi.FakeAPI(models=models)
        res = self.run_cli("image", "--prompt", "fox", "--quote")
        self.assertEqual(res.json["model"], "Flux1schnell")

    def test_partner_model_by_explicit_flag(self):
        res = self.run_cli("image", "--prompt", "fox", "--model", "partner-image-example", "--quote")
        self.assertEqual(res.code, 0, res.err)
        self.assertEqual(res.json["model"], "partner-image-example")

    def test_partner_only_catalog(self):
        models = [m for m in self.fake.models if m["slug"] == "partner-image-example"]
        self.fake = fakeapi.FakeAPI(models=models)
        res = self.run_cli("image", "--prompt", "fox", "--quote")
        self.assertEqual(res.code, core.EXIT_VALIDATION)
        res = self.run_cli("image", "--prompt", "fox", "--quote", "--allow-partner-models")
        self.assertEqual(res.json["model"], "partner-image-example")

    def test_unknown_model(self):
        res = self.run_cli("image", "--prompt", "fox", "--model", "NoSuchModel", "--quote")
        self.assertEqual(res.code, core.EXIT_VALIDATION)
        self.assertEqual(self.fake.calls_to("POST"), [])

    def test_model_for_other_task(self):
        res = self.run_cli("image", "--prompt", "fox", "--model", "Kokoro", "--quote")
        self.assertEqual(res.code, core.EXIT_VALIDATION)
        self.assertIn("does not serve", res.err)

    def test_voice_clone_by_feature(self):
        clip = self.wav("voice.wav", seconds=6)
        res = self.run_cli("tts", "--text", "Hello there, friend", "--clone-audio", clip)
        self.assertEqual(res.code, 0, res.err)
        self.assertEqual(res.json["model"], "Example_TTS_Clone_Base")
        quote = self.fake.calls_to("POST", r"/price$")[0]
        self.assertEqual(quote.field("mode"), "voice_clone")
        self.assertEqual(quote.files, {})          # quotes never upload the recording
        self.assertIn("ref_audio", self.fake.submissions()[0].files)

    def test_voice_design_by_feature(self):
        res = self.run_cli("tts", "--text", "Hello there, friend", "--design", "calm narrator",
                           "--quote")
        self.assertEqual(res.json["model"], "Qwen3_TTS_12Hz_1_7B_VoiceDesign")


class LanguageTests(CliTest):
    def test_tts_polish_picks_model_with_pl(self):
        res = self.run_cli("tts", "--text", "Dzień dobry wszystkim", "--lang", "pl", "--quote")
        self.assertEqual(res.code, 0, res.err)
        self.assertEqual(res.json["model"], "Chatterbox")
        self.assertEqual(self.fake.calls_to("POST", r"/price$")[0].field("lang"), "pl")

    def test_tts_en_maps_to_model_spelling(self):
        res = self.run_cli("tts", "--text", "Hello world", "--lang", "en", "--quote")
        self.assertEqual(res.json["model"], "Kokoro")
        self.assertEqual(self.fake.calls_to("POST", r"/price$")[0].field("lang"), "en-us")
        res = self.run_cli("tts", "--text", "Hello world again", "--lang", "en", "--model",
                           "Qwen3_TTS_12Hz_1_7B_CustomVoice", "--quote")
        self.assertEqual(self.fake.calls_to("POST", r"/price$")[-1].field("lang"), "English")

    def test_tts_unknown_voice(self):
        res = self.run_cli("tts", "--text", "Hello world", "--lang", "en", "--voice", "nope",
                           "--quote")
        self.assertEqual(res.code, core.EXIT_USAGE)

    def test_tts_text_too_long(self):
        res = self.run_cli("tts", "--text", "a" * 2500, "--model", "Chatterbox", "--quote")
        self.assertEqual(res.code, core.EXIT_USAGE)
        self.assertEqual(res.error["max_text"], 2000)
        self.assertEqual(self.fake.calls_to("POST"), [])

    def test_transcribe_lang_must_be_iso(self):
        for bad in ("Polish", "xx", "pol"):
            res = self.run_cli("transcribe", "https://youtu.be/abc", "--lang", bad, "--quote")
            self.assertEqual(res.code, core.EXIT_USAGE, bad)
        res = self.run_cli("transcribe", "https://youtu.be/abc", "--lang", "PL", "--quote")
        self.assertEqual(self.fake.calls_to("POST", r"/price$")[-1].field("lang"), "pl")

    def test_model_language_helper(self):
        model = {"languages": [{"slug": "en-gb", "name": "English (UK)"},
                               {"slug": "en-us", "name": "English (US)"}]}
        self.assertEqual(core.model_language(model, "en")["slug"], "en-gb")
        self.assertIsNone(core.model_language(model, "pl"))


class ParameterTests(CliTest):
    def quote_call(self):
        return self.fake.calls_to("POST", r"/price$")[-1]

    def test_music_uses_pinned_limits(self):
        res = self.run_cli("music", "--caption", "calm piano")
        self.assertEqual(res.code, 0, res.err)
        for call in (self.quote_call(), self.fake.submissions()[0]):
            self.assertEqual(float(call.field("guidance_scale")), 1.0)
            self.assertEqual(int(call.field("inference_steps")), 8)
            self.assertEqual(call.field("lyrics"), "[Instrumental]")
        self.assertEqual(self.fake.submissions()[0].field("format"), "mp3")

    def test_music_guidance_clamped(self):
        self.run_cli("music", "--caption", "calm piano", "--guidance", "7.5", "--quote")
        self.assertEqual(float(self.quote_call().field("guidance_scale")), 1.0)

    def test_video_fps_from_model(self):
        self.run_cli("video", "--prompt", "waves", "--fps", "30", "--quote")
        call = self.quote_call()
        self.assertEqual(call.json["fps"], 24)
        self.assertNotIn("steps", call.json)
        self.assertNotIn("guidance", call.json)
        self.assertEqual(call.json["width"] % 64, 0)

    def test_video_seconds_to_frames(self):
        self.run_cli("video", "--prompt", "waves", "--seconds", "4", "--quote")
        self.assertEqual(self.quote_call().json["frames"], 96)
        self.run_cli("video", "--prompt", "waves", "--seconds", "1", "--quote")
        self.assertEqual(self.quote_call().json["frames"], 49)

    def test_steps_and_guidance_only_when_supported(self):
        self.run_cli("image", "--prompt", "fox", "--guidance", "5", "--negative", "blur",
                     "--quote")
        body = self.quote_call().json
        self.assertEqual(body["model"], "Flux_2_Klein_4B_BF16")
        self.assertNotIn("guidance", body)
        self.assertNotIn("negative_prompt", body)
        self.assertEqual(body["steps"], 4)
        self.run_cli("image", "--prompt", "fox", "--model", "Flux1schnell", "--negative",
                     "blur", "--steps", "50", "--quote")
        body = self.quote_call().json
        self.assertEqual(body["negative_prompt"], "blur")
        self.assertEqual(body["steps"], 10)

    def test_seed_reported(self):
        self.run_cli("image", "--prompt", "fox", "--seed", "42")
        self.assertEqual(self.fake.submissions()[0].json["seed"], 42)

    def test_too_many_edit_images(self):
        images = [self.png("a%d.png" % i) for i in range(4)]
        res = self.run_cli("edit", *images, "--prompt", "snow", "--quote")
        self.assertEqual(res.code, core.EXIT_USAGE)
        self.assertEqual(self.fake.calls_to("POST"), [])

    def test_edit_multiple_images_field(self):
        images = [self.png("a%d.png" % i) for i in range(2)]
        res = self.run_cli("edit", *images, "--prompt", "snow")
        self.assertEqual(res.code, 0, res.err)
        self.assertEqual(self.quote_call().json["prompt"], "snow")
        self.assertEqual(len(self.fake.submissions()[0].files["images[]"]), 2)

    def test_fit_dimensions(self):
        model = {"info": {"limits": {"min_width": 512, "max_width": 1344, "min_height": 512,
                                     "max_height": 1344, "dimension_multiple": 64,
                                     "max_pixels": 1048576}}}
        self.assertEqual(core.fit_dimensions(model, 1000, 1000), (960, 960))
        self.assertEqual(core.fit_dimensions(model, 100, 5000), (512, 1344))
        w, h = core.fit_dimensions(model, 1344, 1344)
        self.assertLessEqual(w * h, 1048576)


if __name__ == "__main__":
    unittest.main()
