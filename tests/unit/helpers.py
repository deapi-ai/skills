"""Test helpers: load deapi.py like an agent harness does and run it offline."""

import contextlib
import importlib.util
import io
import json
import os
import shutil
import struct
import sys
import tempfile
import unittest
import wave
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPT = os.path.join(ROOT, "deapi", "scripts", "deapi.py")
sys.path.insert(0, os.path.join(ROOT, "tests"))

import fakeapi  # noqa: E402


def load_script():
    spec = importlib.util.spec_from_file_location("deapi_script", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


deapi = load_script()
from deapi_lib import core  # noqa: E402  (importable once deapi.py ran)

TEST_KEY = fakeapi.TEST_KEY


class Clock(object):
    def __init__(self):
        self.t = 0.0
        self.sleeps = []

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.t += seconds


class Result(object):
    def __init__(self, code, out, err):
        self.code, self.out, self.err = code, out, err
        try:
            self.json = json.loads(out) if out.strip() else None
        except ValueError:
            self.json = None

    @property
    def error(self):
        return (self.json or {}).get("error") or {}


class CliTest(unittest.TestCase):
    """Base class: fresh fake API, temp dir, fake clock, test key in the env."""

    def setUp(self):
        self.fake = fakeapi.FakeAPI()
        self.tmp = tempfile.mkdtemp(prefix="deapi-test-")
        self.clock = Clock()
        core._SECRETS.clear()
        core._QUIET[0] = False
        self.env = {"DEAPI_API_KEY": TEST_KEY}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_cli(self, *argv, env=None, out_dir=True):
        argv = list(argv)
        if out_dir and "--out-dir" not in argv:
            argv += ["--out-dir", os.path.join(self.tmp, "out")]
        environ = dict(os.environ)
        environ.pop("DEAPI_MAX_COST", None)
        environ.pop("DEAPI_API_KEY", None)
        environ.update(self.env if env is None else env)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, environ, clear=True), \
                mock.patch("urllib.request.urlopen", self.fake), \
                mock.patch.object(core, "_sleep", self.clock.sleep), \
                mock.patch.object(core, "_now", self.clock.now), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = deapi.main(argv)
            except SystemExit as exc:   # argparse errors
                code = exc.code
        return Result(code, out.getvalue(), err.getvalue())

    # -- sample files --------------------------------------------------------

    def path(self, name):
        return os.path.join(self.tmp, name)

    def png(self, name="in.png", width=640, height=480):
        body = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + \
            struct.pack(">II", width, height) + b"\x08\x02\x00\x00\x00" + b"\x00" * 16
        return self.write(name, body)

    def jpeg(self, name="in.jpg", width=320, height=200):
        sof = b"\xff\xc0" + struct.pack(">HBHH", 17, 8, height, width) + b"\x03" + b"\x00" * 9
        return self.write(name, b"\xff\xd8\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" +
                          b"\x00" * 9 + sof + b"\xff\xd9")

    def webp(self, name="in.webp", width=300, height=150):
        body = b"VP8X" + struct.pack("<I", 10) + b"\x00\x00\x00\x00" + \
            (width - 1).to_bytes(3, "little") + (height - 1).to_bytes(3, "little")
        return self.write(name, b"RIFF" + struct.pack("<I", len(body) + 4) + b"WEBP" + body)

    def wav(self, name="in.wav", seconds=2.0, rate=8000):
        path = self.path(name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with wave.open(path, "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(rate)
            handle.writeframes(b"\x00\x00" * int(seconds * rate))
        return path

    def write(self, name, data):
        path = self.path(name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(data)
        return path
