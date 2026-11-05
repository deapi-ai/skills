#!/usr/bin/env python3
"""Scan the repository (a public one) for things that must never be published.

Blocking findings: API keys and other tokens, non-placeholder bearer tokens,
non-public hosts, task numbers, agent session links, machine-specific paths,
personal e-mail addresses, real-looking IP addresses, and key previews in test
fixtures. Warnings only: chat/LLM wording in skill content (not shipped yet).

Usage:
  python tools/scan_leaks.py                  # files in the working tree (git ls-files)
  python tools/scan_leaks.py --staged         # staged content (pre-commit hook)
  python tools/scan_leaks.py --git-meta [--range A..B]   # branch name + commit messages
  python tools/scan_leaks.py --text-env NAME  # text from an env var (e.g. PR title/body)
  python tools/scan_leaks.py PATH ...         # given files

--extra-patterns FILE (or $LEAK_PATTERNS_FILE) adds private patterns, one regex
per line (`label<TAB>regex` also works). Keep that file outside the repo.
A line containing "leak-scan: allow" is skipped. Exit code 1 on findings.
"""

import argparse
import ipaddress
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALLOW_MARK = "leak-scan: allow"
PLACEHOLDER_KEY = "sk-dp-0-TESTKEYNOTREAL"
SPEC_SNAPSHOT = "tests/fixtures/openapi.json"
SKILL_DIRS = ("deapi/", "deapi-api/")

# (category, regex). Keep every pattern generic: this file is public.
PATTERNS = [
    ("api-key", r"sk-dp-\d+-[A-Za-z0-9]{12,}"),
    ("api-key", r"dpn-sk-[A-Za-z0-9]"),
    ("api-key", r"\b\d{1,9}\|[A-Za-z0-9]{30,}\b"),
    ("token", r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
    ("token", r"\bsk-ant-[A-Za-z0-9_-]{20,}"),
    ("token", r"\bsk-(?:proj-)?[A-Za-z0-9]{40,}"),
    ("token", r"\bAKIA[0-9A-Z]{16}\b"),
    ("token", r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    ("token", r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    ("bearer", r"Bearer\s+(?!\$|<|\{|\.\.\.|YOUR|your|sk-dp-0-|\*\*\*|%s|\"\s*\+|'\s*\+|sk-dp-\.)"
               r"[A-Za-z0-9._|=+/-]{16,}"),
    ("host", r"\b[a-z0-9-]+-sandbox\.[a-z]"),
    ("task-number", r"\bDEV-\d+\b"),
    ("session", r"Claude-Sess(?:ion)\b"),
    ("session", r"claude\.ai/code/sess(?:ion)"),
    ("session", r"\bsession_[A-Za-z0-9]{20,}"),
    ("local-path", r"\b[A-Za-z]:[\\/]+Users[\\/]+[^\\/\s\"'<>]+"),
    ("local-path", r"(?<![\w.~])/Users/(?!you\b|user\b|USERNAME\b|<)[A-Za-z0-9._-]+/"),
    ("local-path", r"(?<![\w.~])/home/(?!user\b|runner\b|USERNAME\b|<)[a-z0-9._-]+/"),
    ("local-path", r"AppData[\\/]+Local[\\/]+Temp"),
]
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+\.)+[A-Za-z]{2,}\b")
ALLOWED_EMAILS = re.compile(
    r"^(support@deapi\.ai|noreply@anthropic\.com|git@github\.com|[^@]+@users\.noreply\.github\.com"
    r"|[^@]+@example\.(com|org|net|test))$",
    re.I)
IPV4_RE = re.compile(r"(?<![\d.])(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})(?![\d.])")
DOC_NETS = [ipaddress.ip_network(n) for n in (
    "0.0.0.0/8", "10.0.0.0/8", "127.0.0.0/8", "169.254.0.0/16", "172.16.0.0/12",
    "192.0.2.0/24", "192.168.0.0/16", "198.18.0.0/15", "198.51.100.0/24", "203.0.113.0/24",
    "224.0.0.0/4", "240.0.0.0/4")]
WARN_PATTERNS = [("chat-wording", r"\bLLMs?\b"), ("chat-wording", r"\bchat models?\b"),
                 ("chat-wording", r"\breasoning\b")]
PREVIEW_OK = re.compile(r"^sk-dp-\d+-\.\.\.0+$")


class Finding(object):
    def __init__(self, where, category, snippet, blocking=True):
        self.where, self.category, self.snippet, self.blocking = where, category, snippet, blocking

    def __str__(self):
        return "%s %s [%s] %s" % ("FAIL" if self.blocking else "WARN", self.where,
                                  self.category, self.snippet)


def mask(text):
    text = text.strip()
    return text[:6] + "***" if len(text) > 6 else "***"


def load_extra(path):
    extra = []
    if not path:
        return extra
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            label, _, regex = line.partition("\t") if "\t" in line else ("private", "", line)
            extra.append((label.strip() or "private", regex.strip()))
    return extra


def scan_text(text, where, patterns, warn_words=False, check_ips=True):
    findings = []
    for number, line in enumerate(text.splitlines(), 1):
        if ALLOW_MARK in line:
            continue
        loc = "%s:%d" % (where, number)
        for category, regex in patterns:
            for match in re.finditer(regex, line):
                value = match.group(0)
                if category == "api-key" and PLACEHOLDER_KEY in value:
                    continue
                shown = mask(value) if category in ("api-key", "token", "bearer", "private") \
                    else value[:80]
                findings.append(Finding(loc, category, shown))
        for match in EMAIL_RE.finditer(line):
            if not ALLOWED_EMAILS.match(match.group(0)):
                findings.append(Finding(loc, "email", mask(match.group(0))))
        if check_ips:
            for match in IPV4_RE.finditer(line):
                try:
                    addr = ipaddress.ip_address(match.group(1))
                except ValueError:
                    continue
                if not any(addr in net for net in DOC_NETS):
                    findings.append(Finding(loc, "ip-address", match.group(1)))
        if warn_words:
            for category, regex in WARN_PATTERNS:
                if re.search(regex, line, re.I if "chat" in regex else 0):
                    findings.append(Finding(loc, category, line.strip()[:80], blocking=False))
    return findings


def scan_fixture_json(text, where):
    """Key previews and IP lists in test fixtures must be placeholders."""
    findings = []
    try:
        data = json.loads(text)
    except ValueError:
        return findings

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "preview" and isinstance(value, str) and not PREVIEW_OK.match(value):
                    findings.append(Finding(where, "fixture-preview", mask(value)))
                if key == "ip_whitelist" and value:
                    for item in value if isinstance(value, list) else [value]:
                        text_item = json.dumps(item)
                        if not re.search(r"192\.0\.2\.|198\.51\.100\.|203\.0\.113\.", text_item):
                            findings.append(Finding(where, "fixture-ip", text_item[:40]))
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)
    walk(data)
    return findings


def scan_file(rel, text, patterns):
    rel = rel.replace("\\", "/")
    is_spec = rel == SPEC_SNAPSHOT
    findings = scan_text(text, rel, patterns, warn_words=rel.startswith(SKILL_DIRS),
                         check_ips=not is_spec)
    if rel.startswith("tests/fixtures/") and rel.endswith(".json") and not is_spec:
        findings.extend(scan_fixture_json(text, rel))
    return findings


def git(*args):
    try:
        return subprocess.run(["git"] + list(args), cwd=ROOT, capture_output=True, check=False)
    except OSError:   # git not installed (e.g. a slim container): fall back to walking
        return subprocess.CompletedProcess(args, 127, b"", b"")


def tree_files():
    res = git("ls-files", "-co", "--exclude-standard", "-z")
    if res.returncode != 0:
        files = []
        for base, dirs, names in os.walk(ROOT):
            dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", ".pytest_cache")]
            files.extend(os.path.relpath(os.path.join(base, n), ROOT) for n in names
                         if n != ".git" and not n.endswith(".pyc"))
        return files
    return [f for f in res.stdout.decode("utf-8").split("\0") if f]


def read_tree_file(rel):
    path = os.path.join(ROOT, rel)
    if not os.path.isfile(path):
        return None
    with open(path, "rb") as handle:
        return decode(handle.read())


def decode(raw):
    if b"\0" in raw[:8192]:
        return None   # binary
    return raw.decode("utf-8", "replace")


def staged_files():
    res = git("diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z")
    for rel in [f for f in res.stdout.decode("utf-8").split("\0") if f]:
        blob = git("show", ":" + rel)
        yield rel, decode(blob.stdout) if blob.returncode == 0 else None


def git_meta(rev_range):
    findings = []
    branch = git("rev-parse", "--abbrev-ref", "HEAD").stdout.decode().strip()
    texts = [("branch-name", branch)]
    if not rev_range:
        for base in ("origin/main", "main"):
            if git("rev-parse", "--verify", "-q", base).returncode == 0:
                rev_range = "%s..HEAD" % base
                break
    if rev_range:
        log = git("log", "--format=%H%n%B%n----", rev_range).stdout.decode("utf-8", "replace")
        texts.append(("commit-messages(%s)" % rev_range, log))
        people = git("log", "--format=%an <%ae>%n%cn <%ce>", rev_range).stdout.decode(
            "utf-8", "replace")
        unique = sorted(set(line for line in people.splitlines() if line.strip()))
        texts.append(("commit-authors(%s)" % rev_range, "\n".join(unique)))
    return texts


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("paths", nargs="*")
    parser.add_argument("--staged", action="store_true")
    parser.add_argument("--git-meta", action="store_true")
    parser.add_argument("--range")
    parser.add_argument("--text-env", action="append", default=[])
    parser.add_argument("--extra-patterns", default=os.environ.get("LEAK_PATTERNS_FILE"))
    args = parser.parse_args(argv)
    patterns = PATTERNS + load_extra(args.extra_patterns)

    findings, scanned = [], 0
    if args.git_meta or args.text_env:
        items = git_meta(args.range) if args.git_meta else []
        items += [(name, os.environ.get(name, "")) for name in args.text_env]
        for where, text in items:
            found = scan_text(text, where, patterns)
            if where.startswith("commit-authors"):
                # A personal author address is the committer's choice: warn, so the
                # owner can switch to a users.noreply.github.com address before pushing.
                for finding in found:
                    if finding.category == "email":
                        finding.blocking = False
            findings.extend(found)
            scanned += 1
    elif args.staged:
        for rel, text in staged_files():
            if text is not None:
                findings.extend(scan_file(rel, text, patterns))
                scanned += 1
    else:
        rels = [os.path.relpath(os.path.abspath(p), ROOT) for p in args.paths] or tree_files()
        for rel in rels:
            if rel.startswith(".git" + os.sep) or rel == ".git":
                continue
            text = read_tree_file(rel)
            if text is not None:
                findings.extend(scan_file(rel, text, patterns))
                scanned += 1
    for finding in findings:
        print(finding)
    blocking = [f for f in findings if f.blocking]
    print("scanned %d item(s): %d blocking, %d warning(s)"
          % (scanned, len(blocking), len(findings) - len(blocking)))
    return 1 if blocking else 0


if __name__ == "__main__":
    sys.exit(main())
