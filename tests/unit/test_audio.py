"""Audio commands: tts, music, transcribe (offline, fake API, synthetic files)."""

import copy
import json
import os
import struct
import unittest
from unittest import mock

from helpers import CliTest, core  # first: puts tests/ and the script on sys.path

import fakeapi  # noqa: E402
from deapi_lib.commands import audio  # noqa: E402


def model(fake, slug):
    return next(m for m in fake.models if m["slug"] == slug)


class AudioTest(CliTest):
    def setUp(self):
        super().setUp()
        # Lengths must come from the stdlib readers, even where ffprobe is installed.
        patcher = mock.patch.object(core.shutil, "which", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def quotes(self):
        return self.fake.calls_to("POST", r"/price$")

    def quote(self):
        return self.quotes()[-1]

    def submission(self):
        return self.fake.submissions()[-1]

    def no_posts(self):
        self.assertEqual(self.fake.calls_to("POST"), [])

    # -- synthetic media (headers only, enough for the length readers) -------

    def mp3_cbr(self, name="talk.mp3", frames=100):
        frame = b"\xff\xfb\x90\x00" + b"\x00" * 413      # MPEG1 L3 128 kbps 44.1 kHz
        return self.write(name, b"ID3\x03\x00\x00\x00\x00\x00\x00" + frame * frames)

    def mp3_xing(self, name="vbr.mp3", total=1000):
        first = bytearray(b"\xff\xfb\x90\x00" + b"\x00" * 413)
        first[36:48] = b"Xing" + struct.pack(">II", 1, total)
        frame = b"\xff\xfb\x90\x00" + b"\x00" * 413
        return self.write(name, bytes(first) + frame * 3)

    def mp4(self, name="clip.mp4", timescale=1000, duration=12345):
        ftyp = struct.pack(">I4s", 16, b"ftyp") + b"isom\x00\x00\x02\x00"
        mdat = struct.pack(">I4s", 24, b"mdat") + b"\x00" * 16
        mvhd_body = b"\x00\x00\x00\x00" + struct.pack(">IIII", 0, 0, timescale, duration) + \
            b"\x00" * 80
        mvhd = struct.pack(">I4s", 8 + len(mvhd_body), b"mvhd") + mvhd_body
        moov = struct.pack(">I4s", 8 + len(mvhd), b"moov") + mvhd
        return self.write(name, ftyp + mdat + moov)

    def flac(self, name="song.flac", rate=16000, seconds=7):
        packed = (rate << 44) | (0 << 41) | (15 << 36) | (rate * seconds)
        info = struct.pack(">HH", 4096, 4096) + b"\x00" * 6 + struct.pack(">Q", packed) + \
            b"\x00" * 16
        return self.write(name, b"fLaC" + b"\x80" + len(info).to_bytes(3, "big") + info)

    def ogg_opus(self, name="voice.ogg", seconds=5, pre_skip=312):
        first = b"OggS\x00\x02" + struct.pack("<q", 0) + b"\x00" * 14 + \
            b"OpusHead\x01\x01" + struct.pack("<H", pre_skip) + b"\x00" * 8
        last = b"OggS\x00\x04" + struct.pack("<q", 48000 * seconds + pre_skip) + b"\x00" * 20
        return self.write(name, first + b"\x00" * 100 + last)

    def ogg_vorbis(self, name="music.ogg", rate=22050, seconds=4):
        first = b"OggS\x00\x02" + struct.pack("<q", 0) + b"\x00" * 14 + \
            b"\x01vorbis" + struct.pack("<IBI", 0, 1, rate) + b"\x00" * 10
        last = b"OggS\x00\x04" + struct.pack("<q", rate * seconds) + b"\x00" * 20
        return self.write(name, first + last)


# ---------------------------------------------------------------- tts ---

class TtsTests(AudioTest):
    def test_default_preset_voice(self):
        self.fake.result_ext = "mp3"
        res = self.run_cli("tts", "--text", "Hello world", "--lang", "en")
        self.assertEqual(res.code, 0, res.err)
        self.assertEqual(res.json["model"], "Kokoro")
        self.assertEqual((res.json["mode"], res.json["lang"], res.json["voice"]),
                         ("custom_voice", "en-us", "af_example"))
        quote = self.quote()
        self.assertIsNotNone(quote.json)                   # the quote is JSON
        self.assertEqual(quote.json["voice"], "af_example")
        self.assertEqual(quote.json["sample_rate"], 24000)
        sub = self.submission()
        self.assertEqual(sub.field("text"), "Hello world")
        self.assertEqual(sub.field("format"), "mp3")
        self.assertTrue(res.json["files"][0].endswith(".mp3"))

    def test_voice_by_name_any_case(self):
        res = self.run_cli("tts", "--text", "Hello world", "--voice", "AM_EXAMPLE", "--quote")
        self.assertEqual(res.code, 0, res.err)
        self.assertEqual(self.quote().json["voice"], "am_example")

    def test_voice_from_another_language(self):
        res = self.run_cli("tts", "--text", "Hola a todos", "--voice", "ef_example", "--quote")
        self.assertEqual(res.code, 0, res.err)
        self.assertEqual(self.quote().json["lang"], "es")

    def test_regional_variants_of_a_language(self):
        kokoro = model(self.fake, "Kokoro")
        kokoro["languages"].append({"name": "English (UK)", "slug": "en-gb", "voices": [
            {"name": "Bf_Example", "slug": "bf_example", "gender": "female"}]})
        res = self.run_cli("tts", "--text", "Hello world", "--lang", "en", "--voice",
                           "bf_example", "--quote")
        self.assertEqual(res.code, 0, res.err)
        self.assertEqual((self.quote().json["lang"], self.quote().json["voice"]),
                         ("en-gb", "bf_example"))
        res = self.run_cli("tts", "--list-voices", "--lang", "en")
        self.assertEqual([l["lang"] for l in res.json["languages"]], ["en-us", "en-gb"])

    def test_voice_picks_model(self):
        res = self.run_cli("tts", "--text", "Hello world again", "--voice", "example_voice",
                           "--quote")
        self.assertEqual(res.json["model"], "Qwen3_TTS_12Hz_1_7B_CustomVoice")

    def test_unknown_voice(self):
        res = self.run_cli("tts", "--text", "Hello world", "--model", "Kokoro", "--lang", "en",
                           "--voice", "nobody", "--quote")
        self.assertEqual(res.code, core.EXIT_USAGE)
        self.assertIn("af_example", res.err)
        self.no_posts()

    def test_gender(self):
        kokoro = model(self.fake, "Kokoro")
        kokoro["languages"][0]["voices"][1]["gender"] = "male"
        res = self.run_cli("tts", "--text", "Hello world", "--gender", "male", "--quote")
        self.assertEqual(self.quote().json["voice"], "am_example")
        res = self.run_cli("tts", "--text", "Hello world", "--lang", "es", "--gender", "male",
                           "--model", "Kokoro", "--quote")
        self.assertEqual(res.code, core.EXIT_USAGE)

    def test_language_without_model(self):
        res = self.run_cli("tts", "--text", "Guten Tag zusammen", "--lang", "de", "--quote")
        self.assertEqual(res.code, core.EXIT_VALIDATION)
        res = self.run_cli("tts", "--text", "Dzień dobry wszystkim", "--lang", "pl",
                           "--model", "Kokoro", "--quote")
        self.assertEqual(res.code, core.EXIT_VALIDATION)
        self.assertIn("en-us", res.err)
        self.no_posts()

    def test_text_limit_is_an_error_not_a_split(self):
        res = self.run_cli("tts", "--text", "a" * 2001, "--model", "Chatterbox")
        self.assertEqual(res.code, core.EXIT_USAGE)
        self.assertEqual((res.error["max_text"], res.error["chars"]), (2000, 2001))
        self.assertIn("Split it", res.error["message"])
        self.no_posts()
        res = self.run_cli("tts", "--text", "a" * 2000, "--model", "Chatterbox", "--quote")
        self.assertEqual(res.code, 0, res.err)

    def test_min_text(self):
        res = self.run_cli("tts", "--text", "Hi", "--model", "Chatterbox", "--quote")
        self.assertEqual(res.code, core.EXIT_USAGE)
        self.assertEqual(res.error["min_text"], 10)

    def test_text_file(self):
        path = self.write("story.txt", "﻿Once upon a time.\n".encode("utf-8"))
        res = self.run_cli("tts", "--text-file", path, "--quote")
        self.assertEqual(res.code, 0, res.err)
        self.assertEqual(self.quote().json["text"], "Once upon a time.")
        res = self.run_cli("tts", "--text", "x", "--text-file", path)
        self.assertEqual(res.code, core.EXIT_USAGE)
        res = self.run_cli("tts")
        self.assertEqual(res.code, core.EXIT_USAGE)

    def test_format_and_sample_rate_from_card(self):
        res = self.run_cli("tts", "--text", "Hello world", "--format", "wav", "--quote")
        self.assertEqual(res.code, core.EXIT_USAGE)
        self.assertEqual(res.error["output_formats"], ["mp3"])
        model(self.fake, "Kokoro")["info"]["limits"]["available_ratios"] = [24000, 44100]
        res = self.run_cli("tts", "--text", "Hello world", "--sample-rate", "16000", "--quote")
        self.assertEqual(res.code, core.EXIT_USAGE)
        res = self.run_cli("tts", "--text", "Hello world", "--sample-rate", "44100", "--quote")
        self.assertEqual(self.quote().json["sample_rate"], 44100)

    def test_speed_clamped(self):
        self.run_cli("tts", "--text", "Hello world", "--speed", "3", "--quote")
        self.assertEqual(self.quote().json["speed"], 2)

    def test_voice_clone(self):
        clip = self.wav("voice.wav", seconds=6)
        res = self.run_cli("tts", "--text", "Hello there, friend", "--clone-audio", clip,
                           "--ref-text", "what the clip says")
        self.assertEqual(res.code, 0, res.err)
        self.assertEqual(res.json["model"], "Example_TTS_Clone_Base")
        quote = self.quote()
        self.assertEqual(quote.json["mode"], "voice_clone")
        self.assertNotIn("ref_text", quote.json)           # not in the price schema
        self.assertNotIn("voice", quote.json)
        sub = self.submission()
        self.assertIn("ref_audio", sub.files)
        self.assertEqual(sub.field("ref_text"), "what the clip says")
        self.assertIsNone(sub.field("voice"))

    def test_voice_clone_length_checked(self):
        res = self.run_cli("tts", "--text", "Hello there, friend",
                           "--clone-audio", self.wav("short.wav", seconds=2))
        self.assertEqual(res.code, core.EXIT_USAGE)
        self.assertEqual(res.error["min_seconds"], 5)
        res = self.run_cli("tts", "--text", "Hello there, friend",
                           "--clone-audio", self.wav("long.wav", seconds=20))
        self.assertEqual(res.code, core.EXIT_USAGE)
        self.assertIn("ffmpeg -i", res.error["hint"])
        self.assertIn("-t 15", res.error["hint"])
        self.no_posts()

    def test_clone_rejects_preset_flags(self):
        clip = self.wav("voice.wav", seconds=6)
        res = self.run_cli("tts", "--text", "Hello there", "--clone-audio", clip,
                           "--voice", "af_example")
        self.assertEqual(res.code, core.EXIT_USAGE)
        res = self.run_cli("tts", "--text", "Hello there", "--ref-text", "hi")
        self.assertEqual(res.code, core.EXIT_USAGE)
        res = self.run_cli("tts", "--text", "Hello there", "--clone-audio", clip,
                           "--design", "calm")
        self.assertEqual(res.code, core.EXIT_USAGE)

    def test_voice_design(self):
        res = self.run_cli("tts", "--text", "Hello there, friend", "--lang", "en",
                           "--design", "warm British narrator")
        self.assertEqual(res.code, 0, res.err)
        self.assertEqual(self.quote().json["instruct"], "warm British narrator")
        self.assertEqual(self.quote().json["lang"], "English")
        self.assertEqual(self.submission().field("instruct"), "warm British narrator")

    def test_design_language_missing(self):
        res = self.run_cli("tts", "--text", "Dzień dobry wszystkim", "--lang", "pl",
                           "--design", "calm")
        self.assertEqual(res.code, core.EXIT_VALIDATION)

    def test_explicit_model_without_preset_voices(self):
        res = self.run_cli("tts", "--text", "Hello there, friend", "--model",
                           "Example_TTS_Clone_Base", "--quote")
        self.assertEqual(res.code, core.EXIT_VALIDATION)

    def test_list_voices_is_free(self):
        res = self.run_cli("tts", "--list-voices", "--lang", "es")
        self.assertEqual(res.code, 0, res.err)
        self.assertEqual(res.json["model"], "Kokoro")
        self.assertEqual([l["lang"] for l in res.json["languages"]], ["es"])
        self.assertEqual(res.json["languages"][0]["voices"][0]["voice"], "ef_example")
        self.no_posts()

    def test_quote_only_and_cost_limit(self):
        res = self.run_cli("tts", "--text", "Hello world", "--quote")
        self.assertTrue(res.json["quote_only"])
        self.assertEqual(self.fake.submissions(), [])
        self.fake.price = 0.3
        res = self.run_cli("tts", "--text", "Hello world")
        self.assertEqual(res.code, core.EXIT_OVER_BUDGET)
        self.assertEqual(self.fake.submissions(), [])


# -------------------------------------------------------------- music ---

class MusicTests(AudioTest):
    def test_defaults(self):
        res = self.run_cli("music", "--caption", "calm lofi piano", "--seed", "5")
        self.assertEqual(res.code, 0, res.err)
        quote = self.quote().json
        self.assertEqual(quote["duration"], 30)
        self.assertEqual((quote["inference_steps"], quote["guidance_scale"]), (8, 1.0))
        self.assertNotIn("seed", quote)
        self.assertNotIn("format", quote)
        sub = self.submission()
        self.assertEqual(sub.field("seed"), "5")
        self.assertEqual(sub.field("lyrics"), "[Instrumental]")
        self.assertTrue(res.json["instrumental"])
        self.assertEqual(res.json["seed"], 5)

    def test_pinned_steps_replace_user_value(self):
        res = self.run_cli("music", "--caption", "calm piano", "--steps", "30", "--quote")
        self.assertEqual(self.quote().json["inference_steps"], 8)
        self.assertIn("replaced by 8", res.err)

    def test_free_steps_clamped(self):
        limits = model(self.fake, "AceStep_1_5_Turbo")["info"]["limits"]
        limits.update(min_steps=8, max_steps=50)
        self.run_cli("music", "--caption", "calm piano", "--steps", "80", "--quote")
        self.assertEqual(self.quote().json["inference_steps"], 50)

    def test_duration_range_is_an_error(self):
        for bad in ("5", "301"):
            res = self.run_cli("music", "--caption", "calm piano", "--duration", bad)
            self.assertEqual(res.code, core.EXIT_USAGE, bad)
            self.assertEqual(res.error["max_duration"], 300)
        self.no_posts()

    def test_duration_cap_by_format(self):
        limits = model(self.fake, "AceStep_1_5_Turbo")["info"]["limits"]
        limits["max_duration_by_format"] = {"mp3": 60}
        res = self.run_cli("music", "--caption", "calm piano", "--duration", "90")
        self.assertEqual(res.code, core.EXIT_USAGE)
        self.assertEqual(res.error["max_duration"], 60)

    def test_caption_length(self):
        model(self.fake, "AceStep_1_5_Turbo")["info"]["limits"].update(min_caption=3,
                                                                       max_caption=10)
        res = self.run_cli("music", "--caption", "a very long caption indeed")
        self.assertEqual(res.code, core.EXIT_USAGE)
        self.assertIn("3..10", res.err)

    def test_song_fields(self):
        lyrics = self.write("song.txt", b"[verse]\nla la la\n")
        res = self.run_cli("music", "--caption", "upbeat pop", "--lyrics-file", lyrics,
                           "--bpm", "120", "--key", "C major", "--time-signature", "3",
                           "--vocal-lang", "EN")
        self.assertEqual(res.code, 0, res.err)
        quote = self.quote().json
        self.assertEqual(quote["lyrics"], "[verse]\nla la la")
        self.assertEqual((quote["bpm"], quote["keyscale"], quote["timesignature"],
                          quote["vocal_language"]), (120, "C major", 3, "en"))
        self.assertFalse(res.json["instrumental"])

    def test_bad_song_fields(self):
        for argv in (["--bpm", "20"], ["--vocal-lang", "English"],
                     ["--lyrics", "la", "--instrumental"], ["--time-signature", "5"]):
            res = self.run_cli("music", "--caption", "pop", *argv)
            self.assertEqual(res.code, core.EXIT_USAGE, argv)
        self.no_posts()

    def test_format_from_card(self):
        res = self.run_cli("music", "--caption", "pop", "--format", "flac")
        self.assertEqual(res.code, core.EXIT_USAGE)

    def test_reference_audio(self):
        model(self.fake, "AceStep_1_5_Turbo")["info"]["limits"].update(
            min_ref_audio_duration=5, max_ref_audio_duration=60)
        res = self.run_cli("music", "--caption", "pop",
                           "--reference-audio", self.wav("ref.wav", seconds=2))
        self.assertEqual(res.code, core.EXIT_USAGE)
        res = self.run_cli("music", "--caption", "pop",
                           "--reference-audio", self.wav("ref2.wav", seconds=6))
        self.assertEqual(res.code, 0, res.err)
        self.assertIn("reference_audio", self.submission().files)
        self.assertNotIn("reference_audio", self.quote().json)


# --------------------------------------------------------- transcribe ---

class TranscribeTests(AudioTest):
    def setUp(self):
        super().setUp()
        self.fake.result_text = "plain transcript"

    def test_link_defaults(self):
        res = self.run_cli("stt", "https://www.youtube.com/watch?v=abc", "--lang", "en")
        self.assertEqual(res.code, 0, res.err)
        self.assertEqual(res.json["model"], "WhisperLargeV3Ct2")
        self.assertEqual(res.json["platform"], "youtube")
        quote, sub = self.quote(), self.submission()
        self.assertEqual(quote.field("source_url"), "https://www.youtube.com/watch?v=abc")
        self.assertIsNone(quote.field("return_result_in_response"))
        self.assertEqual(sub.field("return_result_in_response"), "1")
        self.assertIsNone(sub.field("include_ts"))

    def test_x_spaces_link(self):
        res = self.run_cli("transcribe", "https://x.com/i/spaces/1abc", "--quote")
        self.assertEqual(res.code, 0, res.err)
        self.assertEqual(res.json["platform"], "twitter")

    def test_tiktok_needs_length(self):
        res = self.run_cli("transcribe", "https://www.tiktok.com/@a/video/1")
        self.assertEqual(res.code, core.EXIT_USAGE)
        self.assertIn("--duration", res.error["message"])
        self.assertEqual(self.fake.calls, [])
        res = self.run_cli("transcribe", "https://vm.tiktok.com/xyz", "--duration", "40",
                           "--metadata", "--quote")
        quote = self.quote()
        self.assertEqual((quote.field("platform"), quote.field("duration_seconds")),
                         ("tiktok", "40.0"))
        self.assertEqual(quote.field("include_metadata"), "1")
        self.assertIsNone(quote.field("source_url"))

    def test_other_links_refused(self):
        res = self.run_cli("transcribe", "https://example.com/a.mp3")
        self.assertEqual(res.code, core.EXIT_USAGE)
        self.assertEqual(self.fake.calls, [])

    def test_diarize_and_word_level(self):
        res = self.run_cli("transcribe", "https://youtu.be/abc", "--diarize", "--ts-level",
                           "word", "--quote")
        self.assertEqual(res.code, 0, res.err)
        quote = self.quote()
        self.assertEqual((quote.field("diarize"), quote.field("include_ts"),
                          quote.field("ts_level")), ("1", "1", "word"))
        res = self.run_cli("transcribe", "https://youtu.be/abc", "--diarize",
                           "--model", "WhisperLargeV3", "--quote")
        self.assertEqual(res.code, core.EXIT_VALIDATION)

    def test_ts_level_checked(self):
        res = self.run_cli("transcribe", "https://youtu.be/abc", "--ts-level", "word",
                           "--model", "WhisperLargeV3", "--quote")
        self.assertEqual(res.code, core.EXIT_USAGE)
        res = self.run_cli("transcribe", "https://youtu.be/abc", "--ts-level", "char",
                           "--quote")
        self.assertEqual(res.code, core.EXIT_VALIDATION)
        self.no_posts()

    def test_model_language_list(self):
        model(self.fake, "WhisperLargeV3Ct2")["info"]["limits"]["languages"] = ["en", "de"]
        res = self.run_cli("transcribe", "https://youtu.be/abc", "--lang", "pl",
                           "--model", "WhisperLargeV3Ct2", "--quote")
        self.assertEqual(res.code, core.EXIT_VALIDATION)
        res = self.run_cli("transcribe", "https://youtu.be/abc", "--lang", "pl", "--quote")
        self.assertEqual(res.json["model"], "WhisperLargeV3")

    def test_file_lengths_from_headers(self):
        cases = [(self.mp3_cbr(), 100 * 417 * 8 / 128000.0),
                 (self.mp3_xing(), 1000 * 1152 / 44100.0),
                 (self.mp4(), 12.345),
                 (self.mp4("audio.m4a", 44100, 44100 * 3), 3.0),
                 (self.flac(), 7.0),
                 (self.ogg_opus(), 5.0),
                 (self.ogg_vorbis(), 4.0)]
        for path, seconds in cases:
            res = self.run_cli("transcribe", path, "--quote")
            self.assertEqual(res.code, 0, (path, res.err))
            quote = self.quote()
            self.assertEqual(quote.files, {}, path)
            self.assertAlmostEqual(float(quote.field("duration_seconds")), seconds, places=1,
                                   msg=path)
            self.assertEqual(res.json["duration_from"], "file header")

    def test_unknown_length_uploads_for_quote(self):
        path = self.write("odd.mp3", b"ID3\x03" + b"\x00" * 64)
        res = self.run_cli("transcribe", path)
        self.assertEqual(res.code, 0, res.err)
        self.assertIn("source_file", self.quote().files)
        self.assertIn("uploaded twice", res.err)
        res = self.run_cli("transcribe", path, "--duration", "61", "--metadata", "--quote")
        quote = self.quote()
        self.assertEqual(quote.files, {})
        self.assertEqual(quote.field("duration_seconds"), "61.0")
        self.assertIsNone(quote.field("include_metadata"))

    def test_bad_duration(self):
        res = self.run_cli("transcribe", "https://youtu.be/abc", "--duration", "0")
        self.assertEqual(res.code, core.EXIT_USAGE)

    def test_json_transcript_summary(self):
        doc = fakeapi.load_fixture("audio_transcript.json")
        self.fake.result_text = json.dumps(doc)
        res = self.run_cli("transcribe", "https://youtu.be/abc", "--diarize")
        self.assertEqual(res.code, 0, res.err)
        self.assertTrue(res.json["text_file"].endswith(".json"))
        self.assertEqual(res.json["preview"], doc["text"])
        self.assertEqual(res.json["speakers"], ["SPEAKER_00", "SPEAKER_01"])
        self.assertEqual(res.json["segments"], 4)
        self.assertNotIn("subtitles_file", res.json)

    def test_srt(self):
        self.fake.result_text = json.dumps(fakeapi.load_fixture("audio_transcript.json"))
        res = self.run_cli("transcribe", self.mp3_cbr(), "--diarize", "--subtitles", "srt",
                           "--output", "talk.json")
        self.assertEqual(res.code, 0, res.err)
        self.assertEqual(self.submission().field("include_ts"), "1")
        path = res.json["subtitles_file"]
        self.assertEqual(os.path.basename(path), "talk.srt")
        with open(path, encoding="utf-8") as handle:
            srt = handle.read()
        self.assertTrue(srt.startswith("1\n00:00:00,000 --> 00:00:01,520\n"
                                       "SPEAKER_00: Hello there and welcome.\n"))
        self.assertIn("01:01:01,000 --> 01:01:02,500", srt)
        self.assertNotIn("untimed", srt)
        self.assertGreaterEqual(res.json["subtitle_cues"], 4)   # long segment split by words
        for block in srt.strip().split("\n\n"):
            for line in block.splitlines()[2:]:
                self.assertLessEqual(len(line), 2 * audio.SUBTITLE_LINE + 12)

    def test_vtt(self):
        self.fake.result_text = json.dumps(fakeapi.load_fixture("audio_transcript.json"))
        res = self.run_cli("transcribe", "https://youtu.be/abc", "--subtitles", "vtt")
        with open(res.json["subtitles_file"], encoding="utf-8") as handle:
            vtt = handle.read()
        self.assertTrue(vtt.startswith("WEBVTT\n\n00:00:00.000 --> 00:00:01.520\n"
                                       "<v SPEAKER_00>Hello there and welcome."))
        self.assertTrue(res.json["subtitles_file"].endswith(".vtt"))

    def test_subtitles_from_inline_timestamps(self):
        self.fake.result_text = "[0:00 - 0:02.5] Hello there.\n[0:03 - 1:02:03] Bye now.\n"
        res = self.run_cli("transcribe", "https://youtu.be/abc", "--model", "WhisperLargeV3",
                           "--subtitles", "srt")
        with open(res.json["subtitles_file"], encoding="utf-8") as handle:
            srt = handle.read()
        self.assertIn("00:00:00,000 --> 00:00:02,500\nHello there.", srt)
        self.assertIn("00:00:03,000 --> 01:02:03,000\nBye now.", srt)

    def test_subtitles_without_segments_warn(self):
        res = self.run_cli("transcribe", "https://youtu.be/abc", "--subtitles", "srt")
        self.assertEqual(res.code, 0, res.err)
        self.assertNotIn("subtitles_file", res.json)
        self.assertIn("no timed segments", res.err)

    def test_quote_writes_nothing(self):
        res = self.run_cli("transcribe", "https://youtu.be/abc", "--subtitles", "srt",
                           "--quote")
        self.assertTrue(res.json["quote_only"])
        self.assertEqual(self.quote().field("include_ts"), "1")
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "out")))

    def test_unreadable_link_hint(self):
        self.fake.route("POST", r"/transcriptions/price$", (422, {
            "message": "Cannot fetch media metadata.",
            "errors": {"source_url": ["Cannot fetch media metadata."]}}))
        res = self.run_cli("transcribe", "https://x.com/u/status/1")
        self.assertEqual(res.code, core.EXIT_VALIDATION)
        self.assertIn("download the file", res.error["hint"])

    def test_empty_transcript_warns(self):
        self.fake.result_text = json.dumps({"text": "", "segments": []})
        res = self.run_cli("transcribe", "https://youtu.be/abc")
        self.assertEqual(res.code, 0, res.err)
        self.assertIn("empty", res.err)


class SubtitleUnitTests(unittest.TestCase):
    def test_stamp_rounding(self):
        self.assertEqual(audio._stamp(59.9996, ","), "00:01:00,000")
        self.assertEqual(audio._stamp(0.0014, "."), "00:00:00.001")

    def test_vtt_escapes_arrow_and_speaker(self):
        vtt = audio.to_vtt([(0, 1, "a --> b", "<x>")])
        self.assertIn("<v _x_>a -> b", vtt)

    def test_media_length_garbage(self):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as handle:
            handle.write(b"\x00\x00\x00\x18ftypisom" + b"\xff" * 40)
        try:
            self.assertIsNone(audio.media_length(handle.name))
        finally:
            os.unlink(handle.name)


if __name__ == "__main__":
    unittest.main()
