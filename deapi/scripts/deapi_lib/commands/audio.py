"""Audio commands: tts, music, transcribe (alias stt).

Every value sent to the API comes from the live model card (GET /api/v2/models):
languages and voices from `languages[]`, ranges from `info.limits`, switches
from `info.features`. Nothing is split, trimmed or converted behind the user's
back: when an input does not fit the model, the command stops with the limit
and, where it helps, a ready ffmpeg command.
"""

import io
import json
import os
import random
import re
import struct
import textwrap

from .. import core

ORDER = 30  # position in --help

SAMPLES = [
    ["tts", "--text", "Hello world", "--lang", "en"],
    ["tts", "--text", "Hello there, friend", "--lang", "en", "--design", "calm narrator"],
    ["tts", "--text", "Hello there, friend", "--clone-audio", "{mp3}", "--ref-text", "hi"],
    ["transcribe", "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "--lang", "en"],
    ["transcribe", "https://www.tiktok.com/@user/video/1", "--duration", "30", "--metadata"],
    ["transcribe", "{wav}", "--lang", "en", "--timestamps"],
    ["transcribe", "{mp3}", "--diarize", "--ts-level", "word", "--subtitles", "srt"],
    ["transcribe", "{mp4}", "--duration", "12"],
    ["music", "--caption", "calm lofi piano", "--duration", "10"],
    ["music", "--caption", "upbeat pop", "--lyrics", "[verse]\nla la la", "--bpm", "120",
     "--key", "C major", "--time-signature", "4", "--vocal-lang", "en"],
]

TS_LEVELS = ("segment", "word", "char")
SUBTITLE_FORMATS = ("srt", "vtt")
SUBTITLE_LINE = 42          # characters per subtitle line
SUBTITLE_CUE = 2 * SUBTITLE_LINE


def register(sub):
    p = core.add_command(
        sub, "tts", "text-to-speech (preset voice, voice clone or voice design)",
        epilog="examples:\n"
               "  deapi.py tts --text \"Hello\" --lang en --voice af_bella\n"
               "  deapi.py tts --text-file story.txt --lang pl\n"
               "  deapi.py tts --list-voices --lang de\n"
               "  deapi.py tts --text \"Hello, this is my voice.\" --clone-audio me.wav "
               "--ref-text \"what I say\"\n"
               "  deapi.py tts --text \"Welcome to the show.\" --design \"warm British male "
               "narrator\"\n"
               "Text longer than the model's max_text is refused (exit 2); split it, quote "
               "every part and ask the user when the total is above the cost limit.")
    p.add_argument("--text", help="text to speak")
    p.add_argument("--text-file", help="UTF-8 text file to speak")
    p.add_argument("--lang", help="ISO 639-1 language code, e.g. en, pl (default: model "
                                  "default); mapped to the model's own spelling")
    p.add_argument("--voice", help="preset voice slug or name (see --list-voices)")
    p.add_argument("--gender", choices=["female", "male"],
                   help="pick the first preset voice of this gender when --voice is omitted")
    p.add_argument("--speed", type=float, help="speech speed (clamped to the model)")
    p.add_argument("--format", help="audio format from the model's output_formats")
    p.add_argument("--sample-rate", type=int,
                   help="sample rate from the model's available_ratios")
    p.add_argument("--clone-audio", help="reference recording (voice clone) of a voice the "
                                         "user may use; length within the model's limits")
    p.add_argument("--ref-text", "--clone-text", dest="ref_text",
                   help="transcript of the reference recording (improves cloning)")
    p.add_argument("--design", "--instruct", dest="design",
                   help='voice description for voice design, e.g. "warm British male '
                        'narrator"')
    p.add_argument("--list-voices", action="store_true",
                   help="list the model's languages and preset voices (free) and exit")
    p.set_defaults(func=cmd_tts)

    p = core.add_command(
        sub, "transcribe", "transcribe a media link or an audio/video file", aliases=["stt"],
        epilog="examples:\n"
               "  deapi.py transcribe \"https://www.youtube.com/watch?v=...\"\n"
               "  deapi.py transcribe talk.mp3 --diarize --subtitles srt\n"
               "  deapi.py transcribe \"https://www.tiktok.com/@u/video/1\" --duration 45\n"
               "Links: YouTube, X (posts and Spaces), Twitch, Kick, TikTok. TikTok cannot be "
               "priced from the link: ask the user for the length and pass --duration.")
    p.add_argument("source", nargs="?", metavar="SOURCE",
                   help="YouTube/X/Twitch/Kick/TikTok link, local file or job:<id>")
    p.add_argument("--url", help="media link (same as SOURCE)")
    p.add_argument("--file", help="local audio/video file (same as SOURCE)")
    p.add_argument("--lang", help="spoken language, ISO 639-1 (en, pl, ...); set it only "
                                  "when known: a wrong code returns a translation")
    p.add_argument("--timestamps", action="store_true", help="include segment timestamps")
    p.add_argument("--ts-level", choices=TS_LEVELS,
                   help="timestamp level (word costs more; see the model's timestamp_levels)")
    p.add_argument("--diarize", action="store_true",
                   help="label speakers (costs more; implies timestamps)")
    p.add_argument("--subtitles", choices=SUBTITLE_FORMATS,
                   help="also write subtitles built locally from the timed segments "
                        "(no extra cost)")
    p.add_argument("--metadata", action="store_true",
                   help="include source metadata for links (title, channel; small surcharge)")
    p.add_argument("--duration", type=float,
                   help="length in seconds for the quote: required for TikTok links; for a "
                        "file only used when its length cannot be read (never lowers it); "
                        "ignored for other links")
    p.set_defaults(func=cmd_transcribe)

    p = core.add_command(
        sub, "music", "music generation",
        epilog="examples:\n"
               "  deapi.py music --caption \"calm lofi piano\" --duration 30\n"
               "  deapi.py music --caption \"indie rock\" --lyrics-file song.txt --bpm 120 "
               "--key \"E minor\"")
    p.add_argument("--caption", required=True, help="style or genre description")
    p.add_argument("--lyrics", help="lyrics; omit for an instrumental")
    p.add_argument("--lyrics-file", help="UTF-8 file with the lyrics")
    p.add_argument("--instrumental", action="store_true", help="no vocals (the default "
                                                                "without lyrics)")
    p.add_argument("--duration", type=float,
                   help="seconds, within the model's range (default: 30)")
    p.add_argument("--bpm", type=int, help="beats per minute, within the model's range")
    p.add_argument("--key", "--keyscale", dest="keyscale", help='e.g. "C major", "F# minor"')
    p.add_argument("--time-signature", type=int, choices=[2, 3, 4, 6])
    p.add_argument("--vocal-lang", help="language of the vocals, ISO 639-1")
    p.add_argument("--reference-audio", help="reference track for the style (length within "
                                             "the model's limits)")
    p.add_argument("--format", help="audio format from the model's output_formats")
    core.add_generation_args(p, negative=False)
    p.set_defaults(func=cmd_music)


# ------------------------------------------------------------ helpers ---

def _read_text(value, path, flag, file_flag, required=True, allow_hidden=False):
    """The text from --x or --x-file (exactly one when required)."""
    if value is not None and path:
        raise core.DeapiError("Give either %s or %s, not both." % (flag, file_flag),
                              core.EXIT_USAGE)
    if path:
        value = core.read_text_file(path, file_flag, allow_hidden)
    if value is not None:
        value = value.strip()
    if required and not value:
        raise core.DeapiError("Give the text with %s or %s." % (flag, file_flag),
                              core.EXIT_USAGE)
    return value


def _range_text(low, high, unit=""):
    if low is not None and high is not None:
        return "%s..%s%s" % (_num(low), _num(high), unit)
    if high is not None:
        return "at most %s%s" % (_num(high), unit)
    return "at least %s%s" % (_num(low), unit)


def _num(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return str(value)
    return str(int(value)) if value == int(value) else ("%.2f" % value).rstrip("0")


def _pick_format(model, wanted):
    formats = core.limit(model, "output_formats") or []
    fmt = wanted or core.default(model, "format") or (formats[0] if formats else "mp3")
    fmt = str(fmt).lower()
    if formats and fmt not in [str(f).lower() for f in formats]:
        raise core.DeapiError("%s returns only %s (got --format %s)." % (
            model["slug"], ", ".join(formats), fmt), core.EXIT_USAGE,
            output_formats=formats)
    return fmt


def _check_length(model, part, flag, label):
    """Refuse a reference recording outside the model's min/max length."""
    low = core.limit(model, "min_ref_audio_duration")
    high = core.limit(model, "max_ref_audio_duration")
    if low is None and high is None:
        return None
    seconds = media_length(part.path, part.fmt)
    if seconds is None:
        core.log("note: could not read the length of %s; %s accepts %s %s" % (
            part.public(), model["slug"], label, _range_text(low, high, " s")))
        return None
    if (low is not None and seconds < float(low) - 0.05) or \
            (high is not None and seconds > float(high) + 0.05):
        hint = "Use a longer recording."
        if high is not None and seconds > float(high):
            hint = "Cut it first, e.g. `ffmpeg -i %s -t %s -ac 1 clip.wav`." % (
                _quote_arg(part.path), _num(high))
        raise core.DeapiError("%s is %.1f s long; %s needs %s %s (%s)." % (
            part.public(), seconds, model["slug"], label, _range_text(low, high, " s"), flag),
            core.EXIT_USAGE, hint, seconds=round(seconds, 2), min_seconds=low,
            max_seconds=high)
    return seconds


def _quote_arg(path):
    return '"%s"' % path if re.search(r"\s", path) else path


# ----------------------------------------------------- media length ---
# core.media_duration reads WAV (and anything when ffprobe is installed).
# These stdlib readers cover the other common formats so that a quote can be
# made from the length instead of uploading the file twice. Read-only.

def media_length(path, fmt=None):
    """Length in seconds of a local audio/video file, or None if unknown."""
    seconds = core.media_duration(path)
    if seconds:
        return seconds
    fmt = fmt or core.sniff_format(path)
    reader = {"mp3": _mp3_length, "mp4": _mp4_length, "m4a": _mp4_length,
              "mov": _mp4_length, "flac": _flac_length, "ogg": _ogg_length}.get(fmt)
    if not reader:
        return None
    try:
        seconds = reader(path)
    except (OSError, ValueError, struct.error, IndexError):
        return None
    return seconds if seconds and 0 < seconds < 24 * 3600 else None


def _mp4_length(path):
    """mvhd duration / timescale from an MP4/M4A/MOV file (moov may be at the end)."""
    size = os.path.getsize(path)
    with open(path, "rb") as handle:
        moov = _find_box(handle, 0, size, b"moov")
        if not moov:
            return None
        mvhd = _find_box(handle, moov[0], moov[1], b"mvhd")
        if not mvhd:
            return None
        handle.seek(mvhd[0])
        data = handle.read(32)
        if data[0] == 1:
            timescale, duration = struct.unpack(">IQ", data[20:32])
        else:
            timescale, duration = struct.unpack(">II", data[12:20])
        return duration / float(timescale) if timescale else None


def _find_box(handle, start, end, kind):
    """(content_start, content_end) of the first box `kind` between start and end."""
    pos = start
    while pos + 8 <= end:
        handle.seek(pos)
        head = handle.read(16)
        if len(head) < 8:
            return None
        box_size, box_type = struct.unpack(">I4s", head[:8])
        header = 8
        if box_size == 1:
            box_size = struct.unpack(">Q", head[8:16])[0]
            header = 16
        elif box_size == 0:
            box_size = end - pos
        if box_size < header:
            return None
        if box_type == kind:
            return pos + header, min(end, pos + box_size)
        pos += box_size
    return None


def _flac_length(path):
    with open(path, "rb") as handle:
        head = handle.read(42)
    if head[:4] != b"fLaC" or (head[4] & 0x7F) != 0:
        return None
    value = struct.unpack(">Q", head[18:26])[0]
    rate, total = value >> 44, value & ((1 << 36) - 1)
    return total / float(rate) if rate and total else None


def _ogg_length(path):
    """Vorbis or Opus: last page's granule position / sample rate."""
    size = os.path.getsize(path)
    with open(path, "rb") as handle:
        head = handle.read(512)
        handle.seek(max(0, size - 65536))
        tail = handle.read()
    if not head.startswith(b"OggS"):
        return None
    pre_skip, rate = 0, None
    opus = head.find(b"OpusHead")
    vorbis = head.find(b"\x01vorbis")
    if opus >= 0:
        pre_skip = struct.unpack("<H", head[opus + 10:opus + 12])[0]
        rate = 48000
    elif vorbis >= 0:
        rate = struct.unpack("<I", head[vorbis + 12:vorbis + 16])[0]
    last = tail.rfind(b"OggS")
    if not rate or last < 0 or len(tail) < last + 14:
        return None
    granule = struct.unpack("<q", tail[last + 6:last + 14])[0]
    return (granule - pre_skip) / float(rate) if granule > pre_skip else None


_MP3_BITRATES = {
    (1, 1): (32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448),
    (1, 2): (32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384),
    (1, 3): (32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320),
    (2, 1): (32, 48, 56, 64, 80, 96, 112, 128, 144, 160, 176, 192, 224, 256),
    (2, 2): (8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
    (2, 3): (8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
}
_MP3_RATES = {1: (44100, 48000, 32000), 2: (22050, 24000, 16000), 25: (11025, 12000, 8000)}


def _mp3_frame(data, pos):
    """Parse an MPEG audio frame header at pos -> dict, or None."""
    if pos + 4 > len(data) or data[pos] != 0xFF or (data[pos + 1] & 0xE0) != 0xE0:
        return None
    version = {3: 1, 2: 2, 0: 25}.get((data[pos + 1] >> 3) & 3)
    layer = {3: 1, 2: 2, 1: 3}.get((data[pos + 1] >> 1) & 3)
    bitrate_index = data[pos + 2] >> 4
    rate_index = (data[pos + 2] >> 2) & 3
    if not version or not layer or bitrate_index in (0, 15) or rate_index == 3:
        return None
    bitrate = _MP3_BITRATES[(1 if version == 1 else 2, layer)][bitrate_index - 1] * 1000
    rate = _MP3_RATES[version][rate_index]
    padding = (data[pos + 2] >> 1) & 1
    mono = (data[pos + 3] >> 6) == 3
    if layer == 1:
        samples, length = 384, (12 * bitrate // rate + padding) * 4
    else:
        samples = 1152 if (layer == 2 or version == 1) else 576
        length = samples // 8 * bitrate // rate + padding
    return {"version": version, "layer": layer, "bitrate": bitrate, "rate": rate,
            "mono": mono, "samples": samples, "length": length}


def _mp3_length(path):
    size = os.path.getsize(path)
    with open(path, "rb") as handle:
        head = handle.read(10)
        start = 0
        if head[:3] == b"ID3" and len(head) == 10:
            start = 10 + ((head[6] & 0x7F) << 21 | (head[7] & 0x7F) << 14 |
                          (head[8] & 0x7F) << 7 | (head[9] & 0x7F))
            if head[5] & 0x10:
                start += 10
        handle.seek(start)
        data = handle.read(131072)
        handle.seek(max(0, size - 128))
        has_tag = handle.read(3) == b"TAG"
    for pos in range(0, max(0, len(data) - 4)):
        frame = _mp3_frame(data, pos)
        if not frame or not _mp3_frame(data, pos + frame["length"]):
            continue
        offset = pos + 4 + (17 if frame["version"] == 1 and frame["mono"] else
                            32 if frame["version"] == 1 else
                            9 if frame["mono"] else 17)
        tag = data[offset:offset + 4]
        if tag in (b"Xing", b"Info") and struct.unpack(">I", data[offset + 4:offset + 8])[0] & 1:
            frames = struct.unpack(">I", data[offset + 8:offset + 12])[0]
            return frames * frame["samples"] / float(frame["rate"])
        vbri = pos + 36
        if data[vbri:vbri + 4] == b"VBRI":
            frames = struct.unpack(">I", data[vbri + 14:vbri + 18])[0]
            return frames * frame["samples"] / float(frame["rate"])
        audio = size - start - pos - (128 if has_tag else 0)
        return audio * 8.0 / frame["bitrate"]
    return None


# --------------------------------------------------------------- tts ---

def _voices(entry):
    return [v for v in (entry or {}).get("voices") or [] if isinstance(v, dict)]


def _find_voice(entry, wanted):
    """The voice slug in a language entry matching a slug or display name."""
    low = wanted.lower()
    for voice in _voices(entry):
        if str(voice.get("slug", "")).lower() == low or str(voice.get("name", "")).lower() == low:
            return voice.get("slug")
    return None


def _language_entries(model, iso):
    """All language entries for an ISO code (en -> en-us, en-gb), best match first."""
    best = core.model_language(model, iso)
    if best is None:
        return []
    name = core.LANGUAGE_NAMES.get(iso)
    found = [best]
    for entry in model.get("languages") or []:
        slug = str(entry.get("slug") or "").lower()
        label = str(entry.get("name") or "").lower()
        if entry is not best and (slug == iso or re.match(r"^%s[-_]" % re.escape(iso), slug)
                                  or (name and (slug == name or label.startswith(name)))):
            found.append(entry)
    return found


def _language_entry(model, iso):
    """The model's language entry for --lang, or for its default language."""
    if iso:
        return core.model_language(model, iso)
    wanted = str(core.default(model, "lang") or "").lower()
    for entry in model.get("languages") or []:
        if str(entry.get("slug", "")).lower() == wanted:
            return entry
    return None


def _tts_mode(args):
    if args.clone_audio and args.design:
        raise core.DeapiError("Use either --clone-audio or --design, not both.", core.EXIT_USAGE)
    if args.clone_audio:
        mode = "voice_clone"
    elif args.design:
        mode = "voice_design"
    else:
        mode = "custom_voice"
    if mode != "custom_voice" and (args.voice or args.gender):
        raise core.DeapiError("--voice and --gender pick preset voices; they do not apply to "
                              "%s." % mode.replace("_", " "), core.EXIT_USAGE)
    if args.ref_text and mode != "voice_clone":
        raise core.DeapiError("--ref-text goes with --clone-audio.", core.EXIT_USAGE)
    return mode


def _tts_model(ctx, args, mode, iso):
    feature = "supports_" + mode

    def fits(model):
        if core.feature_known(model, feature) and not core.feature(model, feature):
            return False
        if iso and model.get("languages") and not _language_entries(model, iso):
            return False
        if args.voice and model.get("languages"):
            entries = _language_entries(model, iso) if iso else model["languages"]
            if not any(_find_voice(e, args.voice) for e in entries):
                return False
        return True

    why = mode.replace("_", " ")
    if iso:
        why += " in language %s" % iso
    if args.voice:
        why += " with voice %s" % args.voice
    require = None if mode == "custom_voice" else [feature]
    try:
        model = ctx.pick_model("tts", args.model, args.allow_partner_models, require=require,
                               predicate=None if args.model else fits, why=why)
    except core.DeapiError as exc:
        if not args.voice or args.model or exc.code != core.EXIT_VALIDATION:
            raise
        # No model has that voice: pick one without it, so the voice check
        # below lists the voices that do exist.
        voice, args.voice = args.voice, None
        try:
            model = ctx.pick_model("tts", None, args.allow_partner_models, require=require,
                                   predicate=fits, why=why)
        finally:
            args.voice = voice
    if args.model and mode == "custom_voice" and core.feature_known(model, feature) \
            and not core.feature(model, feature):
        raise core.DeapiError("Model %s has no preset voices; use --clone-audio or --design, "
                              "or pick another model." % model["slug"], core.EXIT_VALIDATION,
                              "Run `deapi.py tts --list-voices`.")
    return model


def _list_voices(model, iso):
    languages = []
    for entry in model.get("languages") or []:
        if iso and not any(entry is e for e in _language_entries(model, iso)):
            continue
        languages.append({
            "lang": entry.get("slug"), "name": entry.get("name"),
            "voices": [{"voice": v.get("slug"), "name": v.get("name"),
                        "gender": v.get("gender")} for v in _voices(entry)]})
    return {"command": "tts", "model": model["slug"],
            "default_lang": core.default(model, "lang"),
            "default_voice": core.default(model, "voice"),
            "max_text": core.limit(model, "max_text"),
            "features": {k: v for k, v in core.info(model, "features").items()
                         if k.startswith("supports_")},
            "languages": languages}


def cmd_tts(ctx, args):
    mode = _tts_mode(args)
    iso = core.check_iso_language(args.lang) if args.lang else None
    if args.list_voices:
        model = _tts_model(ctx, args, mode, iso)
        return _list_voices(model, iso)
    text = _read_text(args.text, args.text_file, "--text", "--text-file",
                      allow_hidden=ctx.allow_hidden_path)
    model = _tts_model(ctx, args, mode, iso)
    slug = model["slug"]

    # Never split long text automatically; report the model's limit.
    max_text, min_text = core.limit(model, "max_text"), core.limit(model, "min_text")
    if max_text and len(text) > int(max_text):
        raise core.DeapiError(
            "The text has %d characters; %s accepts at most %d. Split it into parts of at "
            "most %d characters (at sentence ends), quote each part and ask the user when "
            "the total is above the cost limit." % (
                len(text), slug, int(max_text), int(max_text)), core.EXIT_USAGE,
            "Models with a higher limit: see `deapi.py models --type tts --details`.",
            chars=len(text), max_text=int(max_text))
    if min_text and len(text) < int(min_text):
        raise core.DeapiError("The text has %d characters; %s needs at least %d." % (
            len(text), slug, int(min_text)), core.EXIT_USAGE, chars=len(text),
            min_text=int(min_text))

    # Language: the model's own spelling (en-us, English, pl ...).
    entry = _language_entry(model, iso)
    if iso and model.get("languages") and entry is None:
        raise core.DeapiError("%s does not offer language %s (it has: %s)." % (
            slug, iso, ", ".join(str(e.get("slug")) for e in model["languages"])),
            core.EXIT_VALIDATION, "Omit --model to pick a model with this language, or run "
            "`deapi.py tts --list-voices --lang %s`." % iso)

    voice = None
    if mode == "custom_voice":
        if args.voice:
            voice = _find_voice(entry, args.voice) if entry else None
            others = _language_entries(model, iso) if iso else model.get("languages") or []
            for other in others if voice is None else []:
                voice = _find_voice(other, args.voice)
                if voice:
                    entry = other
                    core.log("note: voice %s speaks %s; using that language"
                             % (voice, other.get("slug")))
                    break
            if voice is None and (model.get("languages") or entry):
                names = [v.get("slug") for v in _voices(entry)]
                raise core.DeapiError("Voice %s is not available for %s in %s%s." % (
                    args.voice, slug, (entry or {}).get("slug") or iso or "its languages",
                    " (try: %s)" % ", ".join(names[:12]) if names else ""), core.EXIT_USAGE,
                    "Run `deapi.py tts --list-voices%s`." % (" --lang " + iso if iso else ""))
            voice = voice or args.voice
        else:
            voices = _voices(entry)
            if args.gender:
                matching = [v for v in voices if str(v.get("gender") or "").lower() == args.gender]
                if not matching:
                    raise core.DeapiError("%s has no %s preset voice in %s." % (
                        slug, args.gender, (entry or {}).get("slug")), core.EXIT_USAGE)
                voice = matching[0].get("slug")
            else:
                slugs = [v.get("slug") for v in voices]
                fallback = core.default(model, "voice")
                voice = fallback if (not slugs or fallback in slugs) else slugs[0]
    lang = (entry or {}).get("slug") or core.default(model, "lang") or iso or "en-us"

    speed = args.speed if args.speed is not None else core.default(model, "speed", 1)
    speed = core.clamp_param(model, "speed", float(speed), "--speed")
    fmt = _pick_format(model, args.format)
    rates = core.limit(model, "available_ratios") or []
    rate = args.sample_rate or core.default(model, "sample_rate") or \
        (rates[0] if rates else 24000)
    if rates and int(rate) not in [int(r) for r in rates]:
        raise core.DeapiError("%s supports sample rates %s (got %s)." % (
            slug, ", ".join(str(r) for r in rates), rate), core.EXIT_USAGE)

    fields = {"text": text, "model": slug, "mode": mode, "lang": lang, "speed": speed,
              "format": fmt, "sample_rate": int(rate)}
    if voice:
        fields["voice"] = voice
    files = []
    if mode == "voice_clone":
        part = core.resolve_input(ctx, args.clone_audio, "voice_ref", "ref_audio")
        _check_length(model, part, "--clone-audio", "a reference recording of")
        files.append(part)
        if args.ref_text:
            fields["ref_text"] = args.ref_text
    elif mode == "voice_design":
        fields["instruct"] = args.design
    # The quote is JSON without the recording or its transcript (PriceRequestBody).
    quote = core.pick(fields, "text", "model", "mode", "voice", "lang", "speed", "format",
                      "sample_rate", "instruct")
    job = core.Job("tts", "/api/v2/audio/speech", model, fields=fields, files=files,
                   quote_json=quote, seed_field=None, default_ext=fmt, output=args.output,
                   info={"mode": mode, "lang": lang, "voice": voice, "chars": len(text)})
    return ctx.run(job, args)


# ------------------------------------------------------------- music ---

def _music_steps(model, given, name, label, defaults, cast):
    """Steps/guidance: pinned values win, others are clamped to the card."""
    fixed = core.pinned(model, name)
    if fixed is not None:
        if given is not None and cast(given) != cast(fixed):
            core.log("note: %s %s replaced by %s (%s fixes it)" % (
                label, _num(given), _num(fixed), model["slug"]))
        return cast(fixed)
    value = given
    if value is None:
        for key in defaults:
            value = core.default(model, key)
            if value is not None:
                break
    if value is None:
        value = core.limit(model, "min_" + name)
    if value is None:
        return None
    return cast(core.clamp_param(model, name, cast(value), label))


def cmd_music(ctx, args):
    model = ctx.pick_model("music", args.model, args.allow_partner_models)
    slug = model["slug"]
    caption = (args.caption or "").strip()
    low, high = core.limit(model, "min_caption"), core.limit(model, "max_caption")
    if (low and len(caption) < int(low)) or (high and len(caption) > int(high)):
        raise core.DeapiError("--caption has %d characters; %s accepts %s." % (
            len(caption), slug, _range_text(low, high, " characters")), core.EXIT_USAGE)
    lyrics = _read_text(args.lyrics, args.lyrics_file, "--lyrics", "--lyrics-file",
                        required=False, allow_hidden=ctx.allow_hidden_path)
    if lyrics and args.instrumental:
        raise core.DeapiError("Use either --lyrics or --instrumental, not both.",
                              core.EXIT_USAGE)
    fmt = _pick_format(model, args.format)

    low, high = core.limit(model, "min_duration"), core.limit(model, "max_duration")
    by_format = core.limit(model, "max_duration_by_format") or {}
    if isinstance(by_format, dict) and by_format.get(fmt) is not None:
        high = min(float(high), float(by_format[fmt])) if high is not None \
            else float(by_format[fmt])
    if args.duration is not None:
        duration = float(args.duration)
        if (low is not None and duration < float(low)) or \
                (high is not None and duration > float(high)):
            raise core.DeapiError("--duration %s s is outside %s's range %s for %s." % (
                _num(duration), slug, _range_text(low, high, " s"), fmt), core.EXIT_USAGE,
                min_duration=low, max_duration=high)
    else:
        duration = float(core.default(model, "duration", 30))
        duration = max(float(low or duration), min(float(high or duration), duration))

    steps = _music_steps(model, args.steps, "steps", "--steps",
                         ("inference_steps", "steps"), int)
    guidance = _music_steps(model, args.guidance, "guidance", "--guidance",
                            ("guidance_scale", "guidance"), float)
    fields = {
        "caption": caption,
        "model": slug,
        "lyrics": lyrics or "[Instrumental]",
        "duration": duration,
        "inference_steps": steps if steps is not None else 8,
        "guidance_scale": guidance if guidance is not None else 1.0,
        "seed": args.seed if args.seed is not None else random.randint(0, 2 ** 31 - 1),
        "format": fmt,
    }
    if args.bpm is not None:
        low, high = core.limit(model, "min_bpm"), core.limit(model, "max_bpm")
        if (low is not None and args.bpm < low) or (high is not None and args.bpm > high):
            raise core.DeapiError("--bpm %d is outside %s's range %s." % (
                args.bpm, slug, _range_text(low, high)), core.EXIT_USAGE)
        fields["bpm"] = args.bpm
    if args.keyscale:
        fields["keyscale"] = args.keyscale.strip()
    if args.time_signature:
        fields["timesignature"] = args.time_signature
    if args.vocal_lang:
        if not lyrics:
            core.log("note: --vocal-lang has no effect on an instrumental")
        fields["vocal_language"] = core.check_iso_language(args.vocal_lang, "--vocal-lang")
    files = []
    if args.reference_audio:
        part = core.resolve_input(ctx, args.reference_audio, "music_ref", "reference_audio")
        _check_length(model, part, "--reference-audio", "a reference track of")
        files.append(part)
    # The quote validates every optional field it knows; seed, format and the
    # reference file are not part of the price body.
    quote = core.pick(fields, "model", "duration", "inference_steps", "guidance_scale",
                      "bpm", "caption", "lyrics", "keyscale", "timesignature",
                      "vocal_language")
    job = core.Job("music", "/api/v2/audio/music", model, fields=fields, files=files,
                   quote_json=quote, default_ext=fmt, output=args.output,
                   info={"duration": duration, "instrumental": not lyrics})
    return ctx.run(job, args)


# -------------------------------------------------------- transcribe ---

def _host(url):
    match = re.match(r"^https?://([^/:?#@]+)", url, re.I)
    return (match.group(1) if match else "").lower()


def _transcribe_model(ctx, args, lang):
    require = ["supports_diarization"] if args.diarize else None

    def fits(model):
        levels = core.limit(model, "timestamp_levels") or []
        if args.ts_level and levels and args.ts_level not in levels:
            return False
        languages = core.limit(model, "languages") or []
        if lang and languages and lang not in [str(x).lower() for x in languages]:
            return False
        return True

    why = []
    if args.diarize:
        why.append("speaker labels")
    if args.ts_level:
        why.append("%s timestamps" % args.ts_level)
    if lang:
        why.append("language %s" % lang)
    model = ctx.pick_model("transcribe", args.model, args.allow_partner_models,
                           require=require, predicate=None if args.model else fits,
                           why=", ".join(why) or None)
    slug = model["slug"]
    levels = core.limit(model, "timestamp_levels") or []
    if args.ts_level and levels and args.ts_level not in levels:
        raise core.DeapiError("%s offers timestamp levels: %s (got --ts-level %s)." % (
            slug, ", ".join(levels), args.ts_level), core.EXIT_USAGE,
            "Omit --model to pick a model with %s timestamps." % args.ts_level)
    languages = [str(x).lower() for x in core.limit(model, "languages") or []]
    if lang and languages and lang not in languages:
        raise core.DeapiError("%s does not transcribe language %s (it has: %s)." % (
            slug, lang, ", ".join(languages)), core.EXIT_VALIDATION)
    if lang and core.limit(model, "output_format") != "json" and "ct2" not in slug.lower():
        core.log("note: %s may ignore --lang; the default WhisperLargeV3Ct2 honours it" % slug)
    if args.subtitles and core.limit(model, "output_format") != "json":
        core.log("note: %s returns plain text; subtitles are built from its inline "
                 "timestamps when present" % slug)
    return model


def cmd_transcribe(ctx, args):
    given = [v for v in (args.source, args.url, args.file) if v]
    if len(given) != 1:
        raise core.DeapiError("Give exactly one source: a link or a file.", core.EXIT_USAGE)
    source = given[0]
    if args.duration is not None and args.duration <= 0:
        raise core.DeapiError("--duration must be a positive number of seconds.",
                              core.EXIT_USAGE)
    lang = core.check_iso_language(args.lang) if args.lang else None
    platform = core.detect_platform(source) if core.is_url(source) else None
    if core.is_url(source) and not platform and not _host(source).endswith(".deapi.ai"):
        raise core.DeapiError(
            "Links are accepted from YouTube, X (posts and Spaces), Twitch, Kick and TikTok "
            "only. Download the file and pass it instead, or use the deAPI MCP tools, which "
            "accept file URLs.", core.EXIT_USAGE)
    if platform == "tiktok" and not args.duration:
        # TikTok cannot be priced from the link and no safe upper bound exists.
        raise core.DeapiError(
            "TikTok links cannot be priced from the link. Ask the user how long the video "
            "is and pass --duration SECONDS.", core.EXIT_USAGE,
            "Do not guess the length: the quote, and the cost limit, depend on it.")

    model = _transcribe_model(ctx, args, lang)
    options = {"model": model["slug"]}
    if lang:
        options["lang"] = lang
    if args.timestamps or args.ts_level or args.diarize or args.subtitles:
        options["include_ts"] = True
    if args.ts_level:
        options["ts_level"] = args.ts_level
    if args.diarize:
        options["diarize"] = True

    files, info = [], {}
    if platform:
        if args.metadata:
            options["include_metadata"] = True
        fields = dict(options, source_url=source)
        if platform == "tiktok":
            # Only TikTok is priced by a length the user gives.
            quote_fields = dict(options, duration_seconds=args.duration, platform=platform)
            info["duration_seconds"] = args.duration
            info["duration_from"] = "--duration"
        else:
            # The API reads the length from the link; a smaller --duration must not
            # lower the quote that the cost limit is checked against.
            if args.duration:
                core.log("note: --duration is ignored for %s links; deAPI prices them "
                         "from the link" % platform)
            quote_fields = fields
        quote_files = []
        info["platform"] = platform
    else:
        if args.metadata:
            core.log("note: --metadata applies to links only; ignored for a file")
        part = core.resolve_input(ctx, source, "media", "source_file")
        files = [part]
        fields = dict(options)
        # The measured length wins: --duration may fill a gap or raise the quote,
        # never lower it below what the file really holds.
        measured = media_length(part.path, part.fmt)
        seconds, basis = measured, "file header"
        if args.duration and not measured:
            seconds, basis = args.duration, "--duration"
        elif args.duration and measured and abs(args.duration - measured) > 1:
            core.warn("--duration %g differs from the file's length %.1f s; quoting the "
                      "longer one" % (args.duration, measured))
            if args.duration > measured:
                seconds, basis = args.duration, "--duration"
        if seconds:
            quote_fields, quote_files = dict(options, duration_seconds=round(seconds, 2)), []
            info["duration_seconds"] = round(seconds, 2)
            info["duration_from"] = basis
        else:
            core.log("note: the length of %s is unknown, so the file is uploaded twice (for "
                     "the free quote and for the job); pass --duration SECONDS or install "
                     "ffprobe to avoid that" % part.public())
            quote_fields, quote_files = fields, files
    fields = dict(fields, return_result_in_response=True)
    job = core.Job("transcribe", "/api/v2/audio/transcriptions", model, fields=fields,
                   files=files, quote_fields=quote_fields, quote_files=quote_files,
                   seed_field=None, text_ext="auto", output=args.output, info=info or None)
    try:
        result = ctx.run(job, args)
    except core.DeapiError as exc:
        if platform and platform != "tiktok" and exc.code == core.EXIT_VALIDATION \
                and "metadata" in exc.message.lower():
            exc.hint = ("deAPI could not read the link's details. Check that it is public; "
                        "if it is, download the file and pass the file instead.")
        raise
    if getattr(args, "quote", False):
        return result
    return _finish_transcript(ctx, args, result)


# ---------------------------------------------------------- transcript ---

def _finish_transcript(ctx, args, result):
    path = result.get("text_file")
    if not path:
        return result
    try:
        with io.open(path, "r", encoding="utf-8") as handle:
            raw = handle.read()
    except OSError:
        return result
    doc = _transcript_doc(raw)
    if doc is not None:
        text = doc.get("text") or ""
        result["preview"] = core.preview(text)
        result["text_chars"] = len(text)
        if doc.get("language"):
            result["language"] = doc.get("language")
        segments = doc.get("segments") if isinstance(doc.get("segments"), list) else []
        result["segments"] = len(segments)
        speakers = sorted({s.get("speaker") for s in segments
                           if isinstance(s, dict) and s.get("speaker")})
        if speakers:
            result["speakers"] = speakers
    else:
        text = raw
    if not text.strip():
        core.warn("the transcript is empty (silence or music only?)")
    if args.subtitles:
        cues = subtitle_cues(doc if doc is not None else raw)
        if not cues:
            core.warn("no timed segments in the result, so no %s file was written"
                      % args.subtitles)
        else:
            body = to_srt(cues) if args.subtitles == "srt" else to_vtt(cues)
            explicit = None
            if args.output:
                explicit = os.path.splitext(args.output)[0] + "." + args.subtitles
            name = os.path.splitext(os.path.basename(path))[0]
            result["subtitles_file"] = ctx.write_text(name, body, args.subtitles, explicit)
            result["subtitle_cues"] = len(cues)
    return result


def _transcript_doc(raw):
    """The structured transcript (dict with `text`) or None for plain text."""
    text = (raw or "").lstrip()
    if not text.startswith("{"):
        return None
    try:
        doc = json.loads(text)
    except ValueError:
        return None
    if isinstance(doc, dict) and isinstance(doc.get("text"), str):
        return doc
    return None


_INLINE_TS = re.compile(r"^\s*\[(\d+(?::\d{1,2}){1,2}(?:[.,]\d+)?)\s*-+>?\s*"
                        r"(\d+(?::\d{1,2}){1,2}(?:[.,]\d+)?)\]\s*(.*)$")


def _clock(value):
    seconds = 0.0
    for part in value.replace(",", ".").split(":"):
        seconds = seconds * 60 + float(part)
    return seconds


def _seconds(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def subtitle_cues(doc_or_text):
    """[(start, end, text, speaker)] from a JSON transcript or `[m:ss - m:ss] text` lines."""
    cues = []
    if isinstance(doc_or_text, dict):
        for seg in doc_or_text.get("segments") or []:
            if not isinstance(seg, dict):
                continue
            start, end = _seconds(seg.get("start")), _seconds(seg.get("end"))
            text = " ".join(str(seg.get("text") or "").split())
            if start is None or end is None or not text:
                continue
            speaker = seg.get("speaker")
            words = [w for w in seg.get("words") or [] if isinstance(w, dict)
                     and _seconds(w.get("start")) is not None
                     and _seconds(w.get("end")) is not None and str(w.get("word", "")).strip()]
            if len(text) > SUBTITLE_CUE and words:
                cues.extend(_split_words(words, speaker))
            else:
                cues.append((start, max(end, start), text, speaker))
        return cues
    for line in str(doc_or_text or "").splitlines():
        match = _INLINE_TS.match(line)
        if match and match.group(3).strip():
            start, end = _clock(match.group(1)), _clock(match.group(2))
            cues.append((start, max(start, end), " ".join(match.group(3).split()), None))
    return cues


def _split_words(words, speaker):
    cues, chunk = [], []
    for word in words:
        candidate = " ".join([str(w["word"]).strip() for w in chunk] + [str(word["word"]).strip()])
        if chunk and len(candidate) > SUBTITLE_CUE:
            cues.append(_word_cue(chunk, speaker))
            chunk = []
        chunk.append(word)
    if chunk:
        cues.append(_word_cue(chunk, speaker))
    return cues


def _word_cue(chunk, speaker):
    text = " ".join(str(w["word"]).strip() for w in chunk)
    start, end = float(chunk[0]["start"]), float(chunk[-1]["end"])
    return (start, max(start, end), text, chunk[0].get("speaker") or speaker)


def _stamp(seconds, separator):
    millis = int(round(seconds * 1000))
    hours, rest = divmod(millis, 3600000)
    minutes, rest = divmod(rest, 60000)
    secs, millis = divmod(rest, 1000)
    return "%02d:%02d:%02d%s%03d" % (hours, minutes, secs, separator, millis)


def _wrap(text):
    lines = textwrap.wrap(text, SUBTITLE_LINE) or [text]
    if len(lines) > 2:   # keep two lines; the second takes the rest
        lines = [lines[0], " ".join(lines[1:])]
    return "\n".join(lines)


def to_srt(cues):
    blocks = []
    for number, (start, end, text, speaker) in enumerate(cues, 1):
        label = "%s: %s" % (speaker, text) if speaker else text
        blocks.append("%d\n%s --> %s\n%s\n" % (number, _stamp(start, ","), _stamp(end, ","),
                                              _wrap(label)))
    return "\n".join(blocks)


def to_vtt(cues):
    blocks = ["WEBVTT\n"]
    for start, end, text, speaker in cues:
        body = _wrap(text).replace("-->", "->")
        if speaker:
            body = "<v %s>%s" % (re.sub(r"[<>&]", "_", str(speaker)), body)
        blocks.append("%s --> %s\n%s\n" % (_stamp(start, "."), _stamp(end, "."), body))
    return "\n".join(blocks)
