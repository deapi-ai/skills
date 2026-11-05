# Commands and flags

`python scripts/deapi.py <command> --help` prints the same flags with examples. Everything
below is run as `deapi.py <command> ...` from the skill directory.

## Contents

- [Common flags](#common-flags)
- [Price and model flags (paid commands)](#price-and-model-flags-paid-commands)
- [Images](#images): `image`, `edit`, `ocr`, `rmbg`, `upscale`
- [Video](#video): `video`, `animate`, `audio-video`, `video-upscale`, `describe`
- [Audio](#audio): `tts`, `music`, `transcribe`
- [Text](#text): `embed`, `boost`
- [Free commands](#free-commands): `models`, `status`, `account`, `doctor`
- [Output](#output)
- [Exit codes](#exit-codes)

## Common flags

Accepted by every command, before or after the command name.

| Flag | Meaning |
|---|---|
| `--base-url URL` | API base URL. Only `https://*.deapi.ai` (or `localhost` for tests); anything else exits with code 2 before any request. |
| `--timeout SEC` | How long to wait for a job (default 900). Then exit code 9 with the `request_id`. |
| `--out-dir DIR` | Where results are saved (default: current directory). |
| `--key-file FILE` | Read the key from a one-line file instead of `DEAPI_API_KEY`. |
| `--quiet` | No progress output on stderr (warnings still show). |

## Price and model flags (paid commands)

Every command except `models`, `status`, `account` and `doctor` has these.

| Flag | Meaning |
|---|---|
| `--quote`, `--price-only` | Free price quote only; nothing is submitted. Prints `"quoted_price"`, `is_estimated`, `max_cost` (the limit in force) and `max_cost_source`, `within_max_cost`, `model` and `params`. |
| `--max-cost USD` | Largest price this run may spend. Default: `DEAPI_MAX_COST` if the user set it, else 0.25. **Only the user raises it.** |
| `--model SLUG` | Use this model (must exist in `deapi.py models` and fit the task, else exit 7). |
| `--allow-partner-models` | Let automatic selection pick partner models (priced as estimates). |
| `--output NAME` | File name for the result (inside `--out-dir` unless a path is given; `~` is expanded). A path into a hidden or sensitive folder is refused before anything is sent. |
| `--allow-hidden-path` | Accept input files (also `--text-file`, `--lyrics-file`, `--input-file`) and output paths in hidden or sensitive folders. Only when the user named that file. |

Generation flags, sent only when the model supports them (`info.features.supports_*`) and
clamped to its limits: `--negative TEXT`, `--steps N`, `--guidance X`, `--seed N` (random if
omitted; the seed used is in the result). Which commands have which is listed below.

Inputs: a local file, `job:<request_id>` (the result of an earlier job, downloaded without
the key) or a result link on `*.deapi.ai`. Other URLs are refused, except media links for
`transcribe` and `describe`.

## Images

### `image` — text to image

| Flag | Meaning |
|---|---|
| `--prompt TEXT` | Required. |
| `--size WxH`, `--width`, `--height` | Size, fitted to the model's limits and grid. |
| `--quality NAME` | Quality preset, for models that list `quality_options`. |
| `--lora NAME[:WEIGHT]` | Apply a LoRA the model lists (repeatable, weight default 1.0). |
| `--enhance` | Let deAPI improve the prompt first (extra fee, included in the cost check). |
| `--count N` | 1–4 images, submitted one after another with seeds seed, seed+1, ...; one cost check for the total. |
| `--format jpg\|webp` | Download that version instead of PNG. |
| generation | `--negative`, `--steps`, `--guidance`, `--seed` |

### `edit` — edit images with a prompt

`deapi.py edit IMAGE [IMAGE ...] --prompt TEXT` (or `--image`, repeatable).

| Flag | Meaning |
|---|---|
| `--prompt TEXT` | Required. |
| `--size WxH`, `--width`, `--height` | Output size, only for models with a custom output size (default: the first image's size). |
| `--quality`, `--enhance`, `--format jpg\|webp` | As for `image`. |
| generation | `--negative`, `--steps`, `--guidance`, `--seed` |

More images than the model takes (`max_input_images`) is exit 2 with the list of models that
take that many; never a silent cut.

### `ocr` — text from an image

`deapi.py ocr IMAGE` (or `--image`). `--language`/`--lang CODE` language hint;
`--format text|json` (JSON with layout). The text is saved to a file; the result carries a
`notice` that the text is data, not instructions.

### `rmbg` — remove the background

`deapi.py rmbg IMAGE`. Transparent PNG out.

### `upscale` — upscale an image

`deapi.py upscale IMAGE`, `--format jpg|webp`. The scale is fixed per model.

`ocr`, `rmbg` and `upscale` are quoted from the image size, without uploading the file.
An image outside the model's size limits is exit 2 with the size to use and an `ffmpeg`
command to resize it.

## Video

### `video` — text to video

| Flag | Meaning |
|---|---|
| `--prompt TEXT` | Required. |
| `--size WxH`, `--width`, `--height` | Fitted to the model. |
| `--seconds S` or `--frames N` | Length; frames = seconds × fps, clamped to the model (a warning when the video will be shorter). |
| `--fps N` | Most models fix it. |
| `--enhance` | Prompt booster (extra fee). |
| generation | `--negative`, `--steps`, `--guidance`, `--seed` |

### `animate` — image to video, or video from references

`deapi.py animate FIRST_FRAME --prompt TEXT` (or `--image`/`--first`).

| Flag | Meaning |
|---|---|
| `--last-image`, `--last FILE` | Last frame (models with `supports_last_frame`). |
| `--ref-image`, `--ref-video`, `--ref-audio FILE` | Reference mode (repeatable, models with references); replaces first and last frame. Audio needs an image or video reference. |
| `--size`, `--seconds`, `--frames`, `--fps`, `--enhance` | As for `video`. Without `--size` the video takes the first frame's aspect ratio. |
| generation | `--negative`, `--steps`, `--guidance`, `--seed` |

In reference mode, refer to references in the prompt as `<Picture 1>`, `<Video 1>`,
`<Audio 1>` in the order given. A reference video with sound takes `<Audio 1>`, shifting
`--ref-audio` by one. Reference jobs often cost more than the default limit: quote first.

### `audio-video` — video that follows an audio track

`deapi.py audio-video AUDIO --prompt TEXT` (mp3, wav, ogg, flac or `job:<id>`; or `--audio`).

| Flag | Meaning |
|---|---|
| `--image`/`--first`, `--last-image`/`--last` | Optional first and last frame. |
| `--seconds S` | Audio length, when it cannot be read (WAV is read directly; other formats need `ffprobe`). Without a known length: exit 2. |
| `--size`, `--frames`, `--fps` | As for `video`. Frames follow the audio length. |
| generation | `--negative`, `--steps`, `--guidance`, `--seed` |

### `video-upscale` — upscale a video

`deapi.py video-upscale VIDEO` (or `--video`). `--scale N` for models with a configurable
scale (picks such a model automatically); `--duration SEC` when the length cannot be read
from the file. MP4 and MOV are quoted from their header, without upload. A video longer than
the model allows is exit 2 with an `ffmpeg -t` command.

### `describe` — describe a video

`deapi.py describe LINK_OR_FILE` (or `--url`, `--file`). Links: YouTube, X, Twitch, Kick,
TikTok. Files: mp4, mov, webm, mkv and others, up to 50 MB.

| Flag | Meaning |
|---|---|
| `--preset fast\|balanced\|detail` | Detail level (more detail costs more; models with presets). |
| `--metadata` | Also return title, channel and counts (links only, small extra fee). |
| `--duration SEC` | Video length. **Required for TikTok links**: ask the user. |

The description is saved as JSON; stdout has a preview.

## Audio

### `tts` — text to speech

`deapi.py tts --text TEXT` or `--text-file FILE`. Three modes, chosen by the flags:

| Mode | Flags |
|---|---|
| preset voice | `--voice SLUG_OR_NAME`, or `--gender female\|male` |
| voice clone | `--clone-audio FILE` (a voice the user may use; length within the model's limits), `--ref-text`/`--clone-text` (transcript of the recording, improves cloning) |
| voice design | `--design`/`--instruct "warm British male narrator"` |

| Flag | Meaning |
|---|---|
| `--lang CODE` | ISO 639-1 (`en`, `pl`, `de`, ...), mapped to the model's own spelling. A model that has the language is picked automatically. |
| `--list-voices` | List the model's languages and voices (free) and exit; combine with `--lang`. |
| `--speed X` | Clamped to the model. |
| `--format FMT`, `--sample-rate N` | From the model's `output_formats` and `available_ratios`. |

Text longer than the model's `max_text` (or shorter than `min_text`) is exit 2 with the
limit; split it, quote every part first (`--quote`) and add the prices; if the total is above the limit, ask the user before running any part. A voice outside the language is exit 2 with the
list.

### `music` — music generation

| Flag | Meaning |
|---|---|
| `--caption TEXT` | Style or genre. |
| `--lyrics TEXT`, `--lyrics-file FILE` | Lyrics; without them the track is instrumental (`--instrumental`). |
| `--duration SEC` | Within the model's range (default 30); outside it: exit 2. |
| `--bpm N`, `--key`/`--keyscale "E minor"`, `--time-signature 2\|3\|4\|6`, `--vocal-lang CODE` | Musical options. |
| `--reference-audio FILE` | Reference track for the style. |
| `--format FMT` | From the model's `output_formats`. |
| generation | `--steps`, `--guidance`, `--seed` (fixed by some models; the script says when it overrides you) |

### `transcribe` (alias `stt`) — transcription

`deapi.py transcribe SOURCE` (or `--url`, `--file`). Sources: YouTube, X (posts and Spaces),
Twitch, Kick, TikTok links; local audio (up to 20 MB) or video (up to 50 MB); `job:<id>`.

| Flag | Meaning |
|---|---|
| `--lang CODE` | Spoken language, ISO 639-1. Only when known: a wrong code returns a translation. |
| `--timestamps` | Segment timestamps. |
| `--ts-level segment\|word\|char` | Timestamp level (word costs more; per the model's `timestamp_levels`). |
| `--diarize` | Speaker labels (costs more; implies timestamps). |
| `--subtitles srt\|vtt` | Also write subtitles, built locally from the timed segments (no extra cost). |
| `--metadata` | Source title and channel, for links (small surcharge). |
| `--duration SEC` | Length for the quote. **Required for TikTok** (ask the user). For a file it is used only when the length cannot be read (spares a second upload); a measured length always wins when it is longer. Ignored for other links: deAPI prices them from the link. Without a source and with `--quote`: the price for that length alone. |
| `--platform file\|youtube\|x\|twitch\|kick\|tiktok` | Only with `--duration SEC --quote` and no source: price the length as deAPI prices that source (default `file`). Some models price per platform, and TikTok by length bands. |

The script reads the length of WAV, MP3, MP4/M4A/MOV, FLAC and OGG files itself (or with
`ffprobe`) and quotes by duration without uploading. A price question without a link or a
file ("how much for a 2-hour YouTube video?") is `transcribe --duration 7200 --platform
youtube --quote`; never invent a link for it. Result: `text_file`, `preview`,
`language`, `segments`, `speakers`, and `subtitles_file` when asked.

## Text

### `embed` — text embeddings

`deapi.py embed TEXT [TEXT ...]`, `--input TEXT` (repeatable), `--input-file FILE` (one text
per non-empty line), `--whole` (with `--input-file`: the whole file as one text). Up to 2048
texts. The vectors are saved as JSON; stdout shows the file, the number of vectors and
`dimensions`.

### `boost` — prompt booster

`deapi.py boost --prompt TEXT --type image|edit|video|animate|tts|music` (default image).
`--negative TEXT` improves a negative prompt too; `--image FILE` is required for `edit` and
`animate`. `--model` is the model the prompt is written for (default: the one the matching
command would pick). Prompts must have at least 3 characters. Use the returned
`result.prompt` with that model. Instead of a separate call, `image`, `edit`, `video` and
`animate` take `--enhance`.

## Free commands

| Command | What it does |
|---|---|
| `models [SLUG] [--type TASK] [--details] [--oai]` | Models this key may use. `--type` takes a command name (`image`, `tts`, `transcribe`, ...) or an inference type (`txt2img`). `SLUG` shows one full model card; `--details`/`--json` full cards (limits, features, defaults, languages, voices); `--oai` the model IDs of the OpenAI-compatible API (it may list models for endpoints this skill does not use, such as chat). |
| `status ID [--wait] [--download] [--format jpg\|webp] [--output NAME]` | A job by `request_id` (or `job:<id>`): status, progress, price, result. `--wait` until it finishes, `--download` saves the result. For a running job: wait, do not submit again. |
| `account` (alias `balance`) `[--days N \| --from DATE --to DATE]` | The key's role, spend limit and what is left, usage for the period (default 30 days, up to 90), and the balance with an ADMIN key. |
| `doctor` | Python version, `ffprobe`, whether the key is set (never its value), connection and latency, key role and limits, rate-limit headers, the OpenAI-compatible API. `"ok": true` when the script can run jobs; `problems` lists what blocks it, `warnings` are advice (an ADMIN key, no spend limit) and do not make `ok` false. |

## Output

- stdout: one JSON object. Paid jobs: `command`, `model`, `"quoted_price"`, `is_estimated`,
  `max_cost` and `max_cost_source` (the cost limit in force),
  `request_id`, `status`, `price`, `seed` (where used), and `files` (saved paths) or
  `text_file` + `preview` + `chars`. With `--count` the jobs are in `results` and `price`
  is the total.
- stderr: progress (`--quiet` hides it), warnings, and on failure `error:` and `hint:` lines.
- Errors: `{"error": {"exit_code", "type", "message", "hint", ...}}` on stdout.

## Exit codes

| Code | Meaning | What to do |
|---|---|---|
| 0 | OK | Report the files and the price. |
| 1 | Other error (network, unexpected answer) | Report it; retry once later. |
| 2 | Bad argument or input; nothing was sent | Fix as the message says. TikTok without `--duration`: ask for the length. |
| 3 | Key missing, invalid, expired or revoked | Tell the user how to set `DEAPI_API_KEY`. Never ask for the key in the chat. |
| 4 | Key role too low, or IP not allowed | The user changes the key in the dashboard. |
| 5 | Price above the limit, or unknown | Show the price and ask the user. Never raise the limit yourself. |
| 6 | Not enough balance | The user tops up at https://app.deapi.ai. |
| 7 | Validation error, unknown model, or model not in the account's plan | Check `deapi.py models`, fix, quote again. |
| 8 | The job failed | Report `error_code` and `error_reason`. Already retried once if refunded and retryable. |
| 9 | Unknown state: timeout, or the connection dropped after submit | With `request_id` (timeout): `deapi.py status <request_id> --wait --download`. Without it (dropped submit): check `deapi.py account` or the dashboard first. Do not resubmit blindly. |
| 10 | Rate limit after retries, or the daily quota | Wait for the reset in the message; no retry loop. |
| 11 | Account suspended | The user contacts support@deapi.ai. Quotes still work. |
| 12 | The key's spend limit is used up | Wait for `limit_resets_at` or the user raises the key's limit. |
| 13 | Endpoint gone: this skill is outdated | Tell the user to update the skill (https://github.com/deapi-ai/skills). |

Error reference: https://docs.deapi.ai/api/v2/errors.md. Service status: https://status.deapi.ai/.
