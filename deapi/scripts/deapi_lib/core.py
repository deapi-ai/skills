"""Shared core of the deAPI skill script.

Everything a command needs lives here: configuration, the HTTP client (with
safe retries), error mapping to exit codes, the model catalog and model
selection, parameter fitting to model limits, price quotes and the cost
threshold, job polling, file checks, multipart upload and result download.

Standard library only, Python 3.9+. All HTTP goes through
``urllib.request.urlopen`` (looked up at call time, so tests can replace it).
"""

import argparse
import http.client
import json
import math
import os
import random
import re
import shutil
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import wave

from . import VERSION

# --------------------------------------------------------------- constants ---

USER_AGENT = "deapi-skill/%s (+https://github.com/deapi-ai/skills)" % VERSION
DEFAULT_BASE_URL = "https://api.deapi.ai"
OAI_BASE_URL = "https://oai.deapi.ai"
DEFAULT_MAX_COST = 0.25          # USD per command run; only the user raises it
MAX_COST_ENV = "DEAPI_MAX_COST"  # optional, set by the user, never by the agent
KEY_ENV = "DEAPI_API_KEY"
DEFAULT_TIMEOUT = 900            # seconds to wait for a job
HTTP_TIMEOUT = 120               # seconds per HTTP request
MAX_RETRIES = 3                  # retries for safe requests (GET, price quotes)
MAX_RETRY_AFTER = 60             # longer waits are reported, not slept through
KEY_LIMIT_CHECK_USD = 0.10       # above this, check the key's remaining limit
PRICE_DRIFT_WARN = 0.20          # warn when the final price exceeds the quote by >20%
PREVIEW_CHARS = 2000
MAX_COUNT = 4
LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")
MB = 1024 * 1024
MAX_REQUEST_BYTES = 75 * MB

# Exit codes (see deapi/references/commands.md, "Exit codes").
EXIT_OK = 0
EXIT_ERROR = 1           # anything not covered below (network, 404, unexpected reply)
EXIT_USAGE = 2           # bad argument or rejected input; nothing was sent
EXIT_AUTH = 3            # missing, invalid, expired or revoked key
EXIT_FORBIDDEN = 4       # key role or IP allow-list refuses the call
EXIT_OVER_BUDGET = 5     # quote above --max-cost (or unknown); nothing was submitted
EXIT_BALANCE = 6         # account balance too low
EXIT_VALIDATION = 7      # 422 validation, unknown model, model not on the plan
EXIT_JOB_FAILED = 8      # the job ran and failed
EXIT_UNKNOWN_STATE = 9   # timed out while the job runs, or a submit whose fate is unknown
EXIT_RATE_LIMIT = 10     # 429 after retries, or the daily quota
EXIT_SUSPENDED = 11      # account suspended
EXIT_KEY_LIMIT = 12      # the key's own spend limit is used up
EXIT_GONE = 13           # endpoint removed (410): this skill is outdated

EXIT_NAMES = {
    EXIT_ERROR: "error",
    EXIT_USAGE: "usage",
    EXIT_AUTH: "auth",
    EXIT_FORBIDDEN: "forbidden",
    EXIT_OVER_BUDGET: "over_budget",
    EXIT_BALANCE: "insufficient_balance",
    EXIT_VALIDATION: "validation",
    EXIT_JOB_FAILED: "job_failed",
    EXIT_UNKNOWN_STATE: "unknown_state",
    EXIT_RATE_LIMIT: "rate_limited",
    EXIT_SUSPENDED: "account_suspended",
    EXIT_KEY_LIMIT: "key_limit_exceeded",
    EXIT_GONE: "endpoint_gone",
}

KEY_HELP = (
    "Set the DEAPI_API_KEY environment variable where the agent runs: "
    "`export DEAPI_API_KEY=...` in your shell profile, for Hermes a `DEAPI_API_KEY=...` "
    "line in the .env file of the Hermes home folder (`hermes config env-path` shows "
    "where it is), or your agent's environment settings. Create a key at "
    "https://app.deapi.ai (Settings > API Keys). Never paste the key into the chat."
)
SSL_HINT = (
    "TLS certificate check failed. On macOS with Python from python.org, run "
    "'Install Certificates.command' from the Python folder in Applications."
)
NOT_ACCEPTED_HINT = (
    "The job may have been accepted. Check `deapi.py account` or the dashboard at "
    "https://app.deapi.ai before retrying, so you do not pay twice."
)

# Task name -> inference types served for it (GET /api/v2/models filter values).
TASK_TYPES = {
    "image": ["txt2img"],
    "edit": ["img2img"],
    "ocr": ["img2txt"],
    "rmbg": ["img-rmbg"],
    "upscale": ["img-upscale"],
    "video": ["txt2video"],
    "animate": ["img2video"],
    "audio-video": ["audio2video"],
    "video-upscale": ["vid-upscale"],
    "describe": ["video2desc", "video_file2desc"],
    "tts": ["txt2audio"],
    "music": ["txt2music"],
    "transcribe": ["audio2text", "video2text", "audio_file2text", "video_file2text"],
    "embed": ["txt2embedding"],
}
TASK_ALIASES = {"stt": "transcribe", "speech": "tts", "embedding": "embed",
                "embeddings": "embed", "img2video": "animate"}

# Preference patterns used only to rank the live model list when --model is
# omitted. They are hints, never model IDs sent to the API.
PREFERENCES = {
    "image": [r"klein", r"flux"],
    "edit": [r"klein", r"qwen"],
    "ocr": [r"nanonets", r"ocr"],
    "rmbg": [r"ben"],
    "upscale": [r"esrgan"],
    "video": [r"ltx"],
    "animate": [r"ltx"],
    "audio-video": [r"ltx"],
    "video-upscale": [r"esrgan"],
    "describe": [r"marlin"],
    "tts": [r"kokoro"],
    "music": [r"ace"],
    # Ct2 gives word timestamps, diarization and TikTok, and honours `lang`.
    "transcribe": [r"ct2", r"whisper"],
    "embed": [r"bge"],
}

ISO_639_1 = frozenset((
    "aa ab ae af ak am an ar as av ay az ba be bg bh bi bm bn bo br bs ca ce ch co "
    "cr cs cu cv cy da de dv dz ee el en eo es et eu fa ff fi fj fo fr fy ga gd gl "
    "gn gu gv ha he hi ho hr ht hu hy hz ia id ie ig ii ik io is it iu ja jv ka kg "
    "ki kj kk kl km kn ko kr ks ku kv kw ky la lb lg li ln lo lt lu lv mg mh mi mk "
    "ml mn mr ms mt my na nb nd ne ng nl nn no nr nv ny oc oj om or os pa pi pl ps "
    "pt qu rm rn ro ru rw sa sc sd se sg si sk sl sm sn so sq sr ss st su sv sw ta "
    "te tg th ti tk tl tn to tr ts tt tw ty ug uk ur uz ve vi vo wa wo xh yi yo za "
    "zh zu").split())

# English names, for models whose language slugs are names ("English").
LANGUAGE_NAMES = {
    "ar": "arabic", "bg": "bulgarian", "cs": "czech", "da": "danish", "de": "german",
    "el": "greek", "en": "english", "es": "spanish", "fa": "persian", "fi": "finnish",
    "fr": "french", "he": "hebrew", "hi": "hindi", "hu": "hungarian", "id": "indonesian",
    "it": "italian", "ja": "japanese", "ko": "korean", "ms": "malay", "nl": "dutch",
    "no": "norwegian", "pl": "polish", "pt": "portuguese", "ro": "romanian",
    "ru": "russian", "sk": "slovak", "sv": "swedish", "sw": "swahili", "th": "thai",
    "tr": "turkish", "uk": "ukrainian", "vi": "vietnamese", "zh": "chinese",
}

# Media platforms accepted as source URLs (value = `platform` for price quotes).
PLATFORMS = (
    (r"(^|\.)(youtube\.com|youtu\.be)$", "youtube"),
    (r"(^|\.)(twitter\.com|x\.com)$", "twitter"),
    (r"(^|\.)twitch\.tv$", "twitch"),
    (r"(^|\.)kick\.com$", "kick"),
    (r"(^|\.)tiktok\.com$", "tiktok"),
)

# ------------------------------------------------------------- file kinds ---

IMAGE_FORMATS = {"png", "jpeg", "gif", "bmp", "webp"}
AUDIO_FORMATS = {"aac", "mp3", "ogg", "wav", "webm", "flac", "m4a"}
VIDEO_FORMATS = {"mp4", "mpeg", "mov", "avi", "wmv", "ogg", "webm", "mkv"}

# kind -> allowed formats, hard size cap (MB) and a softer "documented elsewhere
# as lower" cap that only warns (sources disagree for some inputs).
FILE_KINDS = {
    "image": {"formats": IMAGE_FORMATS, "max_mb": 10, "warn_mb": None},
    "audio": {"formats": AUDIO_FORMATS, "max_mb": 20, "warn_mb": None},
    "media": {"formats": AUDIO_FORMATS | {"mp4", "mpeg", "mov", "avi", "wmv"},
              "max_mb": 50, "warn_mb": None},  # audio is capped at 20 MB below
    "video": {"formats": {"mp4", "mpeg", "mov", "avi", "wmv", "ogg"},
              "max_mb": 50, "warn_mb": 10},
    "video_any": {"formats": VIDEO_FORMATS, "max_mb": 50, "warn_mb": None},
    "voice_ref": {"formats": {"mp3", "wav", "flac", "ogg", "m4a"}, "max_mb": 15, "warn_mb": 10},
    "music_ref": {"formats": {"mp3", "wav", "flac", "ogg", "m4a"}, "max_mb": 15, "warn_mb": 10},
    "audio_sync": {"formats": {"mp3", "wav", "ogg", "flac"}, "max_mb": 20, "warn_mb": None},
}
AUDIO_ONLY = {"aac", "mp3", "wav", "flac", "m4a"}

MIME_TYPES = {
    "png": "image/png", "jpeg": "image/jpeg", "gif": "image/gif", "bmp": "image/bmp",
    "webp": "image/webp", "aac": "audio/aac", "mp3": "audio/mpeg", "ogg": "audio/ogg",
    "wav": "audio/wav", "webm": "video/webm", "flac": "audio/flac", "m4a": "audio/mp4",
    "mp4": "video/mp4", "mpeg": "video/mpeg", "mov": "video/quicktime",
    "avi": "video/x-msvideo", "wmv": "video/x-ms-wmv", "mkv": "video/x-matroska",
}
EXTENSIONS = {
    "png": "png", "jpg": "jpeg", "jpeg": "jpeg", "gif": "gif", "bmp": "bmp", "webp": "webp",
    "aac": "aac", "mp3": "mp3", "ogg": "ogg", "oga": "ogg", "ogv": "ogg", "opus": "ogg",
    "wav": "wav", "webm": "webm", "flac": "flac", "m4a": "m4a", "mp4": "mp4",
    "m4v": "mp4", "mpeg": "mpeg", "mpg": "mpeg", "mov": "mov", "avi": "avi", "wmv": "wmv",
    "mkv": "mkv",
}
CONTENT_TYPE_EXT = {
    "image/png": "png", "image/jpeg": "jpg", "image/jpg": "jpg", "image/webp": "webp",
    "image/gif": "gif", "audio/mpeg": "mp3", "audio/mp3": "mp3", "audio/wav": "wav",
    "audio/x-wav": "wav", "audio/flac": "flac", "audio/ogg": "ogg", "audio/aac": "aac",
    "audio/mp4": "m4a", "video/mp4": "mp4", "video/webm": "webm",
    "video/quicktime": "mov", "application/json": "json", "text/plain": "txt",
    "text/vtt": "vtt", "application/x-subrip": "srt", "text/srt": "srt",
}
TEXT_EXTENSIONS = {"txt", "json", "srt", "vtt", "md", "csv"}
# A result file is named only with one of these (from Content-Type or the link).
RESULT_EXTENSIONS = set(CONTENT_TYPE_EXT.values()) | {"jpeg", "mkv", "opus", "bin"}
MAX_DOWNLOAD_BYTES = 1024 * 1024 * 1024   # 1 GB: far above any deAPI result

# Directories that never hold media a user wants processed. Files under them,
# and hidden files such as .env, are refused unless --allow-hidden-path is set.
SENSITIVE_DIRS = {
    ".ssh", ".aws", ".gnupg", ".gpg", ".git", ".svn", ".hg", ".docker", ".kube",
    ".azure", ".gcloud", ".config", ".password-store", ".terraform", ".vault",
}

# --------------------------------------------------------------- errors ---


class DeapiError(Exception):
    """An error with an exit code; details are added to the JSON error output."""

    def __init__(self, message, code=EXIT_ERROR, hint=None, **details):
        super().__init__(message)
        self.message = message
        self.code = code
        self.hint = hint
        self.details = details

    def to_json(self):
        err = {"exit_code": self.code, "type": EXIT_NAMES.get(self.code, "error"),
               "message": self.message}
        if self.hint:
            err["hint"] = self.hint
        for key, value in self.details.items():
            if value is not None:
                err[key] = value
        return {"error": err}


# ------------------------------------------------------------ output/logs ---

_SECRETS = set()
_QUIET = [False]


def redact(text):
    """Replace any known secret (the API key) in text with ***."""
    if not isinstance(text, str):
        text = str(text)
    for secret in _SECRETS:
        if secret:
            text = text.replace(secret, "***")
    return text


def log(message):
    """Diagnostics go to stderr (stdout carries JSON only)."""
    if _QUIET[0]:
        return
    try:
        sys.stderr.write(redact(message) + "\n")
        sys.stderr.flush()
    except (OSError, ValueError):
        pass


def warn(message):
    """Warnings are printed even with --quiet."""
    try:
        sys.stderr.write(redact("warning: " + message) + "\n")
        sys.stderr.flush()
    except (OSError, ValueError):
        pass


def emit(obj):
    """Print the command result as JSON on stdout."""
    text = json.dumps(obj, ensure_ascii=False, indent=2, default=str)
    sys.stdout.write(redact(text) + "\n")
    sys.stdout.flush()


def emit_error(exc):
    try:
        sys.stderr.write(redact("error: " + exc.message) + "\n")
        if exc.hint:
            sys.stderr.write(redact("hint: " + exc.hint) + "\n")
        sys.stderr.flush()
    except (OSError, ValueError):
        pass
    emit(exc.to_json())


def utf8_streams():
    """Avoid UnicodeEncodeError on Windows consoles (cp1250 and friends)."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def preview(text, limit=PREVIEW_CHARS):
    if text is None:
        return None
    if not isinstance(text, str):
        text = json.dumps(text, ensure_ascii=False)
    return text if len(text) <= limit else text[:limit] + "..."


# Indirections so tests can control time.
def _sleep(seconds):
    time.sleep(seconds)


def _now():
    return time.monotonic()


# ------------------------------------------------------------ arguments ---

def _add_common(parser, suppress):
    default = argparse.SUPPRESS if suppress else None
    group = parser.add_argument_group("common options")
    group.add_argument("--base-url", default=default if suppress else DEFAULT_BASE_URL,
                       help="API base URL; only https://*.deapi.ai (or localhost for tests)")
    group.add_argument("--timeout", type=float, default=default if suppress else DEFAULT_TIMEOUT,
                       help="seconds to wait for a job before exiting with code 9 "
                            "(default %d)" % DEFAULT_TIMEOUT)
    group.add_argument("--out-dir", default=default if suppress else ".",
                       help="directory for results (default: current directory)")
    group.add_argument("--key-file", default=default,
                       help="read the API key from this file (one line) instead of "
                            "DEAPI_API_KEY")
    group.add_argument("--quiet", action="store_true",
                       default=default if suppress else False,
                       help="no progress output on stderr")


def add_global_args(parser):
    """Common options on the top-level parser (before the command name)."""
    _add_common(parser, suppress=False)


def add_command(subparsers, name, help, paid=True, aliases=(), description=None,
                epilog=None):
    """Create a sub-command parser with the common options already added.

    paid=True also adds --quote/--price-only, --max-cost, --model,
    --allow-partner-models, --output and --allow-hidden-path.
    """
    parser = subparsers.add_parser(
        name, help=help, aliases=list(aliases), description=description or help,
        epilog=epilog, formatter_class=argparse.RawDescriptionHelpFormatter)
    _add_common(parser, suppress=True)
    if paid:
        group = parser.add_argument_group("price and model options")
        group.add_argument("--quote", "--price-only", dest="quote", action="store_true",
                           help="only get the free price quote; submit nothing")
        group.add_argument("--max-cost", type=float, default=None,
                           help="largest price in USD this run may spend (default: "
                                "$DEAPI_MAX_COST or %.2f). Only the user may raise it."
                                % DEFAULT_MAX_COST)
        group.add_argument("--model", help="model slug (see `deapi.py models`)")
        group.add_argument("--allow-partner-models", action="store_true",
                           help="let automatic selection pick partner models "
                                "(priced as estimates)")
        group.add_argument("--output", help="file name for the result (inside --out-dir "
                                            "unless a path is given)")
        group.add_argument("--allow-hidden-path", action="store_true",
                           help="allow input files from hidden or sensitive folders "
                                "(only when the user named the file)")
    return parser


def add_generation_args(parser, negative=True, steps=True, guidance=True, seed=True):
    """Shared generation flags. Values are sent only when the model supports them."""
    group = parser.add_argument_group("generation options")
    if negative:
        group.add_argument("--negative", help="negative prompt (if the model supports it)")
    if steps:
        group.add_argument("--steps", type=int, help="inference steps (if supported)")
    if guidance:
        group.add_argument("--guidance", type=float, help="guidance scale (if supported)")
    if seed:
        group.add_argument("--seed", type=int, help="seed (random if omitted)")
    return group


# --------------------------------------------------------------- helpers ---

def _host_ok(host):
    host = (host or "").lower()
    return host == "deapi.ai" or host.endswith(".deapi.ai")


def validate_base_url(url):
    """Allow only https://*.deapi.ai, or http(s)://localhost for tests."""
    parsed = urllib.parse.urlparse(url or "")
    host = (parsed.hostname or "").lower()
    if parsed.scheme == "https" and _host_ok(host):
        pass
    elif parsed.scheme in ("http", "https") and host in LOCAL_HOSTS:
        pass
    else:
        raise DeapiError("Refusing --base-url %r: only https://*.deapi.ai is allowed "
                         "(the API key is never sent anywhere else)." % url, EXIT_USAGE)
    if parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise DeapiError("--base-url must not contain credentials, a query or a fragment.",
                         EXIT_USAGE)
    return "%s://%s%s" % (parsed.scheme, parsed.netloc, parsed.path.rstrip("/"))


def resolve_max_cost(flag_value, environ=None):
    if environ is None:
        environ = {MAX_COST_ENV: os.getenv(MAX_COST_ENV, "")}
    if flag_value is not None:
        value, source = flag_value, "--max-cost"
    elif environ.get(MAX_COST_ENV, "").strip():
        source = MAX_COST_ENV
        try:
            value = float(environ[MAX_COST_ENV].strip())
        except ValueError:
            raise DeapiError("%s must be a number of USD." % MAX_COST_ENV, EXIT_USAGE)
    else:
        value, source = DEFAULT_MAX_COST, "default"
    if not (value >= 0) or math.isinf(value):
        raise DeapiError("%s must be a non-negative number of USD." % source, EXIT_USAGE)
    return float(value), source


def _header(headers, name):
    if headers is None:
        return None
    try:
        value = headers.get(name)
    except AttributeError:
        value = None
    if value is None and isinstance(headers, dict):
        for key, val in headers.items():
            if key.lower() == name.lower():
                return val
    return value


def _int(value, default=None):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def safe_name(name, fallback="result"):
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", str(name or "")).strip("._")
    return (name or fallback)[:100]


def unique_path(path):
    """Never overwrite: add -1, -2, ... before the extension."""
    if not os.path.exists(path):
        return path
    root, ext = os.path.splitext(path)
    index = 1
    while os.path.exists("%s-%d%s" % (root, index, ext)):
        index += 1
    return "%s-%d%s" % (root, index, ext)


def detect_platform(url):
    """Return the platform name for a supported media link, else None."""
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    for pattern, name in PLATFORMS:
        if re.search(pattern, host):
            return name
    return None


def is_url(value):
    return isinstance(value, str) and re.match(r"^https?://", value, re.I) is not None


def check_iso_language(code, flag="--lang"):
    """Accept only a two-letter ISO 639-1 code (the API accepts anything)."""
    value = (code or "").strip().lower()
    if value not in ISO_639_1:
        raise DeapiError("%s must be a two-letter ISO 639-1 code such as en, pl or de "
                         "(got %r)." % (flag, code), EXIT_USAGE)
    return value


# ----------------------------------------------------------- model cards ---

def model_slug(model):
    return (model or {}).get("slug")


def info(model, section):
    value = ((model or {}).get("info") or {}).get(section)
    return value if isinstance(value, dict) else {}


def limit(model, name, default=None):
    value = info(model, "limits").get(name)
    return default if value is None else value


def feature(model, name):
    """True only when the model card says the feature is supported."""
    return info(model, "features").get(name) is True


def feature_known(model, name):
    return info(model, "features").get(name) is not None


def default(model, name, fallback=None):
    value = info(model, "defaults").get(name)
    return fallback if value is None else value


def pinned(model, name):
    """The value a model fixes a parameter at (min_<name> == max_<name>), else None."""
    low, high = limit(model, "min_" + name), limit(model, "max_" + name)
    if low is not None and high is not None and float(low) == float(high):
        return low
    return None


def is_partner(model):
    tags = [str(t).lower() for t in ((model or {}).get("tags") or [])]
    return (model or {}).get("status") == "test_model" or "external" in tags \
        or "experimental" in tags


def clamp_param(model, name, value, label=None):
    """Clamp value to the model's min_<name>/max_<name>; log when it changes."""
    if value is None:
        return None
    low, high = limit(model, "min_" + name), limit(model, "max_" + name)
    new = value
    if low is not None and new < low:
        new = low
    if high is not None and new > high:
        new = high
    if isinstance(value, int) and not isinstance(value, bool):
        new = int(new)
    if new != value:
        log("note: %s %s adjusted to %s (model limits %s..%s)"
            % (label or name, value, new, low, high))
    return new


def fit_dimensions(model, width, height):
    """Fit width/height to the model card: ranges, side limits, pixel budget, grid."""
    w, h = int(width), int(height)
    orig = (w, h)
    min_w = limit(model, "min_width", limit(model, "min_side"))
    max_w = limit(model, "max_width", limit(model, "max_side"))
    min_h = limit(model, "min_height", limit(model, "min_side"))
    max_h = limit(model, "max_height", limit(model, "max_side"))
    max_pixels = limit(model, "max_pixels")
    if max_pixels and w * h > max_pixels:
        scale = math.sqrt(float(max_pixels) / (w * h))
        w, h = int(w * scale), int(h * scale)
    if max_w and w > max_w:
        w = int(max_w)
    if max_h and h > max_h:
        h = int(max_h)
    if min_w and w < min_w:
        w = int(min_w)
    if min_h and h < min_h:
        h = int(min_h)
    step = limit(model, "dimension_multiple") or limit(model, "resolution_step")
    if step:
        step = int(step)
        w, h = _snap(w, step, min_w), _snap(h, step, min_h)
    if (w, h) != orig:
        log("note: size %dx%d adjusted to %dx%d to fit the model" % (orig + (w, h)))
    return w, h


def _snap(value, step, minimum):
    snapped = value - value % step
    if snapped < max(step, minimum or 0):
        snapped = int(math.ceil(float(max(step, minimum or 0)) / step) * step)
    return snapped


def apply_generation_args(model, args, body, steps_key="steps", guidance_key="guidance",
                          negative_key="negative_prompt", seed_key="seed"):
    """Fill seed/steps/guidance/negative prompt from flags and the model card.

    A value goes into the body only when the model supports it (supports_*),
    clamped to the model's limits. When the card does not say, a value the
    user passed explicitly is sent and the price quote validates it.
    """
    if seed_key:
        seed = getattr(args, "seed", None)
        body[seed_key] = seed if seed is not None else random.randint(0, 2 ** 31 - 1)
    for flag, feat, key, limit_name, cast in (
            ("steps", "supports_steps", steps_key, "steps", int),
            ("guidance", "supports_guidance", guidance_key, "guidance", float)):
        if not key:
            continue
        given = getattr(args, flag, None)
        if feature(model, feat):
            value = given
            if value is None:
                value = default(model, flag, pinned(model, limit_name))
            if value is not None:
                body[key] = clamp_param(model, limit_name, cast(value), "--" + flag)
        elif given is not None:
            if feature_known(model, feat):
                log("note: %s does not support --%s; ignored" % (model_slug(model), flag))
            else:
                body[key] = cast(given)
    negative = getattr(args, "negative", None)
    if negative_key and negative:
        if feature(model, "supports_negative_prompt") or not feature_known(
                model, "supports_negative_prompt"):
            body[negative_key] = negative
        else:
            log("note: %s does not support --negative; ignored" % model_slug(model))
    return body


def model_language(model, code):
    """Find the model's language entry (languages[]) for an ISO 639-1 code."""
    code = (code or "").lower()
    name = LANGUAGE_NAMES.get(code)
    exact, prefixed, named = None, None, None
    for lang in (model or {}).get("languages") or []:
        slug = str(lang.get("slug") or "").lower()
        label = str(lang.get("name") or "").lower()
        if slug == code and exact is None:
            exact = lang
        elif re.match(r"^%s[-_]" % re.escape(code), slug) and prefixed is None:
            prefixed = lang
        elif name and (slug == name or label.startswith(name)) and named is None:
            named = lang
    return exact or prefixed or named


# --------------------------------------------------------------- files ---

def sniff_format(path):
    """Detect the media format from the first bytes (extension only breaks ties)."""
    with open(path, "rb") as handle:
        head = handle.read(64)
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if head.startswith(b"BM") and len(head) > 14:
        return "bmp"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "wav"
    if head[:4] == b"RIFF" and head[8:12] == b"AVI ":
        return "avi"
    if head.startswith(b"fLaC"):
        return "flac"
    if head.startswith(b"OggS"):
        return "ogg"
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand == b"qt  ":
            return "mov"
        if brand in (b"M4A ", b"M4B ") or ext == "m4a":
            return "m4a"
        return "mp4"
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        return "webm" if (b"webm" in head or ext == "webm") else "mkv"
    if head.startswith(b"\x30\x26\xb2\x75\x8e\x66\xcf\x11"):
        return "wmv"
    if head[:4] in (b"\x00\x00\x01\xba", b"\x00\x00\x01\xb3"):
        return "mpeg"
    if head.startswith(b"ID3"):
        return "mp3"
    if len(head) > 1 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0:
        if (head[1] & 0x06) == 0:
            return "aac"   # ADTS (layer bits 00)
        return "mp3"
    return None


def _sensitive_path(path):
    # Both the given path and its real target: a link named photo.png that points
    # into ~/.ssh is refused like the target itself.
    for candidate in (os.path.abspath(path), os.path.realpath(path)):
        for part in re.split(r"[\\/]+", candidate):
            if part.lower() in SENSITIVE_DIRS:
                return part
        base = os.path.basename(candidate)
        if base.startswith("."):
            return base
    return None


def _sensitive_output(path, is_file):
    """The sensitive folder (or hidden file name) in an output path, else None."""
    for candidate in (os.path.abspath(path), os.path.realpath(path)):
        for part in re.split(r"[\\/]+", candidate):
            if part.lower() in SENSITIVE_DIRS:
                return part
        if is_file and os.path.basename(candidate).startswith("."):
            return os.path.basename(candidate)
    return None


MAX_TEXT_FILE_BYTES = 2 * MB


def read_text_file(path, flag, allow_hidden=False):
    """The UTF-8 text of a local file given with a --*-file flag (exit 2 on problems).

    The same location check as for uploads applies: the text is sent to deAPI,
    so hidden files and sensitive folders (.ssh, .aws, .config, ...) are
    refused unless the user named the file and --allow-hidden-path is given.
    """
    path = os.path.expanduser(path)
    if not os.path.isfile(path):
        raise DeapiError("File not found for %s: %s" % (flag, path), EXIT_USAGE)
    bad = _sensitive_path(path)
    if bad and not allow_hidden:
        raise DeapiError(
            "Refusing to read %s for %s: it is in a hidden or sensitive location (%s). "
            "Never send a file the user did not point to; if the user named this file, add "
            "--allow-hidden-path." % (path, flag, bad), EXIT_USAGE)
    size = os.path.getsize(path)
    if size > MAX_TEXT_FILE_BYTES:
        raise DeapiError("%s is %.1f MB; %s takes text files up to %d MB." % (
            path, size / float(MB), flag, MAX_TEXT_FILE_BYTES // MB), EXIT_USAGE)
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            return handle.read()
    except (OSError, UnicodeDecodeError) as exc:
        raise DeapiError("Cannot read %s: %s" % (flag, exc), EXIT_USAGE)


class FilePart(object):
    """A checked local file to upload as one multipart field."""

    def __init__(self, field, path, fmt, size):
        self.field = field
        self.path = path
        self.fmt = fmt
        self.size = size
        self.mime = MIME_TYPES.get(fmt, "application/octet-stream")
        name = os.path.basename(path)
        self.filename = re.sub(r'["\\\r\n]', "_", name) or "upload"

    def public(self):
        return os.path.basename(self.path)


def check_file(path, kind, field="file", allow_hidden=False):
    """Validate a local input file and return a FilePart (exit 2 on problems)."""
    spec = FILE_KINDS[kind]
    if not path or is_url(path):
        raise DeapiError(
            "Expected a local file for %s, got %r. Download the file first, or use the "
            "deAPI MCP tools, which accept URLs." % (field, path), EXIT_USAGE)
    path = os.path.expanduser(path)
    if not os.path.isfile(path):
        raise DeapiError("File not found: %s" % path, EXIT_USAGE)
    bad = _sensitive_path(path)
    if bad and not allow_hidden:
        raise DeapiError(
            "Refusing to upload %s: it is in a hidden or sensitive location (%s). Never "
            "upload a file the user did not point to; if the user named this file, add "
            "--allow-hidden-path." % (path, bad), EXIT_USAGE)
    fmt = sniff_format(path)
    if fmt not in spec["formats"]:
        raise DeapiError(
            "Unsupported file type for %s: %s (detected: %s; allowed: %s). Convert it "
            "first, e.g. `ffmpeg -i INPUT -ac 1 -ar 16000 -b:a 48k out.mp3` for audio."
            % (field, path, fmt or "unknown", ", ".join(sorted(spec["formats"]))),
            EXIT_USAGE)
    size = os.path.getsize(path)
    max_mb = spec["max_mb"]
    if kind == "media" and fmt in AUDIO_ONLY:
        max_mb = 20
    if size > max_mb * MB:
        raise DeapiError("%s is %.1f MB; the limit for %s is %d MB." % (
            path, size / float(MB), field, max_mb), EXIT_USAGE)
    if kind == "media" and fmt in ("ogg", "webm") and size > 20 * MB:
        warn("%s is %.1f MB; if it holds only audio, the limit is 20 MB." % (
            path, size / float(MB)))
    if spec.get("warn_mb") and size > spec["warn_mb"] * MB:
        warn("%s is %.1f MB; some deAPI docs give %d MB as the limit for %s, so the API "
             "may refuse it." % (path, size / float(MB), spec["warn_mb"], field))
    return FilePart(field, path, fmt, size)


def image_size(path):
    """(width, height) from a PNG, JPEG, GIF or WebP header, else None."""
    try:
        with open(path, "rb") as handle:
            head = handle.read(32)
            if head.startswith(b"\x89PNG\r\n\x1a\n") and head[12:16] == b"IHDR":
                return (int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big"))
            if head[:6] in (b"GIF87a", b"GIF89a"):
                return (int.from_bytes(head[6:8], "little"), int.from_bytes(head[8:10], "little"))
            if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
                chunk = head[12:16]
                if chunk == b"VP8X":
                    return (1 + int.from_bytes(head[24:27], "little"),
                            1 + int.from_bytes(head[27:30], "little"))
                handle.seek(0)
                data = handle.read(40)
                if chunk == b"VP8 ":
                    return (int.from_bytes(data[26:28], "little") & 0x3FFF,
                            int.from_bytes(data[28:30], "little") & 0x3FFF)
                if chunk == b"VP8L":
                    bits = int.from_bytes(data[21:25], "little")
                    return ((bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1)
                return None
            if head.startswith(b"\xff\xd8"):
                handle.seek(2)
                while True:
                    marker = handle.read(2)
                    if len(marker) < 2 or marker[0] != 0xFF:
                        return None
                    code = marker[1]
                    if code in (0xD8, 0x01) or 0xD0 <= code <= 0xD7:
                        continue
                    length = int.from_bytes(handle.read(2), "big")
                    if code in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA,
                                0xCB, 0xCD, 0xCE, 0xCF):
                        data = handle.read(5)
                        return (int.from_bytes(data[3:5], "big"), int.from_bytes(data[1:3], "big"))
                    handle.seek(length - 2, 1)
    except (OSError, ValueError):
        return None
    return None


def media_duration(path):
    """Duration in seconds: WAV via the wave module, else ffprobe if installed.

    ffprobe is used only to read the duration; files are never converted.
    """
    try:
        if sniff_format(path) == "wav":
            with wave.open(path, "rb") as handle:
                frames, rate = handle.getnframes(), handle.getframerate()
                if rate:
                    return frames / float(rate)
    except (OSError, wave.Error, EOFError):
        pass
    probe = shutil.which("ffprobe")
    if not probe:
        return None
    try:
        out = subprocess.run(
            [probe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=30, check=False)
        value = float(out.stdout.strip().splitlines()[0])
        return value if value > 0 else None
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None


class Multipart(object):
    """multipart/form-data body streamed from disk (re-iterable for retries)."""

    CHUNK = 64 * 1024

    def __init__(self, fields=None, files=None):
        self.boundary = "deapi-" + uuid.uuid4().hex
        self._parts = []
        for name, value in _pairs(fields):
            if value is None:
                continue
            values = value if isinstance(value, (list, tuple)) else [value]
            for item in values:
                if isinstance(item, bool):
                    item = "1" if item else "0"
                head = ('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n'
                        % (self.boundary, name)).encode("utf-8")
                self._parts.append((head, str(item).encode("utf-8")))
        for part in files or []:
            head = ('--%s\r\nContent-Disposition: form-data; name="%s"; filename="%s"\r\n'
                    'Content-Type: %s\r\n\r\n'
                    % (self.boundary, part.field, part.filename, part.mime)).encode("utf-8")
            self._parts.append((head, part))
        self._tail = ("--%s--\r\n" % self.boundary).encode("utf-8")
        total = len(self._tail)
        for head, payload in self._parts:
            size = payload.size if isinstance(payload, FilePart) else len(payload)
            total += len(head) + size + 2
        self.length = total
        if total > MAX_REQUEST_BYTES:
            raise DeapiError("The request would be %.1f MB; deAPI accepts at most 75 MB "
                             "per request." % (total / float(MB)), EXIT_USAGE)

    @property
    def content_type(self):
        return "multipart/form-data; boundary=" + self.boundary

    def __iter__(self):
        for head, payload in self._parts:
            yield head
            if isinstance(payload, FilePart):
                with open(payload.path, "rb") as handle:
                    while True:
                        chunk = handle.read(self.CHUNK)
                        if not chunk:
                            break
                        yield chunk
            else:
                yield payload
            yield b"\r\n"
        yield self._tail

    def __len__(self):
        return self.length


def pick(body, *keys):
    """The listed keys of a JSON body or multipart fields (None values dropped).

    Price endpoints accept fewer fields than the job endpoints (see the
    *PriceRequestBody schemas in the spec), so commands build quote bodies
    with it, e.g. pick(body, "model", "width", "height", "frames", "fps").
    """
    source = dict(_pairs(body))
    return {k: source[k] for k in keys if source.get(k) is not None}


def image_quote(part, **fields):
    """Quote fields for single-image endpoints (OCR, background removal,
    upscale): width and height from the file header instead of uploading it.
    Returns (fields, files) for Job(quote_fields=..., quote_files=...)."""
    size = image_size(part.path)
    if size:
        return dict(fields, width=size[0], height=size[1]), []
    return dict(fields), [part]


def _pairs(fields):
    if not fields:
        return []
    if isinstance(fields, dict):
        return list(fields.items())
    return list(fields)


# ----------------------------------------------------------------- HTTP ---

class Response(object):
    def __init__(self, status, headers, body):
        self.status = status
        self.headers = headers
        self.body = body or b""

    def json(self):
        if not self.body:
            return {}
        try:
            return json.loads(self.body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise DeapiError("The API returned a response that is not JSON (HTTP %s)."
                             % self.status, EXIT_ERROR)


class Quote(object):
    def __init__(self, price, is_estimated=False, estimate_basis=None, raw=None):
        self.price = price
        self.is_estimated = bool(is_estimated)
        self.estimate_basis = estimate_basis
        self.raw = raw

    @classmethod
    def parse(cls, payload):
        node = payload
        if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
            node = payload["data"]
        if not isinstance(node, dict):
            raise DeapiError("Unexpected price response.", EXIT_ERROR)
        price = node.get("price")
        estimated = node.get("is_estimated", False)
        if isinstance(price, dict):
            estimated = price.get("is_estimated", estimated)
            price = price.get("amount")
        basis = node.get("estimate_basis")
        try:
            price = None if price is None else float(price)
        except (TypeError, ValueError):
            price = None
        if basis == "unavailable":
            price = None   # "price is 0.0 and must not be relied on"
        return cls(price, estimated, basis, payload)


def _job_price(job):
    price = (job or {}).get("price")
    if isinstance(price, dict):
        amount = price.get("amount")
        estimated = bool(price.get("is_estimated"))
    else:
        amount, estimated = price, False
    try:
        amount = None if amount is None else float(amount)
    except (TypeError, ValueError):
        amount = None
    return amount, estimated


def _validation_text(payload):
    errors = None
    if isinstance(payload, dict):
        errors = payload.get("errors")
        if errors is None and payload and all(isinstance(v, list) for v in payload.values()):
            errors = payload   # bare validation map {"field": ["message"]}
    items = []
    if isinstance(errors, dict):
        for field, messages in errors.items():
            if isinstance(messages, list):
                messages = "; ".join(str(m) for m in messages)
            items.append("%s: %s" % (field, messages))
    elif isinstance(errors, list):
        items = [str(e) for e in errors]
    return items


def map_http_error(status, payload, headers, method="GET", path=""):
    """Turn an API error into DeapiError with the right exit code.

    The `code` field decides first; the HTTP status only when `code` is absent
    or unknown (the list of codes grows).
    """
    if not isinstance(payload, dict):
        payload = {}
    code = payload.get("code")
    message = payload.get("message") or ""
    where = "%s %s" % (method, path)
    if code in ("missing_key", "invalid_key", "key_expired", "key_revoked") or status == 401:
        text = {
            "missing_key": "No API key reached the API.",
            "invalid_key": "The API key was rejected (unknown, wrong secret or malformed).",
            "key_expired": "The API key has expired.",
            "key_revoked": "The API key has been revoked (deactivated).",
        }.get(code, "The API refused the credentials (HTTP 401).")
        return DeapiError(text, EXIT_AUTH, KEY_HELP, api_code=code)
    if code == "ip_not_allowed":
        return DeapiError("This key has an IP allow-list and this machine's address is not "
                          "on it.", EXIT_FORBIDDEN, "Add the address to the key in the "
                          "dashboard, or use another key.", api_code=code)
    if code == "insufficient_permissions":
        return DeapiError("This key's role does not allow %s (account billing and key "
                          "management need an ADMIN key)." % where, EXIT_FORBIDDEN,
                          api_code=code)
    if code == "insufficient_balance":
        return DeapiError("Not enough credit on the account for this job.", EXIT_BALANCE,
                          "Top up at https://app.deapi.ai; the same request succeeds "
                          "after that.", api_code=code)
    if code == "key_limit_exceeded" or status == 402:
        resets = payload.get("resets_at")
        return DeapiError(
            "This API key has used up its own spend limit%s." % (
                " until %s" % resets if resets else ""), EXIT_KEY_LIMIT,
            "Wait for the limit to reset, or ask the user to raise the key's limit in the "
            "dashboard. Do not retry now.", api_code=code or "key_limit_exceeded",
            resets_at=resets)
    if code == "rate_limited" or status == 429:
        kind = _header(headers, "X-RateLimit-Type")
        retry_after = _int(_header(headers, "Retry-After"))
        if kind == "daily":
            reset = _header(headers, "X-RateLimit-Daily-Reset")
            return DeapiError("The daily request quota for this model or plan is used up.",
                              EXIT_RATE_LIMIT, "Try again after the reset%s; topping up "
                              "moves a Basic account to Premium limits." % (
                                  " (%s)" % reset if reset else ""),
                              api_code=code, rate_limit_type=kind, retry_after=retry_after,
                              resets_at=reset)
        return DeapiError("Rate limited (HTTP 429%s)." % (
            ", type %s" % kind if kind else ""), EXIT_RATE_LIMIT,
            "Wait %s seconds and try again." % (retry_after or "a few"),
            api_code=code, rate_limit_type=kind, retry_after=retry_after)
    if status == 403 and not message and not code:
        # No JSON from the API (an HTML page or a bare error code): a proxy,
        # firewall or the CDN refused the request before it reached deAPI.
        return DeapiError(
            "HTTP 403 for %s: the request was blocked before it reached the API (proxy, "
            "firewall or CDN)." % where, EXIT_ERROR,
            "Check the network, VPN or proxy and try again; this is not an account problem.",
            blocked_before_api=True)
    if status == 403:
        return DeapiError(message or "The account is suspended.", EXIT_SUSPENDED,
                          "Contact support@deapi.ai. Price quotes keep working.")
    if status == 410:
        return DeapiError(
            "This endpoint has been removed: %s. This skill is outdated; update it from "
            "https://github.com/deapi-ai/skills." % where, EXIT_GONE,
            successor=payload.get("successor"), documentation=payload.get("documentation"))
    if status == 422:
        items = _validation_text(payload)
        text = message or "The request was rejected by validation."
        hint = "Check the model's limits with `deapi.py models --details`."
        joined = " ".join(items).lower()
        if "not available on your plan" in joined or "higher tier" in joined:
            hint = ("This model is not available on the account's plan; pick another one "
                    "from `deapi.py models`.")
        elif any(i.startswith(("source_url", "video_url")) for i in items):
            hint = ("deAPI could not read the link. Check that it is public and from a "
                    "supported platform, or download the file and pass the file instead.")
        return DeapiError(text if not items else "%s (%s)" % (text, "; ".join(items)),
                          EXIT_VALIDATION, hint, errors=items or None)
    if status == 404:
        return DeapiError("Not found: %s." % where, EXIT_ERROR, message or None)
    return DeapiError("HTTP %s for %s%s" % (status, where, (": " + message) if message else ""),
                      EXIT_ERROR)


# ------------------------------------------------------------ the client ---

class Context(object):
    """Configuration plus the API client. One per command run."""

    def __init__(self, base_url=DEFAULT_BASE_URL, timeout=DEFAULT_TIMEOUT, out_dir=".",
                 max_cost=None, key_file=None, quiet=False, allow_hidden_path=False,
                 environ=None):
        if environ is None:
            # Only the variables this skill uses are read, one by one.
            environ = {name: os.getenv(name, "") for name in (KEY_ENV, MAX_COST_ENV)}
        self.environ = environ
        self.base_url = validate_base_url(base_url or DEFAULT_BASE_URL)
        parsed = urllib.parse.urlparse(self.base_url)
        self.api_host = (parsed.hostname or "").lower()
        self.local = self.api_host in LOCAL_HOSTS
        self.timeout = float(timeout or DEFAULT_TIMEOUT)
        self.out_dir = out_dir or "."
        self.max_cost, self.max_cost_source = resolve_max_cost(max_cost, self.environ)
        self.key_file = key_file
        self.allow_hidden_path = bool(allow_hidden_path)
        _QUIET[0] = bool(quiet)
        self._key = None
        self._models = {}

    @classmethod
    def from_args(cls, args):
        return cls(base_url=getattr(args, "base_url", None),
                   timeout=getattr(args, "timeout", None),
                   out_dir=getattr(args, "out_dir", None),
                   max_cost=getattr(args, "max_cost", None),
                   key_file=getattr(args, "key_file", None),
                   quiet=getattr(args, "quiet", False),
                   allow_hidden_path=getattr(args, "allow_hidden_path", False))

    # -- key -------------------------------------------------------------

    def api_key(self):
        if self._key:
            return self._key
        if self.key_file:
            key = self._read_key_file(self.key_file)
        else:
            key = (self.environ.get(KEY_ENV) or "").strip()
        if not key:
            raise DeapiError("DEAPI_API_KEY is not set.", EXIT_AUTH, KEY_HELP)
        _SECRETS.add(key)
        self._key = key
        return key

    @staticmethod
    def _read_key_file(path):
        try:
            with open(os.path.expanduser(path), "r", encoding="utf-8") as handle:
                raw = handle.read(1024)
        except (OSError, UnicodeDecodeError):
            raise DeapiError("Cannot read --key-file.", EXIT_AUTH, KEY_HELP)
        lines = [line for line in raw.splitlines() if line.strip()]
        key = lines[0].strip() if len(lines) == 1 else ""
        if not key or len(key) > 300 or re.search(r"\s", key):
            # Never echo the content: it may be a key, or something else private.
            raise DeapiError("--key-file must contain exactly one line with the key "
                             "(no spaces, at most 300 characters).", EXIT_AUTH, KEY_HELP)
        return key

    def has_key(self):
        try:
            self.api_key()
            return True
        except DeapiError:
            return False

    # -- URLs ------------------------------------------------------------

    def url(self, path, query=None):
        url = self.base_url + path
        if query:
            url += "?" + urllib.parse.urlencode(query, safe="[],")
        return url

    def oai_url(self, path):
        base = self.base_url if self.local else OAI_BASE_URL
        return base + path

    def _may_send_key(self, url):
        host = (urllib.parse.urlparse(url).hostname or "").lower()
        if host == self.api_host:
            return True
        return host == urllib.parse.urlparse(OAI_BASE_URL).hostname and _host_ok(self.api_host)

    # -- requests --------------------------------------------------------

    def request(self, method, url, body=None, content_type=None, mode="read", auth=True,
                timeout=None):
        """Send one API request and return Response.

        mode: "read" (GET) and "quote" (POST .../price) are safe to retry on 429,
        5xx and network errors. "submit" (a paid POST) is retried only on 429,
        which the API returns before accepting a job; a 5xx or a dropped
        connection after sending exits with code 9.
        """
        path = urllib.parse.urlparse(url).path
        headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
        if auth:
            if not self._may_send_key(url):
                raise DeapiError("Refusing to send the API key to %s." % url, EXIT_USAGE)
            headers["Authorization"] = "Bearer " + self.api_key()
        data = body
        if isinstance(body, Multipart):
            headers["Content-Type"] = body.content_type
            headers["Content-Length"] = str(body.length)
        elif body is not None:
            headers["Content-Type"] = content_type or "application/json"
        attempt = 0
        while True:
            attempt += 1
            req = urllib.request.Request(url, data=data, method=method)
            for name, value in headers.items():
                if name == "Authorization":
                    # Unredirected: urllib copies normal headers to the target of a
                    # 3xx redirect, which may be another host. The key never follows.
                    req.add_unredirected_header(name, value)
                else:
                    req.add_header(name, value)
            try:
                resp = urllib.request.urlopen(req, timeout=timeout or HTTP_TIMEOUT)
                try:
                    raw = resp.read()
                    status = getattr(resp, "status", None) or resp.getcode()
                    resp_headers = resp.headers
                finally:
                    _close(resp)
                return Response(status, resp_headers, raw)
            except urllib.error.HTTPError as exc:
                status, resp_headers = exc.code, exc.headers
                try:
                    raw = exc.read()
                except (OSError, ValueError, http.client.HTTPException):
                    raw = b""
                _close(exc)
                payload = _parse_json(raw)
                if status == 429:
                    kind = _header(resp_headers, "X-RateLimit-Type")
                    wait = _int(_header(resp_headers, "Retry-After"), 0)
                    if kind not in ("daily", "ip-not-allowed") and wait <= MAX_RETRY_AFTER \
                            and attempt <= MAX_RETRIES:
                        log("rate limited; retrying in %ds" % max(1, wait or 2 ** attempt))
                        _sleep(max(1, wait or 2 ** attempt))
                        continue
                elif status >= 500:
                    if mode == "submit":
                        raise DeapiError(
                            "HTTP %s while submitting %s." % (status, path),
                            EXIT_UNKNOWN_STATE, NOT_ACCEPTED_HINT)
                    if attempt <= MAX_RETRIES:
                        log("HTTP %s; retrying (%d/%d)" % (status, attempt, MAX_RETRIES))
                        _sleep(2 ** (attempt - 1))
                        continue
                raise map_http_error(status, payload, resp_headers, method, path)
            except (urllib.error.URLError, http.client.HTTPException, OSError) as exc:
                reason = getattr(exc, "reason", exc)
                if isinstance(reason, ssl.SSLCertVerificationError) or \
                        isinstance(exc, ssl.SSLCertVerificationError):
                    raise DeapiError("TLS error for %s: %s" % (url, reason), EXIT_ERROR,
                                     SSL_HINT)
                not_sent = isinstance(reason, (socket.gaierror, ConnectionRefusedError))
                if mode == "submit" and not not_sent:
                    raise DeapiError("Connection lost while submitting %s (%s)."
                                     % (path, reason), EXIT_UNKNOWN_STATE, NOT_ACCEPTED_HINT)
                if mode != "submit" and attempt <= MAX_RETRIES and not not_sent:
                    log("network error (%s); retrying (%d/%d)" % (reason, attempt, MAX_RETRIES))
                    _sleep(2 ** (attempt - 1))
                    continue
                raise DeapiError("Network error for %s: %s" % (url, reason), EXIT_ERROR,
                                 "Check the connection; the hosts to allow are listed in "
                                 "references/network.md.")

    def get_json(self, path, query=None):
        return self.request("GET", self.url(path, query)).json()

    def post_json(self, path, payload, mode="submit"):
        body = json.dumps(payload).encode("utf-8")
        return self.request("POST", self.url(path), body, "application/json", mode).json()

    def post_multipart(self, path, fields=None, files=None, mode="submit"):
        return self.request("POST", self.url(path), Multipart(fields, files), mode=mode).json()

    def _post(self, path, json_body=None, fields=None, files=None, mode="submit"):
        if json_body is not None:
            return self.post_json(path, json_body, mode)
        return self.post_multipart(path, fields or {}, files or [], mode)

    # -- models ----------------------------------------------------------

    def models(self, types=None):
        """Live model list (GET /api/v2/models), optionally filtered by type."""
        key = ",".join(sorted(types or []))
        if key in self._models:
            return self._models[key]
        found, page = [], 1
        while page <= 20:
            query = {"limit": 100, "per_page": 100, "page": page}
            if types:
                query["filter[inference_types]"] = ",".join(types)
            payload = self.get_json("/api/v2/models", query)
            batch = payload.get("data") if isinstance(payload, dict) else payload
            if not isinstance(batch, list) or not batch:
                break
            found.extend(m for m in batch if isinstance(m, dict) and m.get("slug"))
            meta = payload.get("meta") if isinstance(payload, dict) else None
            last = _int((meta or {}).get("last_page"))
            if last is None or page >= last:
                break
            page += 1
        if types:
            wanted = set(types)
            found = [m for m in found if wanted & set(m.get("inference_types") or [])]
        self._models[key] = found
        return found

    def pick_model(self, task, explicit=None, allow_partner=False, require=None,
                   predicate=None, why=None):
        """Choose a model for a task.

        Order: the user's --model; else standard (non-partner) models that
        have every feature in `require` and pass `predicate`, ranked by the
        task's preference hints. Partner models are chosen automatically only
        with allow_partner. Never invents IDs: everything comes from the API.
        """
        task = TASK_ALIASES.get(task, task)
        types = TASK_TYPES[task]
        candidates = self.models(types)
        if explicit:
            for model in candidates:
                if model.get("slug") == explicit:
                    if require and not all(feature(model, f) for f in require):
                        raise DeapiError("Model %s does not support %s." % (
                            explicit, why or ", ".join(require)), EXIT_VALIDATION,
                            "Run `deapi.py models --type %s --details`." % task)
                    return model
            for model in self.models():
                if model.get("slug") == explicit:
                    raise DeapiError("Model %s does not serve %s (it serves %s)." % (
                        explicit, task, ", ".join(model.get("inference_types") or [])),
                        EXIT_VALIDATION, "Run `deapi.py models --type %s`." % task)
            raise DeapiError("Unknown model %r (not in GET /api/v2/models for this account)."
                             % explicit, EXIT_VALIDATION,
                             "Run `deapi.py models --type %s`; never guess model IDs." % task)
        pool = [m for m in candidates if allow_partner or not is_partner(m)]
        if require:
            pool = [m for m in pool if all(feature(m, f) for f in require)]
        if predicate:
            pool = [m for m in pool if predicate(m)]
        if not pool:
            raise DeapiError("No available model for %s%s." % (
                task, " with " + (why or ", ".join(require)) if (require or why) else ""),
                EXIT_VALIDATION, "Run `deapi.py models --type %s --details`." % task)
        for pattern in PREFERENCES.get(task, []):
            for model in pool:
                haystack = "%s %s" % (model.get("slug", ""), model.get("name", ""))
                if re.search(pattern, haystack, re.IGNORECASE):
                    log("model: %s (auto-selected from GET /api/v2/models)" % model["slug"])
                    return model
        log("model: %s (auto-selected from GET /api/v2/models)" % pool[0]["slug"])
        return pool[0]

    # -- prices ----------------------------------------------------------

    def quote(self, path, json_body=None, fields=None, files=None):
        """Free price quote: POST <path>/price with the same parameters."""
        payload = self._post(path + "/price", json_body, fields, files, mode="quote")
        return Quote.parse(payload)

    def limit_info(self):
        """The cost limit in force, for every paid command's output."""
        return {"max_cost": self.max_cost, "max_cost_source": self.max_cost_source}

    def enforce_budget(self, total, quote, command=None):
        """Exit 5 when the quoted total is unknown or above --max-cost."""
        if total is None:
            raise DeapiError(
                "deAPI could not price this request in advance, so the cost limit cannot "
                "be checked.", EXIT_OVER_BUDGET, "Ask the user before running it; only the "
                "user may raise --max-cost or $DEAPI_MAX_COST.", price=None,
                max_cost=self.max_cost, max_cost_source=self.max_cost_source,
                estimate_basis=quote.estimate_basis)
        if total > self.max_cost + 1e-12:
            raise DeapiError(
                "Quoted price %s USD%s is above the cost limit of %s USD (%s)." % (
                    _fmt_usd(total), " (estimate)" if quote.is_estimated else "",
                    _fmt_usd(self.max_cost), self.max_cost_source), EXIT_OVER_BUDGET,
                "Show the price to the user and ask. Never raise --max-cost yourself; "
                "the user may rerun with a higher --max-cost.",
                price=total, is_estimated=quote.is_estimated, max_cost=self.max_cost,
                max_cost_source=self.max_cost_source, command=command)

    def warn_key_limit(self, total):
        """Before a larger job, warn when the key's remaining limit looks too low."""
        try:
            data = self.get_json("/api/v2/key").get("data") or {}
        except DeapiError:
            return
        remaining = data.get("limit_remaining")
        if isinstance(remaining, (int, float)) and remaining < total:
            warn("the key's remaining spend limit (%s USD) is below the quoted %s USD; the "
                 "API will refuse the job with key_limit_exceeded."
                 % (_fmt_usd(remaining), _fmt_usd(total)))
        if data.get("role") == "admin":
            try:
                balance = (self.get_json("/api/v2/account/balance").get("data") or {}).get(
                    "balance")
            except DeapiError:
                return
            if isinstance(balance, (int, float)) and balance < total:
                warn("the account balance (%s USD) is below the quoted %s USD; top up at "
                     "https://app.deapi.ai." % (_fmt_usd(balance), _fmt_usd(total)))

    # -- jobs ------------------------------------------------------------

    def get_job(self, request_id):
        request_id = check_request_id(request_id)
        payload = self.get_json("/api/v2/jobs/" + urllib.parse.quote(request_id, safe=""))
        job = payload.get("data") if isinstance(payload, dict) else None
        return job if isinstance(job, dict) else (payload if isinstance(payload, dict) else {})

    def wait_job(self, request_id, timeout=None):
        """Poll GET /api/v2/jobs/{id} (2 s growing to 10 s) until done or error."""
        timeout = self.timeout if timeout is None else timeout
        deadline = _now() + timeout
        delay = 2.0
        log("job %s submitted; waiting (timeout %ds)" % (request_id, timeout))
        last = None
        while True:
            job = self.get_job(request_id)
            status = str(job.get("status") or "").lower()
            progress = job.get("progress")
            line = "status=%s%s" % (status, " progress=%s%%" % progress
                                    if progress is not None else "")
            if line != last:
                log(line)
                last = line
            if status == "done":
                return job
            if status in ("error", "failed"):
                raise job_failure(request_id, job)
            if _now() + delay > deadline:
                raise DeapiError(
                    "The job is still running after %ds." % timeout, EXIT_UNKNOWN_STATE,
                    "Check it later with `deapi.py status %s --wait --download`; do not "
                    "submit it again." % request_id, request_id=request_id, status=status)
            _sleep(delay)
            delay = min(10.0, delay * 1.5)

    # -- results ---------------------------------------------------------

    def check_output_target(self, explicit=None):
        """Exit 2 when --out-dir or --output points into a sensitive folder."""
        targets = [("--out-dir", os.path.expanduser(self.out_dir), False)]
        if explicit:
            targets.append(("--output", os.path.expanduser(explicit), True))
        for flag, path, is_file in targets:
            bad = _sensitive_output(path, is_file)
            if bad and not self.allow_hidden_path:
                raise DeapiError(
                    "Refusing to write results to %s (%s): it is in a hidden or sensitive "
                    "location (%s). Use a normal folder; if the user asked for this place, "
                    "add --allow-hidden-path." % (path, flag, bad), EXIT_USAGE)

    def ensure_out_dir(self):
        self.out_dir = os.path.expanduser(self.out_dir)
        self.check_output_target()
        try:
            os.makedirs(self.out_dir, exist_ok=True)
        except OSError as exc:
            raise DeapiError("Cannot create --out-dir %s: %s" % (self.out_dir, exc), EXIT_USAGE)
        return self.out_dir

    def output_path(self, name, ext, explicit=None):
        """Target path in --out-dir, never overwriting an existing file."""
        if explicit:
            explicit = os.path.expanduser(explicit)
            self.check_output_target(explicit)
            path = explicit if os.path.dirname(explicit) else os.path.join(self.out_dir, explicit)
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            if not os.path.splitext(path)[1] and ext:
                path += "." + ext
        else:
            self.ensure_out_dir()
            path = os.path.join(self.out_dir, safe_name(name) + ("." + ext if ext else ""))
        return os.path.abspath(unique_path(path))

    def write_text(self, name, text, ext="txt", explicit=None):
        path = self.output_path(name, ext, explicit)
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        return path

    def download(self, url, name, default_ext=None, explicit=None):
        """Download a result (signed link) without the Authorization header."""
        parsed = urllib.parse.urlparse(url or "")
        host = (parsed.hostname or "").lower()
        if not (parsed.scheme == "https" or (parsed.scheme == "http" and host in LOCAL_HOSTS)):
            raise DeapiError("Refusing to download a result from %r (https only)." % url,
                             EXIT_ERROR)
        attempt = 0
        while True:
            attempt += 1
            req = urllib.request.Request(url, method="GET")
            req.add_header("User-Agent", USER_AGENT)
            try:
                resp = urllib.request.urlopen(req, timeout=HTTP_TIMEOUT)
                try:
                    ctype = (_header(resp.headers, "Content-Type") or "").split(";")[0].strip()
                    url_ext = _url_ext(url)
                    if url_ext not in RESULT_EXTENSIONS:
                        url_ext = None   # never name a result .sh, .exe and the like
                    ext = CONTENT_TYPE_EXT.get(ctype.lower()) or url_ext or default_ext \
                        or "bin"
                    path = self.output_path(name, ext, explicit)
                    tmp = path + ".part"
                    written = 0
                    try:
                        with open(tmp, "wb") as handle:
                            while True:
                                chunk = resp.read(Multipart.CHUNK)
                                if not chunk:
                                    break
                                written += len(chunk)
                                if written > MAX_DOWNLOAD_BYTES:
                                    raise DeapiError(
                                        "The result is larger than %d MB; download stopped."
                                        % (MAX_DOWNLOAD_BYTES // MB), EXIT_ERROR,
                                        "Fetch it from the dashboard or with `deapi.py status "
                                        "ID` and the result link.")
                                handle.write(chunk)
                        os.replace(tmp, path)
                    except BaseException:
                        try:
                            os.remove(tmp)
                        except OSError:
                            pass
                        raise
                    return path
                finally:
                    _close(resp)
            except urllib.error.HTTPError as exc:
                _close(exc)
                if exc.code >= 500 and attempt <= MAX_RETRIES:
                    _sleep(2 ** (attempt - 1))
                    continue
                raise DeapiError("Downloading the result failed (HTTP %s); the link may have "
                                 "expired. Run `deapi.py status ID --download` for a fresh "
                                 "one." % exc.code, EXIT_ERROR)
            except (urllib.error.URLError, http.client.HTTPException, OSError) as exc:
                if attempt <= MAX_RETRIES:
                    _sleep(2 ** (attempt - 1))
                    continue
                raise DeapiError("Downloading the result failed: %s"
                                 % getattr(exc, "reason", exc), EXIT_ERROR)

    def deliver(self, job, request_id, default_ext=None, text_ext="txt", explicit=None,
                alt_format=None):
        """Save the job's result: text to a file (plus a preview), files downloaded."""
        out = {}
        text = job.get("result")
        url = job.get("result_url")
        if alt_format:
            alt = (job.get("results_alt_formats") or {}).get(alt_format)
            if alt:
                url = alt
            else:
                warn("no %s version of the result; saving the original" % alt_format)
        if text not in (None, ""):
            if not isinstance(text, str):
                text = json.dumps(text, ensure_ascii=False, indent=2)
            ext = text_ext
            if text_ext == "json" or (text_ext == "auto" and _looks_json(text)):
                ext = "json"
            elif text_ext == "auto":
                ext = "txt"
            out["text_file"] = self.write_text(request_id, text, ext, explicit)
            out["preview"] = preview(text)
            out["chars"] = len(text)
        elif url:
            path = self.download(url, request_id, default_ext, explicit)
            out["files"] = [path]
            if os.path.splitext(path)[1].lstrip(".").lower() in TEXT_EXTENSIONS:
                try:
                    with open(path, "r", encoding="utf-8", errors="replace") as handle:
                        content = handle.read(PREVIEW_CHARS + 1)
                    out["text_file"] = path
                    out["preview"] = preview(content)
                except OSError:
                    pass
        return out

    # -- the one-call path for paid commands ------------------------------

    def run(self, job, args):
        """Quote, check the cost limit, submit, wait and deliver. See Job."""
        self.check_output_target(job.output)
        model = job.model
        slug = model_slug(model) if isinstance(model, dict) else model
        quote = self.quote(job.path, *job.quote_request())
        count = max(1, int(job.count or 1))
        total = None if quote.price is None else round(quote.price * count, 8)
        summary = {"command": job.command, "model": slug, "quoted_price": quote.price,
                   "is_estimated": quote.is_estimated}
        summary.update(self.limit_info())
        if quote.estimate_basis:
            summary["estimate_basis"] = quote.estimate_basis
        if count > 1:
            summary["count"] = count
            summary["quoted_total"] = total
        if job.info:
            summary.update(job.info)
        log("quote: %s USD%s%s" % (
            "unknown" if quote.price is None else _fmt_usd(quote.price),
            " (estimate)" if quote.is_estimated else "",
            " x %d = %s USD" % (count, _fmt_usd(total)) if count > 1 and total is not None
            else ""))
        if self.max_cost > DEFAULT_MAX_COST + 1e-12:
            summary["max_cost_raised"] = True
            warn("the cost limit is %s USD (%s), above the default %s USD" % (
                _fmt_usd(self.max_cost), self.max_cost_source, _fmt_usd(DEFAULT_MAX_COST)))
        if getattr(args, "quote", False):
            summary.update({"quote_only": True,
                            "within_max_cost": total is not None and total <= self.max_cost,
                            "params": job.public_params()})
            return summary
        self.enforce_budget(total, quote, job.command)
        if total is not None and total > KEY_LIMIT_CHECK_USD:
            self.warn_key_limit(total)
        results = []
        for index in range(count):
            try:
                results.append(self._run_one(job, index, quote))
            except DeapiError as exc:
                if results:
                    # Earlier jobs are paid and saved: report them with the error.
                    exc.details["completed_results"] = results
                    exc.hint = ((exc.hint + " ") if exc.hint else "") + (
                        "%d of %d jobs finished before this one; their files are in "
                        "completed_results." % (len(results), count))
                raise
        if count == 1:
            summary.update(results[0])
        else:
            summary["results"] = results
            prices = [r.get("price") for r in results if r.get("price") is not None]
            summary["price"] = round(sum(prices), 8) if prices else None
        return summary

    def _run_one(self, job, index, quote):
        body_json, fields, files = job.submit_request(index)
        retried = False
        while True:
            payload = self._post(job.path, body_json, fields, files, mode="submit")
            node = payload.get("data") if isinstance(payload, dict) and \
                isinstance(payload.get("data"), dict) else payload
            request_id = node.get("request_id") if isinstance(node, dict) else None
            if not request_id:
                # Synchronous endpoint (e.g. the prompt booster): the reply is the result.
                return {"status": "done", "result": node}
            try:
                done = self.wait_job(request_id)
                break
            except DeapiError as exc:
                if exc.code != EXIT_JOB_FAILED or retried or not job.retry_failed \
                        or not exc.details.get("retryable") or not exc.details.get("refunded") \
                        or exc.details.get("error_reason") in NO_RETRY_REASONS:
                    raise
                retried = True
                warn("job %s failed (%s) and was refunded; retrying once after a new quote"
                     % (request_id, exc.details.get("error_reason")))
                again = self.quote(job.path, *job.quote_request())
                self.enforce_budget(None if again.price is None
                                    else round(again.price * max(1, job.count), 8), again,
                                    job.command)
        price, estimated = _job_price(done)
        result = {"request_id": request_id, "status": "done", "price": price}
        if estimated:
            result["price_is_estimated"] = True
        seed = (body_json or dict(_pairs(fields))).get(job.seed_field) if job.seed_field else None
        if seed is not None:
            result["seed"] = seed
        explicit = job.output
        if explicit and job.count > 1:
            root, ext = os.path.splitext(explicit)
            explicit = "%s-%d%s" % (root, index + 1, ext)
        result.update(self.deliver(done, request_id, job.default_ext, job.text_ext,
                                   explicit, job.alt_format))
        if done.get("prompt_boosted"):
            result["prompt_boost"] = done.get("prompt_boost")
        if done.get("metadata"):
            result["metadata"] = done.get("metadata")
        if price is not None and quote.price and price > quote.price * (1 + PRICE_DRIFT_WARN):
            warn("final price %s USD is more than 20%% above the quote %s USD"
                 % (_fmt_usd(price), _fmt_usd(quote.price)))
        return result


NO_RETRY_REASONS = ("SOURCE_PRIVATE", "SOURCE_RESTRICTED")


class Job(object):
    """Everything Context.run() needs for one paid command.

    path        endpoint, e.g. "/api/v2/images/generations" (quote: path + "/price")
    model       the chosen model card (dict) or slug
    json        JSON body, or
    fields      multipart fields (dict or list of pairs; lists repeat the field)
    files       list of FilePart (from check_file) for multipart
    quote_json / quote_fields / quote_files
                a different body for the quote (e.g. duration_seconds instead of
                the file); default: the same body as the submission
    count       submit N times (seeds base, base+1, ...), one cost check for all
    seed_field  body key holding the seed ("seed"; None to disable)
    default_ext extension when the result's Content-Type is unknown
    text_ext    "txt", "json" or "auto" for text results
    alt_format  "jpg"/"webp" to download results_alt_formats instead
    output      explicit result file name (--output)
    info        extra keys for the JSON output
    """

    def __init__(self, command, path, model, json=None, fields=None, files=None,
                 quote_json=None, quote_fields=None, quote_files=None, count=1,
                 seed_field="seed", default_ext=None, text_ext="txt", alt_format=None,
                 output=None, info=None, retry_failed=True):
        if json is None and fields is None:
            fields = {}
        self.command = command
        self.path = path
        self.model = model
        self.json = json
        self.fields = fields
        self.files = list(files or [])
        self.quote_json = quote_json
        self.quote_fields = quote_fields
        self.quote_files = quote_files
        self.count = min(MAX_COUNT, max(1, int(count or 1)))
        self.seed_field = seed_field
        self.default_ext = default_ext
        self.text_ext = text_ext
        self.alt_format = alt_format
        self.output = output
        self.info = info
        self.retry_failed = retry_failed

    def quote_request(self):
        if self.quote_json is not None:
            return self.quote_json, None, None
        if self.quote_fields is not None:
            return None, self.quote_fields, list(self.quote_files or [])
        return self.json, (None if self.json is not None else self.fields), \
            (None if self.json is not None else self.files)

    def submit_request(self, index=0):
        if self.json is not None:
            body = dict(self.json)
            if index and self.seed_field and self.seed_field in body:
                body[self.seed_field] = (int(body[self.seed_field]) + index) % (2 ** 31)
            return body, None, None
        fields = _pairs(self.fields)
        if index and self.seed_field:
            fields = [(k, (int(v) + index) % (2 ** 31) if k == self.seed_field else v)
                      for k, v in fields]
        return None, fields, self.files

    def public_params(self):
        params = dict(self.json) if self.json is not None else dict(_pairs(self.fields))
        for part in self.files:
            params.setdefault(part.field, part.public())
        return params


def job_failure(request_id, job):
    reason = job.get("error_reason") or ("PROCESSING_FAILED" if job.get("error_code") else None)
    if reason in NO_RETRY_REASONS:
        hint = "The source is private, deleted, age- or region-restricted; do not retry."
    elif job.get("retryable"):
        hint = "The failure may be temporary; one retry after a new quote is reasonable."
    else:
        hint = "Do not retry unchanged; check the inputs and the model's limits."
    return DeapiError("Job %s failed%s." % (request_id, (": " + reason) if reason else ""),
                      EXIT_JOB_FAILED, hint, request_id=request_id,
                      error_code=job.get("error_code"), error_reason=reason,
                      retryable=job.get("retryable"), refunded=job.get("refunded"))


def check_request_id(value):
    value = str(value or "").strip()
    if value.lower().startswith("job:"):
        value = value[4:]
    if not re.match(r"^[A-Za-z0-9-]{8,64}$", value):
        raise DeapiError("Not a job request ID: %r" % value, EXIT_USAGE)
    return value


def resolve_input(ctx, value, kind, field="file", allow_hidden=False):
    """A local file, or `job:<request_id>` (the result of an earlier job).

    Earlier results are downloaded without the Authorization header and then
    checked like any local file. Other URLs are refused: download the file
    first, or use the deAPI MCP tools, which accept URLs.
    """
    if isinstance(value, str) and value.lower().startswith("job:"):
        request_id = check_request_id(value)
        job = ctx.get_job(request_id)
        if str(job.get("status")).lower() != "done" or not job.get("result_url"):
            raise DeapiError("Job %s has no downloadable result (status %s)." % (
                request_id, job.get("status")), EXIT_USAGE)
        path = ctx.download(job["result_url"], request_id + "-input")
        return check_file(path, kind, field, allow_hidden=True)
    if is_url(value):
        host = (urllib.parse.urlparse(value).hostname or "").lower()
        if _host_ok(host) and host != ctx.api_host:
            path = ctx.download(value, "input-" + uuid.uuid4().hex[:8])
            return check_file(path, kind, field, allow_hidden=True)
        raise DeapiError("URLs are not accepted for %s. Download the file first, or use the "
                         "deAPI MCP tools, which accept URLs." % field, EXIT_USAGE)
    return check_file(value, kind, field, allow_hidden=allow_hidden or ctx.allow_hidden_path)


def _looks_json(text):
    text = text.lstrip()
    if not text or text[0] not in "[{":
        return False
    try:
        json.loads(text)
        return True
    except ValueError:
        return False


def _url_ext(url):
    ext = os.path.splitext(urllib.parse.urlparse(url).path)[1].lstrip(".").lower()
    return ext if re.match(r"^[a-z0-9]{1,5}$", ext) else None


def _parse_json(raw):
    try:
        return json.loads((raw or b"").decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {"message": (raw or b"")[:300].decode("utf-8", "replace").strip()}


def _close(resp):
    try:
        resp.close()
    except Exception:  # noqa: BLE001 - closing must never mask the real outcome
        pass


def _fmt_usd(value):
    if value is None:
        return "?"
    return ("%.6f" % value).rstrip("0").rstrip(".") if value else "0"
