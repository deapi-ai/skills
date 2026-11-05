# Native API v2

Base URL `https://api.deapi.ai`, every path below starts with `/api/v2`. Authentication:
`Authorization: Bearer <key>` with the key as is (`sk-dp-<id>-<secret>`; older `<id>|<secret>`
keys keep working). Send `Accept: application/json` and your own `User-Agent`; some networks
block default library user agents.

Spec: https://docs.deapi.ai/openapi.json. Reference: https://docs.deapi.ai/api/v2/overview.md.

## Endpoints

Every job endpoint has a free quote at the same path plus `/price`.

| Task | Job endpoint | Body |
|---|---|---|
| Text to image | `POST /api/v2/images/generations` | JSON |
| Image edit (one `image` or several `images[]`) | `POST /api/v2/images/edits` | multipart |
| OCR | `POST /api/v2/images/ocr` | multipart |
| Background removal | `POST /api/v2/images/background-removals` | multipart |
| Image upscale | `POST /api/v2/images/upscales` | multipart |
| Text to video | `POST /api/v2/videos/generations` | JSON |
| Image to video, video from references | `POST /api/v2/videos/animations` | multipart |
| Audio to video | `POST /api/v2/videos/audio-syncs` | multipart |
| Video upscale | `POST /api/v2/videos/upscales` | multipart |
| Video description (what happens on screen) | `POST /api/v2/videos/descriptions` | multipart |
| Text to speech, voice clone, voice design | `POST /api/v2/audio/speech` | multipart |
| Music | `POST /api/v2/audio/music` | multipart |
| Transcription of a file or a link | `POST /api/v2/audio/transcriptions` | multipart |
| Embeddings | `POST /api/v2/embeddings` | JSON |
| Prompt booster (synchronous) | `POST /api/v2/prompts/enhancements` | multipart |

Other routes:

| Route | Key role | Returns |
|---|---|---|
| `GET /api/v2/jobs/{request_id}` | any | job status and result |
| `GET /api/v2/models` | any | models this account may use, with limits |
| `GET /api/v2/key`, `GET /api/v2/key/usage` | any | the calling key, its usage |
| `GET /api/v2/account/balance`, `GET /api/v2/account`, `/account/usage`, `/account/payments` | ADMIN | billing |
| `GET/POST /api/v2/keys`, `GET/PATCH/DELETE /api/v2/keys/{id}`, `GET /api/v2/keys/{id}/usage` | ADMIN | key management |

Exact fields per endpoint are in the spec; the per-endpoint pages under
`https://docs.deapi.ai/api/v2/` explain them. Documentation page URLs (for example
`/api/v2/utilities/models`) are not API routes and answer 404.

## Model catalog

`GET /api/v2/models?filter[inference_types]=txt2img&limit=100` returns the models this account
may use for that task (`meta.last_page` tells you whether to fetch more pages). Types:
`txt2img`, `img2img`, `img2txt`, `img-rmbg`, `img-upscale`, `txt2video`, `img2video`,
`audio2video`, `vid-upscale`, `video2desc`, `video_file2desc`, `txt2audio`, `txt2music`,
`audio2text`, `audio_file2text`, `video2text`, `video_file2text`, `txt2embedding`.

Build each request from the model card:
- `info.limits` - ranges (`min_width`, `max_steps`, `max_text`, `max_input_images`, …). Sizes
  off the model's grid (`dimension_multiple`, `resolution_step`) are rounded down silently.
- `info.features` - `supports_steps`, `supports_guidance`, `supports_negative_prompt`, … A field
  the model does not support is dropped or rejected; send it only when the flag is true.
- `info.defaults` - working defaults (size, steps, fps).
- `languages`, voices and LoRAs where the model has them.

Models on a higher plan than the account are missing from the list and fail with 422.
`status: test_model` marks partner models served by an external provider: quotes are
estimates and the final charge settles later.

## The job pattern

1. **Quote** (free): `POST <endpoint>/price` with the job's parameters. Price bodies take
   fewer fields than jobs (the `*PriceRequestBody` schemas): no files where dimensions or a
   duration are enough, for example `duration_seconds` for transcription of a local file.
   The content type can differ too: an image edit is multipart, its quote is JSON. Check the
   price operation in the spec.
2. **Submit**: `POST <endpoint>` → `{"data": {"request_id": "<uuid>"}}`. Store the ID.
3. **Get the result** by webhook (`references/webhooks.md`), WebSocket
   (`references/websockets.md`) or polling `GET /api/v2/jobs/{request_id}`.
4. **Download** `result_url` (no `Authorization` header) or read `result` for text.

Quote response:

```json
{"data": {"price": 0.0021, "is_estimated": false, "estimate_basis": null}}
```

- `price` is in the balance's unit (USD). Compare it with your ceiling before submitting.
- `is_estimated: true` (partner models): an estimate; `estimate_basis: "unavailable"` means
  no cost data and `price: 0` must not be trusted.
- The prompt booster quote answers `{"price": …}` without `data`.
- Quotes have their own rate-limit pool and stay available on a suspended account.

### Job status

`GET /api/v2/jobs/{request_id}` → `data`:

| Field | Meaning |
|---|---|
| `status` | `pending` → `processing` → `done` or `error` |
| `progress` | 0-100 |
| `result_url` | pre-signed, expiring link to the file (image, audio, video, JSON document); only when `done` |
| `results_alt_formats` | `{"jpg": …, "webp": …}` for images |
| `result` | text, when the job was sent with `return_result_in_response: true` (OCR, embeddings, transcription, video description; a description arrives as a JSON string to parse) |
| `metadata` | source metadata, only with `include_metadata: true` |
| `price` | `{"amount", "is_estimated"}` once terminal; `null` while running; 0 after a refund |
| `error_code`, `error_reason`, `retryable`, `refunded`, `failed_at` | only on `error` |
| `data.prompt_boosted`, `data.prompt_boost` | inline prompt booster (`enhance_prompt: true`): whether it ran, boosted and original prompts |
| `preview` | in-progress preview, opaque string, cached for one hour |

Poll every 2 s at first and back off to about 10 s; long video and audio jobs take minutes.
Each poll is a request and counts against the rate limit.

On `error`, branch on `retryable` (not on `error_reason`, which is an open list such as
`SOURCE_PRIVATE`, `SOURCE_RESTRICTED`, `SOURCE_DOWNLOAD_FAILED`, `INPUT_TOO_LARGE`, `TIMEOUT`,
`CAPACITY_UNAVAILABLE`, `PROCESSING_FAILED`). `refunded: false` can also mean the job was never
charged. The job status is authoritative for the final charge.

### Results

- `result_url` is a pre-signed link on a deAPI results host. Download it without your
  `Authorization` header (the key is not needed and must not travel to other hosts).
- Links expire. Download promptly, store the file yourself, and re-read
  `GET /api/v2/jobs/{request_id}` for a fresh link.
- A job result is at most 100 MB.

## Example: one job with the standard library

The full version with a model card and a cost ceiling is `assets/native_job.py`
(TypeScript: `assets/native_job.ts`, curl: `assets/native_job.sh`).

```python
import json
import os
import time
import urllib.request

API = "https://api.deapi.ai"
KEY = os.environ["DEAPI_API_KEY"]
MAX_COST = 0.25  # USD; your application's ceiling


def call(method, path, body=None):
    req = urllib.request.Request(
        API + path, method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + KEY, "Accept": "application/json",
                 "Content-Type": "application/json", "User-Agent": "my-app/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


body = {"prompt": "a red fox in the snow", "model": "Flux1schnell",
        "width": 512, "height": 512, "steps": 4, "seed": 42}
quote = call("POST", "/api/v2/images/generations/price", body)["data"]
if quote["price"] > MAX_COST:
    raise SystemExit("too expensive: %s USD" % quote["price"])

request_id = call("POST", "/api/v2/images/generations", body)["data"]["request_id"]
delay = 2
while True:
    job = call("GET", "/api/v2/jobs/" + request_id)["data"]
    if job["status"] in ("done", "error"):
        break
    time.sleep(delay)
    delay = min(delay * 2, 10)

if job["status"] == "done":
    # Pre-signed link: no Authorization header.
    download = urllib.request.Request(job["result_url"], headers={"User-Agent": "my-app/1.0"})
    with urllib.request.urlopen(download, timeout=300) as resp, open(request_id + ".png", "wb") as out:
        out.write(resp.read())
    print("saved", request_id + ".png", "cost", job["price"])
else:
    print("failed:", job.get("error_reason"), "retryable:", job.get("retryable"))
```

The model, size and steps above are an example; read them from the model card.

## Errors

The body always has `message`. A 422 adds `errors`, an object of field name → list of messages
(`errors.model[0]`, not `errors[0]`). Many refusals also carry a stable `code`. Branch on status
and `code`; the wording of `message` can change, and unknown codes may appear (treat them as the
status alone).

| HTTP | `code` | Meaning | What the app should do |
|---|---|---|---|
| 401 | `missing_key`, `invalid_key`, `key_expired`, `key_revoked` | key absent, unknown, expired or disabled | fix the key; do not retry |
| 402 | `key_limit_exceeded` | this key's own spend limit is used up; body has `limit`, `used`, `remaining`, `resets_at` | wait until `resets_at`, or the owner raises the limit |
| 403 | `insufficient_permissions` | an API-role key called a billing or key endpoint | use an ADMIN key for that call only |
| 403 | `ip_not_allowed` | the caller's address is not on the key's allowlist | call from an allowed address |
| 403 | (none) | account suspended; quotes still work | contact support |
| 404 | (none) | unknown job ID or route | check the path and ID |
| 410 | (none) | endpoint removed; body names `successor` and `documentation` | migrate |
| 413 | (none) | request body over 75 MB | smaller file, or a URL source where supported |
| 422 | `insufficient_balance` | not enough credit; the request itself is fine | top up, then send the same request |
| 422 | `active_key_limit_reached` | too many active keys (key creation) | disable unused keys |
| 422 | (none) | validation: see `errors`; also unknown model or model not on the plan (`errors.model`) | fix the fields from the model card |
| 429 | (none) or `rate_limited` | rate limit; see `X-RateLimit-Type` | below |
| 5xx | (none) | server error | GET: retry with backoff. Job POST: see below |

**Job POST after a 5xx or a dropped connection:** the job may exist and be billed. Retrying
can run it twice. Surface the error, or retry once only where a duplicate is acceptable. The
native API has no idempotency key (the OpenAI-compatible API has one).

A job that fails after it was accepted is not an HTTP error: the submit returns 200 and the
job status reports `error`.

## Rate limits and headers

Limits are per model, endpoint and account tier, in requests per minute (RPM) and per day
(RPD). Basic accounts (no payment yet) have low limits; any top-up moves the account to Premium.
Tables: https://docs.deapi.ai/limits-and-quotas.md.

Rate-limited responses (successes included) carry:

| Header | Meaning |
|---|---|
| `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset` | minute window (epoch second for the reset) |
| `X-RateLimit-Daily-Limit`, `-Daily-Remaining`, `-Daily-Reset` | day window, when one applies |
| `X-RateLimit-Policy` | every window, e.g. `3;w=60, 100;w=86400`; one element = no daily limit |
| `X-RateLimit-Source` | `tier`, `model` (per-model pool), `static`, `fallback` |
| `X-RateLimit-Type` (429 only) | which bucket refused: `minute`, `daily`, `key-creation`, `ip-not-allowed` |
| `Retry-After` (429 only) | seconds until that bucket frees up (up to 60, 3600 or 86400) |

- On 429 read `X-RateLimit-Type` first. `minute`: wait `Retry-After` and retry. `daily`: do not
  spin; schedule the work after `X-RateLimit-Daily-Reset`.
- Windows start at your first request into a bucket, not at a calendar boundary.
- API v1 and v2 share one pool; all quote endpoints share a separate pool; the prompt booster
  has its own.
- Requests in parallel can briefly exceed the count; `Remaining` is a snapshot, not a reservation.
- Compare headers only between responses with the same `X-RateLimit-Source`.

## Upload limits

| What | Limit |
|---|---|
| Whole request | 75 MB (413 above) |
| Video | 50 MB; MP4, MPEG, MOV, AVI, WMV, OGG |
| Audio | 20 MB |
| Voice-clone and music reference audio | 15 MB |
| Image | 10 MB, 64-5000 px per side |
| Links (YouTube, X, Twitch, Kick, TikTok, X Spaces) | by duration; up to 600 minutes for X Spaces, X, Kick and Twitch |

Model limits (characters, frames, durations) come from `info.limits`. For long media prefer a
link source where the endpoint accepts one (`source_url` in transcription, `video_url` in video
description).

## Python SDK

`deapi-ai/deapi-python-sdk` wraps every endpoint, quotes (`*_price` methods), polling
(`job.wait()`), retries, webhook verification and WebSockets (`client.realtime()`). It needs
Python 3.11+ and is not on PyPI; install a pinned tag:

```bash
pip install "git+https://github.com/deapi-ai/deapi-python-sdk.git@v0.5.2"
```

`pip install deapi` installs an unrelated package. The import name is `deapi_sdk`; check the
README of the tag you install for the current interface.
