# Recipes

Multi-step jobs and fixes for files the API will not take. `deapi.py` is
`python <skill-dir>/scripts/deapi.py`. Each paid step is quoted and checked against the cost
limit on its own; for a chain, quote every step with `--quote` first and tell the user the
total before starting.

## Contents

- [Chaining jobs with job:ID](#chaining-jobs-with-jobid)
- [Image chains](#image-chains)
- [Video chains](#video-chains)
- [Speech and music](#speech-and-music)
- [Transcription](#transcription)
- [Files the API will not take](#files-the-api-will-not-take)
- [Resuming and checking jobs](#resuming-and-checking-jobs)

## Chaining jobs with job:ID

Every result has a `request_id`. Pass `job:<request_id>` as the input of the next command:
the script downloads that result (without the key) and sends it on. No re-upload from the
user's disk, no guessing file names. The API does not take result links as inputs, so this
is the way to chain.

## Image chains

**Generate, then animate, then upscale the video:**

```bash
deapi.py image --prompt "a lighthouse at dusk, cinematic" --size 1024x576
# -> "request_id": "R1"
deapi.py animate job:R1 --prompt "waves roll in, slow push-in" --seconds 4 --quote
deapi.py animate job:R1 --prompt "waves roll in, slow push-in" --seconds 4
# -> "request_id": "R2"
deapi.py video-upscale job:R2 --quote
```

**Product shot on a clean background:** `deapi.py rmbg photo.jpg`, then
`deapi.py upscale job:<id>`.

**Several variants:** `deapi.py image --prompt "..." --count 4 --quote` quotes all four;
one cost check covers the total.

**Edit with several images:** `deapi.py edit person.png background.png --prompt "put the
person from the first image into the second"`. If the model takes fewer images, the error
lists models that take more.

**Better prompt first:** `image --enhance` (the fee is part of the cost check), or
`deapi.py boost --prompt "a red fox" --type image`, show the user `result.prompt`, then
`deapi.py image --prompt "<that prompt>"`.

## Video chains

**First and last frame:** `deapi.py animate day.png --last-image night.png --prompt "day
turns to night" --quote` (models with `supports_last_frame`).

**From references:** `deapi.py animate --ref-image cat.png --ref-image room.png --prompt
"<Picture 1> walks into <Picture 2>" --quote`. Reference jobs cost more per reference and
often exceed the default limit: show the quote and ask.

**A frame from an existing video** to animate or edit (the user runs or approves this):

```bash
ffmpeg -ss 3 -i clip.mp4 -frames:v 1 frame.png
```

**What happens in a video:** `deapi.py describe "<link>" --preset fast --quote`, then without
`--quote`. For TikTok ask the user for the length first: `--duration 40`.

## Speech and music

**Talking video from text:**

```bash
deapi.py tts --text "Welcome to our shop." --lang en --voice af_bella
# -> "request_id": "T1"
deapi.py audio-video job:T1 --image face.png --prompt "a person talking to the camera" --quote
```

The video length follows the audio. WAV is read directly; for MP3 or other formats without
`ffprobe` pass `--seconds` (or choose `--format wav` in `tts` when the model lists it).

**Text longer than the model's limit** (exit 2 with `max_text`): split the text at sentence
or paragraph boundaries into parts under the limit. Quote every part first (`--quote`) and
add the prices: each run is checked against the limit on its own, so if the total is above
the limit, ask the user before running any part. Then run `tts` per part with the same
`--voice`, and report the files in order. To join them (the user runs or approves this):

```bash
printf "file '%s'\n" part1.mp3 part2.mp3 part3.mp3 > list.txt
ffmpeg -f concat -safe 0 -i list.txt -c copy speech.mp3
```

**Voice clone:** a clean recording of the user's own voice (or one they may use), within the
model's length limits (the error gives the limits and an `ffmpeg -t` command), with its
transcript: `deapi.py tts --text "..." --clone-audio me.wav --ref-text "what I say in the
recording"`.

**Voices for a language:** `deapi.py tts --list-voices --lang de` (free).

**Song with lyrics:** `deapi.py music --caption "indie rock, female vocals" --lyrics-file
song.txt --duration 60 --bpm 120 --key "E minor" --vocal-lang en`.

## Transcription

**Subtitles:** `deapi.py transcribe talk.mp4 --subtitles srt` (built locally from
the timed segments, no extra cost). Add `--diarize` for speaker labels.

**Translation:** deAPI has no translation endpoint. Transcribe in the spoken language
(`--lang` = that language, or omit it), then translate the text yourself.

**Many links:** run `transcribe` once per link, one after another. On the Basic plan
transcription allows 1 request per minute and 10 per day; exit 10 says when it resets.

**X Spaces:** pass the Spaces link like any X link.

## Files the API will not take

The script never converts files. When it refuses one, it prints the fix; these are the usual
ones (the user runs them, or approves running them):

| Problem | Fix |
|---|---|
| PDF for OCR | `pdftoppm -r 150 -png in.pdf page`, then `deapi.py ocr page-1.png` per page |
| Audio over 20 MB | `ffmpeg -i in.wav -ac 1 -ar 16000 -b:a 48k out.mp3` (about 50 minutes in 20 MB) |
| Video over 50 MB, transcription only | take the audio track: `ffmpeg -i in.mp4 -vn -ac 1 -ar 16000 -b:a 48k out.mp3` |
| Unsupported video format | `ffmpeg -i in.mkv -c:v libx264 -c:a aac out.mp4` |
| Image too large or too small | the error gives the size; `ffmpeg -i in.png -vf scale=W:H out.png` |
| Video too long for upscale | the error gives the limit; `ffmpeg -i in.mp4 -t SECONDS -c copy part.mp4` |
| A file the user has as a web link (not YouTube/X/Twitch/Kick/TikTok) | the user downloads it first, or use the MCP tools, which take URLs |

## Resuming and checking jobs

- **Exit 9 after a timeout** (the error has `request_id`): `deapi.py status <request_id>
  --wait --download`. Do not submit again; the job may be running and paid.
- **Exit 9 after a dropped connection while submitting** (no `request_id`): check
  `deapi.py account` or the dashboard for the job before trying again.
- **A job from another session:** `deapi.py status <request_id>` shows status, progress and
  price; `--download` saves the result while the link is valid.
- **What did it cost:** the `price` field of each result, or `deapi.py account --days 7`
  for the key's usage by type.
