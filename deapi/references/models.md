# Models: how the script chooses, and how to look them up

Models are added and retired over time and depend on the account's plan. The script never
keeps a model list of its own: every run reads `GET /api/v2/models` for the task. **Never
invent model IDs**; take them from `deapi.py models`.

```bash
deapi.py models                       # all models this key may use (free)
deapi.py models --type transcribe     # by command name, or an inference type such as txt2img
deapi.py models --type tts --details  # full cards: limits, features, defaults, languages, voices
deapi.py models Kokoro                # one model's full card (exit 7 if it does not exist)
deapi.py models --oai                 # model IDs of the OpenAI-compatible API
```

The short listing shows each model's `slug`, inference types, the script `commands` that use
it and, for partner models, a `note`.

## Automatic choice (no `--model`)

1. Only models that serve the command's inference type (table below).
2. Only standard models. **Partner models** are skipped unless the user passes
   `--allow-partner-models` or names one with `--model`.
3. Only models that can do what the flags ask, read from the model card: for example
   `supports_voice_clone` for `tts --clone-audio`, a language in `languages[]` for
   `tts --lang`, `max_input_images` for several images in `edit`, a scale range for
   `video-upscale --scale`, listed LoRAs for `image --lora`.
4. Among those, a short list of name hints per task (for example Whisper Ct2 for
   transcription, Kokoro for speech) ranks the order; otherwise the first model in the list.

With `--model SLUG` the script still reads that model's card, because it needs its limits.
A slug that is not in the catalog, or that does not serve the task, exits with code 7.

## Command to inference type

| Command | Inference types |
|---|---|
| `image` | `txt2img` |
| `edit` | `img2img` |
| `ocr` | `img2txt` |
| `rmbg` | `img-rmbg` |
| `upscale` | `img-upscale` |
| `video` | `txt2video` |
| `animate` | `img2video` |
| `audio-video` | `audio2video` |
| `video-upscale` | `vid-upscale` |
| `describe` | `video2desc` (links), `video_file2desc` (files) |
| `tts` | `txt2audio` |
| `music` | `txt2music` |
| `transcribe` | `audio2text`, `video2text`, `audio_file2text`, `video_file2text` |
| `embed` | `txt2embedding` |

## The model card

| Field | What the script does with it |
|---|---|
| `slug` | The value sent as `model`. |
| `inference_types` | Which commands may use the model. |
| `status`, `tags` | `standard_model` is a model on deAPI's network; `test_model` (tagged `experimental` or `external`) is a partner model. |
| `info.limits` | Ranges for size, steps, frames, fps, duration, text length (`max_text`), input images (`max_input_images`), references, `quality_options`, `output_formats`, `timestamp_levels`. Values are clamped to them, or the script exits with code 2 and the limit. |
| `info.features` | `supports_steps`, `supports_guidance`, `supports_negative_prompt`, `supports_custom_output_size`, `supports_last_frame`, `supports_voice_clone`, `supports_diarization` and others. A field is sent when its feature is `true`, or when the card does not say and the user set the flag. |
| `info.defaults` | Values used when a flag is omitted (steps, frames, fps, speech format). |
| `languages[]` | Speech models: language `slug` and its `voices[]` (`slug`, `name`, `gender`). |
| `loras` | LoRAs an image model offers (`name`, `display_name`). |

## Partner models

Large models run on partners' infrastructure and listed in the same catalog. Their price is
quoted as an estimate (`is_estimated: true`) and settled after the job, and they appear only
on Premium accounts (after any payment). The script never picks one by itself. Use one only
when the user asks for it, and say that the price is an estimate. See
https://docs.deapi.ai/models.md.

## Transcription

- The default is `WhisperLargeV3Ct2`: word timestamps, speaker labels (`--diarize`), TikTok
  links, and it uses `--lang`. Keep it unless the user names another model.
- `WhisperLargeV3` does not use `lang`. Prefer the default.
- `--lang` takes a 2-letter ISO 639-1 code. Set it only when you know the spoken language: a
  wrong code returns a translation instead of a transcript.

## Speech languages and voices

- Each speech model spells languages its own way (`en-us`, `pl`, `English`). Pass an
  ISO 639-1 code (`--lang en`); the script maps it to the model's spelling. `en` covers
  `en-us` and `en-gb`.
- With `--lang` and no `--model`, the script picks a model that has the language.
- `--voice` is checked within the language; `deapi.py tts --list-voices --lang de` lists them
  (free). Without `--lang`, a voice from another language switches the language, with a note.
- Voice clone and voice design work only on models with those features and the language;
  otherwise exit 7 with an explanation.
- `max_text` differs a lot between models. Over it: exit 2 with the limit (split the text,
  quote every part and ask the user when the total is above the cost limit).

## Images and video

- Size flags are fitted to the model's ranges and size grid; the result shows the size used.
- `animate` and `audio-video` take the first frame's aspect ratio unless `--size` is given.
- `fps` usually comes from the model; `--seconds` becomes frames = seconds × fps, clamped.
- Models with fixed parameters (for example a fixed number of steps) ignore flags they do not
  support; the script logs what it changed.
- `image --quality` must be one of the model's `quality_options`.

## Choosing for the user

When the user asks "which model", run `deapi.py models --type <command>`, show a few slugs
with what differs (from `--details`: limits, features, languages), and quote the
candidates with `--quote --model <slug>` if the price matters.
