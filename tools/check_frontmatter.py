#!/usr/bin/env python3
"""Check every <skill>/SKILL.md against the agentskills.io limits and our rules.

- top-level fields: only name, description, license, compatibility, metadata
  (allowed-tools is valid in the spec but not used here)
- name: 1-64 chars, a-z0-9 and single hyphens, equal to the folder name
- description: 1-1024 chars, no < or >, first sentence <= 60 chars ending with "."
- compatibility: <= 500 chars
- SKILL.md under 500 lines
- no machine-specific paths (/tmp, C:\\Users, /home/...) in SKILL.md or references/
- every environment variable a skill script reads is declared in
  metadata.openclaw.requires.env, or mentioned in SKILL.md as optional
- references/... links exist and are one level deep

Standard library only. Usage: python tools/check_frontmatter.py [skill_dir ...]
Exit code 1 when any check fails.
"""

import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALLOWED_FIELDS = {"name", "description", "license", "compatibility", "metadata"}
NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
PATH_RES = [re.compile(p) for p in (r"(?<![\w.])/tmp/", r"[A-Za-z]:\\Users\\", r"(?<![\w.~])/home/")]
ENV_RES = [re.compile(p, re.M) for p in (
    r"os\.environ\.get\(\s*['\"]([A-Z][A-Z0-9_]+)['\"]",
    r"os\.environ\[\s*['\"]([A-Z][A-Z0-9_]+)['\"]",
    r"os\.getenv\(\s*['\"]([A-Z][A-Z0-9_]+)['\"]",
    r"^[A-Z0-9_]*_ENV\s*=\s*['\"]([A-Z][A-Z0-9_]+)['\"]",
)]
# Variables that only change how Python itself runs; not part of the skill's config.
IGNORED_ENV = {"PYTHONUTF8", "PYTHONIOENCODING", "NO_COLOR"}


def split_frontmatter(text):
    if not text.startswith("---"):
        return None, text
    end = re.search(r"^---\s*$", text[3:], re.M)
    if not end:
        return None, text
    return text[3:3 + end.start()].strip("\n"), text[3 + end.end():]


def parse_frontmatter(block):
    """Tiny YAML subset: top-level scalars, block scalars and nested maps (raw)."""
    fields, order = {}, []
    lines = block.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue
        if line[0] in " \t":
            raise ValueError("unexpected indentation on line %d" % (i + 1))
        match = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", line)
        if not match:
            raise ValueError("cannot parse line %d: %r" % (i + 1, line))
        key, value = match.group(1), match.group(2).strip()
        i += 1
        nested = []
        while i < len(lines) and (not lines[i].strip() or lines[i][0] in " \t"):
            nested.append(lines[i])
            i += 1
        if value in ("|", ">", "|-", ">-", "|+", ">+"):
            body = [l.strip() for l in nested]
            value = ("\n" if value.startswith("|") else " ").join(body).strip()
        elif value == "" and nested:
            value = parse_nested(nested)
        else:
            value = scalar(value + "".join(" " + l.strip() for l in nested if l.strip()))
        fields[key] = value
        order.append(key)
    return fields, order


def parse_nested(lines):
    """Nested `key: value` maps (any depth) and `- item` lists; flow maps via JSON."""
    def depth(line):
        return len(line) - len(line.lstrip())
    indent = min(depth(l) for l in lines if l.strip())
    if any(l.strip().startswith("- ") for l in lines if l.strip() and depth(l) == indent):
        return [l.strip()[2:] for l in lines if l.strip().startswith("- ")]
    out, i = {}, 0
    while i < len(lines):
        line = lines[i]
        i += 1
        if not line.strip():
            continue
        match = re.match(r"^\s*([A-Za-z0-9_-]+):\s*(.*)$", line)
        if not match:
            raise ValueError("cannot parse nested line %r" % line)
        key, raw = match.group(1), match.group(2).strip()
        children = []
        while i < len(lines) and (not lines[i].strip() or depth(lines[i]) > indent):
            children.append(lines[i])
            i += 1
        if raw == "" and any(c.strip() for c in children):
            out[key] = parse_nested(children)
        else:
            out[key] = scalar(raw + "".join(" " + c.strip() for c in children if c.strip()))
    return out


def scalar(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        inner = value[1:-1]
        return inner.replace("''", "'") if value[0] == "'" else json.loads(value)
    if value[:1] in ("[", "{"):
        try:
            return json.loads(value)
        except ValueError:
            return value   # YAML flow collection, e.g. [linux, macos]
    if re.search(r":(\s|$)", value) or " #" in value or (value and value[0] in "&*!|>%@`"):
        raise ValueError("plain value %r needs quotes (': ', ' #' or a leading special "
                         "character is not valid YAML here)" % value[:60])
    return value


def first_sentence(text):
    match = re.match(r"^(.*?[.!?])(\s|$)", text.strip(), re.S)
    return match.group(1) if match else text.strip()


def env_vars_in_scripts(skill_dir):
    found = set()
    for base, _, files in os.walk(os.path.join(skill_dir, "scripts")):
        for name in files:
            if not name.endswith(".py"):
                continue
            with open(os.path.join(base, name), encoding="utf-8") as handle:
                text = handle.read()
            for pattern in ENV_RES:
                found.update(pattern.findall(text))
    return found - IGNORED_ENV


def check_skill(skill_dir):
    errors = []
    name = os.path.basename(os.path.normpath(skill_dir))
    path = os.path.join(skill_dir, "SKILL.md")
    if not os.path.isfile(path):
        return ["%s: SKILL.md missing" % name]
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    block, body = split_frontmatter(text)
    if block is None:
        return ["%s: no YAML frontmatter" % name]
    try:
        fields, _ = parse_frontmatter(block)
    except (ValueError, json.JSONDecodeError) as exc:
        return ["%s: frontmatter: %s" % (name, exc)]

    extra = set(fields) - ALLOWED_FIELDS
    if extra:
        errors.append("%s: unexpected top-level fields: %s (put them under metadata)"
                      % (name, ", ".join(sorted(extra))))
    skill_name = fields.get("name")
    if not isinstance(skill_name, str) or not NAME_RE.match(skill_name) or len(skill_name) > 64:
        errors.append("%s: invalid name %r" % (name, skill_name))
    elif skill_name != name:
        errors.append("%s: name %r must equal the folder name" % (name, skill_name))

    desc = fields.get("description")
    if not isinstance(desc, str) or not desc.strip():
        errors.append("%s: description missing" % name)
    else:
        if len(desc) > 1024:
            errors.append("%s: description has %d chars (max 1024)" % (name, len(desc)))
        if "<" in desc or ">" in desc:
            errors.append("%s: description must not contain < or >" % name)
        first = first_sentence(desc)
        if len(first) > 60 or not first.endswith("."):
            errors.append("%s: first sentence must be <= 60 chars and end with '.': %r (%d)"
                          % (name, first, len(first)))
    compat = fields.get("compatibility")
    if compat is not None and (not isinstance(compat, str) or not 1 <= len(compat) <= 500):
        errors.append("%s: compatibility must be 1-500 chars" % name)
    meta = fields.get("metadata")
    if meta is not None and not isinstance(meta, dict):
        errors.append("%s: metadata must be a mapping" % name)

    lines = text.count("\n") + 1
    if lines >= 500:
        errors.append("%s: SKILL.md has %d lines (keep it under 500)" % (name, lines))

    docs = [path] + [os.path.join(skill_dir, "references", f)
                     for f in sorted(os.listdir(os.path.join(skill_dir, "references")))
                     if f.endswith(".md")] if os.path.isdir(os.path.join(skill_dir, "references")) \
        else [path]
    for doc in docs:
        with open(doc, encoding="utf-8") as handle:
            for number, line in enumerate(handle, 1):
                for pattern in PATH_RES:
                    if pattern.search(line):
                        errors.append("%s:%d: machine-specific path: %s"
                                      % (os.path.relpath(doc, ROOT), number, line.strip()[:80]))

    for ref in sorted(set(re.findall(r"references/[A-Za-z0-9_./-]+", body))):
        ref = ref.rstrip(".")
        if ref.count("/") > 1:
            errors.append("%s: %s is more than one level deep" % (name, ref))
        elif not os.path.isfile(os.path.join(skill_dir, ref)):
            errors.append("%s: %s does not exist" % (name, ref))

    used = env_vars_in_scripts(skill_dir)
    if used:
        openclaw = meta.get("openclaw") if isinstance(meta, dict) else None
        required = set()
        if isinstance(openclaw, dict):
            required = set(((openclaw.get("requires") or {}).get("env")) or [])
        for var in sorted(used - required):
            if var not in text:
                errors.append("%s: script reads %s but metadata.openclaw.requires.env does "
                              "not declare it and SKILL.md does not mention it" % (name, var))
    return errors


def skill_dirs():
    return sorted(os.path.join(ROOT, d) for d in os.listdir(ROOT)
                  if os.path.isfile(os.path.join(ROOT, d, "SKILL.md")))


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    dirs = [os.path.abspath(d) for d in argv] or skill_dirs()
    if not dirs:
        print("no skills found")
        return 1
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
