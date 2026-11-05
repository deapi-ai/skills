#!/usr/bin/env python3
"""Check that the skill script only uses paths and fields from the public API spec.

1. Static: every "/api/v2/..." string in deapi/scripts is a path of the spec.
2. Dynamic: every command module may define SAMPLES (argument lists). Each
   sample runs offline against tests/fakeapi.py, once with --quote and once
   for real; every request must hit a spec path with the right method, send
   only fields the spec's request body defines (query parameters for GET), and
   each paid POST must have a "/price" sibling. QUOTE_ONLY_SAMPLES run with
   --quote only (prices that have nothing to submit).
3. --drift SPEC (URL or file): compare another spec, e.g. the live one, with
   the snapshot and print added/removed paths and request fields. Never fails.

Snapshot: tests/fixtures/openapi.json (public spec, https://docs.deapi.ai/openapi.json).
Usage: python tools/check_openapi.py [--spec FILE] [--drift URL_OR_FILE]
"""

import argparse
import contextlib
import importlib
import io
import json
import os
import re
import shutil
import struct
import sys
import tempfile
import urllib.request
import wave
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "deapi", "scripts")
SNAPSHOT = os.path.join(ROOT, "tests", "fixtures", "openapi.json")
sys.path.insert(0, SCRIPTS)
sys.path.insert(0, os.path.join(ROOT, "tests"))


def load_spec(source):
    if re.match(r"^https?://", source):
        req = urllib.request.Request(source, headers={"User-Agent": "deapi-skills-ci"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    with open(source, encoding="utf-8") as handle:
        return json.load(handle)


class Spec(object):
    def __init__(self, data):
        self.data = data
        self.paths = data.get("paths") or {}
        self._regex = [(re.compile("^" + re.sub(r"\\\{[^}]+\\\}", "[^/]+", re.escape(p)) + "$"), p)
                       for p in self.paths]

    def resolve(self, node):
        seen = 0
        while isinstance(node, dict) and "$ref" in node and seen < 20:
            parts = node["$ref"].lstrip("#/").split("/")
            target = self.data
            for part in parts:
                target = target.get(part, {})
            node, seen = target, seen + 1
        return node

    def match(self, path):
        for regex, template in self._regex:
            if regex.match(path):
                return template
        return None

    def properties(self, schema):
        schema = self.resolve(schema) or {}
        props = set((schema.get("properties") or {}).keys())
        for key in ("allOf", "oneOf", "anyOf"):
            for sub in schema.get(key) or []:
                props |= self.properties(sub)
        return props

    def body_fields(self, template, method):
        op = self.paths[template].get(method.lower()) or {}
        body = self.resolve(op.get("requestBody") or {})
        fields = set()
        for content in (body.get("content") or {}).values():
            fields |= self.properties(content.get("schema") or {})
        return fields

    def query_params(self, template, method):
        op = self.paths[template].get(method.lower()) or {}
        names = set()
        for param in list(op.get("parameters") or []) + list(
                self.paths[template].get("parameters") or []):
            param = self.resolve(param)
            if param.get("in") == "query":
                names.add(param.get("name"))
        return names


def static_check(spec):
    errors = []
    for base, _, files in os.walk(SCRIPTS):
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(base, name)
            with open(path, encoding="utf-8") as handle:
                for number, line in enumerate(handle, 1):
                    for literal in re.findall(r"[\"'](/api/v2/[A-Za-z0-9_./{}-]*)[\"']", line):
                        probe = literal + "x" if literal.endswith("/") else literal
                        if not spec.match(probe):
                            errors.append("%s:%d: %s is not in the spec" % (
                                os.path.relpath(path, ROOT), number, literal))
    return errors


def sample_files(tmp):
    files = {}
    png = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", 64, 64) \
        + b"\x08\x02\x00\x00\x00" + b"\x00" * 16
    jpg = b"\xff\xd8\xff\xe0" + b"\x00" * 32
    for name, data in (("png", png), ("jpg", jpg), ("mp3", b"ID3\x03" + b"\x00" * 64),
                       ("mp4", b"\x00\x00\x00\x18ftypisom" + b"\x00" * 64)):
        files[name] = os.path.join(tmp, "sample." + name)
        with open(files[name], "wb") as handle:
            handle.write(data)
    files["wav"] = os.path.join(tmp, "sample.wav")
    with wave.open(files["wav"], "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(b"\x00\x00" * 8000)
    return files


def dynamic_check(spec):
    import fakeapi
    from deapi_lib import commands, core  # noqa: F401
    script = importlib.import_module("deapi")
    errors, ran = [], 0
    tmp = tempfile.mkdtemp(prefix="deapi-openapi-")
    try:
        files = sample_files(tmp)
        modules = script.command_modules()
        for module in modules:
            runs = [(s, (["--quote"], [])) for s in getattr(module, "SAMPLES", [])]
            runs += [(s, (["--quote"],)) for s in getattr(module, "QUOTE_ONLY_SAMPLES", [])]
            for sample, extras in runs:
                argv = [a.format(**files) for a in sample]
                for extra in extras:
                    fake = fakeapi.FakeAPI()
                    if any(a.startswith("transcribe") or a == "stt" for a in argv[:1]):
                        fake.result_text = "sample transcript"
                    out, err = io.StringIO(), io.StringIO()
                    env = {"DEAPI_API_KEY": fakeapi.TEST_KEY}
                    with mock.patch.dict(os.environ, env), \
                            mock.patch("urllib.request.urlopen", fake), \
                            mock.patch.object(core, "_sleep", lambda s: None), \
                            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                        try:
                            code = script.main(argv + extra + ["--out-dir", tmp, "--quiet"])
                        except SystemExit as exc:
                            code = exc.code
                    ran += 1
                    label = " ".join(sample + extra)
                    if code != 0:
                        errors.append("sample `%s` exited %s: %s" % (label, code,
                                                                     err.getvalue().strip()[-300:]))
                    errors.extend(check_calls(spec, fake.calls, label))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return errors, ran


def check_calls(spec, calls, label):
    errors = []
    for call in calls:
        if call.host != "api.deapi.ai":
            continue
        template = spec.match(call.path)
        if not template:
            errors.append("`%s`: %s %s is not in the spec" % (label, call.method, call.path))
            continue
        if call.method.lower() not in spec.paths[template]:
            errors.append("`%s`: %s not allowed on %s" % (label, call.method, template))
            continue
        if call.method == "GET":
            unknown = set(call.query) - spec.query_params(template, "GET")
        else:
            unknown = call.params() - spec.body_fields(template, call.method)
            if not call.path.endswith("/price") and not spec.match(call.path + "/price"):
                errors.append("`%s`: %s has no /price sibling" % (label, call.path))
        if unknown:
            errors.append("`%s`: %s %s sends fields not in the spec: %s" % (
                label, call.method, template, ", ".join(sorted(unknown))))
    return errors


def drift(spec, other):
    print("drift: snapshot %s paths, other %s paths" % (len(spec.paths), len(other.paths)))
    for path in sorted(set(other.paths) - set(spec.paths)):
        print("  + path %s" % path)
    for path in sorted(set(spec.paths) - set(other.paths)):
        print("  - path %s" % path)
    for path in sorted(set(spec.paths) & set(other.paths)):
        for method in ("get", "post", "patch", "delete"):
            if method in spec.paths[path] and method in other.paths[path]:
                old, new = spec.body_fields(path, method), other.body_fields(path, method)
                if old != new:
                    print("  ~ %s %s fields +%s -%s" % (method.upper(), path,
                                                        sorted(new - old), sorted(old - new)))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Check the script against the public spec.")
    parser.add_argument("--spec", default=SNAPSHOT)
    parser.add_argument("--drift", help="URL or file of a spec to compare with the snapshot")
    args = parser.parse_args(argv)
    spec = Spec(load_spec(args.spec))
    if args.drift:
        drift(spec, Spec(load_spec(args.drift)))
        return 0
    errors = static_check(spec)
    dyn_errors, ran = dynamic_check(spec)
    errors.extend(dyn_errors)
    for err in errors:
        print("FAIL " + err)
    print("checked %d sample run(s): %s" % (ran, "ok" if not errors else "%d problem(s)" % len(errors)))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
