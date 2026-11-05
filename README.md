# deAPI Skills

Agent skills for [deAPI](https://deapi.ai): let AI agents transcribe audio and video links,
generate images, speech, music and video, edit images, read text from images, remove
backgrounds, upscale and create embeddings.

The skills follow the [agentskills.io](https://agentskills.io) open standard, so they run in
Claude Code, Codex, Cursor, Hermes Agent, OpenClaw, Goose, OpenCode, GitHub Copilot, Windsurf
and other agents that support skills.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

## Coming from `deapi-ai/claude-code-skills`?

This repository **is** the former `deapi-ai/claude-code-skills` (renamed; old links redirect here).
The old Claude Code skill used the deprecated deAPI API v1. It is kept for reference at the
[`v1-final`](https://github.com/deapi-ai/skills/tree/v1-final) tag and is no longer maintained.

To switch:
1. Remove the old copy: `~/.claude/skills/deapi` and any deAPI command files you copied
   (`~/.claude/commands/`, `.cursor/commands/`, `.windsurf/workflows/`).
2. Install the current skill (see [Install](#3-install)).
3. Keep your `DEAPI_API_KEY`. It works with the new skill.

The slash commands (`/transcribe`, `/generate-image`, ...) are gone. Ask your agent in plain
language ("transcribe this video", "generate an image of ...") and the `deapi` skill handles it.
The skill folder is still `deapi/` at the repository root.

## Skills

| Skill | What it does |
|-------|--------------|
| **[deapi](deapi)** | Transcription of files and YouTube, X, TikTok, Twitch or Kick links, text-to-image, image editing, OCR, background removal, upscaling, text-to-speech with voice cloning and voice design, music, text-to-video, image animation, embeddings, prompt boosting and balance. Python standard library only; models are discovered live, so no slugs are hardcoded. |

## Setup

### 1. Get an API key

Sign up at [deapi.ai](https://deapi.ai) (free credit on signup, no card required) and create a
key at [app.deapi.ai](https://app.deapi.ai). Keys look like `sk-dp-<id>-<secret>`.

### 2. Set the key

```bash
export DEAPI_API_KEY="sk-dp-..."
```

Hermes Agent asks for the key on first use and stores it for you.

### 3. Install

**Any agent, via [skills.sh](https://skills.sh):**

```bash
npx skills add deapi-ai/skills
```

**Claude Code** (manual):

```bash
git clone https://github.com/deapi-ai/skills.git
mkdir -p ~/.claude/skills && cp -r skills/deapi ~/.claude/skills/
```

**Hermes Agent:**

```bash
git clone https://github.com/deapi-ai/skills.git
mkdir -p ~/.hermes/skills/creative && cp -r skills/deapi ~/.hermes/skills/creative/
```

**Codex, Cursor, Goose, OpenCode, VS Code Copilot** (shared `~/.agents/skills` convention):

```bash
git clone https://github.com/deapi-ai/skills.git
mkdir -p ~/.agents/skills && cp -r skills/deapi ~/.agents/skills/
```

The skill needs Python 3.9 or newer and network access to `api.deapi.ai`.

### 4. Use

Ask your agent naturally: "transcribe this YouTube video, it's in English", "generate an image
of a red fox", "read the text in scan.png". The helper script also works on its own:

```bash
python3 deapi/scripts/deapi.py stt --url "https://www.youtube.com/watch?v=..." --lang en
python3 deapi/scripts/deapi.py image --prompt "a red fox, studio light" --output fox.png
python3 deapi/scripts/deapi.py models --type image
python3 deapi/scripts/deapi.py balance
```

## Other ways to use deAPI from an agent

- **MCP server:** `https://mcp.deapi.ai/mcp` exposes the same jobs as MCP tools
  ([deapi-ai/mcp-server-deapi](https://github.com/deapi-ai/mcp-server-deapi)). Use it in
  clients without a terminal, such as claude.ai.
- **OpenAI-compatible API:** `https://oai.deapi.ai/v1` works with OpenAI SDKs for images,
  speech, transcriptions, embeddings and videos.
- **Native REST API:** [docs.deapi.ai](https://docs.deapi.ai)
  (OpenAPI: [openapi.json](https://docs.deapi.ai/openapi.json)).

## Skill format

```
deapi/
├── SKILL.md              # YAML frontmatter (name, description, metadata) + instructions
├── scripts/deapi.py      # helper CLI for the deAPI v2 API
└── references/           # API reference and model discovery guide
```

## License

[MIT](LICENSE)
