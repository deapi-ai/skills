"""Every `deapi.py ...` example in the docs and in --help parses with the real parser.

Catches renamed or removed flags in SKILL.md, references, README and the command
epilogs. Only argument parsing runs here; nothing is sent anywhere.
"""

import contextlib
import glob
import io
import os
import re
import shlex
import unittest

from helpers import ROOT, deapi

DOCS = ([os.path.join(ROOT, "deapi", "SKILL.md"), os.path.join(ROOT, "README.md")]
        + sorted(glob.glob(os.path.join(ROOT, "deapi", "references", "*.md")))
        + [os.path.join(ROOT, "deapi-api", "SKILL.md")]
        + sorted(glob.glob(os.path.join(ROOT, "deapi-api", "references", "*.md"))))
FENCE = re.compile(r"```[^\n]*\n(.*?)```", re.S)
SPAN = re.compile(r"`([^`]+)`")
CALL = re.compile(r"deapi\.py\s+(.+)")


def _commands(text):
    """(snippet, argv text) for each deapi.py call in code blocks and inline spans."""
    found = []
    for block in FENCE.findall(text):
        joined = re.sub(r"\\\n\s*", " ", block)          # shell line continuations
        for line in joined.splitlines():
            found.extend(_calls(line))
    rest = FENCE.sub("", text)
    for span in SPAN.findall(rest):
        found.extend(_calls(" ".join(span.split())))  # spans wrapped across lines
    return found


def _calls(line):
    out = []
    for match in CALL.finditer(line):
        cmd = re.split(r"\s(?:\||&&|;|>|2>)\s", match.group(1))[0].strip()
        if not cmd or cmd.startswith("<") or cmd in ("...",):
            continue
        out.append((line.strip(), cmd))
    return out


def _epilog_commands(parser):
    found = []
    for action in parser._subparsers._group_actions:            # noqa: SLF001
        for name, sub in action.choices.items():
            for line in (sub.epilog or "").splitlines():
                # "  deapi.py models Kokoro      one model's full card": drop the note.
                found.extend((snippet, re.split(r"\s{2,}", cmd)[0])
                             for snippet, cmd in _calls(line))
    return found


class DocExamplesTest(unittest.TestCase):
    def parse(self, parser, cmd):
        argv = shlex.split(cmd, comments=True)
        err = io.StringIO()
        try:
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                parser.parse_args(argv)
        except SystemExit as exc:
            if exc.code not in (0, None):
                return err.getvalue().strip().splitlines()[-1:]
        return None

    def test_doc_examples_parse(self):
        parser = deapi.build_parser()
        checked, failures = 0, []
        for path in DOCS:
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
            for snippet, cmd in _commands(text):
                checked += 1
                problem = self.parse(parser, cmd)
                if problem is not None:
                    failures.append("%s: `%s` -> %s" % (
                        os.path.relpath(path, ROOT), snippet[:100], problem))
        self.assertGreater(checked, 40)
        self.assertEqual(failures, [], "\n".join(failures))

    def test_help_epilog_examples_parse(self):
        parser = deapi.build_parser()
        examples = _epilog_commands(parser)
        self.assertGreater(len(examples), 15)
        failures = []
        for snippet, cmd in examples:
            problem = self.parse(parser, cmd)
            if problem is not None:
                failures.append("`%s` -> %s" % (snippet[:100], problem))
        self.assertEqual(failures, [], "\n".join(failures))


if __name__ == "__main__":
    unittest.main()
