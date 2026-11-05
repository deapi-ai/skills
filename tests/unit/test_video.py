"""Video commands: video, animate, audio-video, video-upscale, describe (offline)."""

import importlib.util
import json
import os
import re
import struct
from unittest import mock

from helpers import ROOT, CliTest, core  # noqa: I100  (sets sys.path for fakeapi)
import fakeapi  # noqa: E402

FIXTURE = os.path.join(ROOT, "tests", "fixtures", "video_models.json")


def _load_tool(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, "tools", name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CHECK = _load_tool("check_openapi")
SPEC = CHECK.Spec(CHECK.load_spec(CHECK.SNAPSHOT))


def box(kind, payload=b""):
    return struct.pack(">I4s", 8 + len(payload), kind) + payload


def mp4_bytes(width=256, height=144, seconds=2.0, rotate=False, moov_last=False,
              audio_first=True, mvhd_v1=False):
    """A tiny synthetic MP4 with just the headers mp4_info reads."""
    scale = 1000
    if mvhd_v1:
        mvhd = b"\x01\x00\x00\x00" + b"\x00" * 16 + struct.pack(">IQ", scale, int(seconds * scale))
    else:
        mvhd = b"\x00" * 12 + struct.pack(">II", scale, int(seconds * scale))
    mvhd = box(b"mvhd", mvhd + b"\x00" * 80)

    def trak(handler, w, h, rot):
        matrix = [0, 0x10000, 0, -0x10000, 0, 0, 0, 0, 0x40000000] if rot else \
            [0x10000, 0, 0, 0, 0x10000, 0, 0, 0, 0x40000000]
        tkhd = b"\x00" * 4 + b"\x00" * 20 + b"\x00" * 8 + b"\x00" * 8 + \
            struct.pack(">9i", *matrix) + struct.pack(">II", w << 16, h << 16)
        hdlr = b"\x00" * 8 + handler + b"\x00" * 12 + b"\x00"
        return box(b"trak", box(b"tkhd", tkhd) + box(b"mdia", box(b"mdhd", b"\x00" * 24) +
                                                    box(b"hdlr", hdlr)))
    traks = [trak(b"vide", width, height, rotate)]
    if audio_first:
        traks.insert(0, trak(b"soun", 0, 0, False))
    moov = box(b"moov", mvhd + b"".join(traks))
    ftyp = box(b"ftyp", b"isom\x00\x00\x02\x00isomiso2")
    mdat = box(b"mdat", b"\x00" * 64)
    return ftyp + (mdat + moov if moov_last else moov + mdat)


class VideoTest(CliTest):
    def setUp(self):
        super().setUp()
        self.fake.models.extend(fakeapi.load_fixture("video_models.json")["data"])

    # -- helpers ------------------------------------------------------------

    def quote_call(self):
        calls = [c for c in self.fake.calls if c.path.endswith("/price")]
        self.assertTrue(calls, "no price quote was sent")
        return calls[-1]

    def submit_call(self):
        subs = self.fake.submissions()
        self.assertEqual(len(subs), 1)
        return subs[0]

    def assert_spec(self):
        """Every API call uses a spec path and only spec fields (array suffixes stripped)."""
        for call in self.fake.calls:
            if call.host != "api.deapi.ai":
                continue
            template = SPEC.match(call.path)
            self.assertIsNotNone(template, call.path)
            if call.method == "GET":
                continue
            sent = {re.sub(r"\[\d*\]$", "", name) for name in call.params()}
            unknown = sent - SPEC.body_fields(template, call.method)
            self.assertFalse(unknown, "%s sends %s" % (call.path, sorted(unknown)))

    def mp4(self, name="in.mp4", **kw):
        return self.write(name, mp4_bytes(**kw))

    def no_ffprobe(self):
        return mock.patch.object(core.shutil, "which", return_value=None)

    def ok(self, res):
        self.assertEqual(res.code, 0, res.err + res.out)
        return res.json


class MediaHeaderTest(VideoTest):
    def setUp(self):
        super().setUp()
        from deapi_lib.commands import video
        self.video = video

    def test_mp4_size_and_duration(self):
        info = self.video.mp4_info(self.mp4(width=320, height=240, seconds=3.5))
        self.assertEqual(info, {"width": 320, "height": 240, "duration": 3.5})

    def test_moov_at_end_and_v1_header(self):
        info = self.video.mp4_info(self.mp4(moov_last=True, mvhd_v1=True, seconds=1.25))
        self.assertEqual(info["duration"], 1.25)
        self.assertEqual((info["width"], info["height"]), (256, 144))

    def test_rotation_swaps_sides(self):
        info = self.video.mp4_info(self.mp4(width=640, height=360, rotate=True))
        self.assertEqual((info["width"], info["height"]), (360, 640))

    def test_garbage_is_empty(self):
        path = self.write("bad.mp4", b"\x00\x00\x00\x18ftypisom" + b"\x00" * 64)
        self.assertEqual(self.video.mp4_info(path), {})
        path = self.write("trunc.mp4", b"\x00\x00\x10\x00moov\x00\x00")
        self.assertEqual(self.video.mp4_info(path), {})


# ---------------------------------------------------------------- video ---

class TextToVideoTest(VideoTest):
    def test_quote_body_and_fps(self):
        data = self.ok(self.run_cli("video", "--prompt", "waves", "--seconds", "3", "--quote"))
        body = self.quote_call().json
        self.assertEqual(body["model"], "Ltx2_5_22B_Dist_INT8")
        self.assertEqual(body["fps"], 24)
        self.assertEqual(body["frames"], 72)
        self.assertEqual((body["width"], body["height"]), (768, 768))
        self.assertEqual(set(body), {"model", "width", "height", "frames", "fps"})
        self.assertEqual(data["seconds"], 3.0)
        self.assertTrue(data["quote_only"])
        self.assertEqual(self.fake.submissions(), [])
        self.assert_spec()

    def test_submit_has_prompt_seed_and_enhance(self):
        self.fake.result_ext = "mp4"
        data = self.ok(self.run_cli("video", "--prompt", "waves", "--enhance", "--seed", "7"))
        sub = self.submit_call().json
        self.assertEqual(sub["prompt"], "waves")
        self.assertEqual(sub["seed"], 7)
        self.assertTrue(sub["enhance_prompt"])
        self.assertNotIn("enhance_prompt", self.quote_call().json)
        self.assertTrue(data["files"][0].endswith(".mp4"))
        self.assertEqual(data["seed"], 7)
        self.assert_spec()

    def test_size_is_fitted_and_ratio_capped(self):
        model = self.fake.models[[m["slug"] for m in self.fake.models].index(
            "Ltx2_5_22B_Dist_INT8")]
        model["info"]["limits"]["max_ratio"] = 2
        self.ok(self.run_cli("video", "--prompt", "w", "--size", "1344x512", "--quote"))
        body = self.quote_call().json
        self.assertEqual((body["width"], body["height"]), (1024, 512))
        self.ok(self.run_cli("video", "--prompt", "w", "--size", "1000x700", "--quote"))
        body = self.quote_call().json
        self.assertEqual((body["width"] % 64, body["height"] % 64), (0, 0))

    def test_small_size_scaled_up_keeping_aspect(self):
        res = self.run_cli("video", "--prompt", "w", "--size", "640x360", "--quote")
        self.ok(res)
        body = self.quote_call().json
        self.assertEqual((body["width"], body["height"]), (896, 512))
        self.assertIn("keeping the aspect ratio", res.err)

    def test_steps_and_negative_only_when_supported(self):
        self.ok(self.run_cli("video", "--prompt", "w", "--model", "Example_Steps_Video",
                             "--negative", "blur", "--steps", "9", "--guidance", "3"))
        sub = self.submit_call().json
        self.assertEqual(sub["steps"], 4)          # clamped to max_steps
        self.assertEqual(sub["negative_prompt"], "blur")
        self.assertNotIn("guidance", sub)
        self.assertEqual(sub["fps"], 30)
        self.assertEqual(self.quote_call().json["steps"], 4)
        self.assert_spec()

    def test_long_seconds_clamped_to_max_frames(self):
        self.ok(self.run_cli("video", "--prompt", "w", "--seconds", "60", "--quote"))
        self.assertEqual(self.quote_call().json["frames"], 241)

    def test_over_budget_submits_nothing(self):
        self.fake.price = 0.4
        res = self.run_cli("video", "--prompt", "w")
        self.assertEqual(res.code, core.EXIT_OVER_BUDGET)
        self.assertEqual(self.fake.submissions(), [])


# -------------------------------------------------------------- animate ---

class AnimateTest(VideoTest):
    def test_size_from_first_frame_aspect(self):
        png = self.png(width=640, height=360)
        data = self.ok(self.run_cli("animate", png, "--prompt", "pan", "--quote"))
        body = self.quote_call().json
        self.assertEqual((body["width"], body["height"]), (1024, 576))
        self.assertEqual(data["params"]["first_frame_image"], "in.png")
        jpg = self.jpeg(width=360, height=640)
        self.ok(self.run_cli("animate", "--image", jpg, "--prompt", "pan", "--quote"))
        body = self.quote_call().json
        self.assertEqual((body["width"], body["height"]), (576, 1024))
        self.assert_spec()

    def test_explicit_size_wins(self):
        png = self.png(width=640, height=360)
        self.ok(self.run_cli("animate", png, "--prompt", "pan", "--size", "512x512", "--quote"))
        body = self.quote_call().json
        self.assertEqual((body["width"], body["height"]), (512, 512))

    def test_first_and_last_frame_upload(self):
        first, last = self.png("a.png"), self.png("b.png")
        self.ok(self.run_cli("animate", first, "--last-image", last, "--prompt", "pan"))
        sub = self.submit_call()
        self.assertEqual(set(sub.files), {"first_frame_image", "last_frame_image"})
        self.assertEqual(sub.field("fps"), "24")
        quote = self.quote_call()
        self.assertIsNotNone(quote.json)            # quoted as JSON, no upload
        self.assertNotIn("first_frame_image", quote.json)
        self.assert_spec()

    def test_last_frame_needs_model_support(self):
        res = self.run_cli("animate", self.png(), "--last-image", self.png("b.png"),
                           "--prompt", "pan", "--model", "Example_Ref_Video")
        self.assertEqual(res.code, core.EXIT_VALIDATION)
        self.assertEqual(self.fake.calls_to("POST"), [])

    def test_reference_mode(self):
        a, b = self.png("a.png"), self.png("b.png", width=300, height=300)
        clip = self.mp4("ref.mp4", seconds=3)
        wav = self.wav("voice.wav", seconds=2)
        data = self.ok(self.run_cli("animate", "--ref-image", a, "--ref-image", b,
                                    "--ref-video", clip, "--ref-audio", wav,
                                    "--prompt", "<Picture 1> meets <Picture 2>"))
        self.assertEqual(data["model"], "Example_Ref_Video")
        quote = self.quote_call().json
        self.assertEqual(quote["ref_images_count"], 2)
        self.assertEqual(quote["ref_videos_count"], 1)
        self.assertEqual(quote["ref_audios_count"], 1)
        self.assertEqual(quote["ref_videos_duration"], 3.0)
        self.assertEqual(quote["fps"], 25)
        sub = self.submit_call()
        self.assertEqual(len(sub.files["ref_images[]"]), 2)
        self.assertEqual([f["filename"] for f in sub.files["ref_images[]"]], ["a.png", "b.png"])
        self.assertEqual(len(sub.files["ref_videos[]"]), 1)
        self.assertEqual(len(sub.files["ref_audios[]"]), 1)
        self.assertNotIn("first_frame_image", sub.files)
        self.assertEqual((int(sub.field("width")), int(sub.field("height"))), (1024, 576))
        self.assertEqual(data["references"], {"images": ["a.png", "b.png"],
                                              "videos": ["ref.mp4"], "audios": ["voice.wav"]})
        self.assert_spec()

    def test_reference_rules_fail_before_sending(self):
        png = self.png()
        cases = [
            ("animate", png, "--ref-image", png, "--prompt", "x"),           # both modes
            ("animate", "--ref-audio", self.wav(), "--prompt", "x"),          # audio alone
            ("animate", "--prompt", "x"),                                     # nothing
            ("animate", "--ref-image", png, "--ref-image", png, "--ref-image", png,
             "--ref-image", png, "--prompt", "x"),                            # > max_ref_images
            ("animate", "--ref-image", self.png("tall.png", width=100, height=900),
             "--prompt", "x"),                                                # ratio 1:9
            ("animate", "--ref-image", png, "--ref-video",
             self.mp4("long.mp4", seconds=9), "--prompt", "x"),               # > 5 s
        ]
        for argv in cases:
            self.fake.calls[:] = []
            res = self.run_cli(*argv)
            self.assertEqual(res.code, core.EXIT_USAGE, (argv, res.err))
            self.assertEqual(self.fake.calls_to("POST"), [], argv)
        res = self.run_cli("animate", "--ref-image", png, "--ref-image", png, "--ref-image",
                           png, "--ref-image", png, "--prompt", "x")
        self.assertIn("at most 3", res.error["message"])

    def test_reference_needs_capable_model(self):
        res = self.run_cli("animate", "--ref-image", self.png(), "--prompt", "x",
                           "--model", "Ltx2_5_22B_Dist_INT8")
        self.assertEqual(res.code, core.EXIT_VALIDATION)
        self.assertIn("image references", res.error["message"])


# ---------------------------------------------------------- audio-video ---

class AudioVideoTest(VideoTest):
    def test_frames_from_wav_length(self):
        wav = self.wav(seconds=4)
        data = self.ok(self.run_cli("audio-video", wav, "--prompt", "singer"))
        quote = self.quote_call()
        self.assertEqual(quote.path, "/api/v2/videos/audio-syncs/price")
        self.assertEqual(quote.json["frames"], 96)
        self.assertEqual(quote.json["fps"], 24)
        sub = self.submit_call()
        self.assertEqual(sub.path, "/api/v2/videos/audio-syncs")
        self.assertEqual(set(sub.files), {"audio"})
        self.assertEqual(sub.field("frames"), "96")
        self.assertNotIn("enhance_prompt", sub.fields)
        self.assertEqual(data["seconds"], 4.0)
        self.assert_spec()

    def test_long_audio_warns_and_clamps(self):
        res = self.run_cli("audio-video", self.wav(seconds=12), "--prompt", "s", "--quote")
        self.ok(res)
        self.assertEqual(self.quote_call().json["frames"], 241)
        self.assertIn("shorter", res.err)

    def test_mp3_without_ffprobe_needs_seconds(self):
        mp3 = self.write("in.mp3", b"ID3\x03" + b"\x00" * 64)
        with self.no_ffprobe():
            res = self.run_cli("audio-video", mp3, "--prompt", "s", "--quote")
        self.assertEqual(res.code, core.EXIT_USAGE)
        self.assertIn("--seconds", res.error["message"])
        self.assertEqual(self.fake.calls_to("POST"), [])
        with self.no_ffprobe():
            self.ok(self.run_cli("audio-video", mp3, "--prompt", "s", "--seconds", "3",
                                 "--quote"))
        self.assertEqual(self.quote_call().json["frames"], 72)

    def test_first_frame_sets_aspect(self):
        self.ok(self.run_cli("audio-video", "--audio", self.wav(seconds=3), "--image",
                             self.png(width=640, height=360), "--prompt", "s"))
        sub = self.submit_call()
        self.assertEqual(set(sub.files), {"audio", "first_frame_image"})
        self.assertEqual((sub.field("width"), sub.field("height")), ("1024", "576"))

    def test_rejects_video_as_audio(self):
        res = self.run_cli("audio-video", self.mp4(), "--prompt", "s")
        self.assertEqual(res.code, core.EXIT_USAGE)


# -------------------------------------------------------- video-upscale ---

class VideoUpscaleTest(VideoTest):
    def test_fixed_scale_quoted_by_size_and_length(self):
        clip = self.mp4(width=320, height=240, seconds=4)
        self.fake.result_ext = "mp4"
        data = self.ok(self.run_cli("video-upscale", clip))
        self.assertEqual(data["model"], "RealESRGAN_Vid_x4")
        quote = self.quote_call()
        self.assertEqual(quote.fields, {"model": ["RealESRGAN_Vid_x4"], "width": ["320"],
                                        "height": ["240"], "duration": ["4.0"]})
        self.assertEqual(quote.files, {})
        sub = self.submit_call()
        self.assertEqual(set(sub.files), {"video"})
        self.assertNotIn("scale", sub.fields)
        self.assertTrue(data["files"][0].endswith(".mp4"))
        self.assertEqual(data["input"], {"width": 320, "height": 240, "duration": 4.0})
        self.assert_spec()

    def test_scale_picks_configurable_model(self):
        self.ok(self.run_cli("video-upscale", self.mp4(), "--scale", "3"))
        sub = self.submit_call()
        self.assertEqual(sub.field("model"), "Example_Scaled_Upscaler")
        self.assertEqual(sub.field("scale"), "3")
        self.assertEqual(self.quote_call().field("scale"), "3")
        self.assert_spec()

    def test_configurable_model_defaults_to_min_scale(self):
        self.ok(self.run_cli("video-upscale", self.mp4(), "--model",
                             "Example_Scaled_Upscaler", "--quote"))
        self.assertEqual(self.quote_call().field("scale"), "2")

    def test_scale_errors(self):
        clip = self.mp4()
        for argv in (("--scale", "2", "--model", "RealESRGAN_Vid_x4"),
                     ("--scale", "8", "--model", "Example_Scaled_Upscaler"),
                     ("--scale", "0")):
            self.fake.calls[:] = []
            res = self.run_cli("video-upscale", clip, *argv)
            self.assertEqual(res.code, core.EXIT_USAGE, argv)
            self.assertEqual(self.fake.calls_to("POST"), [])
        res = self.run_cli("video-upscale", clip, "--scale", "8")
        self.assertEqual(res.code, core.EXIT_VALIDATION)   # no model can do x8

    def test_too_long_fails_before_sending(self):
        res = self.run_cli("video-upscale", self.mp4(seconds=12))
        self.assertEqual(res.code, core.EXIT_USAGE)
        self.assertIn("at most 10", res.error["message"])
        self.assertEqual(self.fake.calls_to("POST"), [])

    def test_unreadable_header_quotes_with_file(self):
        path = self.write("odd.mp4", b"\x00\x00\x00\x18ftypisom" + b"\x00" * 64)
        with self.no_ffprobe():
            self.ok(self.run_cli("video-upscale", path, "--quote"))
        quote = self.quote_call()
        self.assertEqual(set(quote.files), {"video"})
        self.assertNotIn("duration", quote.fields)
        self.assert_spec()

    def test_duration_flag_used_when_unreadable(self):
        path = self.write("odd.mp4", b"\x00\x00\x00\x18ftypisom" + b"\x00" * 64)
        with self.no_ffprobe():
            res = self.run_cli("video-upscale", path, "--duration", "30")
        self.assertEqual(res.code, core.EXIT_USAGE)


# ------------------------------------------------------------- describe ---

class DescribeTest(VideoTest):
    YT = "https://www.youtube.com/watch?v=abcdefghijk"

    def test_link_quote_and_result(self):
        self.fake.result_text = json.dumps({"events": [], "scenes": [{"t": 0}], "meta": {}})
        data = self.ok(self.run_cli("describe", self.YT, "--preset", "fast", "--metadata"))
        quote = self.quote_call()
        self.assertEqual(quote.path, "/api/v2/videos/descriptions/price")
        self.assertEqual(quote.field("video_url"), self.YT)
        self.assertEqual(quote.field("preset"), "fast")
        self.assertEqual(quote.field("include_metadata"), "1")
        self.assertNotIn("return_result_in_response", quote.fields)
        self.assertNotIn("platform", quote.fields)
        sub = self.submit_call()
        self.assertEqual(sub.field("video_url"), self.YT)
        self.assertEqual(sub.field("return_result_in_response"), "1")
        self.assertTrue(data["text_file"].endswith(".json"))
        self.assertIn("scenes", data["preview"])
        self.assertEqual(data["platform"], "youtube")
        self.assert_spec()

    def test_tiktok_needs_duration(self):
        url = "https://www.tiktok.com/@someone/video/123"
        res = self.run_cli("describe", url)
        self.assertEqual(res.code, core.EXIT_USAGE)
        self.assertIn("--duration", res.error["message"])
        self.assertEqual(self.fake.calls, [])
        self.ok(self.run_cli("describe", url, "--duration", "40"))
        quote = self.quote_call()
        self.assertEqual(quote.field("platform"), "tiktok")
        self.assertEqual(quote.field("duration_seconds"), "40.0")
        self.assertNotIn("video_url", quote.fields)
        self.assertEqual(self.submit_call().field("video_url"), url)
        self.assert_spec()

    def test_preset_checked_against_model(self):
        res = self.run_cli("describe", self.YT, "--preset", "detail")
        self.assertEqual(res.code, core.EXIT_USAGE)
        self.assertIn("fast, balanced", res.error["message"])
        self.ok(self.run_cli("describe", self.YT, "--preset", "detail", "--model",
                             "Example_Link_Describer", "--quote"))
        self.assertNotIn("preset", self.quote_call().fields)

    def test_file_quoted_by_length(self):
        clip = self.mp4(seconds=5)
        data = self.ok(self.run_cli("describe", clip, "--metadata", "--quote"))
        quote = self.quote_call()
        self.assertEqual(quote.field("duration_seconds"), "5.0")
        self.assertEqual(quote.files, {})
        self.assertNotIn("platform", quote.fields)
        self.assertNotIn("include_metadata", quote.fields)
        self.assertEqual(data["duration_seconds"], 5.0)
        self.ok(self.run_cli("describe", "--file", clip))
        sub = self.submit_call()
        self.assertEqual(set(sub.files), {"video_file"})
        self.assert_spec()

    def test_webm_without_ffprobe_quotes_with_file(self):
        webm = self.write("clip.webm", b"\x1a\x45\xdf\xa3" + b"\x00" * 8 + b"webm" + b"\x00" * 40)
        with self.no_ffprobe():
            self.ok(self.run_cli("describe", webm, "--quote"))
        self.assertEqual(set(self.quote_call().files), {"video_file"})

    def test_file_needs_file_model(self):
        res = self.run_cli("describe", self.mp4(), "--model", "Example_Link_Describer")
        self.assertEqual(res.code, core.EXIT_VALIDATION)

    def test_other_links_refused(self):
        res = self.run_cli("describe", "https://example.com/video.mp4")
        self.assertEqual(res.code, core.EXIT_USAGE)
        self.assertIn("YouTube", res.error["message"])
        self.assertEqual(self.fake.calls_to("POST"), [])
        res = self.run_cli("describe", self.YT, "--file", self.mp4())
        self.assertEqual(res.code, core.EXIT_USAGE)

    def test_too_long_file(self):
        res = self.run_cli("describe", self.mp4(seconds=700))
        self.assertEqual(res.code, core.EXIT_USAGE)
        self.assertEqual(self.fake.calls_to("POST"), [])

