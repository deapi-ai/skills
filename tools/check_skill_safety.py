#!/usr/bin/env python3
"""Keep the skill folders free of patterns that agent skill scanners block.

Some agents scan a skill before installing it (for example the Hermes Agent skills
hub) and refuse it when a file looks like it reads secrets. These checks cover the
patterns that would block or flag our skills; they are our own rules, not a copy of
any scanner:

- a literal path to an agent's secrets file (`.hermes/.env`); describe it in words;
- `os.environ` other than `os.environ.get(...)` in code (`os.environ[...]`, the whole
  mapping); read single variables with `os.environ.get` or `os.getenv`;
- `process.env[...]` in JavaScript or TypeScript; use `process.env.NAME`;
- chat-role tags as placeholders (`<tool>`, `<system>`, `<assistant>`, `<user>`), which
  some gateways reject as prompt injection; write `{tool}` instead.

Every file in each skill folder is checked (SKILL.md, references, scripts, assets).
Lines that are Python or shell comments are skipped for the `os.environ` rule.
Standard library only. Usage: python tools/check_skill_safety.py [skill_dir ...]
Exit code 1 when any check fails.
"""

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {"__pycache__", ".git", "node_modules"}
RULES = [
    ("secrets file path", re.compile(r"\.hermes[/\\]\.env"),
     "describe the file in words (the .env file of the Hermes home folder)"),
    ("os.environ access", re.compile(r"^[^#\n]*\bos\.environ\b(?!\s*\.get\s*\()"),
     "read one variable with os.environ.get(NAME) or os.getenv(NAME)"),
    ("process.env[...]", re.compile(r"\bprocess\.env\s*\["),
     "use process.env.NAME"),
    ("chat-role tag", re.compile(r"<\s*/?\s*(tool|system|assistant|user)\s*>", re.I),
     "write the placeholder as {tool}"),
]
TEXT_EXT = {".md", ".py", ".sh", ".js", ".mjs", ".ts", ".json", ".txt", ".toml", ".yaml",
            ".yml"}


def _rel(path):
    try:
        return os.path.relpath(path, ROOT)
    except ValueError:
        return path


def files_of(skill_dir):
    for base, dirs, files in os.walk(skill_dir):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(files):
            if os.path.splitext(name)[1].lower() in TEXT_EXT or name == "SKILL.md":
                yield os.path.join(base, name)


def check_file(path):
    errors = []
    with open(path, encoding="utf-8", errors="replace") as handle:
        for number, line in enumerate(handle, 1):
            for label, pattern, fix in RULES:
                if pattern.search(line):
                    errors.append("%s:%d: %s: %s (%s)" % (_rel(path), number, label,
                                                         line.strip()[:80], fix))
    return errors


def check_skill(skill_dir):
    errors = []
    for path in files_of(skill_dir):
        errors.extend(check_file(path))
    return errors


def skill_dirs():
    return sorted(os.path.join(ROOT, d) for d in os.listdir(ROOT)
                  if os.path.isfile(os.path.join(ROOT, d, "SKILL.md")))


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    dirs = [os.path.abspath(d) for d in argv] or skill_dirs()
    errors = []
    for skill_dir in dirs:
        errors.extend(check_skill(skill_dir))
    for err in errors:
        print("FAIL " + err)
    if not errors:
        print("ok: %s" % ", ".join(os.path.basename(d) for d in dirs))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
