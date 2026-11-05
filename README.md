# deAPI Skills

Agent skills for [deAPI](https://deapi.ai): let AI agents transcribe audio and video links,
generate images, speech, music and video, edit images, read text from images, remove
backgrounds, upscale and create embeddings, and help developers build the same into their apps.

The skills follow the [agentskills.io](https://agentskills.io) open standard, so they run in
Claude Code, Codex, Cursor, Hermes Agent, OpenClaw, Goose, OpenCode, GitHub Copilot and other
agents that support skills.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

## Coming from `deapi-ai/claude-code-skills`?

This repository **is** the former `deapi-ai/claude-code-skills` (renamed; old links redirect here).
The old Claude Code skill used the deprecated deAPI API v1. It is kept for reference at the
[`v1-final`](https://github.com/deapi-ai/skills/tree/v1-final) tag and is no longer maintained.

To switch:
1. Remove the old copy: `~/.claude/skills/deapi` and any deAPI command files you copied
   (`~/.claude/commands/`, `.cursor/commands/`, `.windsurf/workflows/`).
2. Install the current skills (see [Install](#3-install)).
3. Keep your `DEAPI_API_KEY`. It works with the new skills.

The slash commands (`/transcribe`, `/generate-image`, ...) are gone. Ask your agent in plain
language ("transcribe this video", "generate an image of ...") and the `deapi` skill handles it.
The skill folder is still `deapi/` at the repository root.

| Old command | Now |
|---|---|
| `/transcribe` | `deapi transcribe` |
| `/generate-image` | `deapi image` |
| `/generate-audio` | `deapi tts` |
| `/generate-video` | `deapi video`, `deapi animate` |
| `/ocr` | `deapi ocr` |
| `/remove-bg` | `deapi rmbg` |
| `/upscale` | `deapi upscale` |
| `/transform-image` | `deapi edit` |
| `/embed` | `deapi embed` |
| `/deapi-setup` (webhooks, WebSockets) | the `deapi-api` skill |
| `/deapi-balance` | `deapi account` |
| `deapi-audio` skill | `deapi tts`, `deapi transcribe` |

## Skills

| Skill | Use it to | How it works |
|---|---|---|
| **[deapi](deapi)** | Get something done now: transcribe a file or a YouTube/X/TikTok/Twitch/Kick link, generate or edit images, speech, music and video, OCR, background removal, upscaling, embeddings, prompt boosting, job status, account and key limits. | A helper script (Python 3.9+, standard library only) or the deAPI MCP tools when they are connected. Every paid job is priced first and stopped above a cost limit (0.25 USD by default). |
| **[deapi-api](deapi-api)** | Build deAPI into an application: the native API v2, jobs and webhooks, WebSockets, limits and errors, keys and usage, and OpenAI SDKs pointed at `https://oai.deapi.ai/v1`. | Reference and tested code examples; it runs nothing on its own. |

## Setup

### 1. Get an API key

Sign up at [deapi.ai](https://deapi.ai) and create a key at [app.deapi.ai](https://app.deapi.ai)
(Settings > API Keys). Keys look like `sk-dp-<id>-<secret>`; use them as they are.

For an agent, create a dedicated key with the **API** role and a **daily spend limit**. The limit
is enforced by deAPI itself, whatever the agent does.

### 2. Set the key

The key is read from the `DEAPI_API_KEY` environment variable. Never paste it into the chat.

```bash
export DEAPI_API_KEY="sk-dp-..."     # shell profile; Hermes: a line in ~/.hermes/.env
```

### 3. Install

**Any agent, via [skills.sh](https://skills.sh):**

```bash
npx skills add deapi-ai/skills
```

**Claude Code** (manual):

```bash
git clone https://github.com/deapi-ai/skills.git
mkdir -p ~/.claude/skills && cp -r skills/deapi skills/deapi-api ~/.claude/skills/
```

**Hermes Agent:**

```bash
hermes skills install deapi-ai/skills/deapi
```

**Codex, Cursor, Goose, OpenCode, VS Code Copilot** (shared `~/.agents/skills` convention):

```bash
git clone https://github.com/deapi-ai/skills.git
mkdir -p ~/.agents/skills && cp -r skills/deapi skills/deapi-api ~/.agents/skills/
```

The `deapi` skill needs Python 3.9 or newer (the version macOS ships) and network access to
`api.deapi.ai`. On Windows use `python` or `py` instead of `python3`.

### 4. Use

Ask your agent naturally: "transcribe this YouTube video, it's in English", "generate an image
of a red fox", "read the text in scan.png". The helper script also works on its own:

```bash
python3 deapi/scripts/deapi.py doctor
python3 deapi/scripts/deapi.py transcribe "https://www.youtube.com/watch?v=..."
python3 deapi/scripts/deapi.py image --prompt "a red fox, studio light" --quote
python3 deapi/scripts/deapi.py models --type image
python3 deapi/scripts/deapi.py account
```

`--quote` asks deAPI for the price and submits nothing. Without it the script quotes first and
stops with exit code 5 when the price is above the cost limit. Only you raise the limit, with
`--max-cost` or the `DEAPI_MAX_COST` environment variable.

### Commands of the `deapi` skill

| Command | What it does | Cost |
|---|---|---|
| `transcribe` (alias `stt`) | Transcript of a file or a YouTube/X/Twitch/Kick/TikTok link; timestamps, speakers, `--subtitles srt\|vtt` | paid |
| `image`, `edit` | Image from text; edit one or more images with a prompt | paid |
| `ocr`, `rmbg`, `upscale` | Text from an image; background removal; image upscaling | paid |
| `video`, `animate` | Video from text; animate an image (first/last frame or references) | paid |
| `audio-video`, `video-upscale` | Video that follows an audio track; video upscaling | paid |
| `describe` | Scenes and events of a video file or link | paid |
| `tts`, `music` | Speech (preset voice, voice clone, voice design); music with or without lyrics | paid |
| `embed`, `boost` | Text embeddings; a better generation prompt | paid |
| `models`, `status`, `account` (alias `balance`), `doctor` | Model catalog, job status and download, key limits and usage, setup check | free |

Every paid command takes `--quote` (free price, nothing submitted), `--max-cost`, `--model`,
`--output`. `deapi.py <command> --help` lists all flags with examples; the full reference is
[deapi/references/commands.md](deapi/references/commands.md).

## Platforms

| Platform | Install | Runs the job via | Where the key goes | Tested |
|---|---|---|---|---|
| Claude Code | `npx skills add` or copy to `~/.claude/skills` | script, or MCP tools if connected | `DEAPI_API_KEY` in the environment | not yet |
| Codex CLI | `npx skills add` or `~/.agents/skills` | script, or MCP tools if connected | `DEAPI_API_KEY`; MCP: `bearer_token_env_var` | not yet |
| Cursor | `npx skills add` or `~/.agents/skills` | script | `DEAPI_API_KEY` | not yet |
| Hermes Agent | `hermes skills install deapi-ai/skills/deapi` | script, or MCP tools if connected | `~/.hermes/.env` | not yet |
| OpenClaw | ClawHub or copy the folder | script | `DEAPI_API_KEY` | not yet |
| Windsurf, Continue.dev | copy the folder where the agent reads skills | script | `DEAPI_API_KEY` | not yet |
| claude.ai, ChatGPT and other chat apps | not supported by the skills | use the deAPI MCP connector | OAuth or the connector's settings | — |

"Tested" is filled in after a manual run on each platform (install, the skill triggers,
`doctor`, and `--quote` for three commands). On Windows, agents that look for `python3` may
need `python` or `py` instead.

## Where your data goes

- **To deAPI, over HTTPS:** the prompts and parameters of a job and the files you point the
  agent at go to `api.deapi.ai`. Results come back from deAPI result links, which the script
  downloads without your key. With the MCP tools the same data goes to `mcp.deapi.ai`; apps
  built with the OpenAI SDKs talk to `oai.deapi.ai`.
- **Your key** is read from `DEAPI_API_KEY` and sent only to `*.deapi.ai` hosts; the script
  refuses any other base URL and never lets the key follow a redirect. It is never written to
  disk or printed.
- **Your files:** only the files you point the agent at are sent. Hidden files and folders
  such as `.ssh`, `.aws` or `.env` are refused, also for text inputs and output paths, unless
  you name the file and the agent adds `--allow-hidden-path`.
- **Where jobs run:** deAPI runs models on a decentralized GPU network. Do not send recordings,
  documents or images that hold sensitive personal data or trade secrets; see
  [Architecture and security](https://docs.deapi.ai/architecture-and-security).
- **Nothing else:** no telemetry or analytics. Each request carries the header
  `User-Agent: deapi-skill/<version>`. Results are saved in the folder you choose (`--out-dir`).

## Other ways to use deAPI from an agent

- **MCP server:** `https://mcp.deapi.ai/mcp` exposes the same jobs as MCP tools
  ([deapi-ai/mcp-server-deapi](https://github.com/deapi-ai/mcp-server-deapi)). Use it in
  clients without a terminal, such as claude.ai.
- **OpenAI-compatible API:** `https://oai.deapi.ai/v1` works with OpenAI SDKs for images,
  speech, transcriptions, embeddings and videos.
- **Native REST API:** [docs.deapi.ai](https://docs.deapi.ai)
  (OpenAPI: [openapi.json](https://docs.deapi.ai/openapi.json)).
- **Python SDK:** [deapi-ai/deapi-python-sdk](https://github.com/deapi-ai/deapi-python-sdk)
  wraps every endpoint, price quotes, polling, webhook verification and WebSockets
  (Python 3.11+, installed from GitHub: `pip install "git+https://github.com/deapi-ai/deapi-python-sdk.git@v0.5.2"`;
  `pip install deapi` is an unrelated package).

## Repository layout

```
deapi/                 the "do it now" skill: SKILL.md, scripts/deapi.py, references/
deapi-api/             the "build it into an app" skill: SKILL.md, references/, assets/
tests/                 offline unit tests, synthetic fixtures, Dockerfile
tools/                 repository checks (frontmatter, OpenAPI, MCP names, leak scan)
skills.sh.json         skill groupings for skills.sh and the Hermes Skills Hub
```

Contributing: see [AGENTS.md](AGENTS.md).

## License

[MIT](LICENSE)
