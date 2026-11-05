# OpenAI-compatible API (`oai.deapi.ai`)

Point an official OpenAI SDK (or any tool with a base URL setting) at
`https://oai.deapi.ai/v1` and call deAPI models with OpenAI request and response shapes.
Docs: https://docs.deapi.ai/openai-compatibility.md.

- API key: the deAPI key as is (`sk-dp-<id>-<secret>`), in `Authorization: Bearer`.
- Model IDs: **native only** (`Flux1schnell`, `Kokoro`, `WhisperLargeV3Ct2`, …). There are no
  `dall-e-3`, `tts-1` or `whisper-1` aliases; those names answer 404 `model_not_found`.
- The model list depends on the account's plan: build on `GET /v1/models`.
- Keys with an IP allowlist do not work here; use a key without one, or the native API.

## Setup

Python (`pip install openai`):

```python
import os

from openai import OpenAI

deapi = OpenAI(
    api_key=os.environ.get("DEAPI_API_KEY", ""),  # never None: no fallback to OPENAI_API_KEY
    base_url="https://oai.deapi.ai/v1",
    timeout=900.0,   # jobs hold the connection until they finish (up to ~10 minutes)
)
print([m.id for m in deapi.models.list().data])
```

Node.js / TypeScript (`npm install openai`):

```typescript
import OpenAI from "openai";

const deapi = new OpenAI({
  apiKey: process.env.DEAPI_API_KEY,
  baseURL: "https://oai.deapi.ai/v1",
  timeout: 900_000, // milliseconds
});
```

- If the app also calls OpenAI, keep two clients. Do not set `OPENAI_BASE_URL` globally, or the
  OpenAI calls go to deAPI.
- Keep the key on the server. CORS is open and `dangerouslyAllowBrowser: true` works, but a key in
  browser code is readable by every visitor and it is a billing credential. Proxy through your
  backend.

Complete runnable examples: `assets/openai_examples.py` and `assets/openai_examples.ts`.

## Endpoints

| Endpoint | Mode | Notes |
|---|---|---|
| `GET /v1/models`, `GET /v1/models/{model}` | sync | `{id, object, created, owned_by}`; cached 5 minutes |
| `POST /v1/embeddings` | sync | `Bge_M3_FP16`, 1024 dimensions |
| `POST /v1/images/generations` | holds the connection | text to image |
| `POST /v1/images/edits` | holds the connection | multipart or JSON with `images: [{"image_url": …}]` |
| `POST /v1/audio/speech` | holds the connection | returns MP3 bytes |
| `POST /v1/audio/transcriptions` | holds the connection | file upload only |
| `POST /v1/videos`, `GET /v1/videos/{id}`, `GET /v1/videos/{id}/content` | asynchronous | create, poll, download |

Each response has `X-Request-Id` (quote it to support) and job endpoints add
`X-DeAPI-Request-Id` (the native job ID, comma-separated when a request fans out).

### Embeddings

```python
r = deapi.embeddings.create(model="Bge_M3_FP16", input=["first text", "second text"])
vectors = [d.embedding for d in r.data]
```

- `input`: string or array, at most 100 items (400 `input_too_large`). All or nothing.
- **Each array item counts as one request against the rate limit.** Size batches to the plan.
- `dimensions` is accepted and ignored (always 1024); `encoding_format` `float` or `base64`.
- `usage.prompt_tokens` is always 0.

### Image generation

```python
import base64

img = deapi.images.generate(model="Flux1schnell", prompt="an isometric city at dusk",
                            size="1024x1024", n=1, response_format="b64_json")
with open("city.png", "wb") as f:
    f.write(base64.b64decode(img.data[0].b64_json))
```

- `size` is checked against each model's list (for example `Flux1schnell`: 512x512, 768x768,
  1024x1024, 1024x1536, 1536x1024); `n` up to 4 (2 on edits).
- `quality` (`low`, `medium`, `high`, `standard`, `hd`, `auto`) maps to a per-model step preset.
- Output is always PNG without alpha. Rejected: `background="transparent"`,
  `output_format` other than `png`, `output_compression`, `stream`, `partial_images`.
- `response_format="url"` gives a pre-signed link valid about 5 hours and never re-signed:
  download it at once, or use `b64_json`.
- Edits: `QwenImageEdit_Plus_NF4` (1 image) and `Flux_2_Klein_4B_BF16` (up to 3). `mask` and
  `input_fidelity` are rejected (no inpainting). JSON `image_url` takes public URLs (fetched by
  the gateway, 10 MB) or `data:` URLs; `file_id` is rejected.

### Text to speech

```python
speech = deapi.audio.speech.create(model="Kokoro", voice="af_nova",
                                   input="Hello from deAPI.", response_format="mp3")
speech.write_to_file("hello.mp3")
```

- MP3 only. Errors come back as JSON, so check the status before saving bytes.
- `voice` is checked against the model's list. OpenAI names (`alloy`, `echo`, `fable`, `onyx`,
  `nova`, `shimmer`) map to **English** Kokoro voices.
- There is no language detection. For other languages pick a voice of that language (Kokoro
  prefixes: `ef_`/`em_` Spanish, `ff_` French, `hf_`/`hm_` Hindi, `if_`/`im_` Italian,
  `pf_`/`pm_` Brazilian Portuguese, `bf_`/`bm_` British English) or send `language` (a deAPI
  extension; Python: `extra_body={"language": "…"}`) with a model that lists that language in
  the native catalog. Languages Kokoro lacks need another TTS model from `GET /v1/models`.
- `speed` works on Kokoro (0.5-2.0); other models accept only 1.0.
- Voice design: `Qwen3_TTS_12Hz_1_7B_VoiceDesign` with `instructions="a calm, low male voice"`
  (required there, rejected elsewhere). Voice cloning is native API only.

### Transcription

```python
with open("meeting.mp3", "rb") as f:
    t = deapi.audio.transcriptions.create(model="WhisperLargeV3Ct2", file=f,
                                          response_format="verbose_json",
                                          timestamp_granularities=["word"], language="en")
print(t.text)
```

- `WhisperLargeV3Ct2`: `json`, `text`, `verbose_json`, `diarized_json` (speakers `A`, `B`, …
  stable only within one response), word timestamps. `WhisperLargeV3`: segments only; asking it
  for words is 400 `timestamp_level_not_supported`.
- `srt`/`vtt` are rejected; build subtitles from `verbose_json` segments.
- `language` is passed to the model. `prompt`, `temperature` and `keywords[]` are accepted but
  have no effect. `stream`, `include[]`, `languages[]`, `chunking_strategy`, speaker references
  are rejected.
- Files: audio up to 20 MB, video up to 50 MB. Links (YouTube, X, TikTok, …) need the native API.
- Silence or music gives an empty text, not an error.

### Video

```python
import time
import uuid

job = deapi.videos.create(model="Ltx2_3_22B_Dist_INT8", prompt="a paper boat in the rain",
                          seconds="5", size="768x768",
                          extra_headers={"Idempotency-Key": str(uuid.uuid4())})
while job.status not in ("completed", "failed"):
    time.sleep(5)
    job = deapi.videos.retrieve(job.id)
if job.status == "completed":
    deapi.videos.download_content(job.id).write_to_file("boat.mp4")
```

- `seconds` and `size` come from each model's own list and differ from OpenAI's enums: typed
  clients need a cast (`as any` in TypeScript).
- `input_reference: {"image_url": …}` gives image-to-video (first frame).
- A failed job is `status: "failed"` with an `error` object and HTTP 200: branch on `status`.
- `/content` before completion: 409 `video_not_ready`; after a failure: 502 `video_failed`.
  The link behind `/content` is resolved fresh on each call for about 24 hours.
- `progress` is coarse (0, 50, 100).
- `videos` needs an SDK release that has `client.videos`; older ones (Python 1.x, Node 4.x)
  do not. The examples are tested with `openai` 3.46.0 (Python) and 7.51.0 (Node).
- Recent OpenAI SDKs print a deprecation warning on `videos` calls. It concerns OpenAI's own
  video service, not deAPI. If an SDK version drops `videos`, call the three routes directly
  (`client.post("/videos", ...)` or plain HTTP).

## Errors

OpenAI envelope: `{"error": {"message", "type", "param"?, "code"?}}`. Branch on `code`, and on
`type` plus status when `code` is absent.

| HTTP | `code` / `type` | Meaning | Retry |
|---|---|---|---|
| 400 | `param_not_supported`, `size_not_supported`, `invalid_value`, `model_wrong_endpoint`, … | request problem; `param` names your field | no, fix it |
| 401 | `missing_api_key`, `invalid_api_key` | key | no |
| 404 | `model_not_found` | unknown, aliased, partner or out-of-plan model | no |
| 404 | `endpoint_not_implemented`, `endpoint_not_found` | path not served | no |
| 422 | `idempotency_key_reuse` | same key, different body | no |
| 429 | type `rate_limit_exceeded` | too fast | after `Retry-After` |
| 429 | `insufficient_quota` | out of credit | **no**, top up (sent with `X-Should-Retry: false`) |
| 502 | `upstream_job_failed` | the job failed; read `message` | not blindly |
| 503 | `upstream_unavailable` | no capacity | yes, with backoff |
| 504 | `upstream_timeout` | job outlived the wait window | yes, with an `Idempotency-Key` |

- The SDKs retry 5xx and honour `Retry-After` and `X-Should-Retry`. On a daily limit
  (`Retry-After` over 120 s) `X-Should-Retry: false` stops the SDK; schedule the retry yourself.
- `X-RateLimit-*` headers come only with a 429 here. To watch the remaining allowance, read the
  headers of native API responses.

## Idempotency

`Idempotency-Key: <unique value>` on any `POST` (`extra_headers=` in Python, `headers` in the
Node request options) replays the stored response for the same key and body, so a retry does
not create a second job or charge. Scoped per key and endpoint, kept one hour, stored for 2xx
only. Bodies over 1 MiB (multipart uploads) bypass it. Concurrent requests with the same key both
run.

## Where OpenAI-compatible stops

No price quotes on this API: to show a cost first, quote the same job on the native
`…/price` endpoint. Not available here:

| You need | Use |
|---|---|
| OCR, background removal, image or video upscale, music, audio to video, video description | native API (`references/native-api.md`) or the `deapi` skill |
| Transcription of YouTube, X, Twitch, Kick, TikTok or X Spaces links | native `POST /api/v2/audio/transcriptions` with `source_url` |
| Voice cloning | native `POST /api/v2/audio/speech` with `mode=voice_clone` |
| Price quotes, webhooks, prompt booster, partner models (`gpt-image-*`, `nano-banana-*`, …) | native API |
| Last frame or reference images/videos for video | native `POST /api/v2/videos/animations` |
| `/v1/files`, `/v1/audio/translations`, `/v1/moderations`, `/v1/images/variations`, assistants, batches, realtime | not available |
| `/v1/chat/completions`, `/v1/responses` | not served (404 `endpoint_not_implemented`); `GET /v1/models` always shows what your account can call |

No endpoint streams: `stream=true` is rejected.

## Frameworks

Anything with a base URL setting works. Use native model IDs.

| Framework | Setting |
|---|---|
| LangChain | `OpenAIEmbeddings(model="Bge_M3_FP16", openai_api_base="https://oai.deapi.ai/v1", openai_api_key=...)` |
| LlamaIndex | `OpenAIEmbedding(model="Bge_M3_FP16", api_base="https://oai.deapi.ai/v1", api_key=...)` |
| LiteLLM | OpenAI-compatible route: `model="openai/Bge_M3_FP16"`, `api_base="https://oai.deapi.ai/v1"`, `api_key=...` |
| Vercel AI SDK | `createOpenAI({ baseURL: "https://oai.deapi.ai/v1", apiKey: process.env.DEAPI_API_KEY })` from `@ai-sdk/openai` |

Pass the key from the environment in every case.
