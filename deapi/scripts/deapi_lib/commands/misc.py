"""Shared commands: models, status, account (alias balance), doctor, embed, boost."""

import datetime
import json
import os
import platform
import re
import shutil
import sys
import time

from .. import VERSION, core

ORDER = 90  # position in --help

SAMPLES = [
    ["embed", "hello world"],
    ["embed", "--input", "first text", "--input", "second text"],
    ["boost", "--prompt", "a red fox in the snow", "--type", "image"],
    ["boost", "--prompt", "a red fox in the snow", "--type", "video",
     "--negative", "blurry, low quality"],
    ["boost", "--prompt", "make the sky purple", "--type", "edit", "--image", "{png}"],
    ["boost", "--prompt", "the fox starts running", "--type", "animate", "--image", "{png}"],
    ["boost", "--prompt", "calm lo-fi beat with soft piano", "--type", "music"],
]

# --type value -> the booster's `type` (v2 dot notation, as in the spec enum).
BOOST_TYPES = {
    "image": "images.generations", "edit": "images.edits", "video": "videos.generations",
    "animate": "videos.animations", "tts": "audio.speech", "music": "audio.music",
}
BOOST_ALIASES = {"speech": "tts", "img2img": "edit", "img2video": "animate",
                 "txt2img": "image", "txt2video": "video", "txt2audio": "tts",
                 "txt2music": "music"}
BOOST_NEEDS_IMAGE = ("images.edits", "videos.animations")
BOOST_MIN_CHARS = 3          # the API rejects shorter prompts with 422
BOOST_FALLBACKS = 3          # auto-selected target models tried before giving up
BOOST_PATH = "/api/v2/prompts/enhancements"

EMBED_PATH = "/api/v2/embeddings"
EMBED_MAX_ITEMS = 2048       # spec: "max 2048 items"
CHARS_PER_TOKEN = 4          # rough estimate, only used for warnings

# GET /api/v2/key fields that are safe to print. Never `preview` (part of the
# key), the key name, its id or the IP allow-list itself.
KEY_FIELDS = ("role", "status", "limit", "reset_interval", "limit_used", "limit_remaining",
              "limit_resets_at", "expires_at", "ip_whitelist_count")
USAGE_RETENTION_DAYS = 90
RATE_HEADERS = (
    ("limit", "X-RateLimit-Limit"), ("remaining", "X-RateLimit-Remaining"),
    ("reset", "X-RateLimit-Reset"), ("daily_limit", "X-RateLimit-Daily-Limit"),
    ("daily_remaining", "X-RateLimit-Daily-Remaining"),
    ("daily_reset", "X-RateLimit-Daily-Reset"), ("source", "X-RateLimit-Source"),
    ("policy", "X-RateLimit-Policy"),
)
EXPIRY_WARN_DAYS = 7

MODELS_EPILOG = """examples:
  deapi.py models                      all models this key may use
  deapi.py models --type tts --details  full cards: limits, features, voices, languages
  deapi.py models Kokoro               one model's full card
  deapi.py models --oai                model IDs of the OpenAI-compatible API"""

STATUS_EPILOG = """examples:
  deapi.py status 3f2a...-...            one look at the job
  deapi.py status 3f2a...-... --wait --download
  deapi.py status job:3f2a...-... --download --format webp"""

ACCOUNT_EPILOG = """The balance needs an ADMIN key; with an API-role key the key's own spend
limit (limit_remaining) is shown instead. Usage data is kept for 90 days.

examples:
  deapi.py account                     last 30 days
  deapi.py account --days 7
  deapi.py account --from 2026-01-01 --to 2026-02-01"""

EMBED_EPILOG = """examples:
  deapi.py embed "first text" "second text"
  deapi.py embed --input-file lines.txt            one text per non-empty line
  deapi.py embed --input-file notes.md --whole     the whole file as one text
The vectors are saved as JSON in --out-dir; stdout shows the file and the size."""

BOOST_EPILOG = """examples:
  deapi.py boost --prompt "a red fox" --type image
  deapi.py boost --prompt "make it night" --type edit --image photo.png
  deapi.py boost --prompt "lo-fi beat" --type music --model <slug>
--model is the model the prompt is written for (default: the one the matching
command would pick). Use the returned prompt with that model."""


def register(sub):
    p = core.add_command(sub, "models", "list models available to this key (free)", paid=False,
                         epilog=MODELS_EPILOG)
    p.add_argument("slug", nargs="?", metavar="SLUG", help="show this model's full card")
    p.add_argument("--type", help="task (image, edit, ocr, rmbg, upscale, video, animate, "
                                   "audio-video, video-upscale, describe, tts, music, "
                                   "transcribe, embed) or an inference type such as txt2img")
    p.add_argument("--details", "--json", dest="details", action="store_true",
                   help="full model cards: limits, features, defaults, languages, voices")
    p.add_argument("--oai", action="store_true",
                   help="list the models of the OpenAI-compatible API instead")
    p.set_defaults(func=cmd_models)

    p = core.add_command(sub, "status", "show a job, optionally wait and download (free)",
                         paid=False, epilog=STATUS_EPILOG)
    p.add_argument("request_id", metavar="ID", help="request_id or job:<request_id>")
    p.add_argument("--wait", action="store_true",
                   help="wait until the job finishes (up to --timeout)")
    p.add_argument("--download", action="store_true", help="save the result in --out-dir")
    p.add_argument("--format", choices=["jpg", "webp"],
                   help="download this image version (implies --download)")
    p.add_argument("--output", help="file name for the result (implies --download)")
    p.set_defaults(func=cmd_status)

    p = core.add_command(sub, "account", "key spend limit, balance and usage (free)",
                         paid=False, aliases=["balance"], epilog=ACCOUNT_EPILOG)
    p.add_argument("--days", type=int, default=None,
                   help="usage period in days, 1-%d (default 30)" % USAGE_RETENTION_DAYS)
    p.add_argument("--from", dest="date_from", metavar="DATE",
                   help="usage period start, UTC (YYYY-MM-DD or ISO 8601)")
    p.add_argument("--to", dest="date_to", metavar="DATE",
                   help="usage period end, UTC, exclusive (default: now)")
    p.set_defaults(func=cmd_account)

    p = core.add_command(sub, "doctor", "check Python, the key, the connection and limits (free)",
                         paid=False)
    p.set_defaults(func=cmd_doctor)

    p = core.add_command(sub, "embed", "text embeddings", epilog=EMBED_EPILOG)
    p.add_argument("texts", nargs="*", metavar="TEXT")
    p.add_argument("--input", action="append", default=[], help="text to embed (repeatable)")
    p.add_argument("--input-file", help="UTF-8 file, one text per non-empty line")
    p.add_argument("--whole", action="store_true",
                   help="with --input-file: embed the whole file as one text")
    p.set_defaults(func=cmd_embed)

    p = core.add_command(sub, "boost", "improve a generation prompt (prompt booster)",
                         epilog=BOOST_EPILOG)
    p.add_argument("--prompt", required=True)
    p.add_argument("--type", default="image",
                   help="target: image, edit, video, animate, tts, music "
                        "(or images.generations ...); default image")
    p.add_argument("--negative", help="negative prompt to improve as well")
    p.add_argument("--image", help="reference image (required for edit and animate)")
    p.set_defaults(func=cmd_boost)


# ------------------------------------------------------------ models ---

_TASK_OF_TYPE = {}
for _task, _types in core.TASK_TYPES.items():
    for _type in _types:
        _TASK_OF_TYPE.setdefault(_type, []).append(_task)


def _resolve_type(value):
    if not value:
        return None
    lowered = value.strip().lower()
    task = core.TASK_ALIASES.get(lowered, lowered)
    if task in core.TASK_TYPES:
        return core.TASK_TYPES[task]
    if not re.match(r"^[a-z0-9_-]{2,40}$", lowered):
        raise core.DeapiError("--type %r is not a task or an inference type." % value,
                              core.EXIT_USAGE, "Tasks: %s." % ", ".join(core.TASK_TYPES))
    return [lowered]


def _tasks(model):
    found = []
    for kind in model.get("inference_types") or []:
        for task in _TASK_OF_TYPE.get(kind, []):
            if task not in found:
                found.append(task)
    return found


def _summary(model):
    out = {"slug": model.get("slug"), "name": model.get("name"),
           "types": model.get("inference_types") or [], "commands": _tasks(model),
           "partner": core.is_partner(model), "tags": model.get("tags") or []}
    if out["partner"]:
        out["note"] = "partner model, priced as an estimate; never auto-selected"
    return out


def cmd_models(ctx, args):
    if args.oai:
        if args.type or args.slug:
            raise core.DeapiError("--oai lists the OpenAI-compatible API, which has no types; "
                                  "drop --type and SLUG.", core.EXIT_USAGE)
        data = ctx.request("GET", ctx.oai_url("/v1/models")).json()
        items = [m for m in (data.get("data") or []) if isinstance(m, dict) and m.get("id")]
        core.log("%d models (GET %s)" % (len(items), ctx.oai_url("/v1/models")))
        if args.details:
            return items
        return [m["id"] for m in items]
    models = ctx.models(_resolve_type(args.type))
    if args.slug:
        for model in models:
            if model.get("slug") == args.slug:
                return model
        raise core.DeapiError("Unknown model %r (not in GET /api/v2/models for this key%s)."
                              % (args.slug, ", type " + args.type if args.type else ""),
                              core.EXIT_VALIDATION,
                              "Run `deapi.py models` to see the slugs; never guess model IDs.")
    core.log("%d models (GET /api/v2/models)" % len(models))
    if args.details:
        return models
    return [_summary(m) for m in models]


# ------------------------------------------------------------ status ---

def cmd_status(ctx, args):
    request_id = core.check_request_id(args.request_id)
    try:
        job = ctx.wait_job(request_id) if args.wait else ctx.get_job(request_id)
    except core.DeapiError as exc:
        if exc.code == core.EXIT_ERROR and exc.message.startswith("Not found"):
            raise core.DeapiError(
                "No job %s for this API key's account." % request_id, core.EXIT_ERROR,
                "Check the request_id; jobs of another account are not visible.",
                request_id=request_id)
        raise
    status = str(job.get("status") or "").lower()
    price, estimated = core._job_price(job)
    out = {"request_id": request_id, "status": status, "progress": job.get("progress"),
           "price": price}
    if estimated:
        out["price_is_estimated"] = True
    if job.get("preview"):
        out["progress_preview"] = job.get("preview")
    for key in ("error_code", "error_reason", "retryable", "refunded", "failed_at"):
        if job.get(key) is not None:
            out[key] = job.get(key)
    if status in ("error", "failed"):
        raise core.job_failure(request_id, job)
    download = args.download or bool(args.format) or bool(args.output)
    if status != "done":
        out["hint"] = ("Still running. Wait with `deapi.py status %s --wait --download`; "
                       "do not submit the job again." % request_id)
        if download:
            core.warn("job %s is %s; nothing to download yet" % (request_id, status or "unknown"))
        return out
    out["has_file"] = bool(job.get("result_url"))
    alt = sorted(k for k, v in (job.get("results_alt_formats") or {}).items() if v)
    if alt:
        out["alt_formats"] = alt
    if job.get("prompt_boosted"):
        out["prompt_boost"] = job.get("prompt_boost")
    if download or job.get("result") not in (None, ""):
        out.update(ctx.deliver(job, request_id, None, "auto", args.output, args.format))
    elif out["has_file"]:
        out["hint"] = ("Add --download to save the result; the result link is signed and "
                       "expires, so do not share it as a permanent link.")
    if job.get("metadata"):
        out["metadata"] = job.get("metadata")
    return out


# ----------------------------------------------------------- account ---

def _key_info(data):
    return {k: data.get(k) for k in KEY_FIELDS if k in data}


def _get_key(ctx):
    resp = ctx.request("GET", ctx.url("/api/v2/key"))
    payload = resp.json()
    data = payload.get("data") if isinstance(payload, dict) else None
    return _key_info(data if isinstance(data, dict) else {}), resp


def _utc_now():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None)


def _parse_date(value, flag):
    text = (value or "").strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        if re.match(r"^\d{4}-\d{2}-\d{2}$", text):
            stamp = datetime.datetime.strptime(text, "%Y-%m-%d")
        else:
            stamp = datetime.datetime.fromisoformat(text)
    except ValueError:
        raise core.DeapiError("%s must be a date (YYYY-MM-DD) or an ISO 8601 time, got %r."
                              % (flag, value), core.EXIT_USAGE)
    if stamp.tzinfo is not None:
        stamp = stamp.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return stamp.replace(microsecond=0)


def _usage_period(args, now=None):
    now = now or _utc_now()
    if args.days is not None and (args.date_from or args.date_to):
        raise core.DeapiError("Use either --days or --from/--to.", core.EXIT_USAGE)
    if args.days is not None and not 1 <= args.days <= USAGE_RETENTION_DAYS:
        raise core.DeapiError("--days must be between 1 and %d (usage data is kept for %d "
                              "days); got %d." % (USAGE_RETENTION_DAYS, USAGE_RETENTION_DAYS,
                                                  args.days), core.EXIT_USAGE)
    end = _parse_date(args.date_to, "--to") if args.date_to else now
    if args.date_from:
        start = _parse_date(args.date_from, "--from")
    else:
        start = end - datetime.timedelta(days=args.days or 30)
    if start >= end:
        raise core.DeapiError("--from must be earlier than --to.", core.EXIT_USAGE)
    oldest = now - datetime.timedelta(days=USAGE_RETENTION_DAYS)
    if start < oldest:
        first_day = (oldest + datetime.timedelta(days=1)).date().isoformat()
        raise core.DeapiError("Usage data is kept for %d days; use --from %s or later."
                              % (USAGE_RETENTION_DAYS, first_day), core.EXIT_USAGE)
    if end > now:
        end = now
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return start.strftime(fmt), end.strftime(fmt)


def _usage(ctx, path, period):
    query = {"from": period[0], "to": period[1], "group_by": "type"}
    data = ctx.get_json(path, query).get("data") or {}
    groups = []
    for group in data.get("groups") or []:
        if isinstance(group, dict):
            groups.append({"type": group.get("key"), "cost": group.get("cost"),
                           "requests": group.get("requests"), "errors": group.get("errors")})
    return {"from": data.get("from") or period[0], "to": data.get("to") or period[1],
            "currency": data.get("currency"), "totals": data.get("totals"), "by_type": groups}


def cmd_account(ctx, args):
    period = _usage_period(args)
    key, _ = _get_key(ctx)
    out = {"key": key, "balance": None}
    if key.get("role") == "admin":
        balance = ctx.get_json("/api/v2/account/balance").get("data") or {}
        out["balance"] = balance.get("balance")
    else:
        out["note"] = ("The account balance needs an ADMIN key; this key's own spend limit "
                       "is shown instead (key.limit_remaining, USD).")
    try:
        out["key_usage"] = _usage(ctx, "/api/v2/key/usage", period)
    except core.DeapiError as exc:
        out["key_usage_error"] = exc.message
    if key.get("role") == "admin":
        try:
            usage = _usage(ctx, "/api/v2/account/usage", period)
            out["account_usage"] = {"totals": usage["totals"], "by_type": usage["by_type"]}
        except core.DeapiError as exc:
            out["account_usage_error"] = exc.message
    return out


# ------------------------------------------------------------ doctor ---

def _rate_limits(headers):
    found = {}
    for name, header in RATE_HEADERS:
        try:
            value = headers.get(header) if headers is not None else None
        except AttributeError:
            value = None
        if value is None:
            continue
        found[name] = int(value) if re.match(r"^\d+$", str(value)) else value
        if name in ("reset", "daily_reset") and isinstance(found[name], int) \
                and found[name] > 10 ** 9:
            found[name + "_at"] = datetime.datetime.fromtimestamp(
                found[name], datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return found or None


def _days_until(stamp):
    if not stamp:
        return None
    try:
        when = _parse_date(stamp, "expires_at")
    except core.DeapiError:
        return None
    return (when - _utc_now()).total_seconds() / 86400.0


def _key_warnings(key, max_cost):
    warnings = []
    if key.get("status") not in (None, "active"):
        warnings.append("The key status is %s; it cannot run jobs." % key.get("status"))
    if key.get("role") == "admin":
        warnings.append("This is an ADMIN key. For agents, use a dedicated API-role key with "
                        "a daily spend limit (Dashboard > Settings > API Keys).")
    if "limit" in key and key.get("limit") is None:
        warnings.append("This key has no spend limit; a daily limit protects against runaway "
                        "costs (Dashboard > Settings > API Keys).")
    elif key.get("limit") == 0:
        warnings.append("This key's spend limit is 0, so it cannot run paid jobs.")
    remaining = key.get("limit_remaining")
    if isinstance(remaining, (int, float)) and remaining < max_cost:
        warnings.append("Only %s USD of the key's spend limit is left (resets %s)."
                        % (core._fmt_usd(remaining), key.get("limit_resets_at") or "never"))
    days = _days_until(key.get("expires_at"))
    if days is not None and 0 <= days < EXPIRY_WARN_DAYS:
        warnings.append("The key expires on %s." % key.get("expires_at"))
    return warnings


def _key_source(ctx):
    return "--key-file" if ctx.key_file else core.KEY_ENV


def cmd_doctor(ctx, args):
    version = sys.version_info
    out = {"skill_version": VERSION, "python": platform.python_version(),
           "python_ok": version >= (3, 9), "os": platform.system() or None,
           "ffprobe": bool(shutil.which("ffprobe")), "base_url": ctx.base_url,
           "key_set": False, "max_cost": ctx.max_cost, "max_cost_source": ctx.max_cost_source,
           "warnings": [], "notes": []}
    if not out["python_ok"]:
        out["warnings"].append("Python 3.9 or newer is required.")
    if not out["ffprobe"]:
        out["notes"].append("ffprobe not found: durations of non-WAV local files are not "
                            "read, so transcription of such files is quoted with the upload.")
    try:
        ctx.api_key()
    except core.DeapiError as exc:
        # A diagnosis, not a failure: say what is missing and how to set it.
        out["warnings"].append("%s %s" % (exc.message, exc.hint or ""))
        out["ok"] = False
        return out
    out["key_set"] = True
    out["key_source"] = _key_source(ctx)

    started = time.perf_counter()
    try:
        key, resp = _get_key(ctx)
    except core.DeapiError as exc:
        out["api"] = "failed"
        raise core.DeapiError(exc.message, exc.code, exc.hint, **dict(exc.details, report=out))
    out["api"] = "ok"
    out["latency_ms"] = int((time.perf_counter() - started) * 1000)
    out["key"] = key
    out["warnings"].extend(_key_warnings(key, ctx.max_cost))
    limits = {"account_endpoints": _rate_limits(resp.headers)}

    try:
        resp = ctx.request("GET", ctx.url("/api/v2/models", {"limit": 1}))
        meta = resp.json().get("meta") or {}
        out["models_available"] = meta.get("total")
        limits["models_and_prices"] = _rate_limits(resp.headers)
    except core.DeapiError as exc:
        out["warnings"].append("GET /api/v2/models failed: %s" % exc.message)
    out["rate_limits"] = limits if any(limits.values()) else None
    if out["rate_limits"] is None:
        out["notes"].append("The API sent no X-RateLimit-* headers on these requests.")

    if key.get("role") == "admin":
        try:
            balance = ctx.get_json("/api/v2/account/balance").get("data") or {}
            out["balance"] = balance.get("balance")
        except core.DeapiError as exc:
            out["warnings"].append("GET /api/v2/account/balance failed: %s" % exc.message)
    else:
        out["balance"] = None
        out["notes"].append("The account balance needs an ADMIN key; key.limit_remaining is "
                            "this key's own budget.")

    try:
        data = ctx.request("GET", ctx.oai_url("/v1/models")).json()
        out["oai"] = {"base_url": ctx.oai_url("/v1"), "status": "ok",
                      "models": len(data.get("data") or [])}
    except core.DeapiError as exc:
        out["oai"] = {"base_url": ctx.oai_url("/v1"), "status": "failed"}
        out["warnings"].append("OpenAI-compatible API check failed: %s" % exc.message)
    out["ok"] = not out["warnings"]
    return out


# -------------------------------------------------------------- embed ---

def _read_texts(ctx, args):
    texts = list(args.texts or []) + list(args.input or [])
    if args.whole and not args.input_file:
        raise core.DeapiError("--whole needs --input-file.", core.EXIT_USAGE)
    if args.input_file:
        content = core.read_text_file(args.input_file, "--input-file", ctx.allow_hidden_path)
        if args.whole:
            if content.strip():
                texts.append(content.strip())
        else:
            texts.extend(line.strip() for line in content.splitlines() if line.strip())
    return texts


def _vector_stats(value):
    """(count, dimensions) of the embeddings in a parsed result, else None."""
    def is_vector(item):
        return isinstance(item, list) and item and all(
            isinstance(x, (int, float)) and not isinstance(x, bool) for x in item)
    if isinstance(value, dict):
        for key in ("data", "embeddings", "embedding", "result", "vectors"):
            if key in value:
                return _vector_stats(value[key])
        return None
    if is_vector(value):
        return 1, len(value)
    if isinstance(value, list) and value:
        items = [v.get("embedding") if isinstance(v, dict) else v for v in value]
        if all(is_vector(v) for v in items):
            return len(items), len(items[0])
    return None


def _summarize_vectors(out):
    path = out.get("text_file") or (out.get("files") or [None])[0]
    if not path or not os.path.isfile(path):
        return out
    try:
        with open(path, "r", encoding="utf-8") as handle:
            parsed = json.load(handle)
    except (OSError, ValueError, UnicodeDecodeError):
        return out
    stats = _vector_stats(parsed)
    if stats:
        out["vectors"], out["dimensions"] = stats
        out["preview"] = "%d vector(s) of %d numbers in %s" % (stats[0], stats[1],
                                                               os.path.basename(path))
    return out


def cmd_embed(ctx, args):
    texts = _read_texts(ctx, args)
    if not texts:
        raise core.DeapiError("Give at least one text (TEXT, --input or --input-file).",
                              core.EXIT_USAGE)
    if any(not t.strip() for t in texts):
        raise core.DeapiError("Empty texts cannot be embedded.", core.EXIT_USAGE)
    if len(texts) > EMBED_MAX_ITEMS:
        raise core.DeapiError("%d texts given; the API takes at most %d per request. Split "
                              "the input into several runs." % (len(texts), EMBED_MAX_ITEMS),
                              core.EXIT_USAGE)
    model = ctx.pick_model("embed", args.model, args.allow_partner_models)
    per_item = core.limit(model, "max_input_tokens")
    total = core.limit(model, "max_total_tokens")
    longest = max(len(t) for t in texts)
    chars = sum(len(t) for t in texts)
    if per_item and longest > per_item * CHARS_PER_TOKEN:
        core.warn("the longest text has %d characters, likely more than the model's %s "
                  "tokens per text; split it into smaller parts." % (longest, per_item))
    if total and chars > total * CHARS_PER_TOKEN:
        core.warn("the texts have %d characters, likely more than the model's %s tokens per "
                  "request; split them into several runs." % (chars, total))
    body = {"input": texts[0] if len(texts) == 1 else texts, "model": model["slug"],
            "return_result_in_response": True}
    job = core.Job("embed", EMBED_PATH, model, json=body,
                   quote_json=core.pick(body, "input", "model"), seed_field=None,
                   text_ext="json", output=args.output,
                   info={"inputs": len(texts), "chars": chars})
    out = ctx.run(job, args)
    if out.get("quote_only"):
        # Do not echo every text back; the agent already has them.
        out["params"] = {"model": model["slug"], "input": "%d text(s), %d characters"
                         % (len(texts), chars)}
        return out
    return _summarize_vectors(out)


# -------------------------------------------------------------- boost ---

def _boost_type(value):
    raw = (value or "image").strip()
    kind = BOOST_ALIASES.get(raw.lower(), raw.lower())
    if kind in BOOST_TYPES:
        return kind, BOOST_TYPES[kind]
    for task, dotted in BOOST_TYPES.items():
        if raw == dotted:
            return task, dotted
    raise core.DeapiError("--type must be one of: %s (got %r)." % (", ".join(BOOST_TYPES), value),
                          core.EXIT_USAGE)


def _boost_candidates(ctx, task, args):
    """Target models to try: the explicit --model, else the automatic pick first."""
    first = ctx.pick_model(task, args.model, args.allow_partner_models)
    if args.model:
        return [first]
    pool = [m for m in ctx.models(core.TASK_TYPES[task])
            if m.get("slug") != first.get("slug")
            and (args.allow_partner_models or not core.is_partner(m))]
    return [first] + pool[:BOOST_FALLBACKS - 1]


def _model_refused(exc):
    text = " ".join([exc.message] + list(exc.details.get("errors") or [])).lower()
    return exc.code == core.EXIT_VALIDATION and any(
        word in text for word in ("model_slug", "guide", "model '"))


def cmd_boost(ctx, args):
    task, boost_type = _boost_type(args.type)
    if len(args.prompt.strip()) < BOOST_MIN_CHARS:
        raise core.DeapiError("--prompt must have at least %d characters." % BOOST_MIN_CHARS,
                              core.EXIT_USAGE)
    if args.negative is not None and len(args.negative.strip()) < BOOST_MIN_CHARS:
        raise core.DeapiError("--negative must have at least %d characters."
                              % BOOST_MIN_CHARS, core.EXIT_USAGE)
    files = []
    if boost_type in BOOST_NEEDS_IMAGE:
        if not args.image:
            raise core.DeapiError("--image is required for --type %s." % task, core.EXIT_USAGE)
        files.append(core.resolve_input(ctx, args.image, "image", "image"))
    elif args.image:
        raise core.DeapiError("--image is used only with --type edit or animate.",
                              core.EXIT_USAGE)

    first_error, target, quote = None, None, None
    for model in _boost_candidates(ctx, task, args):
        fields = {"prompt": args.prompt, "type": boost_type, "model_slug": model["slug"]}
        if args.negative:
            fields["negative_prompt"] = args.negative
        try:
            # The quote also checks that a guide exists for this model and type.
            # It does not need the image, so nothing is uploaded twice.
            quote = ctx.quote(BOOST_PATH, fields=fields)
            target = model
            break
        except core.DeapiError as exc:
            if args.model or not _model_refused(exc):
                raise
            first_error = first_error or exc
            core.log("no prompt guide for %s (%s); trying another model"
                     % (model["slug"], exc.message))
    if target is None:
        raise first_error

    summary = {"command": "boost", "model": target["slug"], "type": boost_type,
               "quoted_price": quote.price, "is_estimated": quote.is_estimated}
    log_price = "unknown" if quote.price is None else core._fmt_usd(quote.price)
    core.log("quote: %s USD" % log_price)
    if args.quote:
        params = dict(fields)
        for part in files:
            params[part.field] = part.public()
        summary.update({"quote_only": True, "max_cost": ctx.max_cost,
                        "within_max_cost": quote.price is not None
                        and quote.price <= ctx.max_cost, "params": params})
        return summary
    ctx.enforce_budget(quote.price, quote, "boost")
    payload = ctx.post_multipart(BOOST_PATH, fields, files, mode="submit")
    node = payload.get("data") if isinstance(payload, dict) and \
        isinstance(payload.get("data"), dict) else payload
    if not isinstance(node, dict) or not node.get("prompt"):
        raise core.DeapiError("The prompt booster returned no prompt.", core.EXIT_ERROR)
    result = {"prompt": node.get("prompt")}
    if args.negative or node.get("negative_prompt"):
        result["negative_prompt"] = node.get("negative_prompt")
    summary.update({"status": "done", "original_prompt": args.prompt, "result": result})
    summary["hint"] = ("Use result.prompt with --model %s (the booster's fee is the quoted "
                       "price)." % target["slug"])
    return summary
