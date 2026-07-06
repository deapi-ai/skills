# deAPI Skills

Agent skills for [deAPI](https://deapi.ai) — let AI agents generate images, speech,
music and video, transcribe audio, extract text from images, and more.

Compatible with the [agentskills.io](https://agentskills.io) open standard:
**Hermes Agent, Claude Code, Cursor, Goose, OpenCode, Codex, GitHub Copilot,
Windsurf** and 40+ other clients. Up to 20x cheaper inference via decentralized
GPU network.

## Skills

| Skill | Description |
|-------|-------------|
| **[deapi](deapi)** | All deAPI modalities through one portable skill: text-to-image (FLUX.2 Klein), image editing, TTS, transcription (YouTube/X/Twitch links or files), OCR, background removal, upscaling, music, video, embeddings. Python (stdlib only), live model discovery — no hardcoded slugs. |
| **[deapi-audio](deapi-audio)** | Audio only, bash + curl/jq: text-to-speech, voice cloning, voice design, audio transcription. |

Pick **deapi** for full coverage and portability (works in Hermes with automatic
API-key handling); pick **deapi-audio** if you want zero-Python bash scripts for
audio tasks.

## Setup

### 1. Get your API key

Sign up at [deapi.ai](https://deapi.ai) (free $5 credit, no card required) and
create a key (format `dpn-sk-...`).

### 2. Set the key

```bash
export DEAPI_API_KEY="dpn-sk-..."
```

(Hermes Agent instead asks for the key on first use and stores it in `~/.hermes/.env`.)

### 3. Install

**Hermes Agent:**

```bash
mkdir -p ~/.hermes/skills/creative
git clone https://github.com/deapi-ai/skills.git
cp -r skills/deapi ~/.hermes/skills/creative/deapi
```

**Claude Code:**

```bash
mkdir -p ~/.claude/skills
git clone https://github.com/deapi-ai/skills.git
cp -r skills/deapi ~/.claude/skills/       # and/or skills/deapi-audio
```

**Cursor / Goose / OpenCode / Codex / VS Code Copilot** (shared convention):

```bash
mkdir -p ~/.agents/skills
cp -r skills/deapi ~/.agents/skills/
```

Or via [skills.sh](https://skills.sh): `npx skills add deapi-ai/skills`

### 4. Use

Ask your agent naturally — "generate an image of a red fox", "transcribe this
YouTube video", "read the text in scan.png". Direct CLI also works:

```bash
python3 deapi/scripts/deapi.py image --prompt "a red fox, studio light" --output fox.png
python3 deapi/scripts/deapi.py stt --url "https://www.youtube.com/watch?v=..."
python3 deapi/scripts/deapi.py models --type image
bash deapi-audio/scripts/text-to-speech.sh --text "Hello world" --voice bf_emma
```

## Skill Format

Each skill follows the [agentskills.io](https://agentskills.io) standard:

```
deapi/
├── SKILL.md              # YAML frontmatter (name, description, metadata) + docs
├── scripts/              # executable helpers
└── references/           # API reference, model discovery guide
```

## Related

- deAPI docs: https://docs.deapi.ai
- deAPI MCP server (alternative integration path): https://github.com/deapi-ai/mcp-server-deapi

## License

MIT
