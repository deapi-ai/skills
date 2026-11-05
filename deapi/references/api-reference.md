# deAPI API v2 behind the script

What the script calls, for debugging and for explaining a result. To write application code
against deAPI, use the `deapi-api` skill. Full reference: https://docs.deapi.ai (OpenAPI:
https://docs.deapi.ai/openapi.json).

## Basics

- Base URL `https://api.deapi.ai/api/v2`. Headers: `Authorization: Bearer <key>`,
  `Accept: application/json`, a `User-Agent`.
- Keys look like `sk-dp-<id>-<secret>`; use them as they are. Older keys written as
  `<id>|<secret>` still work.
- This is the current native API. The old API v1 is deprecated; skills built on it are
  outdated. A retired endpoint answers 410 with a successor (exit 13).

## Job pattern

1. `POST <endpoint>/price` with the job's parameters: a free quote,
   `{"data": {"price": 0.0123, "is_estimated": false}}` (the prompt booster answers
   `{"price": ...}` without `data`). `estimate_basis: unavailable` means the price is not
   known; the script treats it as above the limit. Quotes validate parameters like a real job.
2. `POST <endpoint>` (JSON or multipart) → `{"data": {"request_id": "..."}}`. The prompt
   booster answers at once, without a job.
3. `GET /jobs/{request_id}` until `status` is `done` or `error`: `progress`, `preview`,
   `price` (`{"amount", "is_estimated"}`), and `result_url` (file), `result` (text, when
   `return_result_in_response` is set), `results_alt_formats` (for example JPG and WebP
   versions). On error: `error_code`, `error_reason`, `retryable`, `refunded`.
4. Download `result_url` at once, without the key: it is a pre-signed link that expires.

The native API has no idempotency key: a job POST that got a 5xx or lost its connection may
still have created a paid job. The script exits with code 9 instead of resubmitting.

## Endpoints by command

| Command | Job endpoint | Quote sent as |
|---|---|---|
| `image` | `POST /images/generations` (JSON) | JSON without the prompt booster flag |
| `edit` | `POST /images/edits` (multipart, `image` or `images[]`) | JSON, no images |
| `ocr` | `POST /images/ocr` (multipart) | `model`, `width`, `height` (+ `language`, `format`) |
| `rmbg` | `POST /images/background-removals` | `model`, `width`, `height` |
| `upscale` | `POST /images/upscales` | `model`, `width`, `height` |
| `video` | `POST /videos/generations` (JSON) | `model`, size, `steps`, `frames`, `fps` |
| `animate` | `POST /videos/animations` (multipart: `first_frame_image`, `last_frame_image`, `ref_images[]`, `ref_videos[]`, `ref_audios[]`) | JSON with size, frames, fps and reference counts |
| `audio-video` | `POST /videos/audio-syncs` (multipart) | JSON like a video quote |
| `video-upscale` | `POST /videos/upscales` (multipart `video`) | `width`, `height`, `duration` from the file header, or the file |
| `describe` | `POST /videos/descriptions` (`video_url` or `video_file`) | `video_url`, or `duration_seconds` (+ `platform` for TikTok) |
| `tts` | `POST /audio/speech` (multipart; `ref_audio` for a clone) | JSON without the recording |
| `music` | `POST /audio/music` (multipart) | JSON without seed and format |
| `transcribe` | `POST /audio/transcriptions` (`source_url` or `source_file`) | `source_url`, or `duration_seconds` (+ `platform` for TikTok) |
| `embed` | `POST /embeddings` (JSON) | `input`, `model` |
| `boost` | `POST /prompts/enhancements` (multipart: `prompt`, `type`, `model_slug`, `image`) | same fields, no image |

Free calls: `GET /models` (`filter[inference_types]=...`), `GET /jobs/{id}`, `GET /key`,
`GET /key/usage`, and with an ADMIN key `GET /account/balance` and `GET /account/usage`.
`models --oai` reads `GET https://oai.deapi.ai/v1/models`.

## Errors

The script maps the `code` field of an error first and the HTTP status only when `code` is
missing; the exit codes are in `references/commands.md`. Main cases:

| HTTP | `code` | Meaning |
|---|---|---|
| 401 | `missing_key`, `invalid_key`, `key_expired`, `key_revoked` | Key problem (exit 3) |
| 402 | `key_limit_exceeded` | The key's spend limit is used up until `limit_resets_at` (exit 12) |
| 403 | `insufficient_permissions`, `ip_not_allowed` | Role or IP (exit 4); without `code`: account suspended (exit 11) |
| 410 | | Endpoint retired; the answer names a successor (exit 13) |
| 422 | `insufficient_balance` | Top up (exit 6) |
| 422 | validation | A parameter outside the model's limits, unknown model, or a model outside the plan (exit 7) |
| 429 | | Rate limit; `X-RateLimit-Type: daily` means the daily quota (exit 10) |

Details: https://docs.deapi.ai/api/v2/errors.md. Limits: https://docs.deapi.ai/limits-and-quotas.md.

## Upload limits

Image 10 MB, audio 20 MB, video 50 MB, voice-clone and music reference 15 MB, a whole request
75 MB. Public sources differ for a few inputs; where they do, the script warns instead of
refusing.
