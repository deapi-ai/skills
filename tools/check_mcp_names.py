#!/usr/bin/env python3
"""Check MCP tool names used in the skill docs against the deAPI MCP server.

Known names come from tests/fixtures/mcp_tools.json (a snapshot of the public
server's tools/list), from --repo PATH (a clone of github.com/deapi-ai/mcp-server-deapi:
the `server.tool()(name)` registrations), or from --tools FILE.

Errors:
- `mcp__deapi__<name>` where <name> is not a tool;
- a backticked name ending in `_price`, or one that looks like a misspelt tool
  name (close match), that is not a tool.
Warnings: tools of the recommended set that deapi/references/mcp.md never mentions.

--update FILE refreshes the snapshot from a saved tools/list response (JSON, or
the SSE text with a `data:` line).
Usage: python tools/check_mcp_names.py [--repo PATH | --tools FILE] [--update FILE]
"""

import argparse
import difflib
import glob
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SNAPSHOT = os.path.join(ROOT, "tests", "fixtures", "mcp_tools.json")
DOC_GLOBS = ("README.md", "deapi/SKILL.md", "deapi/references/*.md",
             "deapi-api/SKILL.md", "deapi-api/references/*.md")
MCP_DOC = os.path.join(ROOT, "deapi", "references", "mcp.md")
# Recommended tool set: 18 generation tools, check_job_status, get_available_models
# and the price tools for long or expensive jobs.
RECOMMENDED = [
    "text_to_image", "image_to_image", "image_to_text", "image_remove_background",
    "image_upscale", "text_to_video", "image_to_video", "audio_to_video", "video_upscale",
    "video_description", "text_to_audio", "text_to_music", "audio_transcription",
    "audio_url_transcription", "video_file_transcription", "video_url_transcription",
    "text_to_embedding", "prompt_booster", "check_job_status", "get_available_models",
    "text_to_video_price", "image_to_video_price", "audio_to_video_price",
    "video_upscale_price", "video_description_price", "audio_transcription_price",
    "audio_url_transcription_price", "video_file_transcription_price",
    "video_url_transcription_price",
]


def names_from_tools_list(path):
    with open(path, encoding="utf-8") as handle:
        raw = handle.read()
    if "data:" in raw and not raw.lstrip().startswith("{"):
        raw = [line[5:].strip() for line in raw.splitlines() if line.startswith("data:")][0]
    data = json.loads(raw)
    if isinstance(data, dict) and "tools" in data and isinstance(data["tools"], list) \
            and data["tools"] and isinstance(data["tools"][0], str):
        return sorted(data["tools"])
    tools = (data.get("result") or data).get("tools") or []
    return sorted(t["name"] for t in tools)


def names_from_repo(path):
    found = set()
    for file in glob.glob(os.path.join(path, "deapi_mcp", "*.py")):
        with open(file, encoding="utf-8") as handle:
            found.update(re.findall(r"\.tool\([^)]*\)\(\s*([a-z_][a-z0-9_]*)\s*\)", handle.read()))
    return sorted(found)


def doc_files():
    files = []
    for pattern in DOC_GLOBS:
        files.extend(sorted(glob.glob(os.path.join(ROOT, pattern))))
    return files


def check(known, files):
    errors = []
    known = set(known)
    for path in files:
        rel = os.path.relpath(path, ROOT).replace("\\", "/")
        with open(path, encoding="utf-8") as handle:
            for number, line in enumerate(handle, 1):
                for name in re.findall(r"mcp__deapi__([A-Za-z0-9_]+)", line):
                    if name not in known:
                        errors.append("%s:%d: mcp__deapi__%s is not a deAPI MCP tool"
                                      % (rel, number, name))
                for name in re.findall(r"`([a-z][a-z0-9]*(?:_[a-z0-9]+)+)`", line):
                    if name in known:
                        continue
                    close = difflib.get_close_matches(name, known, n=1, cutoff=0.88)
                    if name.endswith("_price") or close:
                        errors.append("%s:%d: `%s` is not a deAPI MCP tool%s" % (
                            rel, number, name, " (did you mean `%s`?)" % close[0] if close else ""))
    return errors


def coverage_warnings():
    if not os.path.isfile(MCP_DOC):
        return ["deapi/references/mcp.md does not exist yet"]
    with open(MCP_DOC, encoding="utf-8") as handle:
        text = handle.read()
    return ["deapi/references/mcp.md does not mention recommended tool %s" % name
            for name in RECOMMENDED if not re.search(r"\b%s\b" % re.escape(name), text)]


def main(argv=None):
    parser = argparse.ArgumentParser(description="Check MCP tool names in the docs.")
    parser.add_argument("--repo", help="clone of deapi-ai/mcp-server-deapi")
    parser.add_argument("--tools", help="saved tools/list response or snapshot JSON")
    parser.add_argument("--update", help="write the snapshot from this tools/list response")
    args = parser.parse_args(argv)
    if args.update:
        names = names_from_tools_list(args.update)
        with open(SNAPSHOT, "w", encoding="utf-8") as handle:
            json.dump({"_comment": "Tool names from tools/list of the public deAPI MCP server.",
                       "tools": names}, handle, indent=1)
            handle.write("\n")
        print("snapshot updated: %d tools" % len(names))
        return 0
    if args.repo:
        known = names_from_repo(args.repo)
    else:
        known = names_from_tools_list(args.tools or SNAPSHOT)
    missing = sorted(set(RECOMMENDED) - set(known))
    errors = ["recommended tool %s is not on the server" % n for n in missing]
    errors += check(known, doc_files())
    for warning in coverage_warnings():
        print("WARN " + warning)
    for err in errors:
        print("FAIL " + err)
    print("%d known tools, %d doc file(s): %s" % (len(known), len(doc_files()),
                                                 "ok" if not errors else "%d problem(s)" % len(errors)))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
