#!/usr/bin/env python3
"""deapi.py - command-line helper for the deAPI API v2 (https://docs.deapi.ai).

Standard library only, Python 3.9+. The API key is read from the DEAPI_API_KEY
environment variable (or --key-file) and is sent only to *.deapi.ai.

Every paid command first asks the API for a free price quote and stops with
exit code 5 when the price is above the cost limit (--max-cost, default
0.25 USD). --quote prints the quote and submits nothing.

Results: JSON on stdout, progress and diagnostics on stderr. Files are saved
in --out-dir (default: the current directory) and are never overwritten.

Usage: python deapi.py <command> [options]      (python deapi.py <command> -h)
"""

import argparse
import importlib
import os
import pkgutil
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from deapi_lib import VERSION, core  # noqa: E402
from deapi_lib import commands as _commands  # noqa: E402

EPILOG = """examples:
  python deapi.py transcribe "https://www.youtube.com/watch?v=..." --lang en
  python deapi.py image --prompt "a red fox, studio light" --quote
  python deapi.py models --type image
  python deapi.py status <request_id> --wait --download

exit codes: 0 ok, 2 bad input, 3 key, 4 key role or IP, 5 price above --max-cost
(ask the user), 6 balance, 7 validation or model, 8 job failed, 9 unknown state
(check before retrying), 10 rate limit, 11 account suspended, 12 key spend limit,
13 outdated skill."""


def command_modules():
    """Import every module in deapi_lib/commands that has register(), by ORDER."""
    found = []
    for info in pkgutil.iter_modules(_commands.__path__):
        if info.name.startswith("_"):
            continue
        module = importlib.import_module("deapi_lib.commands." + info.name)
        if hasattr(module, "register"):
            found.append(module)
    return sorted(found, key=lambda m: (getattr(m, "ORDER", 50), m.__name__))


def build_parser():
    parser = argparse.ArgumentParser(
        prog="deapi.py",
        description="deAPI helper: images, video, speech, music, transcription, OCR, "
                    "embeddings. Quotes the price before every paid job.",
        epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version", version="deapi-skill " + VERSION)
    core.add_global_args(parser)
    sub = parser.add_subparsers(dest="command", metavar="<command>")
    sub.required = True
    for module in command_modules():
        module.register(sub)
    return parser


def main(argv=None):
    core.utf8_streams()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        ctx = core.Context.from_args(args)
        result = args.func(ctx, args)
        if result is not None:
            core.emit(result)
        return core.EXIT_OK
    except core.DeapiError as exc:
        core.emit_error(exc)
        return exc.code
    except OSError as exc:
        # Writing a result failed (no permission, disk full, bad --output).
        error = core.DeapiError("File system error: %s" % exc, core.EXIT_ERROR,
                                "Check --out-dir/--output and the free disk space.")
        core.emit_error(error)
        return error.code
    except KeyboardInterrupt:
        core.log("interrupted")
        return 130


if __name__ == "__main__":
    sys.exit(main())
