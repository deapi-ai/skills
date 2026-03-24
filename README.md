# deAPI Skills

Agent skills for [deAPI](https://deapi.ai) — ready-to-use bash scripts that let AI agents generate speech, clone voices, transcribe audio, and more.

Compatible with [Claude Code](https://claude.ai/claude-code), Cursor, Windsurf, and any agent platform that supports the community skills format. Up to 20x cheaper inference via decentralized GPU network.

## Skills

### Audio

| Skill | Description | Scripts |
|-------|-------------|---------|
| **[deapi-audio](deapi-audio)** | Text-to-speech, voice cloning, voice design, and audio transcription | `text-to-speech.sh`, `voice-clone.sh`, `voice-design.sh`, `speech-to-text.sh` |

## Setup

### 1. Get your API key

Sign up at [deapi.ai](https://deapi.ai) (free $5 credit, no card required).

### 2. Set the key

```bash
export DEAPI_API_KEY="your_key_here"
```

Or let the agent store it in a config file on first use — it will ask for your key and write it to `config.json` inside the skill directory.

### 3. Install

```bash
git clone https://github.com/deapi-ai/skills.git
cp -r skills/deapi-audio ~/.claude/skills/
```

### 4. Use with any agent

Every skill is a standalone bash script. Call them directly:

```bash
# Text-to-speech
bash scripts/text-to-speech.sh --text "Hello world" --voice bf_emma

# Voice clone
bash scripts/voice-clone.sh --text "Hello in your voice" --ref-audio sample.mp3

# Voice design
bash scripts/voice-design.sh --text "Good morning" \
  --instruct "A warm male voice with a British accent"

# Transcribe audio
bash scripts/speech-to-text.sh --audio recording.mp3
```

## Skill Format

Each skill follows the community skills standard:

```
deapi-audio/
├── SKILL.md              # Metadata + documentation (YAML frontmatter)
├── scripts/              # Executable bash scripts
│   ├── common.sh
│   ├── text-to-speech.sh
│   ├── voice-clone.sh
│   ├── voice-design.sh
│   └── speech-to-text.sh
└── references/           # Model docs, voice lists, API details
    ├── api.md
    └── voices.md
```

The `SKILL.md` contains a YAML frontmatter with `name`, `description`, and `metadata`, followed by usage documentation that the agent reads to understand how to use the skill.

## License

MIT
