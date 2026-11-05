"""Input files, multipart upload, result files, output encoding, account commands."""

import io
import json
import os
import sys
import unittest

from helpers import CliTest, core


class InputFileTests(CliTest):
    def test_sniffing(self):
        self.assertEqual(core.sniff_format(self.png()), "png")
        self.assertEqual(core.sniff_format(self.jpeg()), "jpeg")
        self.assertEqual(core.sniff_format(self.webp()), "webp")
        self.assertEqual(core.sniff_format(self.wav()), "wav")
        self.assertIsNone(core.sniff_format(self.write("x.png", b"not really an image")))

    def test_image_size_from_headers(self):
        self.assertEqual(core.image_size(self.png(width=640, height=480)), (640, 480))
        self.assertEqual(core.image_size(self.jpeg(width=320, height=200)), (320, 200))
        self.assertEqual(core.image_size(self.webp(width=300, height=150)), (300, 150))

    def test_wav_duration(self):
        self.assertAlmostEqual(core.media_duration(self.wav(seconds=2.5)), 2.5, places=2)

    def test_wrong_type_refused(self):
        fake_png = self.write("doc.png", b"%PDF-1.7 not an image")
        res = self.run_cli("ocr", fake_png, "--quote")
        self.assertEqual(res.code, core.EXIT_USAGE)
        self.assertEqual(self.fake.calls_to("POST"), [])

    def test_sensitive_paths_refused(self):
        key = self.write(os.path.join(".ssh", "id_rsa.png"), b"\x89PNG\r\n\x1a\nfake")
        dotenv = self.write(".env.png", b"\x89PNG\r\n\x1a\nfake")
        for path in (key, dotenv):
            res = self.run_cli("ocr", path, "--quote")
            self.assertEqual(res.code, core.EXIT_USAGE, path)
            self.assertIn("--allow-hidden-path", res.err)
        self.assertEqual(self.fake.calls_to("POST"), [])
        res = self.run_cli("ocr", key, "--quote", "--allow-hidden-path")
        self.assertEqual(res.code, 0, res.err)

    def test_size_limit(self):
        big = self.write("big.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * (10 * core.MB + 1))
        res = self.run_cli("ocr", big, "--quote")
        self.assertEqual(res.code, core.EXIT_USAGE)
        self.assertIn("10 MB", res.err)

    def test_urls_are_not_downloaded(self):
        res = self.run_cli("ocr", "https://example.com/scan.png", "--quote")
        self.assertEqual(res.code, core.EXIT_USAGE)
        self.assertIn("MCP", res.err)
        self.assertEqual(self.fake.calls, [])

    def test_job_reference_input(self):
        rid = "c08a339c-73e5-4d67-a4d5-231302fbff9a"
        self.fake.jobs[rid] = {"status": "done", "result_url": "https://results.example.test/a.png"}
        res = self.run_cli("upscale", "job:" + rid, "--quote")
        self.assertEqual(res.code, 0, res.err)
        download = [c for c in self.fake.calls if c.host == "results.example.test"][0]
        self.assertNotIn("authorization", download.headers)
        quote = self.fake.calls_to("POST", r"/price$")[0]
        self.assertEqual((quote.field("width"), quote.field("height")), ("1", "1"))

    def test_multipart_streams_and_escapes(self):
        if os.name == "nt":
            # Windows file names cannot hold quotes or newlines: build the part from a
            # plain file and give it the odd name directly.
            path = self.write("plain.png", b"\x89PNG\r\n\x1a\n" + b"\x01" * 1000)
            part = core.FilePart("image", os.path.join(self.tmp, 'we"ird\nname.png'), "png",
                                 os.path.getsize(path))
            part.path = path
        else:
            path = self.write('we"ird\nname.png', b"\x89PNG\r\n\x1a\n" + b"\x01" * 1000)
            part = core.check_file(path, "image", "image")
        body = core.Multipart({"model": "M", "flag": True, "tags": ["a", "b"]}, [part])
        chunks = list(body)
        self.assertGreater(len(chunks), 3)
        raw = b"".join(chunks)
        self.assertEqual(len(raw), body.length)
        self.assertIn(b'filename="we_ird_name.png"', raw)
        self.assertEqual(raw.count(b'name="tags"'), 2)
        self.assertIn(b"\r\n1\r\n", raw)
        self.assertEqual(b"".join(body), raw)   # re-iterable for retries

    def test_image_quote_by_size_without_upload(self):
        res = self.run_cli("ocr", self.png(width=800, height=600), "--quote")
        self.assertEqual(res.code, 0, res.err)
        quote = self.fake.calls_to("POST", r"/price$")[0]
        self.assertEqual((quote.field("width"), quote.field("height")), ("800", "600"))
        self.assertEqual(quote.files, {})

    def test_content_length_sent(self):
        res = self.run_cli("rmbg", self.png())
        self.assertEqual(res.code, 0, res.err)
        call = self.fake.submissions()[0]
        self.assertIn("image", call.files)
        self.assertEqual(int(call.headers["content-length"]), len(call.body))


class ResultFileTests(CliTest):
    def test_name_from_request_id_and_content_type(self):
        res = self.run_cli("image", "--prompt", "fox")
        path = res.json["files"][0]
        self.assertEqual(os.path.basename(path), res.json["request_id"] + ".png")

    def test_no_overwrite(self):
        out = os.path.join(self.tmp, "out")
        os.makedirs(out)
        open(os.path.join(out, "fox.png"), "wb").close()
        res = self.run_cli("image", "--prompt", "fox", "--output", "fox.png")
        self.assertEqual(os.path.basename(res.json["files"][0]), "fox-1.png")

    def test_text_result_saved_with_preview(self):
        self.fake.result_text = "zażółć gęślą jaźń → ok " * 200
        res = self.run_cli("transcribe", "https://youtu.be/abc", "--lang", "pl")
        self.assertEqual(res.code, 0, res.err)
        self.assertTrue(res.json["text_file"].endswith(".txt"))
        with open(res.json["text_file"], encoding="utf-8") as handle:
            self.assertEqual(handle.read(), self.fake.result_text)
        self.assertLessEqual(len(res.json["preview"]), core.PREVIEW_CHARS + 3)
        self.assertIn("zażółć gęślą jaźń", res.out)   # ensure_ascii=False

    def test_json_result_extension(self):
        self.fake.result_text = json.dumps({"text": "hi", "segments": []})
        res = self.run_cli("transcribe", "https://youtu.be/abc")
        self.assertTrue(res.json["text_file"].endswith(".json"))

    def test_cp1250_console(self):
        raw = io.BytesIO()
        stream = io.TextIOWrapper(raw, encoding="cp1250")
        saved = sys.stdout
        try:
            sys.stdout = stream
            core.utf8_streams()
            core.emit({"text": "zażółć gęślą jaźń → ok"})
            stream.flush()
        finally:
            sys.stdout = saved
        self.assertIn("→ ok".encode("utf-8"), raw.getvalue())


class AccountTests(CliTest):
    def test_doctor_hides_key_preview(self):
        res = self.run_cli("doctor")
        self.assertEqual(res.code, 0, res.err)
        self.assertNotIn("preview", res.json["key"])
        self.assertNotIn("sk-dp-1", res.out)
        self.assertNotIn("example key", res.out)
        self.assertTrue(res.json["key_set"])

    def test_doctor_warns_for_admin_without_limit(self):
        self.fake.key = dict(self.fake.key, role="admin", limit=None, limit_remaining=None)
        res = self.run_cli("doctor")
        self.assertEqual(len(res.json["warnings"]), 2)

    def test_doctor_without_key(self):
        res = self.run_cli("doctor", env={})
        self.assertEqual(res.code, 0)
        self.assertFalse(res.json["key_set"])
        self.assertEqual(self.fake.calls, [])

    def test_account_api_role(self):
        res = self.run_cli("account")
        self.assertEqual(res.code, 0, res.err)
        self.assertIsNone(res.json["balance"])
        self.assertIn("ADMIN", res.json["note"])
        self.assertEqual(self.fake.calls_to("GET", r"/account/balance"), [])
        res = self.run_cli("balance")
        self.assertEqual(res.code, 0)

    def test_account_admin_role(self):
        self.fake.key = dict(self.fake.key, role="admin")
        res = self.run_cli("account")
        self.assertEqual(res.json["balance"], 12.5)

    def test_models_oai(self):
        res = self.run_cli("models", "--oai")
        self.assertEqual(res.json, ["Flux1schnell"])
        self.assertEqual(self.fake.calls[0].host, "oai.deapi.ai")


if __name__ == "__main__":
    unittest.main()
