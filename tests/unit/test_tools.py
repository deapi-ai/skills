"""Tests for the repository tools (frontmatter, leak scan, MCP names, scanner patterns)."""

import importlib.util
import os
import shutil
import tempfile
import unittest

from helpers import ROOT


def load_tool(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, "tools", name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


frontmatter = load_tool("check_frontmatter")
leaks = load_tool("scan_leaks")
mcp_names = load_tool("check_mcp_names")
safety = load_tool("check_skill_safety")

GOOD = '''---
name: demo
description: Transcribes audio and makes images and video via deAPI. Uses deAPI for many media jobs. Quotes the price before every paid job.
license: MIT
compatibility: Runs in agents with a terminal and network access. Needs Python 3.9+ and DEAPI_API_KEY.
metadata:
  version: "2.0.0"
  author: "deAPI"
  openclaw: {"requires": {"env": ["DEAPI_API_KEY"], "bins": ["python3"]}, "primaryEnv": "DEAPI_API_KEY"}
  hermes: {"tags": ["media"], "category": "creative"}
---

# Demo

See references/errors.md. Optional: DEAPI_MAX_COST.
'''


class FrontmatterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.skill = os.path.join(self.tmp, "demo")
        os.makedirs(os.path.join(self.skill, "references"))
        os.makedirs(os.path.join(self.skill, "scripts"))
        with open(os.path.join(self.skill, "references", "errors.md"), "w") as handle:
            handle.write("# Errors\n")
        with open(os.path.join(self.skill, "scripts", "x.py"), "w") as handle:
            handle.write('KEY_ENV = "DEAPI_API_KEY"\nos.environ.get("DEAPI_MAX_COST")\n')

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def write(self, text):
        with open(os.path.join(self.skill, "SKILL.md"), "w", encoding="utf-8") as handle:
            handle.write(text)
        return frontmatter.check_skill(self.skill)

    def test_plan_frontmatter_passes(self):
        self.assertEqual(self.write(GOOD), [])

    def test_extra_top_level_field(self):
        errors = self.write(GOOD.replace("license: MIT", "license: MIT\nversion: 2.0.0"))
        self.assertTrue(any("unexpected top-level" in e for e in errors))

    def test_long_first_sentence(self):
        # 60 chars: the Hermes skill index would cut it before "deAPI".
        errors = self.write(GOOD.replace("Transcribes audio and makes images and video via deAPI.",
                                         "Transcribes audio, makes images, video and speech via deAPI."))
        self.assertTrue(any("first sentence" in e for e in errors))

    def test_first_sentence_names_deapi(self):
        errors = self.write(GOOD.replace("Transcribes audio and makes images and video via deAPI.",
                                         "Transcribes audio and makes images and video."))
        self.assertTrue(any("first sentence" in e for e in errors))

    def test_angle_brackets_and_length(self):
        errors = self.write(GOOD.replace("many media jobs", "<b>jobs</b> " + "x" * 1100))
        self.assertTrue(any("< or >" in e for e in errors))
        self.assertTrue(any("max 1024" in e for e in errors))

    def test_name_must_match_folder(self):
        errors = self.write(GOOD.replace("name: demo", "name: other"))
        self.assertTrue(any("folder name" in e for e in errors))

    def test_missing_reference_and_machine_path(self):
        errors = self.write(GOOD + "\nSee references/missing.md and /tmp/out.\n")
        self.assertTrue(any("missing.md does not exist" in e for e in errors))
        self.assertTrue(any("machine-specific path" in e for e in errors))

    def test_unquoted_colon_is_invalid_yaml(self):
        errors = self.write(GOOD.replace("Uses deAPI for many", "Uses deAPI: for many"))
        self.assertTrue(any("needs quotes" in e for e in errors), errors)
        quoted = GOOD.replace(
            "description: Transcribes audio and makes images and video via deAPI. Uses deAPI for many media jobs. Quotes the price before every paid job.",
            'description: "Transcribes audio and makes images and video via deAPI. Uses deAPI: many media jobs."')
        self.assertEqual(self.write(quoted), [])

    def test_undeclared_env_var(self):
        errors = self.write(GOOD.replace("Optional: DEAPI_MAX_COST.", ""))
        self.assertTrue(any("DEAPI_MAX_COST" in e for e in errors))


class LeakScanTests(unittest.TestCase):
    def scan(self, text, path="deapi/x.md"):
        return [f for f in leaks.scan_file(path, text, leaks.PATTERNS) if f.blocking]

    def test_clean_text(self):
        clean = ("export DEAPI_API_KEY=sk-dp-...\nAuthorization: Bearer $DEAPI_API_KEY\n"
                 "key sk-dp-<id>-<secret>, test key " + leaks.PLACEHOLDER_KEY + "\n"
                 "support@deapi.ai https://api.deapi.ai 127.0.0.1 owned_by deapi\n")
        self.assertEqual(self.scan(clean), [])

    def test_detects_leaks(self):
        samples = {
            "api-key": "sk-dp-" + "12-" + "a1B2c3D4e5F6g7H8",
            "bearer": "Authorization: Bearer " + "abcdEFGH1234ijklMNOP",
            "host": "https://api-" + "sandbox.example.com",
            "task-number": "fixes " + "DEV" + "-1234",
            "session": "Claude" + "-Session: x",
            "local-path": "C:" + "\\Users\\someone\\file",
            "email": "jane.doe" + "@company.io",
            "ip-address": "8.8." + "4.4",
        }
        for category, text in samples.items():
            found = self.scan(text)
            self.assertTrue(found, category)
            self.assertEqual(found[0].category, category)
            if category == "api-key":
                self.assertNotIn("a1B2c3D4e5F6", str(found[0]))   # masked

    def test_allow_marker(self):
        self.assertEqual(self.scan("jane.doe" + "@company.io  # leak-scan: allow"), [])

    def test_fixture_preview(self):
        bad = '{"preview": "sk-dp-7-...' + 'f3a"}'
        found = [f for f in leaks.scan_file("tests/fixtures/key2.json", bad, leaks.PATTERNS)]
        self.assertTrue(any(f.category == "fixture-preview" for f in found))
        ok = '{"preview": "sk-dp-1-...0000"}'
        self.assertEqual(leaks.scan_file("tests/fixtures/key2.json", ok, leaks.PATTERNS), [])

    def test_chat_words_only_warn(self):
        found = leaks.scan_file("deapi/SKILL.md", "LLM chat models", leaks.PATTERNS)
        self.assertTrue(found and not any(f.blocking for f in found))

    def test_repository_is_clean(self):
        findings = []
        for rel in leaks.tree_files():
            text = leaks.read_tree_file(rel)
            if text is not None:
                findings.extend(f for f in leaks.scan_file(rel, text, leaks.PATTERNS) if f.blocking)
        self.assertEqual([str(f) for f in findings], [])


class McpNamesTests(unittest.TestCase):
    def test_snapshot_has_recommended_set(self):
        known = mcp_names.names_from_tools_list(mcp_names.SNAPSHOT)
        self.assertEqual(len(mcp_names.RECOMMENDED), 29)
        self.assertFalse(set(mcp_names.RECOMMENDED) - set(known))

    def test_typos_and_prefixed_names(self):
        known = mcp_names.names_from_tools_list(mcp_names.SNAPSHOT)
        tmp = tempfile.mkdtemp()
        try:
            doc = os.path.join(tmp, "doc.md")
            with open(doc, "w") as handle:
                handle.write("Use `text_to_imgae` or `mcp__deapi__no_such_tool`, `foo_price`.\n"
                             "Fields `guidance_scale`, `webhook_url`; tool `text_to_image`.\n")
            errors = mcp_names.check(known, [doc])
        finally:
            shutil.rmtree(tmp)
        self.assertEqual(len(errors), 3, errors)


if __name__ == "__main__":
    unittest.main()


class SkillSafetyTests(unittest.TestCase):
    def check(self, name, text):
        tmp = tempfile.mkdtemp()
        try:
            skill = os.path.join(tmp, "demo")
            os.makedirs(os.path.join(skill, "scripts"))
            with open(os.path.join(skill, "SKILL.md"), "w", encoding="utf-8") as handle:
                handle.write("---\nname: demo\n---\n")
            with open(os.path.join(skill, "scripts", name), "w", encoding="utf-8") as handle:
                handle.write(text)
            return safety.check_skill(skill)
        finally:
            shutil.rmtree(tmp)

    def test_blocked_patterns(self):
        # Assembled from parts so this file never holds the patterns verbatim.
        env = "os." + "environ"
        samples = {
            "secrets file path": ("a.md", "a line in ~/" + ".hermes/.env\n"),
            "os.environ access": ("a.py", "key = " + env + '["DEAPI_API_KEY"]\n'),
            "process.env[...]": ("a.ts", "const k = process." + 'env["DEAPI_API_KEY"];\n'),
            "chat-role tag": ("a.md", "Tools appear as `mcp__deapi__<" + "tool>`.\n"),
        }
        for label, (name, text) in samples.items():
            errors = self.check(name, text)
            self.assertTrue(any(label in e for e in errors), (label, errors))
        self.assertTrue(self.check("b.py", "environ = " + env + " if x else y\n"))

    def test_allowed_forms(self):
        env = "os." + "environ"
        text = (env + '.get("DEAPI_API_KEY", "")\nos.getenv("DEAPI_MAX_COST")\n'
                "# " + env + " is never read as a whole\n"
                "const k = process.env.DEAPI_API_KEY;\n"
                "Tools appear as `mcp__deapi__{tool}`; the Hermes .env file.\n")
        self.assertEqual(self.check("ok.py", text), [])

    def test_repository_is_clean(self):
        errors = []
        for skill in safety.skill_dirs():
            errors.extend(safety.check_skill(skill))
        self.assertEqual(errors, [])
