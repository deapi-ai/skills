# deAPI Skills

[![CI](https://github.com/deapi-ai/skills/actions/workflows/ci.yml/badge.svg)](https://github.com/deapi-ai/skills/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Agent skills for [deAPI](https://deapi.ai). With them an AI agent can transcribe recordings and
video links, generate and edit images, create speech, music and video, read text from images,
remove backgrounds, upscale and compute embeddings, and help developers build all of this into
their own apps.

The skills follow the [agentskills.io](https://agentskills.io) open standard, so the same folder
works in Claude Code, Codex, Cursor, Hermes Agent, OpenClaw, Goose, OpenCode, GitHub Copilot and
other agents that support skills.

## Quick start

```bash
export DEAPI_API_KEY="sk-dp-..."      # create a key at https://app.deapi.ai
npx skills add deapi-ai/skills        # or see "Install" below for your agent
```

Then ask your agent, for example:

- "Transcribe this YouTube video, it's in English: https://www.youtube.com/watch?v=..."
- "Generate a 1024x768 image of a red fox in studio light"
- "Read this text aloud in my voice, the sample is voice.wav"
- "How do I verify a deAPI webhook signature in Node?"

Every paid job is priced first, for free, and the agent stops and asks you when the price is
above your limit (0.25 USD per job by default).

## The two skills

| Skill | Use it to | How it works |
|---|---|---|
| **[deapi](deapi)** | Get something done now: transcribe or subtitle a file or a YouTube, X, TikTok, Twitch or Kick link; generate, edit and upscale images; remove backgrounds; read text from images (OCR); turn text or a photo into video and describe videos; speech with preset voices, a cloned voice or a designed voice; music; embeddings; better prompts; job status; account and key limits. | A helper script (`deapi/scripts/deapi.py`, Python 3.9+, standard library only), or the deAPI MCP tools when they are connected. |
| **[deapi-api](deapi-api)** | Build deAPI into an application: the native API v2, async jobs and webhooks, WebSockets, limits and errors, keys and usage, and OpenAI SDKs pointed at `https://oai.deapi.ai/v1`. | Reference pages and tested code examples (Python, TypeScript, Node, curl). It runs nothing on its own. |

## Install

### 1. Get an API key

Sign up at [deapi.ai](https://deapi.ai) and create a key at [app.deapi.ai](https://app.deapi.ai)
(Settings > API Keys). Keys look like `sk-dp-<id>-<secret>`; use them as they are.

For an agent, create a dedicated key with the **API** role and a **daily spend limit**. deAPI
enforces that limit itself, whatever the agent does.

### 2. Make the key available

The skills read the key from the `DEAPI_API_KEY` environment variable. Never paste the key into
the chat.

```bash
export DEAPI_API_KEY="sk-dp-..."      # add it to your shell profile
```

### 3. Install the skills

**Any agent, via [skills.sh](https://skills.sh):**

```bash
npx skills add deapi-ai/skills
```

**Claude Code** (manual):

```bash
git clone https://github.com/deapi-ai/skills.git
mkdir -p ~/.claude/skills && cp -r skills/deapi skills/deapi-api ~/.claude/skills/
```

To let Claude Code run the helper without asking each time, allow only that script in
`settings.json`, for example `"allow": ["Bash(python /path/to/deapi/scripts/deapi.py *)"]` with
the path where you installed the skill. The skill tells the agent to run one command per call,
so the rule matches.

**Hermes Agent:**

```bash
hermes skills install deapi-ai/skills/deapi
```

Hermes does not ask for the key when it loads the skill. Add it to the Hermes `.env` file
(`~/.hermes/.env` by default; `hermes config env-path` prints the path), which Hermes passes to
the terminal where the script runs:

```bash
DEAPI_API_KEY=sk-dp-...
```

If your deAPI account has access to chat models, Hermes can also use one as its main model
through the OpenAI-compatible API (`hermes model`, "Custom endpoint", or in `config.yaml`):

```yaml
model:
  provider: custom
  base_url: https://oai.deapi.ai/v1
  key_env: DEAPI_API_KEY
  default: <a chat model ID from GET https://oai.deapi.ai/v1/models>
```

A text-only chat model cannot handle Hermes' image tasks (vision); point `auxiliary.vision` at
another provider if you need them.

**Codex, Cursor, Goose, OpenCode, VS Code Copilot** (shared `~/.agents/skills` convention):

```bash
git clone https://github.com/deapi-ai/skills.git
mkdir -p ~/.agents/skills && cp -r skills/deapi skills/deapi-api ~/.agents/skills/
```

**OpenClaw:** copy the `deapi` folder to the agent's skills folder. The skill declares that it
needs `DEAPI_API_KEY` and one of `python3`, `python` or `py`.

**Requirements:** the `deapi` skill needs Python 3.9 or newer (the version macOS ships) and
network access to `api.deapi.ai`. On Windows, `python` or `py` works too.

## Use

Ask your agent in plain language. You can also run the helper script yourself:

```bash
python3 deapi/scripts/deapi.py doctor                         # check Python, the key and limits
python3 deapi/scripts/deapi.py transcribe "https://www.youtube.com/watch?v=..." --lang en
python3 deapi/scripts/deapi.py image --prompt "a red fox, studio light" --quote
python3 deapi/scripts/deapi.py transcribe --duration 7200 --quote   # price of 2 hours, no file needed
python3 deapi/scripts/deapi.py models --type image
python3 deapi/scripts/deapi.py account
```

| Command | What it does | Cost |
|---|---|---|
| `transcribe` (alias `stt`) | Transcript of a file or a YouTube, X, TikTok, Twitch or Kick link; language, timestamps, speakers, `--subtitles srt\|vtt` | paid |
| `image`, `edit` | Image from text; edit one or more images with a prompt | paid |
| `ocr`, `rmbg`, `upscale` | Text from an image; background removal; image upscaling | paid |
| `video`, `animate` | Video from text; animate an image (first and last frame, or references) | paid |
| `audio-video`, `video-upscale` | Video that follows an audio track; video upscaling | paid |
| `describe` | Scenes and events of a video file or link | paid |
| `tts`, `music` | Speech (preset voice, cloned voice, designed voice); music with or without lyrics | paid |
| `embed`, `boost` | Text embeddings; a better generation prompt | paid |
| `models`, `status`, `account` (alias `balance`), `doctor` | Model catalog, job status and download, key limits and usage, setup check | free |

Every paid command takes `--quote` (free price, nothing submitted), `--max-cost`, `--model` and
`--output`. `deapi.py <command> --help` lists all flags with examples; the full reference,
including exit codes, is in [deapi/references/commands.md](deapi/references/commands.md).

## Cost control

- **Quote first.** Before any paid job the script asks deAPI for the price (free) and prints it
  together with `max_cost`, the limit in force.
- **Limit per job.** Above the limit (0.25 USD by default) the script stops with exit code 5 and
  the agent asks you. Only you raise it, for one job, with `--max-cost` set to the approved price
  or with `DEAPI_MAX_COST`. The skill tells the agent never to raise it on its own.
- **Limit per key.** A daily spend limit on the key (set in the deAPI dashboard) caps everything
  the agent can spend, independent of the skill.
- **No double charges by accident.** A submitted job is retried only when deAPI answers "rate
  limited" (HTTP 429, nothing was charged) or when it fails and deAPI refunds it (once, after a
  new quote).

## Tested platforms

| Platform | Result | Tested on |
|---|---|---|
| Claude Code | The skill loads for media tasks and `deapi-api` for app questions; transcription, image, balance, price-only questions and a job above the limit (the agent stopped and asked) all behaved as described. | Claude Code 2.1, November 2026 |
| Hermes Agent | Balance, image, price-only question and speech with a deAPI chat model as Hermes' main model; the Hermes skill scanner rates both skills safe. | Hermes Agent in Docker, November 2026 |
| OpenClaw | The skill and its requirements are read correctly by the OpenClaw loader and by ClawHub, also when only `python` is installed. | Loader check, November 2026 |
| Codex, Cursor, Goose, OpenCode, Copilot, Windsurf | Expected to work through the same folder and script; not tested yet. | — |
| claude.ai, ChatGPT and other chat apps | Skills that run scripts are not supported there; use the deAPI MCP connector instead. | — |

## Where your data goes

- **To deAPI, over HTTPS:** the prompts and parameters of a job and the files you point the
  agent at go to `api.deapi.ai`. Results come back from deAPI result links, which the script
  downloads without your key. With the MCP tools the same data goes to `mcp.deapi.ai`; apps
  built with the OpenAI SDKs talk to `oai.deapi.ai`.
- **Your key** is read from `DEAPI_API_KEY` and sent only to `*.deapi.ai` hosts; the script
  refuses any other base URL and never lets the key follow a redirect. It is never written to
  disk or printed.
- **Your files:** only the files you point the agent at are sent. Hidden files and folders such
  as `.ssh`, `.aws` or `.env` are refused, also for text inputs and output paths, unless you name
  the file and the agent adds `--allow-hidden-path`.
- **Where jobs run:** deAPI runs models on a decentralized GPU network. Do not send recordings,
  documents or images that hold sensitive personal data or trade secrets; see
  [Architecture and security](https://docs.deapi.ai/architecture-and-security).
- **Nothing else:** no telemetry or analytics. Each request carries the header
  `User-Agent: deapi-skill/<version>`. Results are saved in the folder you choose (`--out-dir`).

## Other ways to use deAPI

- **MCP server:** `https://mcp.deapi.ai/mcp` exposes the same jobs as MCP tools
  ([deapi-ai/mcp-server-deapi](https://github.com/deapi-ai/mcp-server-deapi)). Use it in clients
  without a terminal, such as claude.ai.
- **OpenAI-compatible API:** `https://oai.deapi.ai/v1` works with OpenAI SDKs for images,
  speech, transcriptions, embeddings and videos.
- **Native REST API:** [docs.deapi.ai](https://docs.deapi.ai)
  (OpenAPI: [openapi.json](https://docs.deapi.ai/openapi.json)).
- **Python SDK:** [deapi-ai/deapi-python-sdk](https://github.com/deapi-ai/deapi-python-sdk) wraps
  every endpoint, price quotes, polling, webhook verification and WebSockets. It needs Python
  3.11+ and installs from GitHub:
  `pip install "git+https://github.com/deapi-ai/deapi-python-sdk.git@v0.5.2"`
  (`pip install deapi` is an unrelated package).

## Updates and versions

- Each skill carries its version in `metadata.version` of its `SKILL.md`; releases are tagged
  `vX.Y.Z` with notes in [CHANGELOG.md](CHANGELOG.md) and on the
  [Releases](https://github.com/deapi-ai/skills/releases) page.
- Installed with `npx skills add`: update with `npx skills update`.
- Installed by copying the folder: pull the repository again and copy the folder over the old one.
- If the script exits with code 13 ("endpoint gone"), the skill is outdated: update it.

## Coming from `deapi-ai/claude-code-skills`?

This repository **is** the former `deapi-ai/claude-code-skills` (renamed; old links redirect
here). The old Claude Code commands used the deprecated deAPI API v1. They are kept for reference
at the [`v1-final`](https://github.com/deapi-ai/skills/tree/v1-final) tag and are no longer
maintained.

To switch:

1. Remove the old copy: `~/.claude/skills/deapi` and any deAPI command files you copied
   (`~/.claude/commands/`, `.cursor/commands/`, `.windsurf/workflows/`).
2. Install the current skills (see [Install](#install)).
3. Keep your `DEAPI_API_KEY`; it works with the new skills.

The slash commands are gone. Ask your agent in plain language and the `deapi` skill picks the
right command:

| Old command | Now |
|---|---|
| `/transcribe` | `transcribe` |
| `/generate-image` | `image` |
| `/generate-audio` | `tts` |
| `/generate-video` | `video`, `animate` |
| `/ocr` | `ocr` |
| `/remove-bg` | `rmbg` |
| `/upscale` | `upscale` |
| `/transform-image` | `edit` |
| `/embed` | `embed` |
| `/deapi-setup` (webhooks, WebSockets) | the `deapi-api` skill |
| `/deapi-balance` | `account` |
| `deapi-audio` skill | `tts`, `transcribe` |

## Repository layout

```
deapi/                 the "do it now" skill: SKILL.md, scripts/deapi.py, references/
deapi-api/             the "build it into an app" skill: SKILL.md, references/, assets/
tests/                 offline unit tests, synthetic fixtures, Dockerfile
tools/                 repository checks (frontmatter, OpenAPI, MCP names, skill safety, leak scan)
skills.sh.json         skill groupings for skills.sh and the Hermes Skills Hub
```

Contributing: see [AGENTS.md](AGENTS.md).

## License

[MIT](LICENSE)
