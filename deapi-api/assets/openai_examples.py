#!/usr/bin/env python3
"""deAPI through the official OpenAI Python SDK (pip install openai).

Tested with openai 3.46.0; `video` needs a release that has client.videos (not 1.x).

    export DEAPI_API_KEY=sk-dp-...
    python openai_examples.py models                                 # free
    python openai_examples.py image "a red fox in the snow" fox.png  # paid
    python openai_examples.py speech "Hello from deAPI." hello.mp3   # paid
    python openai_examples.py transcribe meeting.mp3                 # paid
    python openai_examples.py embed "first text" "second text"       # paid
    python openai_examples.py video "a paper boat in the rain" boat.mp4   # paid

This API has no price quotes; quote the same job on the native API (POST .../price) when the
cost matters. Model IDs are native deAPI IDs and depend on the account: see `models`.
DEAPI_OAI_URL (default https://oai.deapi.ai/v1) may name another *.deapi.ai host or a local mock.
"""

import base64
import os
import sys
import time
import urllib.parse
import uuid

from openai import OpenAI


def make_client():
    base_url = os.environ.get("DEAPI_OAI_URL", "https://oai.deapi.ai/v1")
    parts = urllib.parse.urlsplit(base_url)
    host = (parts.hostname or "").lower()
    if not ((parts.scheme == "https" and host.endswith(".deapi.ai")) or
            (parts.scheme == "http" and host in ("127.0.0.1", "localhost"))):
        raise SystemExit("refusing to send the API key to %r" % host)
    key = os.environ.get("DEAPI_API_KEY")
    if not key:
        raise SystemExit("set DEAPI_API_KEY")
    # A second client next to any OpenAI client of the app; do not set OPENAI_BASE_URL globally.
    return OpenAI(api_key=key, base_url=base_url,
                  timeout=900.0)   # jobs hold the connection until they finish


def list_models(client):
    return [m.id for m in client.models.list().data]


def image(client, prompt, out, model="Flux1schnell", size="1024x1024"):
    result = client.images.generate(model=model, prompt=prompt, size=size, n=1,
                                    response_format="b64_json")   # PNG; a url would expire
    with open(out, "xb") as handle:
        handle.write(base64.b64decode(result.data[0].b64_json))
    return out


def speech(client, text, out, model="Kokoro", voice="af_nova"):
    audio = client.audio.speech.create(model=model, voice=voice, input=text,
                                       response_format="mp3")
    with open(out, "xb") as handle:
        handle.write(audio.content)
    return out


def transcribe(client, path, model="WhisperLargeV3Ct2", language=None):
    extra = {"language": language} if language else {}
    with open(path, "rb") as handle:
        result = client.audio.transcriptions.create(model=model, file=handle,
                                                    response_format="verbose_json", **extra)
    return result.text


def embed(client, texts, model="Bge_M3_FP16"):
    # At most 100 items per call, and each item counts as one request against the rate limit.
    result = client.embeddings.create(model=model, input=list(texts))
    return [item.embedding for item in result.data]


def video(client, prompt, out, model="Ltx2_3_22B_Dist_INT8", seconds="5", size="768x768",
          poll_seconds=5.0, sleep=time.sleep):
    # The Idempotency-Key makes a retried create return the same job instead of a second one.
    job = client.videos.create(model=model, prompt=prompt, seconds=seconds, size=size,
                               extra_headers={"Idempotency-Key": str(uuid.uuid4())})
    while job.status not in ("completed", "failed"):
        sleep(poll_seconds)
        job = client.videos.retrieve(job.id)
    if job.status == "failed":
        raise RuntimeError("video %s failed: %s" % (job.id, getattr(job, "error", None)))
    content = client.videos.download_content(job.id)
    with open(out, "xb") as handle:
        handle.write(content.content)
    return out


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    client = make_client()
    command, args = argv[0], argv[1:]
    if command == "models":
        print("\n".join(list_models(client)))
    elif command == "image":
        print(image(client, args[0], args[1]))
    elif command == "speech":
        print(speech(client, args[0], args[1]))
    elif command == "transcribe":
        print(transcribe(client, args[0]))
    elif command == "embed":
        vectors = embed(client, args)
        print("%d vectors of %d dimensions" % (len(vectors), len(vectors[0]) if vectors else 0))
    elif command == "video":
        print(video(client, args[0], args[1]))
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
