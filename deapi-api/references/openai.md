# OpenAI-compatible API (`oai.deapi.ai`)

Point an official OpenAI SDK (or any tool with a base URL setting) at
`https://oai.deapi.ai/v1` and call deAPI models with OpenAI request and response shapes.
This is also the only way to deAPI's chat models (LLMs): the native API v2 has no chat endpoint.
Docs: https://docs.deapi.ai/openai-compatibility.md.

- API key: the deAPI key as is (`sk-dp-<id>-<secret>`), in `Authorization: Bearer`.
- Model IDs: **deAPI's own only** (`Flux1schnell`, `Kokoro`, `WhisperLargeV3Ct2`, …; chat models
  such as `qwen3.8-flash`). There are no `dall-e-3`, `tts-1`, `whisper-1` or `gpt-4o` aliases;
  those names answer 404 `model_not_found`.
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
    timeout=900.0,   # media jobs hold the connection until they finish (up to ~10 minutes)
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
| `GET /v1/models`, `GET /v1/models/{model}` | sync | `{id, object, created, owned_by}`, chat models also price and limits; cached 5 minutes |
| `POST /v1/chat/completions` | sync or streamed | chat models (LLMs) |
| `POST /v1/embeddings` | sync | `Bge_M3_FP16`, 1024 dimensions |
| `POST /v1/images/generations` | holds the connection | text to image |
| `POST /v1/images/edits` | holds the connection | multipart or JSON with `images: [{"image_url": …}]` |
| `POST /v1/audio/speech` | holds the connection | returns MP3 bytes |
| `POST /v1/audio/transcriptions` | holds the connection | file upload only |
| `POST /v1/videos`, `GET /v1/videos/{id}`, `GET /v1/videos/{id}/content` | asynchronous | create, poll, download |

Each response has `X-Request-Id` (quote it to support) and job endpoints add
`X-DeAPI-Request-Id` (the native job ID, comma-separated when a request fans out).

### Chat completions (LLMs)

```python
reply = deapi.chat.completions.create(
    model="qwen3.8-flash",
    messages=[{"role": "user", "content": "Explain vector embeddings in two sentences."}],
    max_tokens=2000,           # includes reasoning tokens
    reasoning_effort="low",
)
print(reply.choices[0].message.content)
print(reply.usage.cost)        # USD charged for this call (a deAPI field)
```

Streamed, with the cost in the last chunk:

```python
stream = deapi.chat.completions.create(
    model="qwen3.8-flash",
    messages=[{"role": "user", "content": "Write a haiku about GPUs."}],
    stream=True,
    stream_options={"include_usage": True},
)
for chunk in stream:
    if chunk.choices and chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="", flush=True)
    if chunk.usage:            # last chunk: empty choices, full usage
        print("\ncost:", chunk.usage.cost)
```

Chat models this key can call, with prices per million tokens:

```python
for m in deapi.models.list().data:
    if getattr(m, "context_length", None):       # only chat entries have it
        pricing = m.pricing                       # USD per token, decimal strings
        print(m.id, m.context_length,
              float(pricing["prompt"]) * 1e6, float(pricing["completion"]) * 1e6)
```

- **Models:** 13 at the time of writing (`deepseek-v4.1-flash`, `deepseek-v4.1-flash:economy`,
  `glm-5.3`, `glm-5.3-flash`, `gpt-6-luna`, `hy3`, `kimi-k3`, `qwen3.8-flash`, `qwen3.8-max`,
  `seed-2.0-lite`, `seed-2.0-mini`, `seed-2.0-pro`, `seed-2.1-turbo`), available to every account.
  Build on `GET /v1/models`: chat entries are the ones with `context_length`, and they carry
  `pricing` (`prompt`, `completion`, `input_cache_read`, `input_cache_write` where billed),
  `top_provider.max_completion_tokens`, `architecture.input_modalities` and
  `supported_parameters`. They are not in the native `GET /api/v2/models`.
  `deepseek-v4.1-flash:economy` is the same model at a lower price; pass the whole ID.
- **Cost:** no price quote; each response reports what it cost in `usage.cost` (USD), also in
  the `X-DeAPI-Cost` header when not streamed, and in the final chunk of a stream sent with
  `stream_options.include_usage`. `usage.prompt_tokens_details.cached_tokens` is the part billed
  at the cached price. To cap a call, set `max_tokens` and compute the worst case from
  `pricing`. Store `usage.cost` per call for your own accounting.
- **Reasoning:** `reasoning_effort` (`none`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max`)
  as far as the model accepts it: `glm-5.3` takes only `low`, `high` and `max` (thinking cannot
  be turned off), `gpt-6-luna` has no `minimal`; a level the model lacks is 400
  `unsupported_value`, and the message lists the accepted ones. Thinking comes back in
  `message.reasoning_content` (`delta.reasoning_content` when streamed) and is billed as output.
  `max_tokens` covers thinking and answer together: a low cap can return an empty `content`
  with `finish_reason: "length"`, and that call is still billed.
- **Parameters:** a field missing from the model's `supported_parameters` is rejected (400
  `unsupported_parameter`), not dropped. `n` must be 1; `logprobs` is rejected. `json_object`
  works everywhere, `json_schema` only where `supported_parameters` has `structured_outputs`
  (not `glm-5.3`). `stop` is missing on `glm-5.3` and `gpt-6-luna`. `kimi-k3` accepts only
  `temperature` 0, 0.6 or 1, `top_p` 0.95 and `presence_penalty`/`frequency_penalty` 0; other
  values return 400 `unsupported_value`.
- **Tools:** OpenAI function calling (`tools`, `tool_choice`, `parallel_tool_calls`); on
  `finish_reason: "tool_calls"` append the assistant message and a `role: "tool"` message with
  `tool_call_id`, then call again.
- **Images in:** `image_url` content parts on models with `image` in
  `architecture.input_modalities` (not `glm-5.3`, `hy3`: 400 `unsupported_content`). Send
  base64 `data:` URLs; a public URL the provider cannot fetch is 400 `invalid_image_url`.
- Chat does not go through the GPU job queue: answers take seconds, and the 900 s timeout above
  is only needed for media jobs.
- `Idempotency-Key` is ignored here: a repeated request is a second, billed call.

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
  stable only within one response), word timestamps. A model without word timestamps answers
  `timestamp_granularities=["word"]` with 400 `timestamp_level_not_supported`.
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
| 400 | `param_not_supported`, `size_not_supported`, `invalid_value`, `model_wrong_endpoint`, `unsupported_parameter`, `unsupported_value`, `unsupported_content`, `invalid_image_url`, … | request problem; `param` names your field | no, fix it |
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
run. Chat completions ignore it.

## Where OpenAI-compatible stops

No price quotes on this API: to show the cost of a media job first, quote the same job on the
native `…/price` endpoint (chat reports its cost in each response instead). Not available here:

| You need | Use |
|---|---|
| OCR, background removal, image or video upscale, music, audio to video, video description | native API (`references/native-api.md`) or the `deapi` skill |
| Transcription of YouTube, X, Twitch, Kick, TikTok or X Spaces links | native `POST /api/v2/audio/transcriptions` with `source_url` |
| Voice cloning | native `POST /api/v2/audio/speech` with `mode=voice_clone` |
| Price quotes, webhooks, prompt booster, partner models (`gpt-image-*`, `nano-banana-*`, …) | native API |
| Last frame or reference images/videos for video | native `POST /api/v2/videos/animations` |
| `/v1/responses`, `/v1/files`, `/v1/audio/translations`, `/v1/moderations`, `/v1/images/variations`, assistants, batches, realtime | not available (404 `endpoint_not_implemented`); use `/v1/chat/completions` for chat |

Only chat completions stream; every other endpoint rejects `stream=true`.

## Frameworks

Anything with a base URL setting works. Use deAPI model IDs. A framework that sends chat to
OpenAI's Responses API (`/v1/responses`) must be switched to chat completions.

| Framework | Setting |
|---|---|
| LangChain | chat: `ChatOpenAI(model="qwen3.8-flash", base_url="https://oai.deapi.ai/v1", api_key=...)`; embeddings: `OpenAIEmbeddings(model="Bge_M3_FP16", openai_api_base="https://oai.deapi.ai/v1", openai_api_key=...)` |
| LlamaIndex | chat: `OpenAILike(model="qwen3.8-flash", api_base="https://oai.deapi.ai/v1", api_key=..., is_chat_model=True)` (`llama-index-llms-openai-like`); embeddings: `OpenAIEmbedding(model="Bge_M3_FP16", api_base="https://oai.deapi.ai/v1", api_key=...)` |
| LiteLLM | OpenAI-compatible route: `model="openai/qwen3.8-flash"` (or `openai/Bge_M3_FP16`), `api_base="https://oai.deapi.ai/v1"`, `api_key=...` |
| Vercel AI SDK | `@deapi/ai-sdk-provider` 0.4 or later (AI SDK 7): `deapi("qwen3.8-flash")` with `generateText`/`streamText`, cost in `providerMetadata.deapi.cost`, chat models from `deapi.listModels()`, media and embedding models too. Or `createOpenAI({ baseURL: "https://oai.deapi.ai/v1", apiKey: process.env.DEAPI_API_KEY })` from `@ai-sdk/openai` with `.chat("qwen3.8-flash")`, not the default Responses model |

Pass the key from the environment in every case.
